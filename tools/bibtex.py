"""Small dependency-free BibTeX reader for the site's publication bibliography.

It accepts common Zotero and Google Scholar exports, including nested braces,
quoted values, string macros and concatenation. Output fields contain plain
Unicode text; unknown fields are retained so exports remain useful.
"""

from __future__ import annotations

import re
import unicodedata


_ACCENTS = {
    "'": "\u0301", '`': "\u0300", '^': "\u0302", '"': "\u0308",
    '~': "\u0303", '=': "\u0304", '.': "\u0307", 'u': "\u0306",
    'v': "\u030c", 'H': "\u030b", 'c': "\u0327", 'k': "\u0328",
    'b': "\u0331", 'd': "\u0323", 'r': "\u030a",
}
_LETTERS = {
    'ae': 'æ', 'AE': 'Æ', 'oe': 'œ', 'OE': 'Œ', 'aa': 'å', 'AA': 'Å',
    'o': 'ø', 'O': 'Ø', 'l': 'ł', 'L': 'Ł', 'ss': 'ß', 'i': 'ı', 'j': 'ȷ',
    'LaTeX': 'LaTeX', 'TeX': 'TeX', 'textbackslash': '\\',
    'textasciitilde': '~', 'textasciicircum': '^', 'textendash': '–',
    'textemdash': '—', 'textquotesingle': "'", 'textquotedbl': '"',
}


def bibtex_to_text(value: str) -> str:
    """Convert common TeX escapes and formatting to readable Unicode text."""
    # A bare TeX tilde is a nonbreaking space. An escaped literal tilde or
    # \textasciitilde{} must remain a tilde after command conversion.
    value = re.sub(r'(?<!\\)~', ' ', value)
    # Accent commands can wrap either a letter or a dotless i/j command.
    accent_pattern = r"\\([\'`^\"~=\.]|[uvHckbdr](?=\s|\{))\s*(?:\{\s*(\\?[A-Za-z])\s*\}|(\\?[A-Za-z]))"

    def accent(match: re.Match[str]) -> str:
        letter = (match.group(2) or match.group(3)).lstrip('\\')
        if letter in ('i', 'j'):
            letter = {'i': 'i', 'j': 'j'}[letter]
        return unicodedata.normalize('NFC', letter + _ACCENTS[match.group(1)])

    value = re.sub(accent_pattern, accent, value)
    value = re.sub(
        r'\\([A-Za-z]+)(?:\{\})?',
        lambda match: _LETTERS.get(match.group(1), ''),
        value,
    )
    value = re.sub(r'(?<!\\)[{}]', '', value)
    value = re.sub(r'\\([&%_$#{}~"])', lambda match: match.group(1), value)
    value = value.replace('\\ ', ' ')
    value = value.replace('---', '—').replace('--', '–')
    return re.sub(r'\s+', ' ', value).strip()


def _split_protected(value: str, separator: str) -> list[str]:
    """Split at a separator outside braced protection (including author 'and')."""
    parts = []
    start = pos = depth = 0
    while pos < len(value):
        char = value[pos]
        if char == '\\':
            pos += 2
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
        if depth == 0:
            match = re.match(separator, value[pos:], flags=re.IGNORECASE)
            if match:
                parts.append(value[start:pos].strip())
                pos += len(match.group())
                start = pos
                continue
        pos += 1
    parts.append(value[start:].strip())
    return parts


def _fully_braced(value: str) -> bool:
    if not value.startswith('{') or not value.endswith('}'):
        return False
    depth = pos = 0
    while pos < len(value):
        if value[pos] == '\\':
            pos += 2
            continue
        if value[pos] == '{':
            depth += 1
        elif value[pos] == '}':
            depth -= 1
        pos += 1
        if depth == 0 and pos < len(value):
            return False
    return depth == 0


def _normalize_authors(value: str) -> str:
    # Full-name braces are meaningful: {Research and Development, Inc.} is
    # one organization, not two people. Preserve only that protection while
    # still converting accent and capitalization braces to Unicode text.
    result = []
    for name in _split_protected(value, r'\s+and\s+'):
        text = bibtex_to_text(name)
        result.append('{' + text + '}' if _fully_braced(name) else text)
    return ' and '.join(result)


def author_names(value: str) -> list[str]:
    """Return display names from BibTeX's ``and``-separated author field.

    Handles ``First Last``, ``Last, First`` and ``Last, Jr, First`` names.
    A fully braced organization name is left intact.
    """
    names = []
    for name in _split_protected(value, r'\s+and\s+'):
        name = name.strip()
        if not name:
            continue
        if _fully_braced(name):
            names.append(bibtex_to_text(name))
            continue
        parts = _split_protected(name, ',')
        if len(parts) == 2:
            name = f'{parts[1]} {parts[0]}'
        elif len(parts) >= 3:
            name = f'{parts[-1]} {parts[0]}, {", ".join(parts[1:-1])}'
        names.append(bibtex_to_text(name))
    return names


_IDENTIFIER_FIELDS = {'url', 'doi', 'pdf', 'slides', 'file', 'eprint'}
_WEBSITE_FIELDS = {'selected', 'pdf', 'slides', 'shortvenue', 'venue_short', 'legacy_note'}


def _escape_text(value: str) -> str:
    replacements = {
        '\\': r'\textbackslash{}', '{': r'\{', '}': r'\}',
        '~': r'\textasciitilde{}', '&': r'\&', '%': r'\%', '$': r'\$',
        '#': r'\#', '_': r'\_',
    }
    return ''.join(replacements.get(char, char) for char in value)


def _escape_authors(value: str) -> str:
    result = []
    for name in _split_protected(value, r'\s+and\s+'):
        if _fully_braced(name):
            result.append('{' + _escape_text(name[1:-1]) + '}')
        else:
            result.append(_escape_text(name))
    return ' and '.join(result)


def serialize_bibtex(entry: dict[str, str], *, website_fields: bool = True) -> str:
    """Serialize a parsed record safely, preserving all bibliographic fields.

    With ``website_fields=False``, omit presentation-only fields from a public
    citation. A BibTeX ``type`` field is represented as ``bibtex_type`` in parsed
    dictionaries because ``type`` holds the entry kind (article, book, etc.).
    """
    kind, key = str(entry.get('type', 'misc')), str(entry.get('id', ''))
    if not re.fullmatch(r'[A-Za-z]+', kind) or not re.fullmatch(r'[^\s{},()]+', key):
        raise ValueError('Each publication needs a valid BibTeX entry type and citation key.')
    fields = []
    for name, value in entry.items():
        if name in ('id', 'type') or (not website_fields and name in _WEBSITE_FIELDS):
            continue
        field = 'type' if name == 'bibtex_type' else name
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_:.+\-/]*', field):
            raise ValueError(f'Invalid BibTeX field name: {name!r}')
        value = str(value)
        if field in _IDENTIFIER_FIELDS:
            # Retain URL punctuation, including &, ~ and _, while ensuring
            # literal braces cannot break the surrounding BibTeX value.
            escaped = value.replace('{', r'\{').replace('}', r'\}')
        elif field in ('author', 'editor'):
            escaped = _escape_authors(value)
        else:
            escaped = _escape_text(value)
            if field == 'title':
                # Parsing converts capitalization braces to plain Unicode.
                # Protect the complete exported title so BibTeX styles cannot
                # silently change acronyms such as AI, WiFi, or VoicePM.
                escaped = '{' + escaped + '}'
        fields.append(f'  {field} = {{{escaped}}}')
    return '@' + kind + '{' + key + ',\n' + ',\n'.join(fields) + '\n}'


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.macros = dict(zip(
            ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'),
            ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
             'September', 'October', 'November', 'December'),
        ))

    def error(self, message: str) -> ValueError:
        line = self.source.count('\n', 0, self.pos) + 1
        last_newline = self.source.rfind('\n', 0, self.pos)
        return ValueError(f'BibTeX line {line}, column {self.pos - last_newline}: {message}')

    def skip(self) -> None:
        while self.pos < len(self.source):
            if self.source[self.pos].isspace():
                self.pos += 1
            elif self.source[self.pos] == '%':
                end = self.source.find('\n', self.pos)
                self.pos = len(self.source) if end < 0 else end + 1
            else:
                break

    def expect(self, char: str) -> None:
        self.skip()
        if self.pos >= len(self.source) or self.source[self.pos] != char:
            raise self.error(f'Expected {char!r}; check the preceding field for a missing comma or delimiter.')
        self.pos += 1

    def identifier(self) -> str:
        self.skip()
        match = re.match(r'[A-Za-z][A-Za-z0-9_:.+\-/]*', self.source[self.pos:])
        if not match:
            raise self.error('Expected a BibTeX entry type, field name, or string name.')
        self.pos += len(match.group())
        return match.group()

    def braced(self) -> str:
        self.expect('{')
        start = self.pos
        depth = 1
        while self.pos < len(self.source):
            char = self.source[self.pos]
            if char == '\\':
                self.pos += 2
                continue
            self.pos += 1
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return self.source[start:self.pos - 1]
        raise self.error('Unclosed braced value; add its closing }.')

    def quoted(self) -> str:
        self.expect('"')
        start = self.pos
        depth = 0
        while self.pos < len(self.source):
            char = self.source[self.pos]
            if char == '\\':
                self.pos += 2
                continue
            self.pos += 1
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth < 0:
                    raise self.error('Unbalanced } inside a quoted value.')
            elif char == '"' and depth == 0:
                return self.source[start:self.pos - 1]
        raise self.error('Unclosed quoted value; add its closing ".')

    def value(self, closing: str) -> str:
        pieces = []
        while True:
            self.skip()
            if self.pos >= len(self.source):
                raise self.error('Expected a field value before the end of the file.')
            char = self.source[self.pos]
            if char == '{':
                pieces.append(self.braced())
            elif char == '"':
                pieces.append(self.quoted())
            else:
                start = self.pos
                while self.pos < len(self.source):
                    char = self.source[self.pos]
                    if char.isspace() or char in ',#%{}()="':
                        break
                    self.pos += 1
                token = self.source[start:self.pos]
                if not token:
                    raise self.error('Expected a braced, quoted, numeric, or string-macro value.')
                pieces.append(self.macros.get(token.lower(), token))
            self.skip()
            if self.pos < len(self.source) and self.source[self.pos] == '#':
                self.pos += 1
                continue
            if self.pos >= len(self.source) or self.source[self.pos] not in (',', closing):
                raise self.error('Expected a comma or closing delimiter after the field value.')
            return ''.join(pieces)

    def comment(self, opening: str, closing: str) -> None:
        depth = 1
        while self.pos < len(self.source):
            char = self.source[self.pos]
            self.pos += 1
            if char == '\\':
                self.pos += 1
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return
        raise self.error('Unclosed @comment entry.')

    def parse(self) -> list[dict[str, str]]:
        records = []
        ids = set()
        while self.pos < len(self.source):
            self.skip()
            if self.pos >= len(self.source):
                break
            if self.source[self.pos] != '@':
                # BibTeX permits explanatory prose outside entries.
                self.pos += 1
                continue
            self.pos += 1
            kind = self.identifier().lower()
            self.skip()
            if self.pos >= len(self.source) or self.source[self.pos] not in '{(':
                raise self.error(f'Expected {{ or ( after @{kind}.')
            opening = self.source[self.pos]
            closing = '}' if opening == '{' else ')'
            self.pos += 1
            if kind == 'comment':
                self.comment(opening, closing)
                continue
            if kind == 'preamble':
                self.value(closing)
                self.skip()
                if self.pos < len(self.source) and self.source[self.pos] == ',':
                    self.pos += 1
                self.expect(closing)
                continue
            if kind == 'string':
                name = self.identifier().lower()
                self.expect('=')
                self.macros[name] = self.value(closing)
                self.skip()
                if self.pos < len(self.source) and self.source[self.pos] == ',':
                    self.pos += 1
                self.expect(closing)
                continue
            self.skip()
            start = self.pos
            while self.pos < len(self.source) and self.source[self.pos] not in ',{}()\n\r':
                self.pos += 1
            key = self.source[start:self.pos].strip()
            if not key or re.search(r'\s', key):
                raise self.error('Expected a nonempty citation key without spaces before the first comma.')
            if key in ids:
                raise self.error(f'Duplicate citation key {key!r}; give each publication a unique key.')
            ids.add(key)
            record = {'id': key, 'type': kind}
            self.skip()
            if self.pos < len(self.source) and self.source[self.pos] == closing:
                self.pos += 1
                records.append(record)
                continue
            self.expect(',')
            while True:
                self.skip()
                if self.pos < len(self.source) and self.source[self.pos] == closing:
                    self.pos += 1
                    break
                field = self.identifier().lower()
                self.expect('=')
                raw = self.value(closing)
                target_field = 'bibtex_type' if field == 'type' else field
                if field in _IDENTIFIER_FIELDS:
                    # URLs and file paths are identifiers, not typeset prose:
                    # preserve tildes, repeated hyphens and other literal text.
                    record[target_field] = re.sub(r'\\([&%_$#{}~])', lambda m: m.group(1), raw).strip()
                elif field in ('author', 'editor'):
                    record[target_field] = _normalize_authors(raw)
                else:
                    record[target_field] = bibtex_to_text(raw)
                self.skip()
                if self.pos < len(self.source) and self.source[self.pos] == ',':
                    self.pos += 1
                else:
                    self.expect(closing)
                    break
            records.append(record)
        return records


def parse_bibtex(text: str) -> list[dict[str, str]]:
    """Read BibTeX into dictionaries; report malformed input with line/column."""
    return _Parser(text).parse()

import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from bibtex import author_names, bibtex_to_text, parse_bibtex, serialize_bibtex


class BibTeXTests(unittest.TestCase):
    def test_export_styles_and_accents(self):
        records = parse_bibtex(r'''
            % Zotero-style comments and Google Scholar-style bracing
            @string{venue = "Proceedings of "}
            @string{suffix = {ACM}}
            @comment{Ignore {nested} content}
            @preamble{"Ignored " # {formatting}}
            @inproceedings(smith2024,
                title = {A {WiFi} Study of {\"O}zil and {\'e}tudes},
                author = "Garc{\'i}a, Jos{\'e} and Smith, Jr, John",
                booktitle = venue # suffix,
                year = 2024,
                month = jan,
                url = {https://example.org/a_b?x=1&y=2},
            )
        ''')
        self.assertEqual(len(records), 1)
        item = records[0]
        self.assertEqual(item['title'], 'A WiFi Study of Özil and études')
        self.assertEqual(item['booktitle'], 'Proceedings of ACM')
        self.assertEqual(item['month'], 'January')
        self.assertEqual(item['url'], 'https://example.org/a_b?x=1&y=2')
        self.assertEqual(author_names(item['author']), ['José García', 'John Smith, Jr'])

    def test_quoted_braces_escaped_quotes_and_symbol_commands(self):
        item = parse_bibtex(r'''@article{x,
            title = "A {\"o} and {nested {braces}} with {\"}quoted{\"} text",
            note = {\textit{Privacy} \& Security \% \_ \#},
        }''')[0]
        self.assertEqual(item['title'], 'A ö and nested braces with "quoted" text')
        self.assertEqual(item['note'], 'Privacy & Security % _ #')
        self.assertEqual(bibtex_to_text(r'\url{example.org} and \v{S}imek with \{braces\}'),
                         'example.org and Šimek with {braces}')

    def test_identifiers_preserve_url_characters(self):
        item = parse_bibtex(r'''@misc{x,
            url = {https://example.org/~person/a--b?x=1\&y=2},
            pdf = {publication/my--paper.pdf},
        }''')[0]
        self.assertEqual(item['url'], 'https://example.org/~person/a--b?x=1&y=2')
        self.assertEqual(item['pdf'], 'publication/my--paper.pdf')

    def test_corporate_authors_preserve_and_and_commas(self):
        item = parse_bibtex(r'''@techreport{agency2024,
            author = {{Research and Development, Inc.} and {World Health Organization} and Garc{\'i}a, Jos{\'e}},
            editor = {{National Academies of Sciences, Engineering, and Medicine} and Doe, Jane},
            title = {{AI} \& Privacy},
            institution = {Research and Development, Inc.},
            type = {Technical Report},
            number = {TR-24-3},
            year = 2024,
        }''')[0]
        self.assertEqual(item['type'], 'techreport')
        self.assertEqual(item['bibtex_type'], 'Technical Report')
        self.assertEqual(author_names(item['author']), [
            'Research and Development, Inc.', 'World Health Organization', 'José García'])
        self.assertEqual(author_names(item['editor']), [
            'National Academies of Sciences, Engineering, and Medicine', 'Jane Doe'])
        self.assertEqual(parse_bibtex(serialize_bibtex(item)), [item])

    def test_citation_roundtrip_preserves_bibliographic_fields(self):
        item = {
            'id': 'roundtrip2024', 'type': 'article',
            'title': 'AI {systems}: 50% & $5 #1_test ~ paths\\notes',
            'author': '{Research and Development, Inc.} and Doe, Jane',
            'year': '2024', 'journal': 'Transactions on Privacy',
            'volume': '12', 'number': '3', 'pages': '10–20',
            'publisher': 'Publisher & Co.',
            'doi': '10.1234/a--b',
            'url': 'https://example.org/~person/a--b?x=1&y=2',
            'selected': 'true', 'pdf': 'publication/my--paper.pdf',
            'slides': 'publication/slides.pptx', 'shortvenue': 'TEST',
        }
        self.assertEqual(parse_bibtex(serialize_bibtex(item)), [item])
        citation = parse_bibtex(serialize_bibtex(item, website_fields=False))[0]
        self.assertEqual(citation, {k: v for k, v in item.items()
                                   if k not in {'selected', 'pdf', 'slides', 'shortvenue'}})

    def test_serialization_validates_structural_fields(self):
        for entry in ({'id': 'bad}key', 'type': 'article'},
                      {'id': 'okay', 'type': 'article}bad'},
                      {'id': 'okay', 'type': 'article', 'bad=field': 'value'}):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    serialize_bibtex(entry)

    def test_actionable_malformed_errors(self):
        invalid = [
            '@article{x, title = {Missing brace}',
            '@article{x, title = "Missing quote}',
            '@article{x, title = {Missing comma} year = 2024}',
            '@article{x, year = }',
            '@article{, title = {Missing key}}',
            '@article{x, title={A}} @article{x, title={B}}',
        ]
        for source in invalid:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, r'BibTeX line \d+, column \d+:'):
                    parse_bibtex(source)

    def test_undated_records_keep_year_optional(self):
        item = parse_bibtex('@misc{undated,title={An undated thesis},note={Thesis}}')[0]
        self.assertNotIn('year', item)
        self.assertEqual(parse_bibtex(serialize_bibtex(item)), [item])

    def test_editable_bibliography_has_unique_keys_and_existing_local_assets(self):
        items = parse_bibtex((ROOT / 'data/publications.bib').read_text(encoding='utf-8'))
        self.assertTrue(items)
        self.assertEqual(len({item['id'].casefold() for item in items}), len(items))
        for item in items:
            for field in ('pdf', 'slides'):
                if item.get(field) and not urlsplit(item[field]).scheme:
                    with self.subTest(publication=item['id'], field=field):
                        self.assertTrue((ROOT / item[field]).is_file(), item[field])


if __name__ == '__main__':
    unittest.main()

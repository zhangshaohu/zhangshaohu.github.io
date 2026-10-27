#!/usr/bin/env python3
"""Build the academic website from editable content. Python 3.9+, no packages."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

if __package__:
    from .bibtex import author_names, parse_bibtex, serialize_bibtex
    from .email_images import render_email
else:
    from bibtex import author_names, parse_bibtex, serialize_bibtex
    from email_images import render_email

ROOT = Path(__file__).resolve().parent.parent
NAV = [("index.html", "About"), ("lab.html", "Lab"), ("research.html", "Research"),
       ("publication.html", "Publications"), ("student.html", "Team"),
       ("photo.html", "Photos")]
ARROW = '<span aria-hidden="true">↗</span>'


def esc(value):
    return html.escape(str(value), quote=True)


def safe_url(value):
    value = str(value).strip()
    parsed = urlsplit(value)
    if not value or parsed.scheme.lower() not in ("", "http", "https", "mailto") or value.startswith("//"):
        raise ValueError(f"Unsupported or empty link: {value!r}")
    if not parsed.scheme and (".." in Path(parsed.path).parts or value.startswith("/") or "\\" in value):
        raise ValueError(f"Local links must stay inside the website: {value!r}")
    return esc(value)


def a(url, label, cls="", arrow=False):
    return f'<a href="{safe_url(url)}"' + (f' class="{esc(cls)}"' if cls else '') + f'>{esc(label)}{(" " + ARROW) if arrow else ""}</a>'


def terms(value):
    return "; ".join(sorted(value, key=chronology_key, reverse=True)) if isinstance(value, list) else str(value)


MONTHS = ('january', 'february', 'march', 'april', 'may', 'june',
          'july', 'august', 'september', 'october', 'november', 'december')
DATE_WORDS = {word: number for number, month in enumerate(MONTHS, 1)
              for word in (month, month[:3])}
DATE_WORDS.update(sept=9, winter=1, spring=3, summer=6, fall=9, autumn=9)


def chronology_key(value):
    """Use the latest stated date; year ranges use their end and ties stay stable.

    Missing month/day values stay unspecified (zero); undated text sorts last.
    Ignore URLs so a course link's address cannot change its term order.
    """
    text = re.sub(r'https?://\S+', '', value, flags=re.IGNORECASE)
    dates = [(int(year), 0, 0) for year in re.findall(r'\b((?:19|20)\d{2})\b', text)]
    for stamp in re.findall(r'\b(?:19|20)\d{2}-\d{2}-\d{2}\b', text):
        try:
            parsed = date.fromisoformat(stamp)
        except ValueError:
            continue
        dates.append((parsed.year, parsed.month, parsed.day))
    pattern = (r'\b(' + '|'.join(DATE_WORDS)
               + r')\.?\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?((?:19|20)\d{2})\b')
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        dates.append((int(match[3]), DATE_WORDS[match[1].lower()], int(match[2] or 0)))
    return max(dates, default=(0, 0, 0))


def display_date(value):
    d = date.fromisoformat(value)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def validate_site(data, generated_assets=()):
    if not isinstance(data, dict) or not isinstance(data.get("profile"), dict):
        raise ValueError("data/site.json needs a profile object.")
    p = data["profile"]
    for key in ("name", "role", "department", "institution", "email_image", "portrait", "cv", "scholar", "orcid", "lab_name", "lab_url", "github"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ValueError(f"profile.{key} must be a nonempty text value.")
    if any(p.get(key) for key in ('email', 'secondary_email')):
        raise ValueError("Keep email addresses in the local editor's private contact settings, not data/site.json.")
    if not isinstance(p.get('chinese_name', ''), str):
        raise ValueError('profile.chinese_name must be text.')
    for key in ('email_image', 'secondary_email_image'):
        if not p.get(key):
            continue
        path = Path(p[key])
        safe_url(p[key])
        if not p[key].startswith('assets/') or path.suffix.lower() != '.png':
            raise ValueError(f'profile.{key} must point to a PNG image in assets/.')
        if p[key] not in generated_assets and not (ROOT / p[key]).is_file():
            raise ValueError(f'Missing contact image: {p[key]}. Set the address in the local editor.')
    if not isinstance(p.get("bio"), list) or any(not isinstance(t, str) for t in p["bio"]):
        raise ValueError("profile.bio must be a list of paragraphs.")
    for key in ("portrait", "cv", "scholar", "orcid", "lab_url", "github"):
        safe_url(p[key])
    for key in ("portrait", "cv"):
        if not (ROOT / p[key]).is_file():
            raise ValueError(f"profile.{key} points to a missing local file: {p[key]}")
    schema = {"research": ("title", "description", "tag"), "news": ("date", "text"),
              "teaching": ("code", "title", "terms"), "team": ("name", "role", "focus", "since"),
              "service": ("title", "items"), "collaborators": ("name", "url", "affiliation"),
              "resources": ("title", "url", "description")}
    for section, keys in schema.items():
        if not isinstance(data.get(section), list):
            raise ValueError(f"{section} must be a list in data/site.json.")
        for i, row in enumerate(data[section]):
            if not isinstance(row, dict) or any(k not in row for k in keys):
                raise ValueError(f"{section} item {i + 1} needs: {', '.join(keys)}.")
            for key in keys:
                expected = list if key in ("items", "terms") else str
                if not isinstance(row[key], expected) or (expected is list and any(not isinstance(x, str) for x in row[key])):
                    raise ValueError(f"{section} item {i + 1}: {key} must be {'a list of text' if expected is list else 'text'}.")
            if "url" in row and row["url"]:
                safe_url(row["url"])
    for news in data["news"]:
        try:
            date.fromisoformat(news["date"])
        except ValueError:
            raise ValueError(f"News date must use YYYY-MM-DD: {news['date']!r}") from None
    for member in data['team']:
        team_photo(member)
    if 'lab' in data:
        lab = data['lab']
        if not isinstance(lab, dict) or not isinstance(lab.get('tagline', ''), str):
            raise ValueError('lab must contain a text tagline.')
        if not isinstance(lab.get('description', []), list) or any(not isinstance(t, str) for t in lab.get('description', [])):
            raise ValueError('lab.description must be a list of paragraphs.')
    if not isinstance(data.get("awards"), list) or any(not isinstance(t, str) for t in data["awards"]):
        raise ValueError("awards must be a list of text.")
    for link in p.get("bio_links", data.get("bio_links", [])):
        safe_url(link["url"])


def photos():
    result = []
    directory = ROOT / "photos"
    for path in directory.iterdir() if directory.exists() else []:
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            continue
        match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[_ -]+|$)(.*)", path.stem)
        stamp = match[1] if match else ""
        if stamp:
            try:
                date.fromisoformat(stamp)
            except ValueError:
                raise ValueError(f"Photo filename has an invalid date: {path.name}") from None
        caption_file = path.with_suffix(".txt")
        caption = caption_file.read_text(encoding="utf-8").strip() if caption_file.exists() else ""
        caption = caption or re.sub(r"[_-]+", " ", match[2] if match else path.stem).strip() or "Research gallery"
        result.append({"src": quote(path.relative_to(ROOT).as_posix()), "caption": caption, "date": stamp})
    return sorted(result, key=lambda x: (x["date"], x["src"]), reverse=True)


def pub_kind(pub):
    subtype = ' '.join(pub.get(k, '').lower() for k in ('title', 'note', 'pdf', 'entrysubtype'))
    if "poster" in subtype:
        return "Poster"
    if pub['type'] in ('thesis', 'mastersthesis', 'phdthesis') or (pub['type'] == 'misc' and 'thesis' in pub.get('note', '').lower()):
        return 'Thesis'
    return {"article": "Journal article", "techreport": "Report", "inproceedings": "Conference paper", "conference": "Conference paper", "book": "Book", "incollection": "Book chapter"}.get(pub["type"], "Other")


def publication(pub, name, compact=False):
    link = pub.get("pdf") or pub.get("url")
    if not link and pub.get("doi"):
        link = "https://doi.org/" + pub["doi"]
    title_html = a(link, pub["title"]) if link else esc(pub["title"])
    authors = ", ".join(f'<strong>{esc(n)}</strong>' if n == name else esc(n) for n in author_names(pub.get("author", "")))
    venue = pub.get("booktitle") or pub.get("journal") or pub.get("institution") or pub.get("school") or pub.get("note", "")
    label = pub.get("shortvenue", pub.get("venue_short", pub.get("year", "")))
    search = " ".join([pub["title"], " ".join(author_names(pub.get("author", ""))), venue, pub.get("year", "")])
    controls = []
    if pub.get("pdf"):
        controls.append(a(pub["pdf"], "PDF", "paper-link", True))
    elif link and not urlsplit(link).scheme and link.lower().endswith(".pdf"):
        controls.append(a(link, "PDF", "paper-link", True))
    elif link:
        controls.append(a(link, "Publisher", "paper-link", True))
    if pub.get("slides"):
        controls.append(a(pub["slides"], "Slides", "paper-link", True))
    citation = serialize_bibtex(pub, website_fields=False)
    if not compact:
        controls.append(f'<details class="citation"><summary>BibTeX</summary><div class="citation-body"><pre>{esc(citation)}</pre><button type="button" class="copy-citation">Copy citation</button></div></details>')
    return f'''<article class="paper{' paper-compact' if compact else ''}" data-paper data-year="{esc(pub.get('year', 'Undated'))}" data-kind="{esc(pub_kind(pub))}" data-search="{esc(search.lower())}">
      <div class="paper-marker">{esc(label or pub_kind(pub))}</div><div class="paper-content"><h3>{title_html}</h3>
      <p class="authors">{authors}</p><p class="venue">{esc(venue)}</p><div class="paper-actions">{''.join(controls)}</div></div></article>'''


def chinese_name(p, cls='chinese-name'):
    return f'<span class="{cls}" lang="zh-Hans">{esc(p["chinese_name"])}</span>' if p.get('chinese_name') else ''


def member_slug(member):
    return re.sub(r'[^a-z0-9]+', '-', member['name'].lower()).strip('-') or 'student'


def team_photo(member):
    """Use a chosen photo or discover a matching file; empty slots get a template."""
    photo = member.get('photo', '')
    if not isinstance(photo, str):
        raise ValueError(f"Headshot for {member['name']} must be a local photo path or blank.")
    if photo:
        safe_url(photo)
        path = Path(photo)
        if not photo.startswith('people/') or urlsplit(photo).scheme or path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            raise ValueError(f"Headshot for {member['name']} must be a JPG, PNG, or WebP in people/.")
        candidate = ROOT / path
        if candidate.exists() and not candidate.resolve().is_relative_to((ROOT / 'people').resolve()):
            raise ValueError(f"Headshot for {member['name']} must stay inside people/.")
        if candidate.is_file():
            return {'src': quote(path.as_posix()), 'placeholder': False}
    else:
        directory = ROOT / 'people'
        if directory.exists():
            slug = member_slug(member)
            for candidate in sorted(directory.iterdir()):
                if candidate.stem.lower() == slug and candidate.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and candidate.is_file() and not candidate.is_symlink():
                    return {'src': quote(candidate.relative_to(ROOT).as_posix()), 'placeholder': False}
    return {'src': 'assets/headshot-placeholder.svg', 'placeholder': True}


def team_member(member, show_role=False):
    photo = team_photo(member)
    alt = ('Headshot placeholder for ' if photo['placeholder'] else 'Headshot of ') + member['name']
    role = f'<p class="member-role">{esc(member["role"])}</p>' if show_role else ''
    focus = f'<p>{esc(member["focus"])}</p>' if member.get('focus') else ''
    since = f'<span class="member-since">Since {esc(member["since"])}</span>' if member.get('since') else ''
    return f'''<article class="team-member"><div class="member-photo{' is-placeholder' if photo['placeholder'] else ''}"><img class="member-headshot" src="{esc(photo['src'])}" alt="{esc(alt)}" width="240" height="300" loading="lazy"></div><div class="member-info">{role}<h3>{esc(member['name'])}</h3>{focus}{since}</div></article>'''


def contact_image(p, secondary=False):
    key = 'secondary_email_image' if secondary else 'email_image'
    if not p.get(key):
        return ''
    label = 'Secondary email address (image)' if secondary else 'Primary email address (image)'
    cls = 'alternate-contact contact-image' if secondary else 'contact-link contact-image'
    return f'<span class="{cls}"><img src="{safe_url(p[key])}" alt="{label}" height="{18 if secondary else 24}"></span>'


def page(title, current, content, p, description=""):
    nav = "".join(f'<a href="{url}"' + (' aria-current="page"' if url == current else '') + f'>{label}</a>' for url, label in NAV)
    full_title = p["name"] + (f' · {p["chinese_name"]}' if p.get('chinese_name') else '') + (" · " + title if current != "index.html" else " · Computer Science")
    person = {"@context": "https://schema.org", "@type": "Person", "name": p["name"], "jobTitle": p["role"], "affiliation": {"@type": "Organization", "name": p["institution"]}, "sameAs": [p["scholar"], p["orcid"], p["github"]]}
    if p.get('chinese_name'):
        person['alternateName'] = p['chinese_name']
    secondary = contact_image(p, True)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title><meta name="description" content="{esc(description or p['name'] + ' — ' + p['role'] + ', ' + p['institution'] + '. Research in trustworthy AI, mobile computing, security, and privacy.')}">
<meta name="theme-color" content="#004684"><meta property="og:title" content="{esc(full_title)}"><meta property="og:description" content="{esc(description or 'Research, publications, teaching, and people.')}"><meta property="og:type" content="website">
<link rel="icon" href="assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="css/main.css"><script src="assets/site.js" defer></script>
<script type="application/ld+json">{json.dumps(person, ensure_ascii=False).replace('<', chr(92) + 'u003c')}</script></head>
<body{(' class="about-page"' if current == 'index.html' else '')}><a class="skip-link" href="#main">Skip to content</a>
<header class="site-header"><div class="header-inner"><a class="brand" href="index.html" aria-label="{esc(p['name'])} home"><span class="monogram">SZ<span>·</span></span><span>{esc(p['name'])} {chinese_name(p, 'brand-chinese')}<small>Computer Science</small></span></a>
<button class="menu-toggle" type="button" aria-expanded="false" aria-controls="site-nav">Menu <span aria-hidden="true">☰</span></button><nav id="site-nav" aria-label="Main navigation">{nav}</nav></div></header>
<main id="main" class="site-main">{content}</main>
<footer class="site-footer" id="contact"><div class="footer-main"><div><p class="eyebrow">Get in touch</p><h2>Let’s connect</h2><p>For research conversations, collaboration, and student inquiries.</p>{contact_image(p)}</div>
<div class="footer-affiliation"><strong>{esc(p['name'])} {chinese_name(p)}, {esc(p.get('degree', 'Ph.D.'))}</strong><span>{esc(p['department'])}</span><span>{esc(p['institution'])}</span>{secondary}{a(p['lab_url'], p['lab_name'], arrow=True)}</div></div>
<div class="footer-bottom"><span>© {date.today().year} {esc(p['name'])}</span><div>{a(p['scholar'], 'Google Scholar')}{a(p['orcid'], 'ORCID')}{a(p['github'], 'GitHub')}{a('links.html', 'Resources')}{a('blog.html', 'News')}</div><span class="footer-note">Curiosity. Rigor. Impact.</span></div></footer>
</body></html>'''


def section_heading(label, title, url="", action="View all"):
    label_html = f'<p class="eyebrow">{esc(label)}</p>' if label else ''
    return f'<div class="section-heading"><div>{label_html}<h2>{esc(title)}</h2></div>{a(url, action, "text-link", True) if url else ""}</div>'


def page_intro(label, title, description, action=""):
    return f'<section class="page-intro"><p class="eyebrow">{esc(label)}</p><h1>{esc(title)}</h1><p class="page-deck">{esc(description)}</p>{action}</section>'


def news_list(items):
    return '<ol class="news-list">' + ''.join(f'<li><time datetime="{esc(n["date"])}">{display_date(n["date"])}</time><p>{a(n["url"], n["text"]) if n.get("url") else esc(n["text"])}</p></li>' for n in items) + '</ol>'


def home(data, pubs, gallery):
    p = data["profile"]
    recent = pubs[:6]
    intro = 'I study how intelligent systems can better protect people: their privacy, their security, and their everyday lives.'
    interests = ''.join(f'<a href="research.html#area-{i+1}">{esc(r["title"])}</a>' for i, r in enumerate(data["research"]))
    bio = ''.join(f'<p>{esc(t)}</p>' for t in p["bio"])
    bios = p.get("bio_links", data.get("bio_links", []))
    bio_links = '<div class="bio-links">' + ''.join(a(l["url"], l["label"], arrow=True) for l in bios) + '</div>' if bios else ''
    research = ''.join(f'<a class="research-row" href="research.html#area-{i+1}"><span class="row-number">0{i+1}</span><div><h3>{esc(r["title"])}</h3><p>{esc(r["description"])}</p></div>{ARROW}</a>' for i, r in enumerate(data['research']))
    photo_preview = ''
    if gallery:
        photo_preview = f'''<section class="home-photos">{section_heading('', 'Photos', 'photo.html', 'All photos')}<div class="home-photo-grid">{''.join(gallery_item(g) for g in gallery[:6])}</div></section>'''
    name_display = f'<span class="person-name">{esc(p["name"])}</span> {chinese_name(p, "hero-chinese")}'
    return f'''<section class="hero"><div class="hero-copy"><p class="eyebrow"><span class="status-dot"></span> {esc(p['institution'])}</p><h1>{name_display}</h1><p class="hero-role">{esc(p.get('degree', 'Ph.D.'))} <span>·</span> {esc(p['role'])} <span>·</span> {esc(p['department'])}</p><p class="hero-statement">{esc(p.get('intro', intro))}</p><div class="hero-actions">{a(p['cv'], 'Curriculum vitae', 'button button-primary', True)}{a(p['scholar'], 'Google Scholar', 'button button-text', True)}</div></div>
<figure class="hero-portrait"><div class="portrait-frame"><img src="{safe_url(p['portrait'])}" alt="{esc(p['name'])}" fetchpriority="high" width="1280" height="1920"><span class="portrait-corner" aria-hidden="true">+</span></div><figcaption><span>Research &amp; discovery</span>{a(p['lab_url'], p['lab_name'], arrow=True)}</figcaption></figure></section>
<div class="interest-strip"><span class="eyebrow">Research focus</span><div>{interests}</div></div>
<div class="home-columns"><div class="home-primary"><section class="content-section about-section">{section_heading('', 'Technology with people in mind')}<div class="prose">{bio}</div>{bio_links}</section>
<section class="content-section">{section_heading('', 'Research directions', 'research.html', 'Explore research')}<div class="research-rows">{research}</div></section>
<section class="content-section" id="recent-publications">{section_heading('', 'Recent publications', 'publication.html', 'All publications')}<div class="paper-list">{''.join(publication(pub, p['name'], True) for pub in recent)}</div></section>
<div class="about-contributions">{teaching_and_service(data)}</div></div>
<aside class="home-secondary"><section class="news-panel">{section_heading('The latest', 'News & notes', 'blog.html', 'Archive')}{news_list(data['news'][:5])}</section>
<section class="lab-note"><p class="eyebrow">Our research group</p><h2>{esc(p['lab_name'])}</h2><p>Working across disciplines to connect sensing, intelligence, and real-world applications.</p>{a('student.html', 'Meet the team', 'text-link', True)}</section>{photo_preview}</aside></div>{photo_dialog() if gallery else ''}'''


def publications_page(data, pubs):
    p = data['profile']
    years = sorted({x.get('year') for x in pubs if x.get('year')}, reverse=True)
    if any(not x.get('year') for x in pubs):
        years.append('Undated')
    kinds = sorted({pub_kind(x) for x in pubs})
    groups = defaultdict(list)
    for pub in pubs:
        groups[pub.get('year') or 'Undated'].append(pub)
    listing = ''.join(f'<section class="publication-year" data-year-group><div class="year-heading"><h2>{esc(year)}</h2><span>{len(groups[year]):02d}</span></div><div>{"".join(publication(x, p["name"]) for x in groups[year])}</div></section>' for year in years)
    action = '<div class="intro-links">' + a('data/publications.bib', 'Download bibliography', 'text-link', True) + a(p['scholar'], 'Google Scholar', 'text-link', True) + a(p['cv'], 'Curriculum vitae', 'text-link', True) + '</div>'
    return page_intro('Writing & discovery', 'Publications.', 'Research in security, privacy, sensing, and intelligent systems.', action) + f'''
<div class="publication-tools"><label class="search-field"><span class="sr-only">Search publications</span><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input id="publication-search" type="search" placeholder="Search titles, authors, or venues…"></label>
<label class="filter-label">Year<select id="publication-year"><option value="">All years</option>{''.join(f'<option>{esc(y)}</option>' for y in years)}</select></label><label class="filter-label">Type<select id="publication-kind"><option value="">All types</option>{''.join(f'<option>{esc(k)}</option>' for k in kinds)}</select></label></div>
<p class="result-count" id="publication-count" role="status">{len(pubs)} publications</p><div id="publication-list">{listing}</div><p id="no-publications" class="empty-state" hidden>No publications match your search. Try another title, author, or year.</p>'''


def gallery_item(g):
    return f'''<figure class="gallery-item"><a href="{esc(g['src'])}" data-photo><div class="gallery-image"><img src="{esc(g['src'])}" alt="{esc(g['caption'])}" loading="lazy"><span class="photo-expand" aria-hidden="true">↗</span></div></a><figcaption>{f'<time datetime="{g["date"]}">{display_date(g["date"])}</time>' if g['date'] else ''}<p>{esc(g['caption'])}</p></figcaption></figure>'''


def photo_dialog():
    return '''<dialog class="photo-dialog" id="photo-dialog"><div class="dialog-toolbar"><span id="photo-position"></span><button type="button" class="dialog-close" aria-label="Close photo">Close ×</button></div><img id="dialog-image" alt=""><div class="dialog-bottom"><button type="button" id="photo-prev" aria-label="Previous photo">←</button><p id="dialog-caption"></p><button type="button" id="photo-next" aria-label="Next photo">→</button></div></dialog>'''


def gallery_page(gallery):
    figures = ''.join(gallery_item(g) for g in gallery)
    return page_intro('Beyond the papers', 'People, places & moments.', 'A few snapshots from research, conferences, and life along the way.') + f'<div class="gallery-grid">{figures or "<p>New moments coming soon.</p>"}</div>' + photo_dialog()


def research_page(data):
    p = data['profile']
    sections = ''.join(f'<section class="research-detail" id="area-{i+1}"><div class="research-number">0{i+1}</div><div><p class="eyebrow">{esc(r["tag"])}</p><h2>{esc(r["title"])}</h2><p>{esc(r["description"])}</p></div></section>' for i,r in enumerate(data['research']))
    return page_intro('Questions that drive my work', 'Intelligence. Trust. Impact.', 'I explore the intersection of AI, ubiquitous sensing, and cybersecurity to build systems that work reliably for people.') + f'''<div class="research-details">{sections}</div><section class="research-collaboration"><p class="eyebrow">Across disciplines</p><h2>Connected problems.<br>Shared discoveries.</h2><p>My collaborations span transportation engineering, sociology, health, agriculture, and data science.</p><div class="intro-links">{a('publication.html', 'Explore the publications', 'text-link', True)}{a(p['lab_url'], p['lab_name'], 'text-link', True)}</div></section>'''


def team_page(data):
    p = data['profile']
    groups = defaultdict(list)
    for member in data['team']:
        groups[member['role']].append(member)
    sections = ''
    for role, members in groups.items():
        cards = ''.join(team_member(m) for m in members)
        heading = {'Ph.D. Student': 'Ph.D. students', 'Undergraduate Student': 'Undergraduate students'}.get(role, role)
        sections += f'<section class="team-section"><h2>{esc(heading)}</h2><div class="team-grid">{cards}</div></section>'
    return page_intro('The people behind the work', 'Our team.', 'A shared curiosity about intelligent systems and their impact on the world.', '<div class="intro-links">' + a(p['lab_url'], p['lab_name'], 'text-link', True) + '</div>') + sections + '<div class="team-contact"><h2>Interested in working together?</h2><p>Send a note about your interests and background.</p>' + a('#contact', 'Get in touch', 'button button-primary', True) + '</div>'


def lab_page(data, pubs):
    p, lab = data['profile'], data.get('lab', {})
    paragraphs = lab.get('description', [])
    introduction = ''.join(f'<p>{esc(t)}</p>' for t in paragraphs)
    research = ''.join(f'<a class="research-row" href="research.html#area-{i+1}"><span class="row-number">0{i+1}</span><div><h3>{esc(r["title"])}</h3><p>{esc(r["description"])}</p></div>{ARROW}</a>' for i,r in enumerate(data['research']))
    papers = ''.join(publication(pub, p['name'], True) for pub in pubs[:3])
    return f'''<section class="lab-hero"><div><p class="eyebrow">{esc(p['department'])} · North Carolina A&amp;T</p><h1>{esc(p['lab_name'])}.</h1><p class="lab-tagline">{esc(lab.get('tagline', 'Trustworthy intelligence for a connected world.'))}</p><div class="prose lab-description">{introduction}</div><div class="intro-links">{a('#lab-people', 'Meet our people', 'button button-primary', True)}{a('#contact', 'Connect with the lab', 'text-link', True)}</div></div>
<aside class="lab-director"><img src="{safe_url(p['portrait'])}" alt="{esc(p['name'])}, lab director" width="1280" height="1920"><div><p class="eyebrow">Lab director</p><h2>{a('index.html', p['name'])} {chinese_name(p)}</h2><p>{esc(p['role'])}</p>{a('index.html', 'About the director', 'text-link', True)}</div></aside></section>
<section class="content-section lab-research">{section_heading('Our research', 'Sensing, intelligence & trust.', 'research.html', 'Explore our directions')}<div class="lab-research-grid">{research}</div></section>
<section class="content-section" id="lab-people">{section_heading('Our people', 'Meet the group.', 'student.html', 'Full team')}<div class="team-grid lab-team-grid">{''.join(team_member(m, True) for m in data['team'])}</div></section>
<div class="lab-lower"><section class="content-section">{section_heading('Recent work by the lab director', 'Related publications.', 'publication.html', 'All publications')}<div>{papers}</div></section><aside class="news-panel">{section_heading('From the group', 'News & notes.', 'blog.html', 'Archive')}{news_list(data['news'][:3])}</aside></div>
<section class="lab-invitation"><p class="eyebrow">Build with us</p><h2>Bring a question.<br>Find a connection.</h2><p>Interested in research at the intersection of AI, security, and ubiquitous sensing? Tell us about your interests and background.</p>{a('#contact', 'Contact the lab', 'button button-primary', True)}</section>'''


def lab_description(data):
    return data.get('lab', {}).get('tagline') or 'Research in trustworthy AI, security, privacy, and ubiquitous sensing at X+ Intelligence Lab.'


def teaching_and_service(data):
    ordered_courses = sorted(data['teaching'], key=lambda course: max(
        (chronology_key(term) for term in course['terms']), default=(0, 0, 0)), reverse=True)
    courses = ''.join(f'<article class="course-row"><span class="course-code">{esc(t["code"])}</span><h3>{esc(t["title"])}</h3><p>{esc(terms(t["terms"]))}</p></article>' for t in ordered_courses)
    awards = ''.join(f'<li>{esc(t)}</li>' for t in sorted(data['awards'], key=chronology_key, reverse=True))
    services = ''.join(f'<details class="service-detail"><summary>{esc(s["title"])}<span aria-hidden="true">+</span></summary><ul>{"".join(f"<li>{esc(t)}</li>" for t in sorted(s["items"], key=chronology_key, reverse=True))}</ul></details>' for s in data['service'])
    return f'''<section class="content-section teaching-courses" id="teaching">{section_heading('', 'Teaching')}<div>{courses}</div></section><section class="content-section" id="service">{section_heading('', 'Professional service')}<div>{services}</div></section><section class="content-section awards-section" id="awards">{section_heading('', 'Awards & fellowships')}<ul class="simple-list">{awards}</ul></section>'''


def resources_page(data):
    collabs = ''.join(f'<li><h3>{a(c["url"], c["name"], arrow=True)}</h3><p>{esc(c["affiliation"])}</p></li>' for c in data['collaborators'])
    resources = ''.join(f'<li><h3>{a(c["url"], c["title"], arrow=True)}</h3><p>{esc(c["description"])}</p></li>' for c in data['resources'])
    return page_intro('Connections & useful reading', 'Resources.', 'Collaborators, research communities, and a few useful starting points.') + f'<section class="content-section"><h2>Collaborators.</h2><ul class="resource-list">{collabs}</ul></section><section class="content-section"><h2>For researchers.</h2><ul class="resource-list">{resources}</ul></section>'


def build(output_dir=None):
    """Validate everything before replacing any generated page; return record counts."""
    data = json.loads((ROOT / 'data/site.json').read_text(encoding='utf-8'))
    contact_images = {}
    private_path = ROOT / '.website-private/contacts.json'
    if private_path.exists():
        contacts = json.loads(private_path.read_text(encoding='utf-8'))
        if not isinstance(contacts, dict):
            raise ValueError('Private contact settings must be a JSON object.')
        for key, slot in (('email', 'primary'), ('secondary_email', 'secondary')):
            address = contacts.get(key, '')
            if address:
                if not isinstance(address, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', address):
                    raise ValueError(f'Enter a valid {slot} email address in the local editor.')
                filename = f'assets/contact-{slot}.png'
                image_key = 'email_image' if slot == 'primary' else 'secondary_email_image'
                data.setdefault('profile', {})[image_key] = filename
                contact_images[filename] = render_email(address)[0]
    validate_site(data, contact_images)
    pubs = parse_bibtex((ROOT / 'data/publications.bib').read_text(encoding='utf-8'))
    seen = set()
    for pub in pubs:
        if not pub.get('title'):
            raise ValueError(f"Publication {pub['id']} needs a title.")
        if pub['id'].lower() in seen:
            raise ValueError(f"Duplicate BibTeX key: {pub['id']}")
        seen.add(pub['id'].lower())
        if pub.get('year') and not re.fullmatch(r'\d{4}', pub['year']):
            raise ValueError(f"Publication {pub['id']} needs a four-digit year (or omit year).")
        for key in ('url', 'pdf', 'slides'):
            if pub.get(key):
                safe_url(pub[key])
                if not urlsplit(pub[key]).scheme and not (ROOT / pub[key]).is_file():
                    raise ValueError(f"Publication {pub['id']}: missing {key} file {pub[key]}")
    pubs.sort(key=lambda x: x.get('year', ''), reverse=True)
    data['news'].sort(key=lambda x: x['date'], reverse=True)
    gallery = photos()
    p = data['profile']
    rendered = {'index.html': page('About', 'index.html', home(data, pubs, gallery), p),
                'lab.html': page(p['lab_name'], 'lab.html', lab_page(data, pubs), p, lab_description(data)),
                'publication.html': page('Publications', 'publication.html', publications_page(data, pubs), p),
                'photo.html': page('Photos', 'photo.html', gallery_page(gallery), p),
                'research.html': page('Research', 'research.html', research_page(data), p),
                'student.html': page('Team', 'student.html', team_page(data), p),
                'links.html': page('Resources', 'links.html', resources_page(data), p),
                'blog.html': page('News & notes', 'blog.html', page_intro('Updates from the lab & beyond', 'News & notes.', 'Research milestones, community moments, and news from the group.') + '<div class="news-archive">' + news_list(data['news']) + '</div>', p)}
    output = Path(output_dir).resolve() if output_dir else ROOT
    if output != ROOT:
        if output in ROOT.parents:
            raise ValueError('Output directory cannot contain the source directory.')
        output.mkdir(parents=True, exist_ok=True)
        for folder in ('css', 'assets', 'photos', 'pic', 'publication', 'people'):
            if not (ROOT / folder).exists():
                continue
            shutil.copytree(ROOT / folder, output / folder, dirs_exist_ok=True)
        for pdf in ROOT.glob('*.pdf'):
            shutil.copy2(pdf, output / pdf.name)
        (output / 'data').mkdir(exist_ok=True)
        shutil.copy2(ROOT / 'data/publications.bib', output / 'data/publications.bib')
        shutil.copy2(ROOT / 'csc453.html', output / 'csc453.html')
    for filename, pixels in contact_images.items():
        target = output / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.png.tmp')
        temporary.write_bytes(pixels)
        temporary.replace(target)
    for filename, body in rendered.items():
        target = output / filename
        temporary = target.with_suffix('.html.tmp')
        temporary.write_text(body, encoding='utf-8')
        temporary.replace(target)
    (output / 'teaching.html').unlink(missing_ok=True)
    (output / '.nojekyll').touch()
    return {'pages': len(rendered), 'publications': len(pubs), 'photos': len(gallery), 'news': len(data['news'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='Stage only public website files in this directory (for deployment).')
    args = parser.parse_args()
    try:
        result = build(args.output_dir)
        print('Website built: ' + ', '.join(f'{v} {k}' for k,v in result.items()) + '.')
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'Cannot build website: {error}', file=sys.stderr)
        raise SystemExit(1)

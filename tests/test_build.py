"""Exercise content-driven builds and audit the public deployment without editing it."""

from copy import deepcopy
from html.parser import HTMLParser
import json
import re
import struct
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import build_site


PAGES = ("index.html", "lab.html", "research.html", "publication.html", "photo.html",
         "student.html", "links.html", "blog.html")


class Document(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.node_ancestors = []
        self._open_elements = []
        self.ids = set()
        self.words = []
        self._hidden_depth = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs))
        self.node_ancestors.append(tuple(parent for _, parent in self._open_elements))
        if tag not in self.VOID_TAGS:
            self._open_elements.append((tag, attrs))
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag in ("script", "style"):
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_elements[index][0] == tag:
                del self._open_elements[index:]
                break
        if tag in ("script", "style") and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not self._hidden_depth:
            self.words.append(data)

    @property
    def text(self):
        return " ".join(" ".join(self.words).split())

    def with_class(self, name):
        return [(tag, attrs) for tag, attrs in self.nodes
                if name in attrs.get("class", "").split()]

    def within_class(self, name):
        return [node for node, ancestors in zip(self.nodes, self.node_ancestors)
                if any(name in ancestor.get("class", "").split() for ancestor in ancestors)]


class PublicDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.output = Path(temporary.name) / "public"
        cls.result = build_site.build(cls.output)
        cls.documents = {name: Document((cls.output / name).read_text(encoding="utf-8"))
                         for name in PAGES}
        cls.site = json.loads((REPO / "data/site.json").read_text(encoding="utf-8"))
        cls.pubs = build_site.parse_bibtex((REPO / "data/publications.bib").read_text(encoding="utf-8"))

    def test_every_local_link_image_script_and_fragment_resolves(self):
        checked = 0
        for name, document in self.documents.items():
            for tag, attrs in document.nodes:
                for attribute in ("href", "src"):
                    if attribute not in attrs or not attrs[attribute]:
                        continue
                    value = attrs[attribute]
                    with self.subTest(page=name, tag=tag, attribute=attribute, target=value):
                        parsed = urlsplit(value)
                        if parsed.scheme:
                            self.assertIn(parsed.scheme, ("http", "https", "mailto"))
                            continue
                        self.assertFalse(parsed.netloc, "Protocol-relative remote URL")
                        target = self.output / unquote(parsed.path) if parsed.path else self.output / name
                        self.assertTrue(target.is_file(), f"Missing local target: {value}")
                        if parsed.fragment and target.suffix == ".html":
                            target_doc = self.documents.get(target.name)
                            if target_doc is None:
                                target_doc = Document(target.read_text(encoding="utf-8"))
                            self.assertIn(unquote(parsed.fragment), target_doc.ids,
                                          f"Missing HTML fragment: {value}")
                        checked += 1
        self.assertGreater(checked, 100)

    def test_record_counts_and_content_survive_the_build(self):
        data, docs = self.site, self.documents
        self.assertEqual(self.result["pages"], len(PAGES))
        self.assertEqual(self.result["publications"], len(self.pubs))
        self.assertEqual(len(docs["publication.html"].with_class("paper")), len(self.pubs))
        self.assertEqual(len(docs["photo.html"].with_class("gallery-item")), self.result["photos"])
        self.assertEqual(len(docs["student.html"].with_class("team-member")), len(data["team"]))
        self.assertEqual(len(docs["lab.html"].with_class("team-member")), len(data["team"]))
        self.assertEqual(len(docs["index.html"].with_class("course-row")), len(data["teaching"]))
        self.assertEqual(len(docs["research.html"].with_class("research-detail")), len(data["research"]))
        self.assertEqual(sum(tag == "time" for tag, _ in docs["blog.html"].nodes), len(data["news"]))
        page_fields = {
            "index.html": data["profile"]["bio"] + data["awards"]
                          + [value for row in data["service"] for value in (row["title"], *row["items"])]
                          + [value for row in data["teaching"] for value in (row["code"], row["title"], *row["terms"])],
            "lab.html": [data["profile"]["lab_name"], data["profile"]["name"]]
                        + [row["name"] for row in data["team"]]
                        + [row["title"] for row in data["research"]],
            "research.html": [value for row in data["research"] for value in (row["title"], row["description"])],
            "blog.html": [row["text"] for row in data["news"]],
            "student.html": [value for row in data["team"] for value in (row["name"], row["focus"])],
            "links.html": [value for row in data["collaborators"] for value in (row["name"], row["affiliation"])] + [row["title"] for row in data["resources"]],
            "publication.html": [pub["title"] for pub in self.pubs],
            "photo.html": [photo["caption"] for photo in build_site.photos()],
        }
        for page, values in page_fields.items():
            for value in values:
                with self.subTest(page=page, value=value):
                    self.assertIn(" ".join(value.split()), docs[page].text)

    def test_teaching_content_is_on_about_without_a_separate_page_or_navigation_link(self):
        self.assertFalse((self.output / "teaching.html").exists())
        primary_sections = {attrs["id"] for _, attrs in self.documents["index.html"].within_class("home-primary")
                            if attrs.get("id")}
        self.assertTrue({"recent-publications", "teaching", "service"}.issubset(primary_sections),
                        "Publications, teaching, and service must share the primary content column")
        for page, document in self.documents.items():
            with self.subTest(page=page):
                for tag, attrs in document.nodes:
                    if tag == "a" and attrs.get("href"):
                        self.assertNotEqual(urlsplit(attrs["href"]).path, "teaching.html")
            source = (self.output / page).read_text(encoding="utf-8")
            navigation = re.search(r"<nav\b[^>]*>(.*?)</nav>", source, re.DOTALL)
            self.assertIsNotNone(navigation)
            self.assertNotIn("Teaching", Document(navigation[1]).text)

    def test_about_photo_preview_shows_the_latest_gallery_items_with_captions_and_lightbox_links(self):
        expected = build_site.photos()[:6]
        document = self.documents["index.html"]
        self.assertEqual(len(document.with_class("home-photo-grid")), int(bool(expected)))
        nodes = document.within_class("home-photo-grid")
        images = [attrs for tag, attrs in nodes if tag == "img"]
        links = [attrs for tag, attrs in nodes if tag == "a" and "data-photo" in attrs]
        self.assertEqual([(image["src"], image["alt"]) for image in images],
                         [(photo["src"], photo["caption"]) for photo in expected])
        self.assertEqual([link["href"] for link in links], [photo["src"] for photo in expected])
        self.assertEqual(len({image["src"] for image in images}), len(expected))
        self.assertEqual([attrs["datetime"] for tag, attrs in nodes if tag == "time"],
                         [photo["date"] for photo in expected if photo["date"]])
        source = (self.output / "index.html").read_text(encoding="utf-8")
        section = re.search(r'<section\b[^>]*class="[^"]*\bhome-photos\b[^"]*"[^>]*>(.*?)</section>',
                            source, re.DOTALL)
        if expected:
            self.assertIsNotNone(section)
            captions = [Document(caption).text for caption in
                        re.findall(r'<figcaption\b[^>]*>.*?<p\b[^>]*>(.*?)</p>.*?</figcaption>',
                                   section[1], re.DOTALL)]
            self.assertEqual(captions, [" ".join(photo["caption"].split()) for photo in expected])
            dialogs = [attrs for tag, attrs in document.nodes
                       if tag == "dialog" and attrs.get("id") == "photo-dialog"]
            self.assertEqual(len(dialogs), 1)
        else:
            self.assertIsNone(section)

    def test_chinese_name_is_visible_with_english_name(self):
        for page in PAGES:
            source = (self.output / page).read_text(encoding="utf-8")
            for tag in ("header", "footer"):
                with self.subTest(page=page, region=tag):
                    match = re.search(r"<" + tag + r"\b[^>]*>(.*?)</" + tag + r">", source, re.DOTALL)
                    self.assertIsNotNone(match)
                    text = Document(match[1]).text
                    self.assertIn("张少虎", text)
                    self.assertIn(self.site["profile"]["name"], text)
        source = (self.output / "index.html").read_text(encoding="utf-8")
        heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", source, re.DOTALL)
        self.assertIsNotNone(heading)
        self.assertIn(self.site["profile"]["name"], Document(heading[1]).text)
        self.assertIn("张少虎", Document(heading[1]).text)
        self.assertNotRegex(heading[1], r"<br\b", "The English and Chinese names must share a line")

    def test_lab_page_navigation_and_roster_are_current(self):
        expected = {"Md Mehraz Hosen", "Bontu Geremew", "Sharun Akter Khushbu", "Charlestone Mayenga"}
        self.assertEqual({member["name"] for member in self.site["team"]}, expected)
        charlestone = next(member for member in self.site["team"]
                          if member["name"] == "Charlestone Mayenga")
        self.assertEqual(charlestone["role"], "Undergraduate Student")
        self.assertEqual(self.site["profile"]["lab_url"], "lab.html")
        for page, document in self.documents.items():
            source = (self.output / page).read_text(encoding="utf-8")
            navigation = re.search(r"<nav\b[^>]*>(.*?)</nav>", source, re.DOTALL)
            self.assertIsNotNone(navigation)
            links = [attrs for tag, attrs in Document(navigation[1]).nodes
                     if tag == "a" and attrs.get("href") == "lab.html"]
            with self.subTest(page=page):
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0].get("aria-current"), "page" if page == "lab.html" else None)
                self.assertNotIn("Mesafint Fanuel", document.text)
        for page in ("student.html", "lab.html"):
            with self.subTest(page=page):
                for name in expected:
                    self.assertIn(name, self.documents[page].text)

    def test_each_student_has_an_accessible_headshot_template(self):
        for page in ("student.html", "lab.html"):
            source = (self.output / page).read_text(encoding="utf-8")
            cards = re.findall(r'<article\b[^>]*class="[^"]*\bteam-member\b[^"]*"[^>]*>(.*?)</article>',
                               source, re.DOTALL)
            self.assertEqual(len(cards), len(self.site["team"]))
            dimensions = set()
            for member in self.site["team"]:
                with self.subTest(page=page, member=member["name"]):
                    matches = [Document(card) for card in cards if member["name"] in Document(card).text]
                    self.assertEqual(len(matches), 1)
                    images = [attrs for tag, attrs in matches[0].nodes if tag == "img"]
                    self.assertEqual(len(images), 1)
                    image = images[0]
                    if image["src"] == "assets/headshot-placeholder.svg":
                        self.assertEqual(image["alt"], f'Headshot placeholder for {member["name"]}')
                    else:
                        self.assertEqual(image["alt"], f'Headshot of {member["name"]}')
                        self.assertTrue((self.output / unquote(image["src"])).is_file())
                    self.assertGreater(int(image["width"]), 0)
                    self.assertGreater(int(image["height"]), 0)
                    dimensions.add((image["width"], image["height"]))
            self.assertEqual(len(dimensions), 1, "Student headshot templates must have consistent dimensions")

    def test_contact_addresses_are_images_without_source_text_exposure(self):
        # The private source may retain editable addresses. Public assets must
        # expose only pixels and generic accessibility labels.
        private_path = REPO / ".website-private" / "contacts.json"
        private = json.loads(private_path.read_text(encoding="utf-8")) if private_path.is_file() else {}
        addresses = {value.casefold() for value in private.values()
                     if isinstance(value, str) and "@" in value}
        self.assertNotIn("email", self.site["profile"])
        self.assertNotIn("secondary_email", self.site["profile"])
        public_settings = json.dumps(self.site, ensure_ascii=False).casefold()
        for address in addresses:
            self.assertNotIn(address, public_settings)
        expected_images = {self.site["profile"][key] for key in ("email_image", "secondary_email_image")}
        image_paths = set()
        for page, document in self.documents.items():
            page_images = {attrs["src"] for tag, attrs in document.nodes if tag == "img" and attrs.get("src")}
            with self.subTest(page=page):
                self.assertTrue(expected_images.issubset(page_images), "Both email images must be visible")
            for tag, attrs in document.nodes:
                source = attrs.get("src", "")
                if tag != "img" or not source.lower().endswith(".png") or not re.search(r"email|contact", source, re.I):
                    continue
                with self.subTest(page=page, source=source):
                    label = attrs.get("alt", "")
                    self.assertTrue(label.strip(), "Contact images need generic alt labels")
                    self.assertIn("email address", label.casefold())
                    self.assertNotIn("@", label)
                    image = self.output / unquote(urlsplit(source).path)
                    self.assertTrue(image.is_file())
                    raw = image.read_bytes()
                    self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertEqual(raw[12:16], b"IHDR")
                    width, height = struct.unpack(">II", raw[16:24])
                    self.assertGreater(width, 20)
                    self.assertGreater(height, 5)
                    for address in addresses:
                        self.assertNotIn(address.encode("utf-8"), raw,
                                         "Email must not appear as PNG text metadata")
                    image_paths.add(source)
        self.assertTrue(expected_images.issubset(image_paths), "Both contact addresses need image assets")
        for path in self.output.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in (".html", ".css", ".js", ".json", ".bib", ".txt", ".svg"):
                continue
            source = path.read_text(encoding="utf-8").casefold()
            with self.subTest(public_file=path.relative_to(self.output)):
                self.assertNotIn("mailto:", source)
                for address in addresses:
                    self.assertNotIn(address, source)

    def test_deployment_contains_public_assets_without_the_local_editor(self):
        self.assertTrue((self.output / "data/publications.bib").is_file())
        self.assertTrue((self.output / ".nojekyll").is_file())
        self.assertTrue((self.output / "csc453.html").is_file())
        for private in ("tools", "tests", "data/site.json", "Update Website.command", ".website-backup", ".website-private"):
            self.assertFalse((self.output / private).exists(), private)


class ContentWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for folder in ("data", "photos", "pic"):
            (self.root / folder).mkdir()
        self.site = json.loads((REPO / "data/site.json").read_text(encoding="utf-8"))
        # Only existence is relevant to the generator; keep fixtures small.
        for field in ("portrait", "cv", "email_image", "secondary_email_image"):
            target = self.root / self.site["profile"][field]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fixture")
        (self.root / "assets/headshot-placeholder.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320"></svg>', encoding="utf-8")
        self.site_path = self.root / "data/site.json"
        self.bib_path = self.root / "data/publications.bib"
        self.write_site()
        self.bib_path.write_text("@article{first,title={Existing publication},year={2024}}\n", encoding="utf-8")
        root_patch = patch.object(build_site, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def write_site(self):
        self.site_path.write_text(json.dumps(self.site), encoding="utf-8")

    def html_snapshot(self):
        return {name: (self.root / name).read_bytes() for name in PAGES}

    def assert_build_fails_without_changing_html(self):
        before = self.html_snapshot()
        with self.assertRaises((ValueError, TypeError, KeyError)):
            build_site.build()
        self.assertEqual(self.html_snapshot(), before)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def contribution_section(self, source, identifier):
        match = re.search(r'<section\b[^>]*id="' + identifier + r'"[^>]*>(.*?)</section>',
                          source, re.DOTALL)
        self.assertIsNotNone(match, f"Missing About section: {identifier}")
        return match[1]

    def assert_items_in_order(self, source, expected):
        text = Document(source).text
        for item in expected:
            self.assertEqual(text.count(item), 1, f"Content was lost or duplicated: {item}")
        positions = [text.index(item) for item in expected]
        self.assertEqual(positions, sorted(positions), "Content must be newest first with stable ties")

    def test_build_without_private_settings_reuses_committed_contact_images(self):
        image_paths = [self.root / self.site["profile"][key]
                       for key in ("email_image", "secondary_email_image")]
        before = {path: path.read_bytes() for path in image_paths}
        self.assertFalse((self.root / ".website-private").exists())
        build_site.build()
        self.assertEqual({path: path.read_bytes() for path in image_paths}, before)
        source = (self.root / "index.html").read_text(encoding="utf-8")
        for path in image_paths:
            self.assertIn(path.relative_to(self.root).as_posix(), source)

    def test_successful_builds_remove_the_previous_teaching_page(self):
        for folder in ("css", "publication"):
            (self.root / folder).mkdir()
        (self.root / "csc453.html").write_text("<!doctype html><title>Course</title>", encoding="utf-8")
        for output in (self.root, self.root / "deployment"):
            with self.subTest(output=output.name):
                output.mkdir(exist_ok=True)
                previous = output / "teaching.html"
                previous.write_text("<!doctype html><title>Previous teaching page</title>", encoding="utf-8")
                result = build_site.build(None if output == self.root else output)
                self.assertEqual(result["pages"], len(PAGES))
                self.assertFalse(previous.exists())
                self.assertTrue((output / "index.html").is_file())

    def test_failed_build_keeps_the_previous_teaching_page(self):
        build_site.build()
        previous = self.root / "teaching.html"
        previous.write_text("<!doctype html><title>Previous teaching page</title>", encoding="utf-8")
        before = previous.read_bytes()
        self.site_path.write_text("{broken", encoding="utf-8")
        self.assert_build_fails_without_changing_html()
        self.assertEqual(previous.read_bytes(), before)

    def test_courses_and_terms_sort_newest_first_without_changing_source_order(self):
        self.site["teaching"] = [
            {"code": "UNDATED1", "title": "Undated first course", "terms": ["To be announced"]},
            {"code": "SPRING", "title": "Spring course", "terms": ["Spring 2030"]},
            {"code": "MIXED", "title": "Mixed-term course",
             "terms": ["Fall 2028", "Course Website: https://example.com/courses/2099", "Spring 2031",
                       "Summer 2029", "Fall 2030", "Summer 2031", "Fall 2031"]},
            {"code": "FALL", "title": "Fall course", "terms": ["Fall 2030"]},
            {"code": "SUMMER", "title": "Summer course", "terms": ["Summer 2030"]},
            {"code": "FALLTIE", "title": "Fall tied course", "terms": ["Fall 2030"]},
            {"code": "UNDATED2", "title": "Undated second course", "terms": ["Future offering"]},
        ]
        before = deepcopy(self.site)
        rendered = build_site.teaching_and_service(self.site)
        self.assertEqual(self.site, before)
        course_codes = re.findall(r'<span class="course-code">(.*?)</span>', rendered)
        expected_codes = ["MIXED", "FALL", "FALLTIE", "SUMMER", "SPRING", "UNDATED1", "UNDATED2"]
        self.assertEqual(course_codes, expected_codes)
        self.assertIn("Fall 2031; Summer 2031; Spring 2031; Fall 2030; Summer 2029; Fall 2028; "
                      "Course Website: https://example.com/courses/2099",
                      Document(rendered).text)
        for course in self.site["teaching"]:
            with self.subTest(course=course["code"]):
                self.assertIn(course["title"], Document(rendered).text)
                for term in course["terms"]:
                    self.assertIn(term, Document(rendered).text)

        # New records may be appended in the editor; their source position must
        # not determine where they appear on the public About page.
        self.site["teaching"].append({"code": "NEW", "title": "New future course", "terms": ["Fall 2038"]})
        self.write_site()
        before_file = self.site_path.read_bytes()
        build_site.build()
        self.assertEqual(self.site_path.read_bytes(), before_file)
        source = (self.root / "index.html").read_text(encoding="utf-8")
        course_codes = re.findall(r'<span class="course-code">(.*?)</span>', source)
        self.assertEqual(course_codes, ["NEW", *expected_codes])

    def test_service_and_awards_sort_dates_ranges_and_ties_without_mutating_source(self):
        first_items = [
            "Legacy committee, 2025.",
            "Undated advisory service.",
            "Long-running review service, 2021–2030.",
            "Early panel, March 3, 2030.",
            "Multiple service dates: 2027, 2029, 2032.",
            "Same-year committee, 2030.",
            "Late panel, November 2, 2030.",
            "Undated mentoring service.",
            "Recent panel & review, 2035.",
        ]
        self.site["service"] = [
            {"title": "Journal reviews", "items": first_items},
            {"title": "Conference committees", "items": [
                "Older conference range, 2026–2033.", "Latest conference range, 2019-2034."]},
        ]
        self.site["awards"] = [
            "Earlier award, 2024.",
            "Undated society recognition.",
            "Range award, 2022–2030.",
            "First tied award, 2030.",
            "Newest award, 2036.",
            "Multiple years fellowship, 2028 and 2034.",
            "Second tied award, 2030.",
            "Undated community recognition.",
        ]
        expected_service = ["Journal reviews"] + [first_items[i] for i in (8, 4, 6, 3, 2, 5, 0, 1, 7)] + [
            "Conference committees", "Latest conference range, 2019-2034.", "Older conference range, 2026–2033."]
        expected_awards = [self.site["awards"][i] for i in (4, 5, 2, 3, 6, 0, 1, 7)]
        before = deepcopy(self.site)
        rendered = build_site.teaching_and_service(self.site)
        self.assertEqual(self.site, before)
        self.assert_items_in_order(self.contribution_section(rendered, "service"), expected_service)
        self.assert_items_in_order(self.contribution_section(rendered, "awards"), expected_awards)

        future_service = "Future committee service, 2038."
        future_award = "Future fellowship, 2040."
        self.site["service"][0]["items"].append(future_service)
        self.site["awards"].append(future_award)
        self.write_site()
        before_file = self.site_path.read_bytes()
        build_site.build()
        self.assertEqual(self.site_path.read_bytes(), before_file)
        source = (self.root / "index.html").read_text(encoding="utf-8")
        self.assert_items_in_order(self.contribution_section(source, "service"),
                                   [expected_service[0], future_service, *expected_service[1:]])
        self.assert_items_in_order(self.contribution_section(source, "awards"),
                                   [future_award, *expected_awards])

    def test_drop_image_add_caption_then_import_a_new_publication(self):
        build_site.build()
        old_gallery = (self.root / "photo.html").read_text(encoding="utf-8")
        (self.root / "photos/2026-10-23-lab-moment.JPG").write_bytes(b"fixture")
        (self.root / "photos/2026-10-23-lab-moment.txt").write_text("A lab visit & discussion <together>.\n", encoding="utf-8")
        (self.root / "photos/undated-photo.png").write_bytes(b"fixture")
        (self.root / "photos/ignored.md").write_text("This is not a photo.", encoding="utf-8")
        self.bib_path.write_text(self.bib_path.read_text(encoding="utf-8") + "@inproceedings{new,title={New imported paper},year={2026}}\n", encoding="utf-8")
        result = build_site.build()
        self.assertEqual(result["photos"], 2)
        self.assertEqual(result["publications"], 2)
        photos = build_site.photos()
        self.assertEqual(photos[0]["caption"], "A lab visit & discussion <together>.")
        self.assertEqual(photos[0]["date"], "2026-10-23")
        self.assertEqual(photos[1]["caption"], "undated photo")
        gallery = (self.root / "photo.html").read_text(encoding="utf-8")
        self.assertNotEqual(gallery, old_gallery)
        self.assertIn("A lab visit &amp; discussion &lt;together&gt;.", gallery)
        self.assertNotIn("ignored.md", gallery)
        self.assertIn("New imported paper", (self.root / "publication.html").read_text(encoding="utf-8"))

    def test_new_unselected_publications_appear_on_home_and_lab_automatically(self):
        # A previous selection must not hide a newly added paper. Keep enough
        # records to check both preview limits and the complete bibliography.
        publications = [
            {"id": "old", "title": "Older selected publication", "year": "2019", "selected": "true"},
            {"id": "new", "title": "New automatic publication", "year": "2026"},
        ] + [{"id": f"recent{year}", "title": f"Recent work from {year}", "year": str(year)}
             for year in range(2025, 2019, -1)]
        self.bib_path.write_text("\n\n".join(
            build_site.serialize_bibtex({"type": "article", **publication})
            for publication in publications), encoding="utf-8")

        result = build_site.build()
        ordered = sorted(publications, key=lambda publication: publication["year"], reverse=True)
        self.assertEqual(result["publications"], len(publications))
        for page, limit in (("index.html", 6), ("lab.html", 3), ("publication.html", len(publications))):
            document = Document((self.root / page).read_text(encoding="utf-8"))
            with self.subTest(page=page):
                self.assertEqual(len(document.with_class("paper")), limit)
                for publication in ordered[:limit]:
                    self.assertIn(publication["title"], document.text)
                for publication in ordered[limit:]:
                    self.assertNotIn(publication["title"], document.text)

    def test_drop_student_headshot_updates_team_and_lab_without_editing_json(self):
        build_site.build()
        before = self.site_path.read_bytes()
        directory = self.root / "people"
        directory.mkdir()
        portrait = directory / "charlestone-mayenga.jpg"
        portrait.write_bytes(b"student headshot fixture")
        build_site.build()
        self.assertEqual(self.site_path.read_bytes(), before)
        for page in ("student.html", "lab.html"):
            document = Document((self.root / page).read_text(encoding="utf-8"))
            image = next(attrs for tag, attrs in document.nodes
                         if tag == "img" and attrs.get("alt") == "Headshot of Charlestone Mayenga")
            self.assertEqual(image["src"], "people/charlestone-mayenga.jpg")
        portrait.unlink()
        build_site.build()
        for page in ("student.html", "lab.html"):
            document = Document((self.root / page).read_text(encoding="utf-8"))
            image = next(attrs for tag, attrs in document.nodes
                         if tag == "img" and attrs.get("alt") == "Headshot placeholder for Charlestone Mayenga")
            self.assertEqual(image["src"], "assets/headshot-placeholder.svg")

    def test_headshots_are_staged_and_a_missing_people_directory_is_supported(self):
        for folder in ("css", "publication"):
            (self.root / folder).mkdir()
        (self.root / "csc453.html").write_text("<!doctype html><title>Course</title>", encoding="utf-8")
        output = self.root / "deployment"
        build_site.build(output)
        self.assertTrue((output / "assets/headshot-placeholder.svg").is_file())
        self.assertFalse((output / "people").exists())
        (self.root / "people").mkdir()
        portrait = self.root / "people/charlestone-mayenga.webp"
        portrait.write_bytes(b"student headshot fixture")
        build_site.build(output)
        self.assertEqual((output / "people/charlestone-mayenga.webp").read_bytes(), portrait.read_bytes())
        source = (output / "lab.html").read_text(encoding="utf-8")
        self.assertIn('src="people/charlestone-mayenga.webp"', source)

    def test_optional_student_photo_path_renders_and_missing_photo_keeps_the_template(self):
        (self.root / "people").mkdir()
        portrait = self.root / "people/custom-headshot.png"
        portrait.write_bytes(b"student headshot fixture")
        member = self.site["team"][0]
        member["photo"] = "people/custom-headshot.png"
        self.write_site()
        build_site.build()
        for page in ("student.html", "lab.html"):
            document = Document((self.root / page).read_text(encoding="utf-8"))
            image = next(attrs for tag, attrs in document.nodes
                         if tag == "img" and attrs.get("alt") == f'Headshot of {member["name"]}')
            self.assertEqual(image["src"], "people/custom-headshot.png")
        portrait.unlink()
        build_site.build()
        for page in ("student.html", "lab.html"):
            document = Document((self.root / page).read_text(encoding="utf-8"))
            image = next(attrs for tag, attrs in document.nodes
                         if tag == "img" and attrs.get("alt") == f'Headshot placeholder for {member["name"]}')
            self.assertEqual(image["src"], "assets/headshot-placeholder.svg")

    def test_unsafe_student_photo_paths_keep_previous_pages(self):
        build_site.build()
        for photo in ("https://example.com/head.jpg", "people/../pic/Head.JPG",
                      "/people/head.jpg", "people/head.svg", "pic/Head.JPG", 42):
            with self.subTest(photo=photo):
                self.site["team"][0]["photo"] = photo
                self.write_site()
                self.assert_build_fails_without_changing_html()

    def test_bad_json_dates_and_missing_required_fields_keep_previous_pages(self):
        build_site.build()
        for bad in ("{broken", json.dumps([])):
            with self.subTest(bad=bad):
                self.site_path.write_text(bad, encoding="utf-8")
                self.assert_build_fails_without_changing_html()
        mutations = [
            lambda data: data["profile"].update(email_image="../private-contact.png"),
            lambda data: data["profile"].pop("name"),
            lambda data: data["news"][0].update(date="2026-02-30"),
            lambda data: data["teaching"][0].update(terms="Fall 2026"),
            lambda data: data["resources"][0].update(url="javascript:alert(1)"),
        ]
        original = deepcopy(self.site)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.site = deepcopy(original)
                mutation(self.site)
                self.write_site()
                self.assert_build_fails_without_changing_html()

    def test_invalid_bibliography_missing_attachment_and_bad_photo_date_keep_previous_pages(self):
        build_site.build()
        cases = [
            "@article{broken,title={Oops}",
            "@article{x,title={One}}\n@article{x,title={Two}}",
            "@article{x,title={Bad year},year={soon}}",
            "@article{x,title={Missing attachment},pdf={publication/missing.pdf}}",
        ]
        for source in cases:
            with self.subTest(source=source):
                self.bib_path.write_text(source, encoding="utf-8")
                self.assert_build_fails_without_changing_html()
        self.bib_path.write_text("@article{ok,title={Fine},year={2026}}", encoding="utf-8")
        (self.root / "photos/2026-02-30-invalid.jpg").write_bytes(b"fixture")
        self.assert_build_fails_without_changing_html()


if __name__ == "__main__":
    unittest.main()

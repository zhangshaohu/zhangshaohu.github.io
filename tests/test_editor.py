"""Check local editing workflows without modifying the real website files."""

import errno
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, call, patch
import urllib.error
import urllib.request


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import manage


class EditorHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        for folder in ("tools", "data", "photos", "people"):
            (cls.root / folder).mkdir()
        shutil.copyfile(REPO / "tools" / "editor.html", cls.root / "tools" / "editor.html")
        (cls.root / "index.html").write_text("<h1>Preview</h1>", encoding="utf-8")
        globals_patch = patch.multiple(manage, ROOT=cls.root, SITE_FILE=cls.root / "data" / "site.json",
                                       BIB_FILE=cls.root / "data" / "publications.bib", PHOTO_DIR=cls.root / "photos",
                                       HEADSHOT_DIR=cls.root / "people",
                                       PRIVATE_CONTACTS=cls.root / ".website-private" / "contacts.json")
        globals_patch.start()
        cls.addClassCleanup(globals_patch.stop)
        builder_patch = patch.object(manage, "build_site")
        cls.builder = builder_patch.start()
        cls.addClassCleanup(builder_patch.stop)
        try:
            cls.server = manage.EditorServer(("127.0.0.1", 0))
        except PermissionError:
            raise unittest.SkipTest("Local sockets are blocked; run this test outside the filesystem sandbox.")
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        self.builder.reset_mock(side_effect=True)
        manage.SITE_FILE.write_text(json.dumps({"profile": {"name": "Example", "email_image": "assets/contact-primary.png", "secondary_email_image": "assets/contact-secondary.png"}, "research": []}), encoding="utf-8")
        manage.BIB_FILE.write_text("@article{existing,title={Original title},year={2024}}", encoding="utf-8")
        manage.PRIVATE_CONTACTS.unlink(missing_ok=True)
        for path in manage.PHOTO_DIR.iterdir():
            path.unlink()
        for path in manage.HEADSHOT_DIR.iterdir():
            path.unlink()
        (self.root / "index.html").write_text("<h1>Preview</h1>", encoding="utf-8")
        self.server.preview.mark_current(rebuilt=True)

    def request(self, path, body=None, token=True, headers=None):
        request_headers = {"X-Editor-Token": self.server.token} if token else {}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.read()
            finally:
                error.close()

    def test_editor_token_and_origin_protection(self):
        status, body = self.request("/_editor")
        self.assertEqual(status, 200)
        self.assertNotIn(b"__EDITOR_TOKEN__", body)
        self.assertEqual(self.request("/_api/state", token=False)[0], 403)
        self.assertEqual(self.request("/_api/state", headers={"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("/_api/state", headers={"Host": f"other.example:{self.server.server_port}"})[0], 403)
        self.assertEqual(self.request("/_api/state")[0], 200)
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/tools/editor.html")[0], 404)

    def test_studio_identity_is_local_guarded_and_has_no_private_content(self):
        status, body = self.request("/_studio/status", token=False)
        self.assertEqual(status, 200)
        identity = json.loads(body)
        self.assertEqual(identity, manage.studio_identity())
        self.assertEqual(set(identity), {"app", "workspace"})
        self.assertEqual(identity["app"], manage.STUDIO_APP_ID)
        self.assertRegex(identity["workspace"], r"^[a-f0-9]{64}$")
        self.assertNotIn(str(self.root).encode(), body)
        self.assertNotIn(self.server.token.encode(), body)
        self.assertNotIn(b"@", body)
        self.assertEqual(self.request("/_studio/status", token=False,
                                      headers={"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("/_studio/status", token=False,
                                      headers={"Host": f"other.example:{self.server.server_port}"})[0], 403)
        self.assertTrue(manage.existing_studio(self.server.server_port))
        self.builder.assert_not_called()

    def test_append_skips_existing_and_preserves_record(self):
        status, body = self.request("/_api/publications", {"text": "@article{existing,title={Ignored}}\n@article{new,title={New title},year={2026}}", "mode": "append"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["skipped"], 1)
        records = manage.parse_bibliography(manage.BIB_FILE.read_text(encoding="utf-8"))
        self.assertEqual([record["id"] for record in records], ["existing", "new"])
        self.assertEqual(records[0]["title"], "Original title")

    def test_replace_uses_complete_new_record(self):
        text = "@article{replacement,title={{AI} title},year={2026}}\n"
        status, body = self.request("/_api/publications", {"text": text, "mode": "replace"})
        self.assertEqual(status, 200, body)
        self.assertEqual(manage.BIB_FILE.read_text(encoding="utf-8"), text)

    def test_invalid_input_does_not_change_sources(self):
        before = manage.BIB_FILE.read_bytes()
        status, _ = self.request("/_api/publications", {"text": "@article{broken,title={Oops}", "mode": "append"})
        self.assertEqual(status, 400)
        self.assertEqual(manage.BIB_FILE.read_bytes(), before)
        status, _ = self.request("/_api/site", {"site": {"profile": {"name": "", "email": "a@example.org"}}})
        self.assertEqual(status, 400)
        self.builder.assert_not_called()

    def test_photo_upload_caption_and_traversal(self):
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQEBAaFfXzYAAAAASUVORK5CYII="
        status, body = self.request("/_api/photos", {"filename": "test image.png", "data": png, "caption": "A moment", "date": "2026-10-23"})
        self.assertEqual(status, 200, body)
        filename = json.loads(body)["filename"]
        self.assertEqual(filename, "2026-10-23-test-image.png")
        self.assertEqual((manage.PHOTO_DIR / "2026-10-23-test-image.txt").read_text(), "A moment\n")
        status, body = self.request("/_api/photos", {"action": "caption", "filename": filename, "caption": "Updated"})
        self.assertEqual(status, 200, body)
        self.assertEqual((manage.PHOTO_DIR / "2026-10-23-test-image.txt").read_text(), "Updated\n")
        self.assertEqual(self.request("/_api/photos", {"action": "caption", "filename": "../test.png", "caption": "No"})[0], 400)

    def test_rebuild_failure_rolls_back_content(self):
        before = manage.SITE_FILE.read_bytes()
        self.builder.side_effect = ValueError("Deliberate rebuild failure")
        status, body = self.request("/_api/site", {"site": {"profile": {"name": "New name", "email": "new@example.org"}}})
        self.assertEqual(status, 400)
        self.assertIn(b"Nothing was saved", body)
        self.assertEqual(manage.SITE_FILE.read_bytes(), before)

    def test_broken_content_can_still_be_repaired_in_editor(self):
        manage.SITE_FILE.write_text('{"profile": invalid JSON}', encoding="utf-8")
        manage.BIB_FILE.write_text('@article{broken,title={Missing closure}', encoding="utf-8")
        status, body = self.request("/_api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["site"], {})
        self.assertEqual(data["publications"], [])
        self.assertEqual(len(data["warnings"]), 2)
        self.assertIn("invalid JSON", data["siteText"])

    def test_contacts_are_private_and_local_forms_receive_addresses(self):
        site = json.loads(manage.SITE_FILE.read_text())
        site["profile"].update({"chinese_name": "张少虎", "email": "a@example.org", "secondary_email": "second@example.org"})
        status, body = self.request("/_api/site", {"site": site})
        self.assertEqual(status, 200, body)
        public = json.loads(manage.SITE_FILE.read_text())
        self.assertNotIn("email", public["profile"])
        self.assertNotIn("secondary_email", public["profile"])
        self.assertEqual(public["profile"]["chinese_name"], "张少虎")
        self.assertNotIn("a@example.org", manage.SITE_FILE.read_text())
        private = json.loads(manage.PRIVATE_CONTACTS.read_text())
        self.assertEqual(private["email"], "a@example.org")
        status, body = self.request("/_api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state["site"]["profile"]["secondary_email"], "second@example.org")
        self.assertNotIn("a@example.org", state["siteText"])
        self.assertEqual(self.request("/.website-private/contacts.json")[0], 404)

    def test_cloned_site_can_save_without_private_addresses(self):
        site = json.loads(self.request("/_api/state")[1])["site"]
        site["profile"]["name"] = "Updated name"
        status, body = self.request("/_api/site", {"site": site})
        self.assertEqual(status, 200, body)
        self.assertFalse(manage.PRIVATE_CONTACTS.exists())
        public = json.loads(manage.SITE_FILE.read_text())
        self.assertEqual(public["profile"]["email_image"], "assets/contact-primary.png")
        self.assertEqual(public["profile"]["secondary_email_image"], "assets/contact-secondary.png")

    def test_secondary_clear_and_primary_validation(self):
        manage.atomic_write(manage.PRIVATE_CONTACTS, json.dumps({"email": "a@example.org", "secondary_email": "second@example.org"}).encode())
        site = json.loads(self.request("/_api/state")[1])["site"]
        site["profile"]["secondary_email"] = ""
        status, body = self.request("/_api/site", {"site": site})
        self.assertEqual(status, 200, body)
        self.assertNotIn("secondary_email_image", json.loads(manage.SITE_FILE.read_text())["profile"])
        self.assertEqual(json.loads(manage.PRIVATE_CONTACTS.read_text())["secondary_email"], "")
        site["profile"]["email"] = ""
        self.assertEqual(self.request("/_api/site", {"site": site})[0], 400)

    def test_failed_build_restores_private_contacts_too(self):
        manage.atomic_write(manage.PRIVATE_CONTACTS, json.dumps({"email": "a@example.org", "secondary_email": ""}).encode())
        before_site = manage.SITE_FILE.read_bytes()
        before_private = manage.PRIVATE_CONTACTS.read_bytes()
        site = json.loads(self.request("/_api/state")[1])["site"]
        site["profile"]["email"] = "updated@example.org"
        self.builder.side_effect = ValueError("Deliberate rebuild failure")
        self.assertEqual(self.request("/_api/site", {"site": site})[0], 400)
        self.assertEqual(manage.SITE_FILE.read_bytes(), before_site)
        self.assertEqual(manage.PRIVATE_CONTACTS.read_bytes(), before_private)

    def headshot_fixture(self):
        site = json.loads(manage.SITE_FILE.read_text())
        site["team"] = [{"name": "Student One", "role": "Ph.D. student", "photo": ""},
                        {"name": "Student Two", "role": "Undergraduate student", "photo": ""}]
        manage.SITE_FILE.write_text(json.dumps(site))
        manage.atomic_write(manage.PRIVATE_CONTACTS, json.dumps({"email": "a@example.org", "secondary_email": "second@example.org"}).encode())
        return {"index": 1, "name": "Student Two", "filename": "portrait.png",
                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQEBAaFfXzYAAAAASUVORK5CYII="}

    def test_headshot_assigns_student_and_preserves_private_contacts(self):
        payload = self.headshot_fixture()
        before_private = manage.PRIVATE_CONTACTS.read_bytes()
        status, body = self.request("/_api/headshots", payload)
        self.assertEqual(status, 200, body)
        photo = json.loads(body)["photo"]
        self.assertEqual(photo, "people/student-two.png")
        self.assertTrue((manage.ROOT / photo).is_file())
        public = json.loads(manage.SITE_FILE.read_text())
        self.assertEqual(public["team"][0]["photo"], "")
        self.assertEqual(public["team"][1]["photo"], photo)
        self.assertNotIn("email", public["profile"])
        self.assertEqual(manage.PRIVATE_CONTACTS.read_bytes(), before_private)
        status, body = self.request("/_api/state")
        self.assertEqual(status, 200)
        previews = json.loads(body)["headshots"]
        self.assertTrue(previews[0]["placeholder"])
        self.assertEqual(previews[1]["src"], "/people/student-two.png")
        self.assertFalse(previews[1]["placeholder"])

    def test_headshot_rejects_stale_student_and_invalid_image(self):
        payload = self.headshot_fixture()
        before = manage.SITE_FILE.read_bytes()
        payload["name"] = "Unsaved renamed student"
        self.assertEqual(self.request("/_api/headshots", payload)[0], 400)
        payload["name"] = "Student Two"
        payload["filename"] = "portrait.jpg"
        self.assertEqual(self.request("/_api/headshots", payload)[0], 400)
        payload["filename"] = "../portrait.png"
        self.assertEqual(self.request("/_api/headshots", payload)[0], 400)
        self.assertEqual(manage.SITE_FILE.read_bytes(), before)
        self.assertEqual(list(manage.HEADSHOT_DIR.iterdir()), [])

    def test_headshot_rebuild_failure_restores_image_and_roster(self):
        payload = self.headshot_fixture()
        image = manage.HEADSHOT_DIR / "student-two.png"
        image.write_bytes(b"Previous image")
        before_site = manage.SITE_FILE.read_bytes()
        before_private = manage.PRIVATE_CONTACTS.read_bytes()
        self.builder.side_effect = ValueError("Deliberate rebuild failure")
        self.assertEqual(self.request("/_api/headshots", payload)[0], 400)
        self.assertEqual(image.read_bytes(), b"Previous image")
        self.assertEqual(manage.SITE_FILE.read_bytes(), before_site)
        self.assertEqual(manage.PRIVATE_CONTACTS.read_bytes(), before_private)

    def render_publication_preview(self):
        entries = manage.parse_bibliography(manage.BIB_FILE.read_text(encoding="utf-8"))
        titles = " | ".join(entry["title"] for entry in entries)
        (self.root / "index.html").write_text("<h1>" + titles + "</h1>", encoding="utf-8")

    def test_external_bibliography_edit_rebuilds_before_public_page(self):
        self.builder.side_effect = self.render_publication_preview
        before = json.loads(self.request("/_preview/status", token=False)[1])
        self.builder.assert_not_called()
        manage.BIB_FILE.write_text("@article{new,title={Manually updated title},year={2026}}", encoding="utf-8")
        status, body = self.request("/index.html", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Manually updated title", body)
        self.assertEqual(self.builder.call_count, 1)
        after = json.loads(self.request("/_preview/status", token=False)[1])
        self.assertNotEqual(after["version"], before["version"])
        self.assertFalse(after["has_error"])
        self.request("/")
        self.request("/_preview/status", token=False)
        self.assertEqual(self.builder.call_count, 1)

    def test_editor_state_rebuilds_external_bibliography_edit(self):
        self.builder.side_effect = self.render_publication_preview
        manage.BIB_FILE.write_text("@article{new,title={Updated in file},year={2026}}", encoding="utf-8")
        status, body = self.request("/_api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["publications"][0]["title"], "Updated in file")
        self.assertEqual(self.builder.call_count, 1)
        self.request("/_api/state")
        self.assertEqual(self.builder.call_count, 1)

    def test_rejected_request_does_not_hide_unbuilt_external_edit(self):
        self.builder.side_effect = self.render_publication_preview
        manage.BIB_FILE.write_text("@article{new,title={External edit preserved},year={2026}}", encoding="utf-8")
        status, _ = self.request("/_api/build", {}, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 400)
        self.builder.assert_not_called()
        status, body = self.request("/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"External edit preserved", body)
        self.assertEqual(self.builder.call_count, 1)

    def test_invalid_external_edit_keeps_preview_and_waits_for_correction(self):
        self.builder.side_effect = self.render_publication_preview
        before = json.loads(self.request("/_preview/status", token=False)[1])
        manage.BIB_FILE.write_text("@article{broken,title={Missing closure}", encoding="utf-8")
        status, body = self.request("/index.html", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Preview", body)
        failed = json.loads(self.request("/_preview/status", token=False)[1])
        self.assertEqual(set(failed), {"version", "has_error"})
        self.assertTrue(failed["has_error"])
        self.assertEqual(failed["version"], before["version"])
        status, body = self.request("/_api/state")
        self.assertEqual(status, 200)
        self.assertTrue(any("previous preview" in warning for warning in json.loads(body)["warnings"]))
        self.assertEqual(self.builder.call_count, 1)
        manage.BIB_FILE.write_text("@article{fixed,title={Corrected title},year={2026}}", encoding="utf-8")
        corrected = json.loads(self.request("/_preview/status", token=False)[1])
        self.assertFalse(corrected["has_error"])
        self.assertNotEqual(corrected["version"], before["version"])
        self.assertEqual(self.builder.call_count, 2)
        self.assertIn(b"Corrected title", self.request("/index.html")[1])

    def test_separate_build_outputs_update_version_without_rebuilding(self):
        before = json.loads(self.request("/_preview/status", token=False)[1])
        (self.root / "index.html").write_text("<h1>Built in another terminal</h1>", encoding="utf-8")
        after = json.loads(self.request("/_preview/status", token=False)[1])
        self.assertNotEqual(after["version"], before["version"])
        self.assertFalse(after["has_error"])
        self.request("/_preview/status", token=False)
        self.builder.assert_not_called()

    def test_photo_headshot_caption_and_private_changes_are_watched(self):
        for relative in ("data/extra.json", "photos/added.jpg", "photos/added.txt", "people/student.png", ".website-private/contacts.json", "pic/portrait.JPG", "publication/paper.pdf", "slides/talk.pptx", "profile-cv.pdf"):
            with self.subTest(source=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
                calls = self.builder.call_count
                status, _ = self.request("/_preview/status", token=False)
                self.assertEqual(status, 200)
                self.assertEqual(self.builder.call_count, calls + 1)
                path.unlink()
                self.server.preview.mark_current(rebuilt=True)

    def test_adding_missing_referenced_pdf_retries_failed_preview(self):
        target = self.root / "documents" / "paper.pdf"
        manage.BIB_FILE.write_text("@article{new,title={Paper},year={2026},pdf={documents/paper.pdf}}", encoding="utf-8")

        def build_with_pdf():
            if not target.is_file():
                raise ValueError("Publication new: missing pdf file documents/paper.pdf")
            self.render_publication_preview()

        self.builder.side_effect = build_with_pdf
        first = json.loads(self.request("/_preview/status", token=False)[1])
        self.assertTrue(first["has_error"])
        self.assertEqual(self.builder.call_count, 1)
        self.request("/_preview/status", token=False)
        self.assertEqual(self.builder.call_count, 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(b"Example PDF")
            second = json.loads(self.request("/_preview/status", token=False)[1])
            self.assertFalse(second["has_error"])
            self.assertNotEqual(first["version"], second["version"])
            self.assertEqual(self.builder.call_count, 2)
        finally:
            target.unlink(missing_ok=True)


class StudioLauncherTests(unittest.TestCase):
    def test_probe_accepts_only_this_workspace_and_disables_proxies_and_redirects(self):
        identity = manage.studio_identity()
        with patch.object(manage.urllib.request, "build_opener") as factory:
            opener = factory.return_value
            response = opener.open.return_value.__enter__.return_value
            response.status = 200
            response.headers.get_content_type.return_value = "application/json"
            for payload, expected in (
                (identity, True),
                ({"app": "unrelated-service", "workspace": identity["workspace"]}, False),
                ({"app": manage.STUDIO_APP_ID, "workspace": "different-workspace"}, False),
                ({"status": "running"}, False),
                ([], False),
            ):
                with self.subTest(payload=payload):
                    response.read.return_value = json.dumps(payload).encode()
                    self.assertEqual(manage.existing_studio(8001), expected)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:8001/_studio/status")
            self.assertEqual(opener.open.call_args.kwargs, {"timeout": 0.5})
            handlers = factory.call_args.args
            self.assertIsInstance(handlers[0], manage.urllib.request.ProxyHandler)
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsInstance(handlers[1], manage.urllib.request.HTTPRedirectHandler)
            self.assertIsNone(handlers[1].redirect_request(None, None, 302, "Found", {}, "https://example.org"))
            response.read.return_value = b"x" * 4097
            self.assertFalse(manage.existing_studio(8001))
            opener.open.side_effect = urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)
            self.assertFalse(manage.existing_studio(8001))
            opener.open.side_effect = OSError("Connection refused")
            self.assertFalse(manage.existing_studio(8001))

    def test_main_reopens_an_existing_studio_without_rebuilding_or_stopping_it(self):
        with patch.object(sys, "argv", ["manage.py", "--port", "8001"]), \
                patch.object(manage, "existing_studio", return_value=True) as probe, \
                patch.object(manage, "EditorServer") as server, \
                patch.object(manage, "build_site") as build, \
                patch.object(manage.webbrowser, "open") as browser, \
                patch("builtins.print"):
            manage.main()
        probe.assert_called_once_with(8001)
        server.assert_not_called()
        build.assert_not_called()
        browser.assert_called_once_with("http://127.0.0.1:8001/_editor")

    def test_main_uses_the_next_port_when_an_unrelated_service_is_running(self):
        running = Mock(server_port=8002)
        running.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(sys, "argv", ["manage.py", "--port", "8001", "--no-browser"]), \
                patch.object(manage, "existing_studio", return_value=False) as probe, \
                patch.object(manage, "EditorServer", side_effect=[OSError(errno.EADDRINUSE, "Occupied"), running]) as server, \
                patch.object(manage, "PreviewState"), \
                patch.object(manage, "build_site") as build, \
                patch.object(manage.webbrowser, "open") as browser, \
                patch("builtins.print"):
            manage.main()
        self.assertEqual(probe.call_args_list, [call(8001), call(8002)])
        self.assertEqual(server.call_args_list, [call(("127.0.0.1", 8001)), call(("127.0.0.1", 8002))])
        build.assert_called_once_with()
        running.serve_forever.assert_called_once_with()
        running.server_close.assert_called_once_with()
        browser.assert_not_called()

    def test_main_reuses_matching_studio_after_an_occupied_port(self):
        with patch.object(sys, "argv", ["manage.py", "--port", "8001"]), \
                patch.object(manage, "existing_studio", side_effect=[False, True]), \
                patch.object(manage, "EditorServer", side_effect=OSError(errno.EADDRINUSE, "Occupied")) as server, \
                patch.object(manage, "build_site") as build, \
                patch.object(manage.webbrowser, "open") as browser, \
                patch("builtins.print"):
            manage.main()
        server.assert_called_once_with(("127.0.0.1", 8001))
        build.assert_not_called()
        browser.assert_called_once_with("http://127.0.0.1:8002/_editor")

    def test_main_does_not_try_ports_above_65535(self):
        with patch.object(sys, "argv", ["manage.py", "--port", "65535", "--no-browser"]), \
                patch.object(manage, "existing_studio", return_value=False) as probe, \
                patch.object(manage, "EditorServer", side_effect=OSError(errno.EADDRINUSE, "Occupied")) as server, \
                patch.object(manage, "build_site") as build, \
                patch("builtins.print"):
            with self.assertRaises(SystemExit) as stopped:
                manage.main()
        self.assertEqual(stopped.exception.code, 1)
        probe.assert_called_once_with(65535)
        server.assert_called_once_with(("127.0.0.1", 65535))
        build.assert_not_called()

    def test_main_preserves_os_selected_port_behavior(self):
        running = Mock(server_port=60321)
        running.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(sys, "argv", ["manage.py", "--port", "0", "--no-browser"]), \
                patch.object(manage, "existing_studio") as probe, \
                patch.object(manage, "EditorServer", return_value=running) as server, \
                patch.object(manage, "PreviewState"), \
                patch.object(manage, "build_site") as build, \
                patch("builtins.print"):
            manage.main()
        probe.assert_not_called()
        server.assert_called_once_with(("127.0.0.1", 0))
        build.assert_called_once_with()
        running.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

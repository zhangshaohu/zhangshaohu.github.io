#!/usr/bin/env python3
"""A small, local-only editor for the website. Requires Python 3.9 or newer."""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import errno
import hashlib
from http.client import HTTPException
import importlib
import json
import os
from pathlib import Path
import re
import secrets
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import sys
import tempfile
import threading
from urllib.parse import quote, unquote, urlsplit
import urllib.error
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent.parent
SITE_FILE = ROOT / "data" / "site.json"
BIB_FILE = ROOT / "data" / "publications.bib"
PHOTO_DIR = ROOT / "photos"
HEADSHOT_DIR = ROOT / "people"
PRIVATE_CONTACTS = ROOT / ".website-private" / "contacts.json"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE = 15 * 1024 * 1024
MAX_REQUEST = 22 * 1024 * 1024
MAX_BIB = 4 * 1024 * 1024
LOCK = threading.Lock()
TOOL_STAMPS: dict[str, tuple[int, int] | None] = {}
STUDIO_APP_ID = "academic-website-studio"
STUDIO_PORT_ATTEMPTS = 10


class EditorError(ValueError):
    pass


def studio_identity() -> dict[str, str]:
    """Identify this workspace without exposing its local path or content."""
    workspace = hashlib.sha256(os.fsencode(ROOT.resolve())).hexdigest()
    return {"app": STUDIO_APP_ID, "workspace": workspace}


class _NoStudioRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def existing_studio(port: int) -> bool:
    """Probe only this loopback port; proxies and redirects are never used."""
    if not 1 <= port <= 65535:
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoStudioRedirects())
    request = urllib.request.Request(f"http://127.0.0.1:{port}/_studio/status",
                                     headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=0.5) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                return False
            content = response.read(4097)
            if len(content) > 4096:
                return False
            return json.loads(content) == studio_identity()
    except (OSError, ValueError, HTTPException, urllib.error.URLError):
        return False


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".editor-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_tool(name: str, force_reload: bool = False):
    sys.path.insert(0, str(ROOT / "tools")) if str(ROOT / "tools") not in sys.path else None
    path = ROOT / "tools" / (name + ".py")
    try:
        metadata = path.stat()
        stamp = (metadata.st_mtime_ns, metadata.st_size)
    except OSError:
        stamp = None
    loaded = sys.modules.get(name)
    if loaded is not None and (force_reload or TOOL_STAMPS.get(name) != stamp):
        # Python bytecode caches use whole-second timestamps. Discard only this
        # generated cache so rapid edits of the same size load the current code.
        cached = getattr(loaded, "__cached__", None)
        if cached and stamp is not None:
            Path(cached).unlink(missing_ok=True)
        importlib.invalidate_caches()
        loaded = importlib.reload(loaded)
    elif loaded is None:
        loaded = importlib.import_module(name)
    TOOL_STAMPS[name] = stamp
    return loaded


def build_site() -> None:
    load_tool("bibtex")
    if (ROOT / "tools" / "email_images.py").is_file():
        load_tool("email_images", force_reload=True)
    load_tool("build_site", force_reload=True).build()


def content_fingerprint() -> tuple:
    """Track editable inputs; generated pages and contact images are excluded."""
    sources = {SITE_FILE, BIB_FILE, PRIVATE_CONTACTS}

    def include_local(value):
        if not isinstance(value, str) or not value or urlsplit(value).scheme or value.startswith("//"):
            return
        local_path = unquote(urlsplit(value).path)
        if not local_path:
            return
        candidate = ROOT / local_path
        try:
            candidate.resolve().relative_to(ROOT)
        except (OSError, ValueError):
            return
        sources.add(candidate)

    data = ROOT / "data"
    if data.is_dir():
        sources.update(path for path in data.rglob("*") if path.suffix.lower() in {".json", ".bib"}
                       and not any(part.startswith(".") for part in path.relative_to(data).parts))
    for directory, extensions in ((PHOTO_DIR, PHOTO_EXTENSIONS | {".gif", ".txt"}),
                                  (HEADSHOT_DIR, PHOTO_EXTENSIONS),
                                  (ROOT / "pic", PHOTO_EXTENSIONS | {".gif", ".svg"}),
                                  (ROOT / "publication", {".pdf", ".ppt", ".pptx", ".key", ".odp"}),
                                  (ROOT / "slides", {".pdf", ".ppt", ".pptx", ".key", ".odp"})):
        if directory.is_dir():
            sources.update(path for path in directory.rglob("*") if path.suffix.lower() in extensions
                           and not any(part.startswith(".") for part in path.relative_to(directory).parts))
    try:
        profile = json.loads(SITE_FILE.read_text(encoding="utf-8")).get("profile", {})
        include_local(profile.get("cv", ""))
        portrait = ROOT / profile.get("portrait", "")
        portrait.resolve().relative_to(ROOT)
        if portrait.suffix.lower() in PHOTO_EXTENSIONS | {".gif", ".svg"} and portrait not in {ROOT / "assets" / "contact-primary.png", ROOT / "assets" / "contact-secondary.png"}:
            sources.add(portrait)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    sources.update(ROOT.glob("*.pdf"))
    try:
        bibliography = BIB_FILE.read_text(encoding="utf-8")
        for publication in importlib.import_module("bibtex").parse_bibtex(bibliography):
            for key in ("pdf", "slides", "url"):
                include_local(publication.get(key, ""))
    except Exception:
        # Invalid BibTeX is reported by the build. Known paper folders remain
        # watched so adding a missing PDF can recover the preview as well.
        pass
    sources.update(ROOT / "tools" / filename for filename in ("build_site.py", "bibtex.py", "email_images.py", "email_font.json"))
    fingerprint = []
    for path in sorted(sources, key=str):
        try:
            metadata = path.stat()
            fingerprint.append((str(path.relative_to(ROOT)), metadata.st_mtime_ns, metadata.st_size))
        except OSError:
            fingerprint.append((str(path.relative_to(ROOT)), None, None))
    return tuple(fingerprint)


def output_fingerprint() -> tuple:
    """Notice separate command-line builds without treating outputs as inputs."""
    paths = set(ROOT.glob("*.html"))
    paths.update((ROOT / "css" / "main.css", ROOT / "assets" / "site.js",
                  ROOT / "assets" / "contact-primary.png", ROOT / "assets" / "contact-secondary.png"))
    result = []
    for path in sorted(paths, key=str):
        try:
            metadata = path.stat()
            result.append((str(path.relative_to(ROOT)), metadata.st_mtime_ns, metadata.st_size))
        except OSError:
            result.append((str(path.relative_to(ROOT)), None, None))
    return tuple(result)


class PreviewState:
    """One cache per server. Call its methods while holding LOCK."""

    def __init__(self, error: str = ""):
        self.signature = content_fingerprint()
        self.outputs = output_fingerprint()
        self.version = secrets.token_hex(16)
        self.error = error

    def mark_current(self, rebuilt: bool = False):
        self.signature = content_fingerprint()
        self.outputs = output_fingerprint()
        if rebuilt:
            self.version = secrets.token_hex(16)
            self.error = ""

    def check(self):
        signature = content_fingerprint()
        if signature == self.signature:
            outputs = output_fingerprint()
            if outputs != self.outputs:
                self.outputs = outputs
                self.version = secrets.token_hex(16)
            return
        # Cache failures too: unchanged invalid input should not rebuild on
        # every browser poll. Saving a correction changes the signature again.
        self.signature = signature
        try:
            build_site()
        except Exception as exc:
            self.error = f"The preview could not update from your edited files: {exc}. The previous preview is still available. Correct the content and save again, or click Rebuild."
        else:
            self.outputs = output_fingerprint()
            self.version = secrets.token_hex(16)
            self.error = ""

    def status(self) -> dict:
        return {"version": self.version, "has_error": bool(self.error)}


def parse_bibliography(text: str) -> list[dict[str, str]]:
    sys.path.insert(0, str(ROOT / "tools")) if str(ROOT / "tools") not in sys.path else None
    try:
        entries = load_tool("bibtex").parse_bibtex(text)
    except Exception as exc:
        raise EditorError(f"The BibTeX file could not be read: {exc}") from exc
    if not isinstance(entries, list):
        raise EditorError("The BibTeX parser returned an unexpected result.")
    return entries


def transaction(changes: dict[Path, bytes]) -> None:
    """Restore source files and the previous preview if a rebuild fails."""
    previous = {path: path.read_bytes() if path.exists() else None for path in changes}
    try:
        for path, content in changes.items():
            atomic_write(path, content)
        build_site()
    except Exception as exc:
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content)
        try:
            build_site()
        except Exception:
            pass
        raise EditorError(f"Nothing was saved. The website could not be rebuilt: {exc}") from exc


def validate_site(site: object) -> dict:
    if not isinstance(site, dict):
        raise EditorError("Site content must be a JSON object.")
    profile = site.get("profile")
    if not isinstance(profile, dict):
        raise EditorError("Site content must include a profile object.")
    if not isinstance(profile.get("name"), str) or not profile["name"].strip():
        raise EditorError("The profile name cannot be empty.")
    email = profile.get("email", "")
    if not isinstance(email, str) or (email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
        raise EditorError("Enter a valid primary email address in the profile.")
    for section in ("research", "news", "teaching", "team", "awards", "service", "collaborators", "resources", "bio_links"):
        if section in site and not isinstance(site[section], list):
            raise EditorError(f"The {section} section must be a list.")
    for section in ("research", "resources"):
        for entry in site.get(section, []):
            if not isinstance(entry, dict) or not isinstance(entry.get("title"), str) or not entry["title"].strip():
                raise EditorError(f"Each {section} entry needs a title.")
    return site


def read_contacts() -> dict[str, str]:
    if not PRIVATE_CONTACTS.is_file():
        return {}
    try:
        data = json.loads(PRIVATE_CONTACTS.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise EditorError("The private contact file could not be read. Enter your contact addresses again and save.") from exc
    if not isinstance(data, dict) or any(not isinstance(data.get(key, ""), str) for key in ("email", "secondary_email")):
        raise EditorError("The private contact file must contain text email addresses.")
    return {key: data.get(key, "") for key in ("email", "secondary_email")}


def public_site_content(site: dict) -> dict:
    public = json.loads(json.dumps(site))
    if isinstance(public.get("profile"), dict):
        public["profile"].pop("email", None)
        public["profile"].pop("secondary_email", None)
    return public


def member_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "student"


def headshot_preview(member: dict) -> dict:
    explicit = member.get("photo", "")
    candidates = []
    if isinstance(explicit, str) and explicit:
        candidate = ROOT / explicit
        try:
            if candidate.resolve().parent == HEADSHOT_DIR.resolve() and not candidate.is_symlink():
                candidates.append(candidate)
        except (OSError, ValueError):
            pass
    slug = member_slug(str(member.get("name", "")))
    candidates.extend(HEADSHOT_DIR / (slug + suffix) for suffix in (".jpg", ".jpeg", ".png", ".webp"))
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink() and candidate.suffix.lower() in PHOTO_EXTENSIONS:
            return {"name": member.get("name", ""), "src": "/" + quote(candidate.relative_to(ROOT).as_posix()), "placeholder": False}
    return {"name": member.get("name", ""), "src": "/assets/headshot-placeholder.svg", "placeholder": True}


def save_site(payload: dict) -> dict:
    site = payload.get("site")
    if not isinstance(site, dict) or not isinstance(site.get("profile"), dict):
        raise EditorError("Site content must include a profile object.")
    profile = site["profile"]
    try:
        previous = read_contacts()
    except EditorError:
        previous = {}
    primary = profile.pop("email", previous.get("email", ""))
    secondary = profile.pop("secondary_email", previous.get("secondary_email", ""))
    for address, label in ((primary, "primary"), (secondary, "secondary")):
        if not isinstance(address, str) or (address.strip() and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address.strip())):
            raise EditorError(f"Enter a valid {label} email address in the profile.")
    primary, secondary = primary.strip(), secondary.strip()
    if previous.get("email") and not primary:
        raise EditorError("Enter a valid primary email address. The existing local address cannot be cleared.")
    if primary:
        profile.setdefault("email_image", "assets/contact-primary.png")
    if secondary:
        profile.setdefault("secondary_email_image", "assets/contact-secondary.png")
    elif previous.get("secondary_email"):
        profile.pop("secondary_email_image", None)
    site = validate_site(site)
    changes = {SITE_FILE: (json.dumps(site, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
    if primary or secondary or previous:
        contacts = {"email": primary, "secondary_email": secondary}
        changes[PRIVATE_CONTACTS] = (json.dumps(contacts, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    transaction(changes)
    return {"message": "Site content saved. Contact addresses stay in your private local file. Preview updated."}


def serialize_entry(entry: dict[str, str]) -> str:
    try:
        return load_tool("bibtex").serialize_bibtex(entry)
    except Exception as exc:
        raise EditorError(f"This publication could not be imported: {exc}") from exc


def state() -> dict:
    warnings = []
    site_text = SITE_FILE.read_text(encoding="utf-8") if SITE_FILE.exists() else "{}"
    site_error = ""
    try:
        site = json.loads(site_text)
        if not isinstance(site, dict):
            raise ValueError("Site content must be a JSON object.")
        site_text = json.dumps(public_site_content(site), ensure_ascii=False, indent=2) + "\n"
    except (ValueError, UnicodeError) as exc:
        site = {}
        site_error = str(exc)
        warnings.append(f"Site content needs a correction: {exc}. Open Site content and use the full content editor to repair it.")
    if isinstance(site.get("profile"), dict):
        try:
            contacts = read_contacts()
        except EditorError as exc:
            contacts = {}
            warnings.append(str(exc))
        for key in ("email", "secondary_email"):
            site["profile"][key] = contacts.get(key, site["profile"].get(key, ""))
    bibliography = BIB_FILE.read_text(encoding="utf-8") if BIB_FILE.exists() else ""
    try:
        entries = parse_bibliography(bibliography)
    except EditorError as exc:
        entries = []
        warnings.append(f"{exc} You can replace the record with a corrected .bib file.")
    photos = []
    if PHOTO_DIR.exists():
        for path in sorted(PHOTO_DIR.iterdir(), reverse=True):
            if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS or path.is_symlink():
                continue
            caption_path = path.with_suffix(".txt")
            caption = caption_path.read_text(encoding="utf-8") if caption_path.is_file() else ""
            photos.append({"filename": path.name, "caption": caption, "url": "/photos/" + quote(path.name)})
    headshots = [headshot_preview(member) for member in site.get("team", []) if isinstance(member, dict)]
    return {"site": site, "siteText": site_text, "siteError": site_error, "warnings": warnings,
            "publications": entries, "bibliography": bibliography, "photos": photos, "headshots": headshots}


def import_publications(payload: dict) -> dict:
    text = payload.get("text")
    mode = payload.get("mode", "append")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BIB:
        raise EditorError("Choose a BibTeX file smaller than 4 MB.")
    if mode not in ("append", "replace"):
        raise EditorError("Choose append or replace for the import.")
    imported = parse_bibliography(text.lstrip("\ufeff"))
    if not imported:
        raise EditorError("No publications were found. Export records as BibTeX (.bib) and try again.")
    existing = BIB_FILE.read_text(encoding="utf-8") if BIB_FILE.exists() else ""
    seen = {entry["id"].casefold() for entry in parse_bibliography(existing)} if mode == "append" else set()
    additions = []
    skipped = 0
    for entry in imported:
        key = str(entry.get("id", "")).casefold()
        if not key:
            raise EditorError("A publication is missing its citation key.")
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        additions.append(entry)
    if not additions and mode == "append":
        return {"message": f"No new publications. Skipped {skipped} duplicate citation keys.", "added": 0, "skipped": skipped}
    # Preserve the reference manager's original export whenever nothing was
    # skipped. With duplicate keys, serialize only the genuinely new records.
    new_text = (text.lstrip("\ufeff").strip() + "\n") if not skipped else "\n\n".join(serialize_entry(entry) for entry in additions) + "\n"
    if mode == "append" and existing.strip():
        new_text = existing.rstrip() + "\n\n" + new_text
    parse_bibliography(new_text)
    transaction({BIB_FILE: new_text.encode("utf-8")})
    verb = "Imported" if mode == "replace" else "Added"
    return {"message": f"{verb} {len(additions)} publication(s). Skipped {skipped} duplicate citation key(s). Preview updated.", "added": len(additions), "skipped": skipped}


def photo_path(filename: object) -> Path:
    if not isinstance(filename, str) or not filename or filename.startswith(".") or "/" in filename or "\\" in filename:
        raise EditorError("Choose a photo from the gallery.")
    path = PHOTO_DIR / filename
    if path.suffix.lower() not in PHOTO_EXTENSIONS or path.is_symlink() or path.resolve().parent != PHOTO_DIR.resolve():
        raise EditorError("This photo filename is not supported.")
    return path


def uploaded_image(payload: dict) -> tuple[str, str, bytes]:
    original = payload.get("filename")
    if not isinstance(original, str) or "/" in original or "\\" in original:
        raise EditorError("The image needs a valid filename.")
    suffix = Path(original).suffix.lower()
    if suffix not in PHOTO_EXTENSIONS:
        raise EditorError("Use a JPG, PNG, or WebP image.")
    encoded = payload.get("data")
    if not isinstance(encoded, str):
        raise EditorError("No image data was received.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EditorError("The uploaded image could not be read.") from exc
    if not data or len(data) > MAX_IMAGE:
        raise EditorError("Each image must be smaller than 15 MB.")
    valid = ((suffix in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff"))
             or (suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
             or (suffix == ".webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"))
    if not valid:
        raise EditorError("The file contents do not match the image type. Export a JPG, PNG, or WebP image.")
    return original, suffix, data


def save_headshot(payload: dict) -> dict:
    try:
        site = json.loads(SITE_FILE.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise EditorError("Correct and save the site content before adding a headshot.") from exc
    index, name = payload.get("index"), payload.get("name")
    team = site.get("team", []) if isinstance(site, dict) else []
    if (not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(team)
            or not isinstance(name, str) or not isinstance(team[index], dict) or team[index].get("name") != name):
        raise EditorError("Save the team content first, then choose a headshot for this student.")
    if HEADSHOT_DIR.is_symlink() or HEADSHOT_DIR.resolve().parent != ROOT:
        raise EditorError("Student headshots must be saved in the website's people folder.")
    _, suffix, data = uploaded_image(payload)
    slug = member_slug(name)
    if any(i != index and isinstance(member, dict) and member_slug(str(member.get("name", ""))) == slug for i, member in enumerate(team)):
        slug += "-" + str(index + 1)
    path = HEADSHOT_DIR / (slug + suffix)
    if path.is_symlink():
        raise EditorError("The headshot must be a regular image file in the people folder.")
    photo = path.relative_to(ROOT).as_posix()
    team[index]["photo"] = photo
    public = public_site_content(site)
    transaction({path: data, SITE_FILE: (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")})
    return {"message": f"Headshot saved for {name}. Preview updated.", "photo": photo, "src": "/" + quote(photo)}


def save_photo(payload: dict) -> dict:
    caption = payload.get("caption", "")
    if not isinstance(caption, str) or len(caption) > 4000 or "\0" in caption:
        raise EditorError("Keep the caption under 4,000 characters.")
    caption = caption.strip()
    if payload.get("action") == "caption":
        path = photo_path(payload.get("filename"))
        if not path.is_file():
            raise EditorError("This photo no longer exists. Refresh the editor and try again.")
        if path.with_suffix(".txt").is_symlink():
            raise EditorError("The caption must be a regular text file in the photos folder.")
        transaction({path.with_suffix(".txt"): (caption + "\n").encode("utf-8")})
        return {"message": "Caption saved. Preview updated."}
    original, suffix, data = uploaded_image(payload)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip(".-")[:110] or "photo"
    date = payload.get("date", "")
    if date:
        try:
            if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError("Invalid date format")
            dt.date.fromisoformat(date)
        except (ValueError, TypeError) as exc:
            raise EditorError("Use a valid photo date in YYYY-MM-DD format.") from exc
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}(?:[-_]+|$)", "", stem)
        stem = date + "-" + stem
    path = PHOTO_DIR / (stem + suffix)
    used_stems = {item.stem.casefold() for item in PHOTO_DIR.iterdir()
                  if item.suffix.lower() in PHOTO_EXTENSIONS | {".txt"}} if PHOTO_DIR.exists() else set()
    counter = 2
    while path.exists() or path.stem.casefold() in used_stems:
        path = PHOTO_DIR / (f"{stem}-{counter}" + suffix)
        counter += 1
    transaction({path: data, path.with_suffix(".txt"): (caption + "\n").encode("utf-8")})
    return {"message": f"Added {path.name}. Preview updated.", "filename": path.name}


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], initial_error: str = ""):
        self.token = secrets.token_urlsafe(32)
        self.preview = PreviewState(initial_error)
        super().__init__(address, EditorHandler)


class EditorHandler(SimpleHTTPRequestHandler):
    server: EditorServer

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_request(self, code="-", size="-"):
        if urlsplit(self.path).path == "/_preview/status" and code == 200:
            return
        super().log_request(code, size)

    def allowed_request(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://" + host)
            valid = (parsed.hostname in ("localhost", "127.0.0.1", "::1")
                     and parsed.port == self.server.server_port and not parsed.username
                     and not parsed.password and parsed.path == "" and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            self.send_error(403, "This editor is available only on this computer.")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            self.send_error(403, "Requests must come from this local editor.")
            return False
        return True

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json_response(self, status: int, payload: dict):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def authorized(self) -> bool:
        token = self.headers.get("X-Editor-Token", "")
        if not secrets.compare_digest(token, self.server.token):
            self.json_response(403, {"error": "This editor session has changed. Refresh the editor and try again."})
            return False
        return True

    def do_GET(self):
        if not self.allowed_request():
            return
        path = urlsplit(self.path).path
        if path == "/_studio/status":
            self.json_response(200, studio_identity())
            return
        if path == "/_preview/status":
            with LOCK:
                self.server.preview.check()
                self.json_response(200, self.server.preview.status())
            return
        if path == "/_editor" or path == "/_editor/":
            source = (ROOT / "tools" / "editor.html").read_text(encoding="utf-8")
            content = source.replace("__EDITOR_TOKEN__", self.server.token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if path.startswith("/_api/"):
            if not self.authorized():
                return
            if path != "/_api/state":
                self.json_response(404, {"error": "Unknown editor action."})
                return
            try:
                with LOCK:
                    self.server.preview.check()
                    result = state()
                    if self.server.preview.error:
                        result["warnings"].append(self.server.preview.error)
                    self.json_response(200, result)
            except Exception as exc:
                self.json_response(400, {"error": str(exc)})
            return
        decoded = unquote(path)
        candidate = (ROOT / decoded.lstrip("/")).resolve()
        try:
            parts = candidate.relative_to(ROOT).parts
        except ValueError:
            self.send_error(403)
            return
        if any(part.startswith(".") for part in parts) or (parts and parts[0] in ("tools", "tests")):
            self.send_error(404)
            return
        if candidate.is_dir() and not (candidate / "index.html").is_file():
            self.send_error(404)
            return
        if candidate.is_dir() or candidate.suffix.lower() == ".html":
            with LOCK:
                self.server.preview.check()
                super().do_GET()
        else:
            super().do_GET()

    def do_HEAD(self):
        # HEAD shares all access checks without exposing source directories.
        if not self.allowed_request():
            return
        self.send_error(405, "Use GET for the local preview.")

    def do_POST(self):
        if not self.allowed_request() or not self.authorized():
            return
        path = urlsplit(self.path).path
        if path not in ("/_api/publications", "/_api/photos", "/_api/headshots", "/_api/site", "/_api/build"):
            self.json_response(404, {"error": "Unknown editor action."})
            return
        attempted = False
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                raise EditorError("Send content as JSON.")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST:
                raise EditorError("This upload is too large. Use images smaller than 15 MB.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise EditorError("The editor request must contain a JSON object.")
            with LOCK:
                self.server.preview.check()
                attempted = True
                if path == "/_api/publications":
                    result = import_publications(payload)
                elif path == "/_api/photos":
                    result = save_photo(payload)
                elif path == "/_api/headshots":
                    result = save_headshot(payload)
                elif path == "/_api/site":
                    result = save_site(payload)
                else:
                    build_site()
                    result = {"message": "Preview rebuilt from your content files."}
                rebuilt = path != "/_api/publications" or result.get("added", 0) > 0
                self.server.preview.mark_current(rebuilt=rebuilt)
            self.json_response(200, result)
        except (EditorError, ValueError, UnicodeError) as exc:
            if attempted:
                with LOCK:
                    self.server.preview.mark_current()
            self.json_response(400, {"error": str(exc)})
        except Exception as exc:
            if attempted:
                with LOCK:
                    self.server.preview.mark_current()
            self.json_response(500, {"error": f"The change could not be saved: {exc}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the website editor on this computer.")
    parser.add_argument("--port", type=int, default=8001, help="Local port (default: 8001)")
    parser.add_argument("--no-browser", action="store_true", help="Print the editor URL without opening a browser")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    ports = [0] if args.port == 0 else range(args.port, min(args.port + STUDIO_PORT_ATTEMPTS, 65536))
    server = None
    for port in ports:
        if port and existing_studio(port):
            url = f"http://127.0.0.1:{port}/_editor"
            print(f"\nWebsite editor already running: {url}\nPreview: http://127.0.0.1:{port}/\n\nContinue editing in the existing window. Keep its terminal window open.\n", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
        try:
            server = EditorServer(("127.0.0.1", port))
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                print(f"Could not start the editor: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc
    if server is None:
        tried = str(args.port) if args.port == 0 else f"{args.port}–{min(args.port + STUDIO_PORT_ATTEMPTS - 1, 65535)}"
        print(f"Could not start the editor: localhost port(s) {tried} are occupied.\nChoose another starting port: python3 tools/manage.py --port <port>", file=sys.stderr)
        raise SystemExit(1)
    initial_error = ""
    try:
        build_site()
    except Exception as exc:
        initial_error = f"The initial preview could not be built: {exc}. Correct the content files or use the editor to fix them."
        print(f"The initial preview could not be built: {exc}\nYou can still open the editor to correct the content.", file=sys.stderr)
    server.preview = PreviewState(initial_error)
    url = f"http://127.0.0.1:{server.server_port}/_editor"
    print(f"\nWebsite editor: {url}\nPreview: http://127.0.0.1:{server.server_port}/\n\nChanges are saved locally. Publish with your normal GitHub workflow.\nKeep this window open while editing. Press Control+C to stop.\n", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWebsite editor stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

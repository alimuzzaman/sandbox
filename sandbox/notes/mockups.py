"""Mockups: a folder with ``index.html`` and its files, stored as one HTML note plus image assets.

Publishing packs the folder into one document. Local stylesheets and scripts
are inlined (``<style data-sb-src>``, ``<script data-sb-src>``), PNG, JPEG, GIF
and WebP files become note assets, and any other local file (SVG, fonts) becomes
a ``data:`` URI. A JSON manifest in the page maps each asset path back to its
file, so ``pull`` can rebuild the folder. A mockup's name is stored as the
note's download filename ``mockup-<name>``, which is how list and get find it.
"""

from __future__ import annotations

import base64
import html as _html
import json
import mimetypes
import posixpath
from pathlib import Path, PurePosixPath
import re
import urllib.parse
import urllib.request

from .errors import NotesError
from .service import NotesClient

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
PREFIX = "mockup-"
ASSET_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MANIFEST_ID = "sb-mockup"
_ATTR = re.compile(r"""(\s(?:src|href|poster)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
_SRCSET = re.compile(r"""(\ssrcset\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
_CSS_URL = re.compile(r"""url\(\s*(["']?)([^"')]+)\1\s*\)""", re.I)
_LINK = re.compile(r"<link\b[^>]*>", re.I)
_SCRIPT = re.compile(r"<script\b([^>]*)>\s*</script>", re.I)
_SRC = re.compile(r"""\ssrc\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_HREF = re.compile(r"""\shref\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_STYLESHEET = re.compile(r"""\srel\s*=\s*(["'])?[^"'>]*stylesheet""", re.I)
_MANIFEST = re.compile(r'\s*<script type="application/json" id="%s">(.*?)</script>' % MANIFEST_ID, re.S)
_INLINED_STYLE = re.compile(r'<style data-sb-src="([^"]+)">(.*?)</style>', re.S)
_INLINED_SCRIPT = re.compile(r'<script data-sb-src="([^"]+)"([^>]*)>(.*?)</script>', re.S)


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise NotesError("notes_input_invalid",
                         "mockup names use lowercase letters, digits and '-' (max 63)")
    return name


def is_local(ref: str) -> bool:
    ref = ref.strip()
    if not ref or ref.startswith(("#", "/", "\\")):
        return False
    return not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", ref)


class Packer:
    """Turn one mockup folder into note HTML; ``upload`` maps an image file to its asset path."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.images: dict[str, Path] = {}   # placeholder -> file
        self.missing: list[str] = []

    def _resolve(self, ref: str, base: PurePosixPath) -> PurePosixPath | None:
        path = urllib.parse.unquote(ref.split("#", 1)[0].split("?", 1)[0])
        if not path:
            return None
        joined = PurePosixPath(posixpath.normpath(str(base / path)))
        if str(joined).startswith(".."):
            self.missing.append(ref)
            return None
        target = (self.root / joined).resolve()
        # A symlink must not publish a file from outside the folder.
        if self.root not in target.parents or not target.is_file():
            self.missing.append(ref)
            return None
        return joined

    def _asset_ref(self, ref: str, base: PurePosixPath) -> str:
        if not is_local(ref):
            return ref
        rel = self._resolve(ref, base)
        if rel is None:
            return ref
        file = self.root / rel
        if file.suffix.lower() in ASSET_TYPES:
            placeholder = "sb-asset:" + str(rel)
            self.images[placeholder] = file
            return placeholder
        mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        return f"data:{mime};base64," + base64.b64encode(file.read_bytes()).decode()

    def _css(self, text: str, base: PurePosixPath) -> str:
        # Keep the original quote: url() inside style="" must not gain a double quote.
        return _CSS_URL.sub(
            lambda m: f"url({m.group(1)}{self._asset_ref(m.group(2), base)}{m.group(1)})", text)

    def _attrs(self, tag_text: str, base: PurePosixPath) -> str:
        # A link to another page (<a href>, <area href>) is navigation, not a file to embed.
        navigation = re.match(r"<(a|area)\b", tag_text, re.I) is not None

        def attr(match):
            if navigation and match.group(1).strip().lower().startswith("href"):
                return match.group(0)
            ref = _html.unescape(match.group(3))
            new = self._asset_ref(ref, base)
            return match.group(1) + match.group(2) + (match.group(3) if new == ref else new) \
                + match.group(2)
        tag_text = _ATTR.sub(attr, tag_text)

        def srcset(match):
            parts = []
            for candidate in match.group(3).split(","):
                bits = candidate.strip().split(None, 1)
                if bits:
                    bits[0] = self._asset_ref(bits[0], base)
                    parts.append(" ".join(bits))
            return match.group(1) + match.group(2) + ", ".join(parts) + match.group(2)
        return _SRCSET.sub(srcset, tag_text)

    def pack(self, name: str) -> str:
        index = self.root / "index.html"
        if not index.is_file():
            raise NotesError("notes_input_invalid", f"{self.root} has no index.html")
        try:
            text = index.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise NotesError("notes_input_invalid", "index.html is not UTF-8") from None
        top = PurePosixPath(".")

        def link(match):
            tag = match.group(0)
            href = _HREF.search(tag)
            if not (href and _STYLESHEET.search(tag) and is_local(href.group(2))):
                return tag
            rel = self._resolve(href.group(2), top)
            if rel is None:
                return tag
            css = self._css((self.root / rel).read_text(encoding="utf-8"), rel.parent)
            return f'<style data-sb-src="{_html.escape(str(rel))}">{css}</style>'

        def script(match):
            attrs = match.group(1)
            src = _SRC.search(attrs)
            if not (src and is_local(src.group(2))):
                return match.group(0)
            rel = self._resolve(src.group(2), top)
            if rel is None:
                return match.group(0)
            body = (self.root / rel).read_text(encoding="utf-8").replace("</script", "<\\/script")
            rest = _SRC.sub("", attrs)
            return f'<script data-sb-src="{_html.escape(str(rel))}"{rest}>{body}</script>'

        text = _LINK.sub(link, text)
        text = _SCRIPT.sub(script, text)
        # Rewrite <style> bodies as CSS and every other tag's attributes (and style=""),
        # leaving script bodies alone: markup inside JavaScript strings is not a reference.
        parts = re.split(r"(<script\b.*?</script\s*>|<style\b.*?</style\s*>)", text, flags=re.I | re.S)
        for i, part in enumerate(parts):
            if i % 2:
                if part[:6].lower() == "<style":
                    head, _, rest = part.partition(">")
                    body, _, tail = rest.rpartition("</")
                    parts[i] = head + ">" + self._css(body, top) + "</" + tail
                continue
            parts[i] = re.sub(r"<(?!/|!)[A-Za-z][^>]*>",
                              lambda m: self._css(self._attrs(m.group(0), top), top), part)
        text = "".join(parts)
        if self.missing:
            raise NotesError("notes_input_invalid", "local files not found in the mockup folder: "
                             + ", ".join(sorted(set(self.missing))[:20]))
        return text


def _with_manifest(text: str, manifest: dict) -> str:
    block = ('<script type="application/json" id="%s">%s</script>'
             % (MANIFEST_ID, json.dumps(manifest, sort_keys=True).replace("</", "<\\/")))
    text = _MANIFEST.sub("", text)
    match = re.search(r"</head\s*>", text, re.I)
    if match:
        return text[:match.start()] + block + "\n" + text[match.start():]
    return block + "\n" + text


def read_manifest(text: str) -> dict:
    match = _MANIFEST.search(text or "")
    if not match:
        return {}
    try:
        value = json.loads(match.group(1).replace("<\\/", "</"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class Mockups:
    def __init__(self, client: NotesClient, *, fetch=None) -> None:
        self.client = client
        self._fetch = fetch or _fetch

    def _find(self, name: str) -> dict | None:
        wanted = PREFIX + name + ".html"
        for note in self.client.list():
            if note.get("filename") == wanted and note.get("format", "html") == "html":
                return note
        return None

    def _require(self, name: str) -> dict:
        note = self._find(check_name(name))
        if note is None:
            raise NotesError("mockup_not_found", f"no mockup named '{name}'")
        return note

    def list(self) -> list[dict]:
        out = []
        for note in self.client.list():
            filename = note.get("filename") or ""
            if filename.startswith(PREFIX) and filename.endswith(".html") \
                    and note.get("format", "html") == "html":
                out.append({"name": filename[len(PREFIX):-len(".html")], "slug": note.get("slug"),
                            "url": note.get("url"), "title": note.get("title"),
                            "updated_at": note.get("updated_at")})
        return out

    def get(self, name: str) -> dict:
        note = self._require(name)
        data = self.client.get(note["slug"])
        data["name"] = name
        return data

    def _upload_and_write(self, slug: str, name: str, packer: Packer, text: str,
                          existing_assets: list[dict], *, title: str | None) -> dict:
        placeholders = sorted(packer.images)
        uploads = self.client.many([
            {"tool": "upload_asset", "arguments": {
                "slug": slug, "data": base64.b64encode(packer.images[p].read_bytes()).decode()}}
            for p in placeholders])
        failed = [str(packer.images[p].relative_to(packer.root))
                  for p, r in zip(placeholders, uploads) if not r.get("ok")]
        if failed:
            first = next(r for r in uploads if not r.get("ok"))
            raise NotesError("mockup_asset_failed", "image upload failed for: " + ", ".join(failed[:20]),
                             reason=first.get("error"), slug=slug)
        assets = {}
        for placeholder, result in zip(placeholders, uploads):
            path = (result.get("result") or {}).get("path")
            if not path:
                raise NotesError("notes_bad_response", "upload_asset returned no path", slug=slug)
            text = text.replace(placeholder, path)
            assets[path] = placeholder[len("sb-asset:"):]
        text = _with_manifest(text, {"name": name, "assets": assets, "version": 1})
        arguments = {"slug": slug, "content": text, "format": "html", "filename": PREFIX + name}
        if title:
            arguments["title"] = title
        note = self.client.one("update_note", **arguments)
        keep = {posixpath.basename(path) for path in assets}
        stale = [a.get("name") for a in existing_assets
                 if isinstance(a, dict) and a.get("name") and a.get("name") not in keep]
        if stale:
            self.client.many([{"tool": "delete_asset", "arguments": {"slug": slug, "name": n}}
                              for n in stale])
        note.update({"name": name, "assets": len(assets), "removed_assets": len(stale)})
        return note

    def publish(self, directory: Path, name: str, *, title: str | None = None,
                public: bool = False) -> dict:
        name = check_name(name)
        if self._find(name) is not None:
            raise NotesError("mockup_exists",
                             f"a mockup named '{name}' exists; use `sb mockups update {name} <dir>`")
        packer = Packer(directory)
        text = packer.pack(name)
        title = title or _title(text, name)
        arguments = {"title": title, "content": _with_manifest(text, {"name": name, "assets": {},
                                                                      "version": 1}),
                     "format": "html", "filename": PREFIX + name}
        if public:
            arguments["visibility"] = "public"
        created = self.client.one("create_note", **arguments)
        slug = created.get("slug")
        if not packer.images:
            created.update({"name": name, "assets": 0, "removed_assets": 0})
            return created
        try:
            return self._upload_and_write(slug, name, packer, text, [], title=None)
        except NotesError as exc:
            exc.detail.setdefault("slug", slug)
            exc.message += f" (the note {slug} exists; finish with `sb mockups update {name} <dir>`)"
            raise

    def update(self, name: str, directory: Path, *, title: str | None = None) -> dict:
        note = self._require(name)
        packer = Packer(directory)
        text = packer.pack(name)
        current = self.client.get(note["slug"])
        return self._upload_and_write(note["slug"], name, packer, text,
                                      current.get("assets") or [], title=title)

    def delete(self, name: str) -> dict:
        note = self._require(name)
        self.client.delete(note["slug"])
        return {"name": name, "slug": note["slug"], "deleted": True}

    def pull(self, name: str, directory: Path, *, force: bool = False) -> dict:
        target = Path(directory)
        if target.exists() and any(target.iterdir()) and not force:
            raise NotesError("notes_input_invalid", f"{target} is not empty; pass --force to overwrite")
        data = self.get(name)
        text = data.get("content") or ""
        manifest = read_manifest(text)
        mapped = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
        # Download everything before writing anything, so a failed pull leaves no partial folder.
        downloads = [(asset, self._fetch(asset.get("url"))) for asset in data.get("assets") or []
                     if isinstance(asset, dict) and asset.get("path")]
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        written: list[str] = []

        def safe(rel: str) -> Path:
            path = (root / rel).resolve()
            if root not in path.parents:
                raise NotesError("notes_bad_response", f"refusing to write outside {root}: {rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        files: dict[str, str] = {}          # asset path -> rel file
        for asset, content in downloads:
            rel = mapped.get(asset["path"]) or "assets/" + posixpath.basename(asset["path"])
            safe(rel).write_bytes(content)
            files[asset["path"]] = rel
            written.append(rel)

        def relink(segment: str, base: str) -> str:
            for path, rel in files.items():
                segment = segment.replace(path, posixpath.relpath(rel, base or "."))
            return segment

        def style(match):
            rel = _html.unescape(match.group(1))
            safe(rel).write_text(relink(match.group(2), posixpath.dirname(rel)), encoding="utf-8")
            written.append(rel)
            return f'<link rel="stylesheet" href="{_html.escape(rel)}">'

        def script(match):
            rel = _html.unescape(match.group(1))
            safe(rel).write_text(match.group(3).replace("<\\/script", "</script"), encoding="utf-8")
            written.append(rel)
            return f'<script src="{_html.escape(rel)}"{match.group(2)}></script>'

        text = _MANIFEST.sub("", text)
        text = _INLINED_SCRIPT.sub(script, text)
        text = _INLINED_STYLE.sub(style, text)
        safe("index.html").write_text(relink(text, ""), encoding="utf-8")
        written.append("index.html")
        return {"name": name, "slug": data.get("slug"), "directory": str(root),
                "files": sorted(set(written))}

    def share(self, name: str, hours: int) -> dict:
        note = self._require(name)
        link = self.client.share(note["slug"], hours)
        link["name"] = name
        return link

    def revoke(self, name: str) -> dict:
        note = self._require(name)
        result = self.client.revoke_shares(note["slug"])
        result["name"] = name
        return result


def _title(text: str, name: str) -> str:
    from .service import html_title
    return html_title(text) or name


def _fetch(url: str) -> bytes:
    from .config import service_url
    parsed = urllib.parse.urlparse(url if isinstance(url, str) else "")
    try:
        service_url(f"{parsed.scheme}://{parsed.netloc}")
    except NotesError:
        raise NotesError("notes_bad_response", "asset url is not https") from None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "sandbox-notes"}),
                         timeout=60) as response:
            return response.read()
    except OSError as exc:
        raise NotesError("notes_unreachable", f"could not download an asset: {exc}") from None

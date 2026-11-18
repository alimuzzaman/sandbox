"""Note operations on top of a transport that runs MCP tool calls.

A transport is anything with ``call(calls) -> results``: one result dict per
call, ``{"ok": True, "result": {...}}`` or ``{"ok": False, "code", "error"}``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .errors import NotesError

MAP_FILE = ".notes.json"
SYNC_SUFFIXES = (".md", ".markdown")
_TTL = re.compile(r"(\d{1,5})\s*([hd]?)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HTML_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def ttl_hours(value: str) -> int:
    match = _TTL.fullmatch((value or "").strip().lower())
    if not match or int(match.group(1)) < 1:
        raise NotesError("notes_input_invalid", "--ttl takes hours or days, e.g. 24h, 72, 7d")
    hours = int(match.group(1)) * (24 if match.group(2) == "d" else 1)
    return hours


def markdown_title(text: str) -> str | None:
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            match = _HEADING.match(line)
            if match:
                return match.group(1).strip()[:200] or None
    return None


def html_title(text: str) -> str | None:
    import html as _html
    for pattern in (_HTML_TITLE, _HTML_H1):
        match = pattern.search(text)
        if match:
            title = " ".join(_html.unescape(_TAGS.sub("", match.group(1))).split())
            if title:
                return title[:200]
    return None


def read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NotesError("notes_input_invalid", f"no such file: {path}") from None
    except UnicodeDecodeError:
        raise NotesError("notes_input_invalid", f"{path} is not UTF-8 text") from None
    except OSError as exc:
        raise NotesError("notes_input_invalid", f"cannot read {path}: {exc.strerror}") from None


def title_for(path: Path, text: str, *, html: bool) -> str:
    return (html_title(text) if html else markdown_title(text)) or Path(path).stem


def slugify(text: str) -> str:
    """The service's slugify (src/notes.php): ASCII only, like PCRE without /u;
    PHP's `?: 'note'` also treats "0" as empty."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:60]
    return slug if slug not in ("", "0") else "note"


def service_filename(name: str) -> str:
    """The download filename the service stores for a file published as ``name``."""
    return slugify(Path(name).stem)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    fd, staged = tempfile.mkstemp(prefix=".notes.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, 0o644)
        os.replace(staged, path)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise


class NotesClient:
    def __init__(self, transport, remote: str) -> None:
        self.transport = transport
        self.remote = remote

    # -- plumbing -----------------------------------------------------------

    def many(self, calls: list[dict], on_batch=None) -> list[dict]:
        results = self.transport.call(calls, on_batch=on_batch)
        if len(results) != len(calls):
            raise NotesError("notes_bad_response", "the notes client returned an incomplete batch")
        return results

    def one(self, tool: str, **arguments) -> dict:
        result = self.many([{"tool": tool, "arguments": arguments}])[0]
        if not result.get("ok"):
            raise NotesError(result.get("code") or "notes_failed", result.get("error") or "failed")
        data = result.get("result")
        return data if isinstance(data, dict) else {}

    # -- notes --------------------------------------------------------------

    def create(self, path: Path, *, title: str | None = None, html: bool = False,
               public: bool = False) -> dict:
        text = read_text(path)
        arguments = {"title": title or title_for(path, text, html=html), "content": text,
                     "filename": Path(path).stem}
        if html:
            arguments["format"] = "html"
        if public:
            arguments["visibility"] = "public"
        return self.one("create_note", **arguments)

    def update(self, slug: str, path: Path, *, title: str | None = None) -> dict:
        arguments = {"slug": slug, "content": read_text(path)}
        if title:
            arguments["title"] = title
        return self.one("update_note", **arguments)

    def get(self, slug: str) -> dict:
        return self.one("get_note", slug=slug)

    def list(self) -> list[dict]:
        notes: list[dict] = []
        while True:
            page = self.one("list_notes", limit=200, offset=len(notes)).get("notes") or []
            notes.extend(item for item in page if isinstance(item, dict))
            if len(page) < 200:
                return notes

    def delete(self, slug: str) -> dict:
        return self.one("delete_note", slug=slug)

    def upload(self, slug: str, path: Path) -> dict:
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise NotesError("notes_input_invalid", f"cannot read {path}: {exc.strerror}") from None
        return self.one("upload_asset", slug=slug, data=base64.b64encode(data).decode())

    def share(self, slug: str, hours: int) -> dict:
        return self.one("create_share_link", slug=slug, ttl_hours=hours)

    def revoke_shares(self, slug: str) -> dict:
        links = self.one("list_share_links", slug=slug).get("links") or []
        active = [link["id"] for link in links
                  if isinstance(link, dict) and link.get("status") == "active"
                  and isinstance(link.get("id"), int)]
        if active:
            results = self.many([{"tool": "revoke_share_link", "arguments": {"id": ident}}
                                 for ident in active])
            failed = [ident for ident, result in zip(active, results) if not result.get("ok")]
            if failed:
                raise NotesError("notes_failed", "some share links were not revoked",
                                 revoked=[i for i in active if i not in failed], failed=failed)
        return {"slug": slug, "revoked": active}

    # -- sync ---------------------------------------------------------------

    def _adopt(self, names, texts, entries, *, adopt, fail, run) -> list[str]:
        """Find already-published notes for unmapped files; return the files still to create.

        Candidates are tailnet markdown notes not in this map. A file matches the one
        note whose stored filename is its slugified name; files with no filename match
        then match by title, over the notes still unclaimed. Without ``adopt`` a match
        is taken only if its content already equals the file, so nothing is
        overwritten; a match that differs, or more than one match, fails the file.
        """
        mapped = {e["slug"] for e in entries.values()}
        existing, seen = [], set()
        for n in self.list():      # offset paging can repeat a note under concurrent edits
            if (n.get("slug") not in mapped and n.get("slug") not in seen
                    and n.get("format", "markdown") == "markdown"
                    and n.get("visibility", "tailnet") == "tailnet"):
                seen.add(n.get("slug"))
                existing.append(n)
        hint = (f"map the right slug for this file in {MAP_FILE}, or rerun with --adopt "
                "(overwrites the match for every unmapped file) or --create-duplicates")
        matches: dict[str, list[dict]] = {}
        for name in names:
            wanted = service_filename(name) + ".md"
            matches[name] = [n for n in existing if n.get("filename") == wanted]
        taken = {n["slug"] for m in matches.values() for n in m}
        for name in names:
            if not matches[name]:
                # Only notes published without a filename (stored as the title's slug):
                # a note sync made elsewhere stores its own file name, never this.
                matches[name] = [n for n in existing if n.get("title") == texts[name][1]
                                 and n["slug"] not in taken
                                 and n.get("filename") == slugify(n["title"]) + ".md"]
        claims: dict[str, list[str]] = {}
        for name in names:
            if len(matches[name]) == 1:
                claims.setdefault(matches[name][0]["slug"], []).append(name)
        fresh, pending = [], []
        for name in names:
            found = matches[name]
            if not found:
                fresh.append(name)
            elif len(found) > 1 or len(claims[found[0]["slug"]]) > 1:
                others = sorted(n["slug"] for n in found) if len(found) > 1 else claims[found[0]["slug"]]
                fail(name, {"code": "notes_adopt_ambiguous",
                            "error": f"more than one match ({', '.join(others)}); {hint}"})
            else:
                pending.append((name, found[0]["slug"]))
        if pending and not adopt:
            notes = self.many([{"tool": "get_note", "arguments": {"slug": slug}}
                               for _, slug in pending])
            same = []
            for (name, slug), result in zip(pending, notes):
                content = (result.get("result") or {}).get("content") if result.get("ok") else None
                if content == texts[name][0]:
                    same.append((name, slug))
                elif result.get("ok"):
                    fail(name, {"code": "notes_adopt_differs",
                                "error": f"existing note {slug} matches this file but its "
                                         f"content differs; {hint}"})
                else:
                    fail(name, result)
            pending = same
        if pending:
            run(pending)
        return fresh

    def sync(self, directory: Path, *, prune: bool = False, adopt: bool = False,
             create_duplicates: bool = False) -> dict:
        directory = Path(directory)
        if not directory.is_dir():
            raise NotesError("notes_input_invalid", f"not a directory: {directory}")
        map_path = directory / MAP_FILE
        state = {"version": 1, "remote": self.remote, "notes": {}}
        if map_path.exists():
            try:
                loaded = json.loads(map_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise NotesError("notes_map_invalid", f"{map_path} is not readable JSON; "
                                 "fix or remove it (removing it publishes every file again)") from None
            if not isinstance(loaded, dict) or not isinstance(loaded.get("notes"), dict):
                raise NotesError("notes_map_invalid", f"{map_path} has no notes map")
            if loaded.get("remote") not in (None, self.remote):
                raise NotesError(
                    "notes_map_remote_mismatch",
                    f"{map_path} belongs to remote '{loaded.get('remote')}', not '{self.remote}'")
            state = {"version": 1, "remote": self.remote, "notes": dict(loaded["notes"])}
        entries: dict = state["notes"]
        for name in [n for n, e in entries.items() if not (isinstance(e, dict) and e.get("slug"))]:
            del entries[name]   # a damaged entry is treated as never published
        root = directory.resolve()
        files = sorted(p for p in directory.iterdir()
                       if p.is_file() and not p.name.startswith(".")
                       and p.suffix.lower() in SYNC_SUFFIXES
                       # a symlink must not publish a file from outside the folder
                       and p.resolve().parent == root)
        report = {"directory": str(directory), "map": str(map_path), "remote": self.remote,
                  "created": [], "adopted": [], "updated": [], "recreated": [], "unchanged": [],
                  "orphaned": [], "pruned": [], "failed": []}
        texts = {}
        updates, creates = [], []
        for path in files:
            text = read_text(path)
            title = title_for(path, text, html=False)
            texts[path.name] = (text, title)
            entry = entries.get(path.name)
            if entry is not None:
                if entry.get("sha256") == sha256(text) and entry.get("title") == title:
                    report["unchanged"].append({"file": path.name, "slug": entry["slug"],
                                                "url": entry.get("url")})
                else:
                    updates.append(path.name)
            else:
                creates.append(path.name)

        def save():
            write_json_atomic(map_path, state)

        def fail(name, result):
            report["failed"].append({"file": name, "code": result.get("code"),
                                     "error": result.get("error")})

        def each(names, calls, handle):
            """Handle results batch by batch and save the map after each, so an
            interrupted run keeps every note it already published."""
            def on_batch(offset, results):
                for name, result in zip(names[offset:offset + len(results)], results):
                    handle(name, result)
                save()
            self.many(calls, on_batch=on_batch)

        def record(name, data, bucket):
            text, title = texts[name]
            entries[name] = {"slug": data.get("slug"), "url": data.get("url"),
                             "title": title, "sha256": sha256(text)}
            report[bucket].append({"file": name, "slug": data.get("slug"), "url": data.get("url")})

        def not_found(result):
            return result.get("code") == "notes_tool_error" and "not found" in str(result.get("error"))

        recreate = set()

        def updated(name, result):
            if result.get("ok"):
                record(name, result.get("result") or {}, "updated")
            elif not_found(result):
                recreate.add(name)
            else:
                fail(name, result)

        def update_calls(slugs):
            return [{"tool": "update_note", "arguments": {
                "slug": slug, "title": texts[name][1], "content": texts[name][0],
                "filename": Path(name).stem}} for name, slug in slugs]

        # Mapped files first: they need no lookup, so a failed lookup cannot block them.
        if updates:
            each(updates, update_calls([(n, entries[n]["slug"]) for n in updates]), updated)
        if creates and not create_duplicates:
            creates = self._adopt(creates, texts, entries, adopt=adopt, fail=fail,
                                  run=lambda slugs: each([n for n, _ in slugs], update_calls(slugs),
                                                         lambda name, result: record(
                                                             name, result.get("result") or {},
                                                             "adopted")
                                                         if result.get("ok") else fail(name, result)))
        creates += sorted(recreate)
        if creates:
            each(creates, [{"tool": "create_note", "arguments": {
                "title": texts[name][1], "content": texts[name][0],
                "filename": Path(name).stem}} for name in creates],
                lambda name, result: record(name, result.get("result") or {},
                                            "recreated" if name in recreate else "created")
                if result.get("ok") else fail(name, result))
        gone = sorted(name for name in entries if name not in texts)
        if gone and prune:
            def pruned(name, result):
                if result.get("ok") or not_found(result):
                    report["pruned"].append({"file": name, "slug": entries[name].get("slug")})
                    del entries[name]
                else:
                    fail(name, result)
            each(gone, [{"tool": "delete_note", "arguments": {"slug": entries[name].get("slug")}}
                        for name in gone], pruned)
        else:
            report["orphaned"] = [{"file": name, "slug": entries[name].get("slug")} for name in gone]
        save()
        report["ok"] = not report["failed"]
        return report

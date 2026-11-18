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

    def many(self, calls: list[dict]) -> list[dict]:
        results = self.transport.call(calls)
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
        arguments = {"title": title or title_for(path, text, html=html), "content": text}
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

    def sync(self, directory: Path, *, prune: bool = False) -> dict:
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
        files = sorted(p for p in directory.iterdir()
                       if p.is_file() and not p.name.startswith(".")
                       and p.suffix.lower() in SYNC_SUFFIXES)
        report = {"directory": str(directory), "map": str(map_path), "remote": self.remote,
                  "created": [], "updated": [], "recreated": [], "unchanged": [],
                  "orphaned": [], "pruned": [], "failed": []}
        texts = {}
        updates, creates = [], []
        for path in files:
            text = read_text(path)
            title = title_for(path, text, html=False)
            texts[path.name] = (text, title)
            entry = entries.get(path.name)
            if isinstance(entry, dict) and entry.get("slug"):
                if entry.get("sha256") == sha256(text) and entry.get("title") == title:
                    report["unchanged"].append({"file": path.name, "slug": entry["slug"],
                                                "url": entry.get("url")})
                else:
                    updates.append(path.name)
            else:
                creates.append(path.name)

        def record(name, data, bucket):
            text, title = texts[name]
            entries[name] = {"slug": data.get("slug"), "url": data.get("url"),
                             "title": title, "sha256": sha256(text)}
            report[bucket].append({"file": name, "slug": data.get("slug"), "url": data.get("url")})
            write_json_atomic(map_path, state)

        def fail(name, result):
            report["failed"].append({"file": name, "code": result.get("code"),
                                     "error": result.get("error")})

        if updates:
            calls = [{"tool": "update_note", "arguments": {
                "slug": entries[name]["slug"], "title": texts[name][1], "content": texts[name][0]}}
                for name in updates]
            for name, result in zip(updates, self.many(calls)):
                if result.get("ok"):
                    record(name, result.get("result") or {}, "updated")
                elif result.get("code") == "notes_tool_error" and "not found" in str(result.get("error")):
                    creates.append(name)
                    report.setdefault("_recreate", set()).add(name)
                else:
                    fail(name, result)
        recreate = report.pop("_recreate", set())
        if creates:
            calls = [{"tool": "create_note", "arguments": {
                "title": texts[name][1], "content": texts[name][0]}} for name in creates]
            for name, result in zip(creates, self.many(calls)):
                if result.get("ok"):
                    record(name, result.get("result") or {},
                           "recreated" if name in recreate else "created")
                else:
                    fail(name, result)
        gone = sorted(name for name in entries if name not in texts)
        if gone and prune:
            calls = [{"tool": "delete_note", "arguments": {"slug": entries[name].get("slug")}}
                     for name in gone]
            for name, result in zip(gone, self.many(calls)):
                missing = (result.get("code") == "notes_tool_error"
                           and "not found" in str(result.get("error")))
                if result.get("ok") or missing:
                    report["pruned"].append({"file": name, "slug": entries[name].get("slug")})
                    del entries[name]
                    write_json_atomic(map_path, state)
                else:
                    fail(name, result)
        else:
            report["orphaned"] = [{"file": name, "slug": entries[name].get("slug")} for name in gone]
        if not map_path.exists():
            write_json_atomic(map_path, state)
        report["ok"] = not report["failed"]
        return report

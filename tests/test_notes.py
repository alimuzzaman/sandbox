"""``sb notes`` / ``sb mockups``: config, login, the broker child, sync, mockups and the CLI.

The fixtures under tests/fixtures/notes/ are agent-notes ``views/dashboard.php`` and
``views/token_created.php`` (branch feat/html-notes-images-visibility) rendered with
stubbed helpers; the token in them is fake.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

from sandbox.notes import config as notes_config
from sandbox.notes import login as notes_login
from sandbox.notes.errors import NotesError
from sandbox.notes.mockups import Mockups, read_manifest
from sandbox.notes.service import MAP_FILE, NotesClient, markdown_title, ttl_hours
from tests.notes_fake_service import FakeNotes

FIXTURES = Path(__file__).parent / "fixtures" / "notes"


def png(rgb) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgb))) + chunk(b"IEND", b""))


class InProcess:
    """A transport that calls the fake service directly; ``fail_after`` simulates a lost child."""

    def __init__(self, fake: FakeNotes, fail_after: int | None = None) -> None:
        self.fake = fake
        self.fail_after = fail_after
        self.batches: list[list[str]] = []

    def call(self, calls, on_batch=None):
        self.batches.append([c["tool"] for c in calls])
        out = []
        for call in calls:
            if self.fail_after is not None and self.fail_after <= 0:
                out.append({"ok": False, "code": "notes_unreachable", "error": "down", "i": None})
                continue
            if self.fail_after is not None:
                self.fail_after -= 1
            data, is_error = self.fake.tool(call["tool"], dict(call["arguments"]))
            out.append({"ok": False, "code": "notes_tool_error", "error": data["error"]}
                       if is_error else {"ok": True, "result": data})
        if on_batch is not None:
            on_batch(0, out)
        return out


class ScriptedBroker:
    """A broker whose use_profile writes chosen result lines, then exits or raises."""

    def __init__(self, lines, raise_after=None, list_result=None):
        self.lines = lines
        self.raise_after = raise_after
        self.list_result = list_result   # answers a batch of only list_notes calls

    def __call__(self, profiles):
        return self

    def use_profile(self, name, surface):
        from sandbox.notes.transport import PROFILE
        assert name == PROFILE
        response = Path(self.profile_argv[-1])
        calls = json.loads(Path(self.profile_argv[-2]).read_text())["calls"]
        if self.list_result is not None and all(c["tool"] == "list_notes" for c in calls):
            with response.open("a") as handle:
                for i, _ in enumerate(calls):
                    handle.write(json.dumps(dict(self.list_result, i=i)) + "\n")
            return {"ok": True}
        with response.open("a") as handle:
            for line in self.lines:
                handle.write(json.dumps(line) + "\n")
        if self.raise_after is not None:
            raise self.raise_after
        return {"ok": True}


def scripted_transport(tmp, lines, raise_after=None, list_result=None):
    from sandbox.notes.transport import BrokerTransport
    broker = ScriptedBroker(lines, raise_after, list_result)

    def factory(profiles):
        broker.profile_argv = next(iter(profiles.values()))["argv"]
        return broker
    service = notes_config.NotesService("fake", "https://n.example", "personal", "K")
    return BrokerTransport(service, service_factory=factory, work_root=tmp / "work")


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNotes().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.transport = InProcess(self.fake)
        self.client = NotesClient(self.transport, "fake")


class ConfigTests(unittest.TestCase):
    def test_resolve_uses_default_remote_and_derived_token_key(self):
        cfg = {"notes": {"default_remote": "xspeed-dev",
                         "services": {"xspeed-dev": {"url": "https://notes.example.com/"}}}}
        service = notes_config.resolve(cfg, None)
        self.assertEqual((service.remote, service.url, service.endpoint),
                         ("xspeed-dev", "https://notes.example.com", "https://notes.example.com/mcp"))
        self.assertEqual((service.token_source, service.token_key),
                         ("personal", "SANDBOX_NOTES_TOKEN_XSPEED_DEV"))

    def test_resolve_refusals(self):
        for cfg, remote, code in (
                ({}, None, "notes_remote_missing"),
                ({"notes": {"services": {}}}, "x", "notes_remote_unknown"),
                ({"notes": {"services": {"x": {"url": "http://notes.example.com"}}}}, "x",
                 "notes_config_invalid"),
                ({"notes": {"services": {"x": {"url": "https://n.example/path"}}}}, "x",
                 "notes_config_invalid"),
                ({"notes": {"services": {"x": {"url": "https://n.example",
                                               "token": {"value": "an_x"}}}}}, "x",
                 "notes_config_invalid")):
            with self.subTest(cfg=cfg), self.assertRaises(NotesError) as caught:
                notes_config.resolve(cfg, remote)
            self.assertEqual(caught.exception.code, code)

    def test_a_bare_login_prints_a_runnable_command(self):
        one = {"notes": {"services": {"xspeed-dev": {"url": "https://notes.xspeedcdn.online"}}}}
        for cfg, hint in ((one, "sb notes login --remote xspeed-dev --url https://notes.xspeedcdn.online"),
                          ({}, "sb notes login --remote NAME --url https://notes.example.com")):
            with self.subTest(cfg=cfg), self.assertRaises(NotesError) as caught:
                notes_config.resolve(cfg, None)
            self.assertEqual(caught.exception.detail["hint"], hint)
            self.assertIn(hint, caught.exception.message)

    def test_loopback_http_is_accepted_for_local_services(self):
        self.assertEqual(notes_config.service_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080")


class HelperTests(unittest.TestCase):
    def test_ttl(self):
        self.assertEqual([ttl_hours(v) for v in ("24h", "72", "7d")], [24, 72, 168])
        for bad in ("0h", "1w", "", "-3"):
            with self.subTest(bad=bad), self.assertRaises(NotesError):
                ttl_hours(bad)

    def test_markdown_title_skips_fenced_code(self):
        self.assertEqual(markdown_title("```\n# not this\n```\n\n## Real title ##\n"), "Real title")
        self.assertIsNone(markdown_title("no heading"))


class LoginTests(FakeCase):
    def test_parsers_read_the_real_dashboard_templates(self):
        page = (FIXTURES / "dashboard.html").read_text()
        self.assertEqual(notes_login.token_rows(page),
                         [("laptop-claude", 7), ("sb-notes-mac & co", 9), ("sb-notes-mac & co", 12)])
        created = (FIXTURES / "token_created.html").read_text()
        self.assertEqual(notes_login._TOKEN.search(created).group(1), "an_" + "9f" * 24)

    def test_login_revokes_same_name_tokens_then_stores_the_new_one(self):
        other = self.fake.add_token("laptop-claude")
        self.fake.add_token("sb-notes-ci")
        self.fake.add_token("sb-notes-ci")
        service = notes_config.NotesService("fake", self.fake.url, "personal", "K")
        stored = []
        result = notes_login.login(service, token_name="sb-notes-ci", store=stored.append,
                                   verify=lambda: None)
        live = self.fake.live_tokens("sb-notes-ci")
        self.assertEqual(len(live), 1)
        self.assertEqual(stored, [live[0]["value"]])
        self.assertEqual([t["value"] for t in self.fake.live_tokens("laptop-claude")], [other])
        self.assertEqual(result["revoked_previous"], 2)
        self.assertTrue(result["verified"])
        self.assertNotIn(live[0]["value"], json.dumps(result))

    def test_login_refuses_when_tailnet_trust_does_not_sign_in(self):
        self.fake.trusted = False
        service = notes_config.NotesService("fake", self.fake.url, "personal", "K")
        with self.assertRaises(NotesError) as caught:
            notes_login.login(service, token_name="sb-notes-ci", store=self.fail)
        self.assertEqual(caught.exception.code, "notes_login_untrusted")
        self.assertEqual(self.fake.tokens, [])


class BrokerChildTests(FakeCase):
    """The real broker path: SecretService.use_profile runs child.py with the token."""

    def _transport(self, token: str):
        from sandbox.notes.transport import BrokerTransport
        from sandbox.secrets.context import build_secret_service

        personal = self.tmp / "personal.env"
        personal.write_text(f"SANDBOX_NOTES_TOKEN_FAKE='{token}'\n")
        personal.chmod(0o600)
        runtime = self.tmp / "runtime"

        def factory(profiles):
            return build_secret_service(project_root=self.tmp,
                                        config={"secrets": {"useProfiles": profiles}},
                                        personal_path=personal, runtime_root=runtime)
        service = notes_config.NotesService("fake", self.fake.url, "personal",
                                            "SANDBOX_NOTES_TOKEN_FAKE")
        return BrokerTransport(service, service_factory=factory, work_root=self.tmp / "work"), runtime

    def test_calls_run_in_the_child_and_the_token_is_never_returned_or_audited(self):
        token = self.fake.add_token("sb-notes-ci")
        transport, runtime = self._transport(token)
        results = transport.call([
            {"tool": "create_note", "arguments": {"title": "T", "content": "# T\n"}},
            {"tool": "list_notes", "arguments": {}},
            {"tool": "get_note", "arguments": {"slug": "missing"}}])
        self.assertEqual([r["ok"] for r in results], [True, True, False])
        self.assertEqual(results[1]["result"]["count"], 1)
        self.assertEqual(results[2]["code"], "notes_tool_error")
        self.assertEqual(list((self.tmp / "work").iterdir()), [])
        audit = (runtime / "secrets" / "audit.jsonl").read_text()
        self.assertIn("sandbox-notes-mcp", audit)
        self.assertNotIn(token, audit + json.dumps(results))

    def test_a_refused_token_stops_the_batch(self):
        transport, _ = self._transport("an_" + "0" * 32)
        results = transport.call([{"tool": "list_notes", "arguments": {}}] * 3)
        self.assertEqual([r["code"] for r in results], ["notes_unauthorized"] * 3)
        self.assertEqual([bool(r.get("skipped")) for r in results], [False, True, True])

    def test_a_redirect_is_refused_and_the_token_never_follows_it(self):
        token = self.fake.add_token("sb-notes-ci")
        transport, _ = self._transport(token)
        self.fake.redirect_mcp = True
        results = transport.call([{"tool": "list_notes", "arguments": {}}])
        self.assertEqual(results[0]["code"], "notes_bad_response")
        self.assertEqual(self.fake.sink_headers, [])

    def test_missing_token_key_says_to_log_in(self):
        transport, _ = self._transport("an_x")
        transport.service = notes_config.NotesService("fake", self.fake.url, "personal", "OTHER_KEY")
        with self.assertRaises(NotesError) as caught:
            transport.call([{"tool": "list_notes", "arguments": {}}])
        self.assertEqual(caught.exception.code, "notes_token_missing")


class SyncTests(FakeCase):
    def _write(self, name, text):
        (self.tmp / name).write_text(text)

    def _map(self):
        return json.loads((self.tmp / MAP_FILE).read_text())

    def test_sync_creates_then_is_a_no_op_then_updates_in_place(self):
        self._write("a.md", "# Alpha\nbody\n")
        self._write("b.md", "no heading\n")
        self._write(".hidden.md", "# skip\n")
        self._write("c.txt", "skip\n")
        first = self.client.sync(self.tmp)
        self.assertEqual(sorted(i["file"] for i in first["created"]), ["a.md", "b.md"])
        self.assertEqual({n["title"] for n in self.fake.notes.values()}, {"Alpha", "b"})
        slug = self._map()["notes"]["a.md"]["slug"]
        calls = len(self.fake.calls)
        again = self.client.sync(self.tmp)
        self.assertEqual(len(again["unchanged"]), 2)
        self.assertEqual(len(self.fake.calls), calls)
        self._write("a.md", "# Alpha two\n")
        third = self.client.sync(self.tmp)
        self.assertEqual([i["slug"] for i in third["updated"]], [slug])
        self.assertEqual(self.fake.notes[slug]["title"], "Alpha two")
        self.assertEqual(len(self.fake.notes), 2)

    def test_a_note_deleted_on_the_service_is_published_again(self):
        self._write("a.md", "# A\n")
        self.client.sync(self.tmp)
        old = self._map()["notes"]["a.md"]["slug"]
        self.fake.tool("delete_note", {"slug": old})
        self._write("a.md", "# A changed\n")
        report = self.client.sync(self.tmp)
        self.assertEqual(len(report["recreated"]), 1)
        self.assertNotEqual(self._map()["notes"]["a.md"]["slug"], old)

    def test_missing_files_are_reported_and_deleted_only_with_prune(self):
        self._write("a.md", "# A\n")
        self._write("b.md", "# B\n")
        self.client.sync(self.tmp)
        (self.tmp / "b.md").unlink()
        report = self.client.sync(self.tmp)
        self.assertEqual([i["file"] for i in report["orphaned"]], ["b.md"])
        self.assertEqual(len(self.fake.notes), 2)
        pruned = self.client.sync(self.tmp, prune=True)
        self.assertEqual([i["file"] for i in pruned["pruned"]], ["b.md"])
        self.assertEqual(len(self.fake.notes), 1)
        self.assertNotIn("b.md", self._map()["notes"])

    def test_an_interrupted_run_keeps_what_it_published_and_a_rerun_adds_no_duplicates(self):
        for name in ("a.md", "b.md", "c.md"):
            self._write(name, f"# {name}\n")
        self.client.transport = InProcess(self.fake, fail_after=2)   # the listing and one create
        report = self.client.sync(self.tmp)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["created"]), 1)
        self.assertEqual(len(self._map()["notes"]), 1)
        self.client.transport = InProcess(self.fake)
        rerun = self.client.sync(self.tmp)
        self.assertEqual(len(rerun["created"]), 2)
        self.assertEqual(len(self.fake.notes), 3)

    def test_a_child_that_stops_early_never_lends_its_result_to_later_calls(self):
        for name in ("a.md", "b.md", "c.md"):
            self._write(name, f"# {name}\n")
        ok = {"ok": True, "i": 0, "result": {"slug": "AAA", "url": "https://n.example/n/AAA"}}
        self.client.transport = scripted_transport(self.tmp, [ok], list_result={"ok": True, "result": {"notes": []}})
        report = self.client.sync(self.tmp)
        self.assertEqual([i["slug"] for i in report["created"]], ["AAA"])
        self.assertEqual([i["code"] for i in report["failed"]], ["notes_incomplete"] * 2)
        self.assertEqual(list(self._map()["notes"]), ["a.md"])

    def test_skipped_calls_after_a_tool_error_are_incomplete_not_the_tool_error(self):
        bad = {"ok": False, "i": 0, "code": "notes_tool_error", "error": "too big"}
        results = scripted_transport(self.tmp, [bad]).call(
            [{"tool": "get_note", "arguments": {"slug": "x"}}] * 30)
        self.assertEqual(results[0]["code"], "notes_tool_error")
        self.assertEqual({r["code"] for r in results[1:]}, {"notes_incomplete"})
        self.assertEqual(len(results), 30)

    def test_an_interrupt_keeps_the_notes_the_child_finished(self):
        for name in ("a.md", "b.md"):
            self._write(name, f"# {name}\n")
        ok = {"ok": True, "i": 0, "result": {"slug": "AAA", "url": "https://n.example/n/AAA"}}
        self.client.transport = scripted_transport(self.tmp, [ok], raise_after=KeyboardInterrupt(),
                                                   list_result={"ok": True, "result": {"notes": []}})
        with self.assertRaises(KeyboardInterrupt):
            self.client.sync(self.tmp)
        self.assertEqual(self._map()["notes"]["a.md"]["slug"], "AAA")
        self.assertEqual(list((self.tmp / "work").iterdir()), [])

    def test_symlinked_and_damaged_entries_are_handled(self):
        outside = self.tmp.parent / (self.tmp.name + "-outside.md")
        outside.write_text("# secret\n")
        self.addCleanup(outside.unlink)
        (self.tmp / "link.md").symlink_to(outside)
        self._write("a.md", "# A\n")
        (self.tmp / MAP_FILE).write_text(json.dumps({"version": 1, "remote": "fake",
                                                     "notes": {"a.md": "garbage"}}))
        report = self.client.sync(self.tmp)
        self.assertEqual([i["file"] for i in report["created"]], ["a.md"])
        self.assertNotIn("secret", json.dumps([n["title"] for n in self.fake.notes.values()]))

    def _existing(self, **arguments):
        arguments.setdefault("title", "t")
        arguments.setdefault("content", "x")
        return self.fake.tool("create_note", arguments)[0]

    def test_without_a_map_an_identical_existing_note_is_adopted_not_duplicated(self):
        # Published earlier by another client (the xspeed-notes MCP) with filename = file stem.
        text = "# Test 11: PHP\nbody\n"
        old = self._existing(title="Old title", content=text, filename="11-php-versions-7.4-8.4")
        self.assertEqual(old["filename"], "11-php-versions-7-4-8-4.md")
        self._write("11-php-versions-7.4-8.4.md", text)
        report = self.client.sync(self.tmp)
        self.assertEqual((len(report["created"]), [i["slug"] for i in report["adopted"]]),
                         (0, [old["slug"]]))
        self.assertEqual((len(self.fake.notes), self.fake.notes[old["slug"]]["content"]), (1, text))
        self.assertEqual(self._map()["notes"]["11-php-versions-7.4-8.4.md"]["slug"], old["slug"])
        self.assertEqual(len(self.client.sync(self.tmp)["unchanged"]), 1)

    def test_a_match_with_different_content_is_refused_unless_adopt(self):
        old = self._existing(content="theirs", filename="plan")
        self._write("plan.md", "# Plan\nmine\n")
        report = self.client.sync(self.tmp)
        self.assertEqual([i["code"] for i in report["failed"]], ["notes_adopt_differs"])
        self.assertIn("--adopt", report["failed"][0]["error"])
        self.assertEqual((report["created"], self.fake.notes[old["slug"]]["content"]), ([], "theirs"))
        report = self.client.sync(self.tmp, adopt=True)
        self.assertEqual([i["slug"] for i in report["adopted"]], [old["slug"]])
        self.assertEqual(self.fake.notes[old["slug"]]["content"], "# Plan\nmine\n")

    def test_create_duplicates_skips_the_lookup(self):
        self._existing(content="# A\n", filename="a")
        self._write("a.md", "# A\n")
        report = self.client.sync(self.tmp, create_duplicates=True)
        self.assertEqual((len(report["created"]), len(self.fake.notes)), (1, 2))
        self.assertNotIn("list_notes", self.fake.calls)

    def test_another_folders_note_with_the_same_file_name_is_never_overwritten(self):
        first, second = self.tmp / "a", self.tmp / "b"
        first.mkdir()
        second.mkdir()
        (first / "README.md").write_text("# Project A\n")
        (second / "README.md").write_text("# Project B\n")
        slug = self.client.sync(first)["created"][0]["slug"]
        report = self.client.sync(second)
        self.assertEqual([i["code"] for i in report["failed"]], ["notes_adopt_differs"])
        self.assertEqual(self.fake.notes[slug]["content"], "# Project A\n")

    def test_public_and_html_notes_are_never_candidates(self):
        public = self._existing(title="A", content="# A\n", filename="a", visibility="public")
        html = self._existing(title="B", content="# B\n", format="html")
        self._write("a.md", "# A\n")
        self._write("b.md", "# B\n")
        report = self.client.sync(self.tmp)
        self.assertEqual(sorted(i["file"] for i in report["created"]), ["a.md", "b.md"])
        self.assertEqual(report["adopted"], [])
        self.assertEqual(self.fake.notes[public["slug"]]["visibility"], "public")
        self.assertEqual(self.fake.notes[html["slug"]]["content"], "# B\n")

    def test_a_note_with_a_title_derived_filename_is_adopted_by_its_title(self):
        old = self._existing(title="Test 13: object cache", content="# Test 13: object cache\n")
        self._write("13-object-cache.md", "# Test 13: object cache\n")
        report = self.client.sync(self.tmp)
        self.assertEqual([i["slug"] for i in report["adopted"]], [old["slug"]])
        self.assertEqual(len(self.fake.notes), 1)

    def test_a_filename_match_wins_over_an_earlier_title_match(self):
        old = self._existing(title="Project Plan", content="# Project Plan\n", filename="overview")
        self._write("notes.md", "# Project Plan\n")       # sorts first, matches only by title
        self._write("overview.md", "# Project Plan\n")    # matches by filename
        report = self.client.sync(self.tmp)
        self.assertEqual([(i["file"], i["slug"]) for i in report["adopted"]], [("overview.md", old["slug"])])
        self.assertEqual([i["file"] for i in report["created"]], ["notes.md"])

    def test_two_existing_matches_are_refused_and_nothing_is_created(self):
        for _ in range(2):
            self._existing(content="# A\n", filename="a")
        self._write("a.md", "# A\n")
        report = self.client.sync(self.tmp)
        self.assertEqual([i["code"] for i in report["failed"]], ["notes_adopt_ambiguous"])
        self.assertFalse(report["ok"])
        self.assertEqual((report["created"], len(self.fake.notes)), ([], 2))
        self.assertNotIn("a.md", self._map()["notes"])

    def test_a_note_mapped_to_another_file_is_not_adopted(self):
        self._write("a.md", "# A\n")
        slug = self.client.sync(self.tmp)["created"][0]["slug"]
        self.fake.notes[slug]["filename"] = "b"
        self._write("b.md", "# A\n")
        report = self.client.sync(self.tmp)
        self.assertEqual([i["file"] for i in report["created"]], ["b.md"])

    def test_a_failed_lookup_still_saves_the_mapped_updates(self):
        self._write("a.md", "# A\n")
        self.client.sync(self.tmp)
        self._write("a.md", "# A two\n")
        self._write("b.md", "# B\n")
        self.client.transport = scripted_transport(
            self.tmp, [{"ok": True, "i": 0, "result": {"slug": self._map()["notes"]["a.md"]["slug"]}}],
            list_result={"ok": False, "code": "notes_unreachable", "error": "down"})
        with self.assertRaises(NotesError):
            self.client.sync(self.tmp)
        self.assertEqual(self._map()["notes"]["a.md"]["title"], "A two")

    def test_new_notes_store_the_source_file_name(self):
        self._write("11-php-versions-7.4-8.4.md", "# Different title\n")
        self.client.sync(self.tmp)
        self.assertEqual([n["filename"] for n in self.fake.notes.values()], ["11-php-versions-7-4-8-4"])

    def test_service_filename_matches_the_php_slugify(self):
        from sandbox.notes.service import service_filename
        # Vectors from agent-notes src/notes.php slugify() run under php -r.
        for name, want in (("11-php-versions-7.4-8.4.md", "11-php-versions-7-4-8-4"),
                           ("0.md", "note"), ("café au lait.md", "caf-au-lait"),
                           ("İstanbul.md", "stanbul"), ("a" * 59 + "-b.md", "a" * 59 + "-")):
            with self.subTest(name=name):
                self.assertEqual(service_filename(name), want)

    def test_a_map_for_another_remote_is_refused(self):
        (self.tmp / MAP_FILE).write_text(json.dumps({"version": 1, "remote": "elsewhere", "notes": {}}))
        with self.assertRaises(NotesError) as caught:
            self.client.sync(self.tmp)
        self.assertEqual(caught.exception.code, "notes_map_remote_mismatch")


class MockupTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.src = self.tmp / "src"
        for sub in ("css", "img", "js"):
            (self.src / sub).mkdir(parents=True)
        (self.src / "img" / "logo.png").write_bytes(png((255, 0, 0)))
        (self.src / "img" / "bg.png").write_bytes(png((0, 0, 255)))
        (self.src / "img" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        (self.src / "css" / "app.css").write_text("body{background:url(../img/bg.png)}\n")
        (self.src / "js" / "app.js").write_text('const s = "<img src=\\"nope.png\\">";\n')
        self.index = ('<!doctype html><html><head><title>Checkout</title>'
                      '<link rel="stylesheet" href="css/app.css">'
                      '<script src="https://cdn.example/x.js?a=1&amp;b=2"></script>'
                      '<script src="js/app.js"></script></head><body>'
                      '<img src="img/logo.png"><img src="img/icon.svg">'
                      "<div style=\"background:url('img/logo.png')\"></div></body></html>")
        (self.src / "index.html").write_text(self.index)
        fake = self.fake
        self.mockups = Mockups(self.client, fetch=lambda url: fake.assets[url.split("/")[-3]][
            url.split("/")[-1]])

    def test_publish_packs_the_folder_and_refuses_an_existing_name(self):
        note = self.mockups.publish(self.src, "checkout")
        stored = self.fake.notes[note["slug"]]
        self.assertEqual((stored["format"], stored["filename"], stored["title"]),
                         ("html", "mockup-checkout", "Checkout"))
        self.assertEqual(len(self.fake.assets[note["slug"]]), 2)
        html = stored["content"]
        self.assertIn('<style data-sb-src="css/app.css">', html)
        self.assertIn('nope.png', html)   # markup inside a script body is not a reference
        self.assertIn("data:image/svg+xml;base64,", html)
        self.assertIn("https://cdn.example/x.js?a=1&amp;b=2", html)
        self.assertIn("background:url('/n/%s/assets/" % note["slug"], html)
        self.assertEqual(set(read_manifest(html)["assets"].values()), {"img/logo.png", "img/bg.png"})
        with self.assertRaises(NotesError) as caught:
            self.mockups.publish(self.src, "checkout")
        self.assertEqual(caught.exception.code, "mockup_exists")
        self.assertEqual([m["name"] for m in self.mockups.list()], ["checkout"])

    def test_update_keeps_the_url_and_drops_unused_images(self):
        slug = self.mockups.publish(self.src, "checkout")["slug"]
        (self.src / "css" / "app.css").write_text("body{color:red}\n")
        (self.src / "img" / "bg.png").unlink()
        note = self.mockups.update("checkout", self.src)
        self.assertEqual(note["slug"], slug)
        self.assertEqual((note["assets"], note["removed_assets"]), (1, 1))
        self.assertEqual(len(self.fake.assets[slug]), 1)
        self.assertEqual(len(self.fake.notes), 1)

    def test_pull_rebuilds_the_folder(self):
        self.mockups.publish(self.src, "checkout")
        out = self.tmp / "out"
        result = self.mockups.pull("checkout", out)
        self.assertEqual(result["files"], ["css/app.css", "img/bg.png", "img/logo.png",
                                           "index.html", "js/app.js"])
        self.assertEqual((out / "img" / "logo.png").read_bytes(), png((255, 0, 0)))
        self.assertEqual((out / "css" / "app.css").read_text(), "body{background:url(../img/bg.png)}\n")
        self.assertEqual((out / "js" / "app.js").read_text(), (self.src / "js" / "app.js").read_text())
        index = (out / "index.html").read_text()
        self.assertIn('<link rel="stylesheet" href="css/app.css">', index)
        self.assertIn("background:url('img/logo.png')", index)
        self.assertNotIn("sb-mockup", index)
        with self.assertRaises(NotesError):
            self.mockups.pull("checkout", out)

    def test_missing_local_files_are_refused_before_anything_is_sent(self):
        (self.src / "index.html").write_text('<img src="img/gone.png"><img src="../outside.png">')
        with self.assertRaises(NotesError) as caught:
            self.mockups.publish(self.src, "checkout")
        self.assertIn("img/gone.png", caught.exception.message)
        self.assertEqual(self.fake.notes, {})

    def test_a_symlink_out_of_the_folder_is_refused(self):
        outside = self.tmp / "outside.png"
        outside.write_bytes(png((1, 2, 3)))
        (self.src / "img" / "leak.png").symlink_to(outside)
        (self.src / "index.html").write_text('<img src="img/leak.png">')
        with self.assertRaises(NotesError) as caught:
            self.mockups.publish(self.src, "checkout")
        self.assertIn("img/leak.png", caught.exception.message)
        self.assertEqual(self.fake.notes, {})

    def test_page_links_are_left_alone(self):
        (self.src / "page2.html").write_text("<p>two</p>")
        (self.src / "index.html").write_text('<a href="page2.html">next</a><a href="gone.html">x</a>')
        note = self.mockups.publish(self.src, "checkout")
        html = self.fake.notes[note["slug"]]["content"]
        self.assertIn('<a href="page2.html">', html)
        self.assertIn('<a href="gone.html">', html)

    def test_bad_names_are_refused(self):
        for name in ("Bad Name", "a" * 54, "a-" * 26 + "aa", "foo-", "-foo", "a--b"):
            with self.subTest(name=name), self.assertRaises(NotesError):
                self.mockups.publish(self.src, name)
        longest = "a" * 53
        self.mockups.publish(self.src, longest)
        self.assertEqual([m["name"] for m in self.mockups.list()], [longest])

    def test_a_symlinked_index_is_refused(self):
        outside = self.tmp / "outside.html"
        outside.write_text("<p>private</p>")
        (self.src / "index.html").unlink()
        (self.src / "index.html").symlink_to(outside)
        with self.assertRaises(NotesError):
            self.mockups.publish(self.src, "checkout")
        self.assertEqual(self.fake.notes, {})


class CliTests(FakeCase):
    def setUp(self):
        super().setUp()
        import sandbox.commands.notes as cli
        self.cli = cli
        patcher = mock.patch.object(cli, "_transport_factory", lambda service: self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = {"notes": {"default_remote": "fake",
                              "services": {"fake": {"url": "https://notes.example"}}}}

    def run_cli(self, command, argv, env=None):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        configure = self.cli.configure_notes if command == "notes" else self.cli.configure_mockups
        configure(sub.add_parser(command))
        args = parser.parse_args([command, *argv])
        handler = self.cli.cmd_notes if command == "notes" else self.cli.cmd_mockups
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, env or {}):
            try:
                handler(self.cfg, args)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_create_prints_slug_and_url_and_json_has_the_note(self):
        path = self.tmp / "n.md"
        path.write_text("# Hi\n")
        code, text = self.run_cli("notes", ["create", "--file", str(path)])
        slug, url = text.split()
        self.assertEqual((code, url), (0, f"{self.fake.url}/n/{slug}"))
        code, text = self.run_cli("notes", ["get", slug, "--json"])
        self.assertEqual(json.loads(text)["note"]["content"], "# Hi\n")

    def test_errors_exit_1_with_a_json_envelope(self):
        code, text = self.run_cli("notes", ["delete", "nope", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["error"]["code"], "notes_tool_error")

    def test_share_creation_is_refused_inside_a_durable_job(self):
        note = self.client.one("create_note", title="t", content="c")
        code, text = self.run_cli("notes", ["share", note["slug"], "--ttl", "1h", "--json"],
                                  env={"SANDBOX_DURABLE_JOB_ID": "job-1"})
        self.assertEqual((code, json.loads(text)["error"]["code"]), (1, "notes_refused_in_job"))
        self.assertEqual(self.fake.links, [])
        code, text = self.run_cli("notes", ["share", note["slug"], "--ttl", "1h", "--json"])
        self.assertTrue(json.loads(text)["link"]["url"].startswith("https://share.example/s/"))
        code, text = self.run_cli("notes", ["share", note["slug"], "--revoke", "--json"],
                                  env={"SANDBOX_DURABLE_JOB_ID": "job-1"})
        self.assertEqual(json.loads(text)["revoked"], [1])

    def test_mockups_publish_refusal_points_to_update(self):
        src = self.tmp / "m"
        src.mkdir()
        (src / "index.html").write_text("<html><body>x</body></html>")
        self.assertEqual(self.run_cli("mockups", ["publish", str(src), "--name", "m1"])[0], 0)
        code, text = self.run_cli("mockups", ["publish", str(src), "--name", "m1", "--json"])
        error = json.loads(text)["error"]
        self.assertEqual((code, error["code"]), (1, "mockup_exists"))
        self.assertIn("sb mockups update m1", error["message"])

    def test_both_commands_are_registered_feature_owned(self):
        from sandbox.registry import COMMAND_SPECS
        for name in ("notes", "mockups"):
            spec = COMMAND_SPECS.get(name)
            self.assertEqual(spec.owner, "sandbox.commands.notes")
            self.assertIsNotNone(spec.configure)


if __name__ == "__main__":
    unittest.main()

"""Spec 058 server capture helper, run for real against a temporary root."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

from sandbox.recovery import server_capture_helper as helper
from sandbox.recovery.server_capture import derive_state, slot_for
from tests.server_capture_support import (
    DUMPS, HELPER, SECRET, HelperHarness, declarations_bytes, request_for,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HelperCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.h = HelperHarness(self._directory.name)

    def tearDown(self):
        # Never leave a detached fake job running past the test.
        deadline = time.monotonic() + 30
        for slot in (self.h.root.glob("capture-*") if self.h.root.exists() else ()):
            while not helper.lock_free(str(slot / "job.lock")) and time.monotonic() < deadline:
                time.sleep(0.05)
        self._directory.cleanup()


class TestHelperPrimitives(HelperCase):
    """T007: owner-only primitives, JSON-only output, fixed codes, size bound."""

    def test_source_is_under_64_kib(self):
        self.assertLess(HELPER.stat().st_size, 64 * 1024)

    def test_private_write_is_owner_only_and_atomic(self):
        directory = Path(self._directory.name) / "records"
        directory.mkdir()
        helper.write_private(str(directory), "state.json", b'{"a":1}')
        path = directory / "state.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_bytes(), b'{"a":1}')
        self.assertEqual(sorted(item.name for item in directory.iterdir()), ["state.json"])
        helper.write_private(str(directory), "state.json", b'{"a":2}')
        self.assertEqual(path.read_bytes(), b'{"a":2}')

    def test_owned_read_rejects_links_sharing_and_foreign_owner(self):
        directory = Path(self._directory.name)
        good = directory / "good.json"
        helper.write_private(str(directory), "good.json", b"{}")
        self.assertEqual(helper.read_owned(str(good), 1024), b"{}")
        link = directory / "link.json"
        link.symlink_to(good)
        with self.assertRaises(helper.Refusal):
            helper.read_owned(str(link), 1024)
        shared = directory / "shared.json"
        shared.write_text("{}")
        os.chmod(shared, 0o640)
        with self.assertRaises(helper.Refusal):
            helper.read_owned(str(shared), 1024)
        hard = directory / "hard.json"
        os.link(good, hard)
        with self.assertRaises(helper.Refusal):
            helper.read_owned(str(good), 1024)
        hard.unlink()
        with mock.patch.object(helper.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(helper.Refusal):
                helper.read_owned(str(good), 1024)
        with self.assertRaises(helper.Refusal):
            helper.read_owned(str(good), 1)

    def test_unknown_op_and_bad_arity_print_only_fixed_json_codes(self):
        for argv in (("nonsense",), ("status",), ("status", "a", "b")):
            completed = self.h.raw(*argv)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout), {"code": "request_invalid", "ok": False})
            self.assertEqual(completed.stderr, b"")
        self.assertEqual(self.h.run("status", "capture-" + "0" * 64)["code"], "capture_not_found")
        self.assertEqual(self.h.run("status", "../escape")["code"], "request_invalid")
        relative = self.h.raw("list", root=Path("relative/root"))
        self.assertEqual(json.loads(relative.stdout)["code"], "request_invalid")

    def test_root_is_created_owner_only(self):
        self.h.start()
        info = self.h.root.lstat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.h.slot_path().lstat().st_mode), 0o700)
        self.h.wait()

    def test_helper_and_service_derive_identical_states(self):
        for state in (None, "queued", "running", "complete", "failed", "retired"):
            for lock_free in (True, False):
                for receipt_valid in (True, False):
                    facts = {"state": None if state is None else {"state": state},
                             "lock_free": lock_free, "receipt_valid": receipt_valid}
                    self.assertEqual(helper.derive(facts), derive_state(facts), facts)


class TestHelperStart(HelperCase):
    """T011/T012: detached start, replay, conflicts and refusals."""

    def test_start_returns_promptly_and_job_holds_locks(self):
        env = self.h.env(FAKE_DUMP_DELAY="2")
        started = time.monotonic()
        outcome = self.h.start(env=env)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((outcome["ok"], outcome["existing"], outcome["state"]), (True, False, "queued"))
        slot = self.h.slot_path()
        for name in ("request.json", "declarations.json", "state.json", "job.lock"):
            self.assertTrue((slot / name).exists(), name)
            if name != "job.lock":
                self.assertEqual(stat.S_IMODE((slot / name).stat().st_mode), 0o600)
        self.assertFalse(helper.lock_free(str(slot / "job.lock")))
        self.assertFalse(helper.lock_free(str(self.h.root / "active.lock")))
        running = self.h.status()
        self.assertIn(derive_state(running), {"queued", "running"})
        final = self.h.wait()
        self.assertEqual(final["state"]["state"], "complete")
        self.assertTrue(helper.lock_free(str(self.h.root / "active.lock")))
        self.assertNotIn(SECRET.encode(), self.h.all_bytes())
        self.assertTrue(all(SECRET not in json.dumps(call["argv"]) for call in self.h.docker_calls()))
        self.assertTrue(any(call["mysql_pwd_set"] for call in self.h.docker_calls()))

    def test_replay_returns_existing_even_while_running(self):
        env = self.h.env(FAKE_DUMP_DELAY="1")
        self.h.start(env=env)
        replay = self.h.start(env=env)
        self.assertTrue(replay["existing"])
        self.assertEqual(replay["status"]["request"]["request_id"],
                         request_for("fixture-remote", "set-a")["request_id"])
        self.h.wait()
        again = self.h.start()
        self.assertTrue(again["existing"])
        self.assertEqual(again["status"]["state"]["state"], "complete")
        self.assertEqual(sum(1 for call in self.h.docker_calls()
                             if "mariadb-dump" in call["argv"]), 1)

    def test_racing_starts_for_one_id_create_one_slot(self):
        env = self.h.env(FAKE_DUMP_DELAY="1")
        outcomes = []
        threads = [threading.Thread(target=lambda: outcomes.append(self.h.start(env=env)))
                   for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(item["ok"] for item in outcomes), [True, True])
        self.assertEqual(sorted(item["existing"] for item in outcomes), [False, True])
        self.assertEqual(len(list(self.h.root.glob("capture-*"))), 1)
        self.h.wait()

    def test_different_binding_is_refused_and_files_unchanged(self):
        self.h.start()
        self.h.wait()
        before = {path.name: path.read_bytes() for path in self.h.slot_path().iterdir()
                  if path.is_file()}
        conflict = self.h.start(request=request_for("fixture-remote", "set-a", salt="drift"))
        self.assertEqual(conflict, {"code": "capture_binding_conflict", "ok": False})
        after = {path.name: path.read_bytes() for path in self.h.slot_path().iterdir()
                 if path.is_file()}
        self.assertEqual(before, after)

    def test_second_capture_on_remote_is_refused_while_one_is_active(self):
        env = self.h.env(FAKE_DUMP_DELAY="2")
        self.h.start(env=env)
        other = self.h.start(backup_id="set-b", env=env)
        self.assertEqual(other["code"], "capture_in_progress")
        self.assertEqual(other["active_backup_id"], "set-a")
        self.assertFalse(self.h.slot_path(backup_id="set-b").exists())
        self.h.wait()

    def test_kill_user_processes_refuses_detach(self):
        outcome = self.h.start(env=self.h.env(FAKE_KILL_USER_PROCESSES="yes"))
        self.assertEqual(outcome, {"code": "detach_unsupported", "ok": False})
        self.assertFalse(self.h.slot_path().exists())
        self.assertEqual(self.h.docker_calls(), [])
        absent_logind = self.h.start(env=self.h.env(FAKE_LOGINCTL_EXIT="1"))
        self.assertTrue(absent_logind["ok"])
        self.h.wait()

    def test_missing_password_or_mismatched_declarations_are_refused(self):
        self.assertEqual(self.h.start(password="")["code"], "missing_database_credential")
        request = request_for("fixture-remote", "set-a")
        tampered = self.h.start(request=request, declarations=b'{"other":1}\n')
        self.assertEqual(tampered["code"], "request_invalid")
        self.assertFalse(self.h.slot_path().exists())


class TestHelperJob(HelperCase):
    """T013-T015: preflight, inventory, archive, receipt, timeouts and cleanup."""

    def _only_records_left(self):
        names = {path.name for path in self.h.slot_path().iterdir()}
        self.assertTrue(names <= set(helper.KEEP), names)

    def test_insufficient_space_fails_before_any_dump(self):
        self.h.start(env=self.h.env(FAKE_DB_BYTES=str(10 ** 18)))
        final = self.h.wait()
        state = final["state"]
        self.assertEqual((state["state"], state["reason"], state["phase"]),
                         ("failed", "insufficient_space", "preflight"))
        detail = state["detail"]
        self.assertEqual(detail["shortfall_bytes"], detail["need_bytes"] - detail["available_bytes"])
        self.assertEqual(detail["need_bytes"], int(2 * (10 ** 18 + 1000) * 1.10))
        self.assertFalse(any("mariadb-dump" in call["argv"] for call in self.h.docker_calls()))
        self._only_records_left()

    def test_inventory_lists_tables_and_views_with_estimates(self):
        self.h.start(env=self.h.env(FAKE_DUMP=str(DUMPS / "with_views.sql"),
                                    FAKE_INVENTORY=str(DUMPS / "with_views.tsv")))
        self.assertEqual(self.h.wait()["state"]["state"], "complete")
        receipt = json.loads((self.h.slot_path() / "receipt.json").read_text())
        inventory = receipt["inventory"]
        self.assertEqual(inventory["tables"], [
            {"name": "wp_options", "type": "table", "rows_estimate": 2},
            {"name": "wp_posts", "type": "table", "rows_estimate": 0},
            {"name": "wp_recent_posts", "type": "view", "rows_estimate": None},
        ])
        self.assertEqual(inventory["summary"], {"table_count": 2, "view_count": 1,
                                                "rows_estimate_total": 2})
        self.assertTrue(inventory["dump_matches"])
        self.assertLessEqual(inventory["taken_at"], receipt["completed_at"])

    def test_missing_or_extra_dump_table_is_an_inventory_mismatch(self):
        for fixture, key, names in (("missing_table.sql", "missing_from_dump", ["wp_users"]),
                                    ("extra_table.sql", "not_in_inventory", ["wp_extra"])):
            with self.subTest(fixture=fixture):
                backup = fixture.split(".")[0].replace("_", "-")
                self.h.start(backup_id=backup, env=self.h.env(FAKE_DUMP=str(DUMPS / fixture)))
                state = self.h.wait(backup_id=backup)["state"]
                self.assertEqual((state["state"], state["reason"]), ("failed", "inventory_mismatch"))
                self.assertEqual(state["detail"][key], names)
                self.assertFalse((self.h.slot_path(backup_id=backup) / "archive.tar").exists())

    def test_archive_and_member_hashes_and_receipt_fields(self):
        self.h.start()
        final = self.h.wait()
        slot = self.h.slot_path()
        archive_bytes = (slot / "archive.tar").read_bytes()
        receipt = json.loads((slot / "receipt.json").read_text())
        request = request_for("fixture-remote", "set-a")
        self.assertEqual(receipt["archive_sha256"], _sha(archive_bytes))
        self.assertEqual(receipt["archive_size"], len(archive_bytes))
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
            names = archive.getnames()
            members = {name: archive.extractfile(name).read() for name in names}
        self.assertEqual(names, ["database.sql", "wordpress.tar"])
        self.assertEqual(receipt["members"], [
            {"name": name, "sha256": _sha(members[name]), "size": len(members[name])}
            for name in names])
        self.assertEqual(members["database.sql"], (DUMPS / "tables_only.sql").read_bytes())
        for key in ("request_id", "backup_operation_id", "source_binding", "declarations_sha256"):
            self.assertEqual(receipt[key], request[key])
        self.assertLessEqual(receipt["started_at"], receipt["completed_at"])
        self.assertGreaterEqual((slot / "receipt.json").stat().st_mtime_ns,
                                (slot / "archive.tar").stat().st_mtime_ns)
        self.assertEqual(stat.S_IMODE((slot / "archive.tar").stat().st_mode), 0o600)
        tar_calls = [call for call in self.h.docker_calls() if "tar" in call["argv"]]
        self.assertEqual(len(tar_calls), 2)
        self.assertFalse((slot / "work").exists())
        self.assertEqual(sorted(path.name for path in slot.iterdir()),
                         ["archive.tar", "declarations.json", "job.lock", "receipt.json",
                          "request.json", "state.json"])
        self.assertTrue(final["receipt_valid"])

    def test_changed_tree_between_streams_is_source_changed(self):
        counter = Path(self._directory.name) / "generation"
        self.h.start(env=self.h.env(FAKE_TAR_CHANGE_FILE=str(counter)))
        state = self.h.wait()["state"]
        self.assertEqual((state["state"], state["reason"]), ("failed", "source_changed"))
        self._only_records_left()

    def test_step_and_job_timeouts_fail_and_clean_up(self):
        self.h.start(backup_id="step", env=self.h.env(FAKE_DUMP_DELAY="3",
                                                      SANDBOX_CAPTURE_STEP_SECONDS="1"))
        state = self.h.wait(backup_id="step")["state"]
        self.assertEqual((state["state"], state["reason"]), ("failed", "capture_timeout"))
        self.h.start(backup_id="job", env=self.h.env(FAKE_TAR_DELAY="1",
                                                     SANDBOX_CAPTURE_JOB_SECONDS="1.5"))
        state = self.h.wait(backup_id="job")["state"]
        self.assertEqual((state["state"], state["reason"]), ("failed", "capture_timeout"))
        for backup in ("step", "job"):
            names = {path.name for path in self.h.slot_path(backup_id=backup).iterdir()}
            self.assertTrue(names <= set(helper.KEEP), names)

    def test_dump_and_tar_failures_have_typed_reasons(self):
        for backup, env, reason in (("dump", {"FAKE_DUMP_EXIT": "2"}, "dump_failed"),
                                    ("files", {"FAKE_TAR_EXIT": "2"}, "files_failed"),
                                    ("pre", {"FAKE_DU_EXIT": "2"}, "preflight_failed"),
                                    ("inv", {"FAKE_MARIADB_EXIT": "1"}, "preflight_failed")):
            with self.subTest(reason=reason, backup=backup):
                self.h.start(backup_id=backup, env=self.h.env(**env))
                state = self.h.wait(backup_id=backup)["state"]
                self.assertEqual((state["state"], state["reason"]), ("failed", reason))

    def test_phases_only_move_forward(self):
        self.h.start(env=self.h.env(FAKE_DUMP_DELAY="0.5", FAKE_TAR_DELAY="0.3"))
        seen = []
        while True:
            status = self.h.status()
            raw = status["state"] or {}
            if raw.get("phase"):
                seen.append(helper.PHASES.index(raw["phase"]))
            if raw.get("state") in ("complete", "failed") and status["lock_free"]:
                break
            time.sleep(0.02)
        self.assertEqual(seen, sorted(seen))
        self.assertGreaterEqual(len(set(seen)), 2)


class TestHelperStatusAndList(HelperCase):
    """T025/T044: read-only status and bounded listing."""

    def test_status_reports_facts_and_never_writes(self):
        self.h.start()
        self.h.wait()
        slot = self.h.slot_path()

        def snapshot():
            return ({path.name: (path.stat().st_mtime_ns, path.read_bytes())
                     for path in slot.iterdir()}, slot.stat().st_mtime_ns,
                    self.h.root.stat().st_mtime_ns)

        before = snapshot()
        answers = [self.h.status() for _ in range(3)]
        self.assertEqual(snapshot(), before)
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[1], answers[2])
        status = answers[0]
        self.assertTrue(status["lock_free"])
        self.assertTrue(status["receipt_valid"])
        self.assertEqual(status["archive_size"], (slot / "archive.tar").stat().st_size)
        self.assertEqual(status["residue_bytes"], 0)
        self.assertEqual(status["request"]["backup_id"], "set-a")
        self.assertEqual(status["receipt"]["inventory_summary"]["table_count"], 3)

    def test_missing_or_malformed_receipt_is_not_valid(self):
        self.h.start()
        self.h.wait()
        receipt = self.h.slot_path() / "receipt.json"
        payload = receipt.read_bytes()
        receipt.write_bytes(b"{not json")
        self.assertFalse(self.h.status()["receipt_valid"])
        self.assertEqual(derive_state(self.h.status()), "incomplete")
        receipt.unlink()
        status = self.h.status()
        self.assertFalse(status["receipt_valid"])
        self.assertEqual(status["residue_bytes"], status["archive_size"])
        helper.write_private(str(self.h.slot_path()), "receipt.json", payload)
        self.assertTrue(self.h.status()["receipt_valid"])

    def test_list_returns_slot_facts_and_legacy_archives(self):
        self.h.start()
        self.h.wait()
        legacy = self.h.home / "runtime" / "recovery-controller"
        legacy.mkdir(parents=True)
        (legacy / "recovery-old.tar").write_bytes(b"x" * 10)
        (legacy / "recovery-old.receipt.json").write_text("{}")
        listing = self.h.run("list")
        self.assertEqual(listing["legacy"], [{"name": "recovery-old.tar", "size": 10}])
        self.assertEqual(len(listing["slots"]), 1)
        item = listing["slots"][0]
        self.assertEqual((item["backup_id"], item["state"], item["receipt_valid"], item["promoted"]),
                         ("set-a", "complete", True, False))
        self.assertEqual(item["slot"], slot_for("fixture-remote", "set-a"))
        self.assertIsInstance(item["completed_at"], float)

    def test_list_is_bounded_to_500_slots(self):
        self.h.root.mkdir(parents=True, mode=0o700)
        for index in range(501):
            (self.h.root / ("capture-%064x" % index)).mkdir(mode=0o700)
        listing = self.h.run("list")
        self.assertEqual(len(listing["slots"]), 500)
        self.assertTrue(listing["truncated"])
        self.assertLessEqual(len(json.dumps(listing)), 256 * 1024)

    def test_list_without_root_is_empty(self):
        self.assertEqual(self.h.run("list"), {"legacy": [], "ok": True, "slots": [],
                                              "truncated": False})
        self.assertFalse(self.h.root.exists())


class TestHelperReadOps(HelperCase):
    """T031: bounded read ops and the idempotent promotion marker."""

    def setUp(self):
        super().setUp()
        self.h.start()
        self.h.wait()
        self.slot = slot_for("fixture-remote", "set-a")
        self.archive = (self.h.slot_path() / "archive.tar").read_bytes()

    def test_read_receipt_and_declaration(self):
        receipt = self.h.run("read-receipt", self.slot)["receipt"]
        self.assertEqual(receipt["archive_sha256"], _sha(self.archive))
        text = self.h.run("read-declaration", self.slot)["text"]
        self.assertEqual(text.encode(), declarations_bytes("set-a"))
        self.assertEqual(_sha(text.encode()), receipt["declarations_sha256"])

    def test_read_chunk_returns_header_and_exact_bytes(self):
        completed = self.h.raw("read-chunk", self.slot, "10", "100")
        header, _, data = completed.stdout.partition(b"\n")
        header = json.loads(header)
        self.assertEqual(data, self.archive[10:110])
        self.assertEqual(header, {"length": 100, "offset": 10, "ok": True, "sha256": _sha(data)})
        tail = self.h.raw("read-chunk", self.slot, str(len(self.archive) - 5), "100")
        header, _, data = tail.stdout.partition(b"\n")
        self.assertEqual(json.loads(header)["length"], 5)
        self.assertEqual(data, self.archive[-5:])
        for offset, length in ((0, 16 * 1024 * 1024 + 1), (len(self.archive), 1), (-1, 1), (0, 0)):
            with self.subTest(offset=offset, length=length):
                refused = self.h.run("read-chunk", self.slot, str(offset), str(length))
                self.assertEqual(refused["code"], "request_invalid")

    def test_mark_promoted_is_idempotent_and_needs_a_valid_receipt(self):
        request_id = request_for("fixture-remote", "set-a")["request_id"]
        marker = json.dumps({"request_id": request_id, "set_id": "set-a",
                             "ciphertext_sha256": "d" * 64, "promoted_at": "2026-10-08T00:00:00+00:00"})
        self.assertEqual(self.h.run("mark-promoted", self.slot, marker), {"existing": False, "ok": True})
        self.assertEqual(self.h.run("mark-promoted", self.slot, marker), {"existing": True, "ok": True})
        self.assertEqual(stat.S_IMODE((self.h.slot_path() / "promoted.json").stat().st_mode), 0o600)
        self.assertTrue((self.h.slot_path() / "archive.tar").exists())
        wrong = json.dumps({"request_id": "recovery-" + "0" * 64, "set_id": "set-a",
                            "ciphertext_sha256": "d" * 64, "promoted_at": "x"})
        self.h.start(backup_id="set-b", env=self.h.env(FAKE_DUMP_EXIT="2"))
        self.h.wait(backup_id="set-b")
        failed_slot = slot_for("fixture-remote", "set-b")
        self.assertEqual(self.h.run("mark-promoted", failed_slot, marker)["code"], "capture_not_complete")
        self.assertEqual(self.h.run("read-receipt", failed_slot)["code"], "capture_not_complete")
        self.assertEqual(self.h.run("read-chunk", failed_slot, "0", "1")["code"], "capture_not_complete")
        (self.h.slot_path() / "promoted.json").unlink()
        self.assertEqual(self.h.run("mark-promoted", self.slot, wrong)["code"], "request_invalid")


class TestHelperRetire(HelperCase):
    """T050 (helper side): confirmed retire and integrity mismatch."""

    def _plan(self, backup_id="set-a"):
        status = self.h.status(backup_id=backup_id)
        receipt = status["receipt"] or {}
        return json.dumps({"state": helper.review_state(status),
                           "archive_sha256": receipt.get("archive_sha256"),
                           "archive_size": status["archive_size"]})

    def test_complete_unpromoted_capture_is_not_retirable(self):
        self.h.start()
        self.h.wait()
        refused = self.h.run("retire", slot_for("fixture-remote", "set-a"), self._plan())
        self.assertEqual(refused, {"code": "not_retirable", "ok": False, "state": "complete"})
        self.assertTrue((self.h.slot_path() / "archive.tar").exists())

    def test_promoted_capture_retires_and_keeps_its_record(self):
        self.h.start()
        self.h.wait()
        slot = slot_for("fixture-remote", "set-a")
        request_id = request_for("fixture-remote", "set-a")["request_id"]
        self.h.run("mark-promoted", slot, json.dumps({
            "request_id": request_id, "set_id": "set-a", "ciphertext_sha256": "d" * 64,
            "promoted_at": "2026-10-08T00:00:00+00:00"}))
        plan = self._plan()
        size = self.h.status()["archive_size"]
        retired = self.h.run("retire", slot, plan)
        self.assertTrue(retired["ok"])
        self.assertGreaterEqual(retired["removed_bytes"], size)
        names = sorted(path.name for path in self.h.slot_path().iterdir())
        self.assertEqual(names, ["declarations.json", "job.lock", "promoted.json", "request.json",
                                 "state.json"])
        state = self.h.status()["state"]
        self.assertEqual((state["state"], state["previous_state"]), ("retired", "promoted"))
        self.assertEqual(self.h.run("retire", slot, plan)["code"], "not_retirable")

    def test_failed_capture_retires_and_drift_is_refused(self):
        self.h.start(env=self.h.env(FAKE_DUMP=str(DUMPS / "missing_table.sql")))
        self.h.wait()
        slot = slot_for("fixture-remote", "set-a")
        stale = json.dumps({"state": "failed", "archive_sha256": None, "archive_size": 99})
        self.assertEqual(self.h.run("retire", slot, stale)["code"], "retire_candidate_changed")
        self.assertTrue(self.h.run("retire", slot, self._plan())["ok"])
        self.assertEqual(self.h.status()["state"]["reason"], "inventory_mismatch")

    def test_running_capture_is_not_retirable(self):
        self.h.start(env=self.h.env(FAKE_DUMP_DELAY="1.5"))
        slot = slot_for("fixture-remote", "set-a")
        refused = self.h.run("retire", slot, json.dumps({"state": "running"}))
        self.assertEqual(refused["code"], "not_retirable")
        self.h.wait()

    def test_integrity_check_marks_a_drifted_archive_failed(self):
        self.h.start()
        self.h.wait()
        slot = slot_for("fixture-remote", "set-a")
        self.assertEqual(self.h.run("check-integrity", slot), {"mismatch": False, "ok": True})
        archive = self.h.slot_path() / "archive.tar"
        data = bytearray(archive.read_bytes())
        data[-1] ^= 0xFF
        archive.write_bytes(bytes(data))
        self.assertEqual(self.h.run("check-integrity", slot), {"mismatch": True, "ok": True})
        status = self.h.status()
        self.assertEqual((status["state"]["state"], status["state"]["reason"]),
                         ("failed", "integrity_mismatch"))
        self.assertEqual(helper.review_state(status), "failed")
        self.assertTrue(self.h.run("retire", slot, self._plan())["ok"])


if __name__ == "__main__":
    unittest.main()

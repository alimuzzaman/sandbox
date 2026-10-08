"""Spec 058 ``ServerCaptureService``: state model, start gates, status, retention
and retire (T005, T016, T026, T046, T050) plus the secret-leak sweep (T052)."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import tempfile
import unittest

from sandbox.recovery.capture import StagingCaptureCoordinator
from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.server_capture import (
    PHASES, ServerCaptureService, derive_state, phase_index, retention_days, retention_view,
    review_state, slot_for,
)
from tests.server_capture_support import (
    HELPER, SECRET, FakeTransport, HelperHarness, TaggedFileCrypto, fake_facts,
    local_transport,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(ROOT / "config" / "recovery-profiles.json")
PROFILES = ["amarsonar-bangla-prod"]
DAY = 86400.0
ENV = {"SANDBOX_RECOVERY_DB_PASSWORD": SECRET}


def service(transport, *, environment=None, config=None, now=1_000.0 + DAY, state_root=None,
            drive=None, capture=None):
    return ServerCaptureService(CATALOG, transport,
                                environment=ENV if environment is None else environment,
                                config=config or {}, clock=lambda: now,
                                state_root=state_root or tempfile.mkdtemp(),
                                drive=drive, capture=capture)


class TestStateModel(unittest.TestCase):
    """T005: slot, phases, derived state and retention policy."""

    def test_slot_is_stable_and_distinct_per_remote_and_backup_id(self):
        self.assertEqual(slot_for("r", "a"), slot_for("r", "a"))
        self.assertRegex(slot_for("r", "a"), r"^capture-[0-9a-f]{64}$")
        self.assertNotEqual(slot_for("r", "a"), slot_for("r", "b"))
        self.assertNotEqual(slot_for("r", "a"), slot_for("s", "a"))

    def test_phase_order(self):
        self.assertEqual(PHASES, ("preflight", "inventory", "dump", "files", "verify",
                                  "archive", "receipt"))
        self.assertEqual([phase_index(p) for p in PHASES], list(range(7)))
        self.assertEqual(phase_index(None), -1)

    def test_derive_state_incomplete_rules(self):
        cases = [
            ({"state": None, "lock_free": False}, "queued"),
            ({"state": None, "lock_free": True}, "incomplete"),
            ({"state": {"state": "queued"}, "lock_free": False}, "queued"),
            ({"state": {"state": "running"}, "lock_free": False}, "running"),
            ({"state": {"state": "running"}, "lock_free": True}, "incomplete"),
            ({"state": {"state": "queued"}, "lock_free": True}, "incomplete"),
            ({"state": {"state": "complete"}, "receipt_valid": True}, "complete"),
            ({"state": {"state": "complete"}, "receipt_valid": False}, "incomplete"),
            ({"state": {"state": "failed"}, "lock_free": True}, "failed"),
            ({"state": {"state": "retired"}, "lock_free": True}, "retired"),
            ({"state": {"state": "bogus"}, "lock_free": True}, "incomplete"),
            ({"state": "running", "lock_free": False}, "running"),
        ]
        for facts, expected in cases:
            with self.subTest(facts=facts):
                self.assertEqual(derive_state(facts), expected)
        self.assertEqual(review_state({"state": {"state": "complete"}, "receipt_valid": True,
                                       "promoted": {"set_id": "x"}}), "promoted")
        self.assertEqual(review_state({"state": {"state": "failed"}, "promoted": None}), "failed")

    def test_retention_policy_resolution(self):
        self.assertEqual(retention_days({}, "r"), 7)
        self.assertEqual(retention_days(None, None), 7)
        config = {"recovery": {"server_capture_retention_days": 3,
                               "remotes": {"r": {"server_capture_retention_days": 30}}}}
        self.assertEqual(retention_days(config, "r"), 30)
        self.assertEqual(retention_days(config, "other"), 3)
        for bad in (0, 366, -1, "7", True, 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(RecoveryError) as caught:
                    retention_days({"recovery": {"server_capture_retention_days": bad}}, "r")
                self.assertEqual(caught.exception.code, "invalid_retention_policy")
        self.assertEqual(retention_days({"recovery": {"server_capture_retention_days": 1}}, "r"), 1)
        self.assertEqual(retention_days({"recovery": {"server_capture_retention_days": 365}}, "r"),
                         365)

    def test_retention_view_only_flags_complete_unpromoted(self):
        now = 100 * DAY
        old = now - 8 * DAY
        self.assertTrue(retention_view("complete", False, old, 7, now)["retention_exceeded"])
        self.assertFalse(retention_view("complete", False, now - 6 * DAY, 7, now)["retention_exceeded"])
        self.assertFalse(retention_view("complete", True, old, 7, now)["retention_exceeded"])
        for state in ("failed", "incomplete", "running", "retired"):
            self.assertFalse(retention_view(state, False, old, 7, now)["retention_exceeded"])
        self.assertIsNone(retention_view("complete", False, None, 7, now)["age_seconds"])


class TestStartGates(unittest.TestCase):
    """T016: gate order and codes; no helper start on any refusal."""

    def assertRefused(self, outcome, code, transport):
        self.assertFalse(outcome["ok"], outcome)
        self.assertEqual(outcome["error"]["code"], code)
        self.assertNotIn("start", transport.calls)

    def test_gates_in_contract_order(self):
        cases = [
            (dict(remote="r", backup_id="set-a", profiles=PROFILES, confirm=False), {},
             "confirmation_required"),
            (dict(remote=None, backup_id="set-a", profiles=PROFILES, confirm=True), {},
             "missing_remote"),
            (dict(remote="r", backup_id=None, profiles=PROFILES, confirm=True), {},
             "missing_backup_id"),
            (dict(remote="r", backup_id="../x", profiles=PROFILES, confirm=True), {},
             "invalid_set_id"),
            (dict(remote="r", backup_id="set-a", profiles=[], confirm=True), {},
             "missing_profiles"),
            (dict(remote="r", backup_id="set-a", profiles=["control-plane"], confirm=True), {},
             "unsupported_materialization"),
            (dict(remote="r", backup_id="set-a", profiles=["lenzora-prod"], confirm=True), {},
             "unsupported_materialization"),
        ]
        for kwargs, env, code in cases:
            with self.subTest(code=code, kwargs=kwargs):
                transport = FakeTransport()
                outcome = service(transport).start(
                    kwargs["remote"], kwargs["backup_id"], kwargs["profiles"],
                    confirm=kwargs["confirm"])
                self.assertRefused(outcome, code, transport)
                self.assertEqual(transport.calls, [])

    def test_missing_credential_is_refused_before_any_remote_contact(self):
        transport = FakeTransport()
        outcome = service(transport, environment={}).start("r", "set-a", PROFILES, confirm=True)
        self.assertRefused(outcome, "missing_database_credential", transport)
        self.assertEqual(transport.calls, [])

    def test_remote_errors_map_without_start(self):
        for code in ("remote_unavailable", "remote_runtime_stale"):
            with self.subTest(code=code):
                transport = FakeTransport()
                transport.errors["observe"] = RecoveryError("x", code)
                outcome = service(transport).start("r", "set-a", PROFILES, confirm=True)
                self.assertRefused(outcome, code, transport)
        transport = FakeTransport()
        transport.errors["list"] = RecoveryError("x", "remote_unavailable")
        self.assertRefused(service(transport).start("r", "set-a", PROFILES, confirm=True),
                           "remote_unavailable", transport)

    def test_retention_guard_blocks_new_ids_but_not_a_replay(self):
        transport = FakeTransport()
        transport.add(fake_facts("old-set", remote="r", completed_at=1_000.0))
        outcome = service(transport, now=1_000.0 + 8 * DAY).start("r", "set-a", PROFILES,
                                                                  confirm=True)
        self.assertRefused(outcome, "retention_exceeded", transport)
        self.assertEqual(outcome["data"]["blocking"], ["old-set"])
        replay = service(transport, now=1_000.0 + 8 * DAY).start("r", "old-set", PROFILES,
                                                                 confirm=True)
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["data"]["existing"])
        self.assertEqual(replay["status"], "complete")

    def test_non_blocking_captures_never_block_start(self):
        transport = FakeTransport()
        transport.add(fake_facts("promoted", remote="r", promoted={"set_id": "promoted"}))
        transport.add(fake_facts("failed", remote="r", state="failed", reason="dump_failed",
                                 receipt_valid=False))
        transport.add(fake_facts("residue", remote="r", state="running", lock_free=True,
                                 receipt_valid=False, residue_bytes=99))
        transport.legacy.append({"name": "old.tar", "size": 5})
        outcome = service(transport, now=1_000.0 + 30 * DAY).start("r", "set-a", PROFILES,
                                                                   confirm=True)
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(outcome["status"], "queued")
        self.assertFalse(outcome["data"]["existing"])

    def test_start_without_passphrase_or_destination(self):
        transport = FakeTransport()
        outcome = service(transport, environment=dict(ENV)).start("r", "set-a", PROFILES,
                                                                  confirm=True)
        self.assertTrue(outcome["ok"], outcome)
        _remote, slot, request, password, declarations = transport.started[0]
        self.assertEqual(slot, slot_for("r", "set-a"))
        self.assertEqual(password, SECRET)
        self.assertEqual(request["backup_id"], "set-a")
        self.assertEqual(sorted(request["profiles"]), ["amarsonar-bangla-prod", "control-plane"])
        self.assertTrue(declarations.endswith(b"\n"))
        self.assertNotIn(SECRET, json.dumps(outcome))

    def test_invalid_retention_config_refuses_start(self):
        transport = FakeTransport()
        outcome = service(transport, config={"recovery": {"server_capture_retention_days": 0}}
                          ).start("r", "set-a", PROFILES, confirm=True)
        self.assertRefused(outcome, "invalid_retention_policy", transport)


class TestStatus(unittest.TestCase):
    """T026: derived status view."""

    def status(self, facts, **kwargs):
        transport = FakeTransport()
        transport.add(facts)
        return service(transport, **kwargs).status("fixture-remote", "set-a"), transport

    def test_killed_job_and_missing_receipt_are_incomplete(self):
        outcome, _ = self.status(fake_facts(state="running", phase="dump", lock_free=True,
                                            receipt_valid=False))
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["status"], "incomplete")
        self.assertEqual(outcome["data"]["phase"], "dump")
        outcome, _ = self.status(fake_facts(state="complete", receipt_valid=False))
        self.assertEqual(outcome["status"], "incomplete")
        self.assertIsNone(outcome["data"]["archive"])

    def test_queued_window_reports_acceptance_time(self):
        outcome, _ = self.status(fake_facts(state="queued", phase=None, lock_free=False,
                                            receipt_valid=False, accepted_at=123.0))
        self.assertEqual(outcome["status"], "queued")
        self.assertEqual(outcome["data"]["accepted_at"], 123.0)

    def test_unknown_id_and_unavailable_remote(self):
        transport = FakeTransport()
        outcome = service(transport).status("fixture-remote", "nope")
        self.assertEqual(outcome["error"]["code"], "capture_not_found")
        transport.errors["status"] = RecoveryError("x", "remote_unavailable")
        outcome = service(transport).status("fixture-remote", "nope")
        self.assertEqual(outcome["error"]["code"], "remote_unavailable")

    def test_phase_never_regresses_across_polls(self):
        transport = FakeTransport()
        facts = transport.add(fake_facts(state="running", phase=None, lock_free=False,
                                         receipt_valid=False))
        sequence = []
        svc = service(transport)
        for phase in (None, "preflight", "inventory", "inventory", "dump", "files", "verify",
                      "archive", "receipt"):
            facts["state"]["phase"] = phase
            sequence.append(phase_index(svc.status("fixture-remote", "set-a")["data"]["phase"]))
        self.assertEqual(sequence, sorted(sequence))

    def test_terminal_status_is_stable_and_complete_shows_receipt(self):
        transport = FakeTransport()
        transport.add(fake_facts())
        svc = service(transport)
        outcomes = [svc.status("fixture-remote", "set-a") for _ in range(3)]
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[1], outcomes[2])
        data = outcomes[0]["data"]
        self.assertEqual(outcomes[0]["status"], "complete")
        self.assertEqual(data["archive"]["size"], len(b"archive-bytes"))
        self.assertEqual([m["name"] for m in data["members"]], ["database.sql", "wordpress.tar"])
        self.assertEqual(data["inventory_summary"]["table_count"], 3)
        self.assertEqual(data["retention_days"], 7)
        self.assertIsNone(data["local_transfer_bytes"])

    def test_status_does_not_probe_runtime_revision(self):
        transport = FakeTransport()
        transport.add(fake_facts())
        transport.errors["observe"] = RecoveryError("x", "remote_runtime_stale")
        outcome = service(transport).status("fixture-remote", "set-a")
        self.assertTrue(outcome["ok"])
        self.assertNotIn("observe", transport.calls)

    def test_status_flags_retention_exceeded(self):
        outcome, _ = self.status(fake_facts(completed_at=0.0), now=8 * DAY)
        self.assertTrue(outcome["data"]["retention_exceeded"])
        outcome, _ = self.status(fake_facts(completed_at=0.0, promoted={"set_id": "set-a"}),
                                 now=8 * DAY)
        self.assertFalse(outcome["data"]["retention_exceeded"])
        self.assertTrue(outcome["data"]["promoted"])
        self.assertEqual(outcome["data"]["promoted_set_id"], "set-a")


class TestRetention(unittest.TestCase):
    """T046 and T050: retention listing, retire refusals and the retire path."""

    def transport(self):
        transport = FakeTransport()
        transport.add(fake_facts("complete", remote="r", completed_at=0.0))
        transport.add(fake_facts("promoted", remote="r", completed_at=0.0,
                                 promoted={"set_id": "promoted"}))
        transport.add(fake_facts("failed", remote="r", state="failed", receipt_valid=False))
        transport.add(fake_facts("residue", remote="r", state="running", lock_free=True,
                                 receipt_valid=False, residue_bytes=50))
        transport.add(fake_facts("running", remote="r", state="running", lock_free=False,
                                 receipt_valid=False))
        transport.legacy.append({"name": "legacy.tar", "size": 7})
        return transport

    def test_list_view_marks_retention_and_retirable(self):
        transport = self.transport()
        data = service(transport, now=8 * DAY).captures("r")
        by_id = {item["backup_id"]: item for item in data["server_captures"]}
        self.assertTrue(by_id["complete"]["retention_exceeded"])
        self.assertFalse(by_id["complete"]["retirable"])
        self.assertFalse(by_id["promoted"]["retention_exceeded"])
        self.assertTrue(by_id["promoted"]["retirable"])
        self.assertTrue(by_id["failed"]["retirable"])
        self.assertEqual(by_id["residue"]["state"], "incomplete")
        self.assertTrue(by_id["residue"]["retirable"])
        self.assertEqual(by_id["residue"]["residue_bytes"], 50)
        self.assertFalse(by_id["running"]["retirable"])
        self.assertEqual(data["legacy_server_archives"], [{"name": "legacy.tar", "size": 7}])
        self.assertEqual(data["retention_days"], 7)
        plan = service(transport, now=8 * DAY).retention("r")
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["data"]["requires_confirmation"])
        self.assertNotIn("retire", transport.calls)

    def test_retire_refusals(self):
        transport = self.transport()
        svc = service(transport, now=8 * DAY)
        outcome = svc.retire("r", "promoted", confirm=False)
        self.assertEqual(outcome["error"]["code"], "confirmation_required")
        outcome = svc.retire("r", None, confirm=True)
        self.assertEqual(outcome["error"]["code"], "confirmation_required")
        for backup_id, state in (("complete", "complete"), ("running", "running")):
            with self.subTest(backup_id=backup_id):
                outcome = svc.retire("r", backup_id, confirm=True)
                self.assertEqual(outcome["error"]["code"], "not_retirable")
                self.assertEqual(outcome["data"]["state"], state)
        # Legacy archives are not addressable as captures: by name or by a look-alike id.
        outcome = svc.retire("r", "legacy.tar", confirm=True)
        self.assertEqual(outcome["error"]["code"], "invalid_set_id")
        outcome = svc.retire("r", "legacy", confirm=True)
        self.assertEqual(outcome["error"]["code"], "capture_not_found")
        transport.errors["retire"] = RecoveryError("x", "retire_candidate_changed")
        outcome = svc.retire("r", "failed", confirm=True)
        self.assertEqual(outcome["error"]["code"], "retire_candidate_changed")
        self.assertEqual(transport.retired, [])

    def test_retire_path_sends_the_reviewed_plan(self):
        for backup_id in ("promoted", "failed", "residue"):
            with self.subTest(backup_id=backup_id):
                transport = self.transport()
                outcome = service(transport, now=8 * DAY).retire("r", backup_id, confirm=True)
                self.assertTrue(outcome["ok"], outcome)
                self.assertEqual(outcome["status"], "retired")
                self.assertEqual(outcome["data"]["removed_bytes"], 13)
                facts = transport.slots[slot_for("r", backup_id)]
                self.assertEqual(transport.retired, [{
                    "state": review_state(facts),
                    "archive_sha256": (facts["receipt"] or {}).get("archive_sha256"),
                    "archive_size": facts["archive_size"]}])

    def test_only_retire_and_job_cleanup_delete_server_files(self):
        tree = ast.parse(HELPER.read_text())
        deleting = set()
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                name = ast.unparse(call.func)
                if name in ("os.unlink", "os.remove", "shutil.rmtree", "os.rmdir", "remove_path"):
                    deleting.add(function.name)
        self.assertEqual(deleting, {"write_private", "remove_path", "op_retire", "run_job"})
        service_source = (ROOT / "sandbox" / "recovery" / "server_capture.py").read_text()
        transport_methods = {node.attr for node in ast.walk(ast.parse(service_source))
                             if isinstance(node, ast.Attribute)
                             and isinstance(node.value, ast.Attribute)
                             and node.value.attr == "transport"}
        self.assertEqual(transport_methods & {"retire"}, {"retire"})
        self.assertFalse({"delete", "remove", "cleanup", "prune"} & transport_methods)


class TestSecretLeak(unittest.TestCase):
    """T052: the DB credential and passphrase never surface anywhere."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.base = Path(self._directory.name)
        self.h = HelperHarness(self.base / "server")

    def tearDown(self):
        self._directory.cleanup()

    def test_capture_status_list_and_promote_never_leak(self):
        passphrase = "sentinel-passphrase-91c2"
        transport, ssh = local_transport(self.h)
        drive = MemoryDrive()
        materialized = self.base / "materialized"
        materialized.mkdir(mode=0o700)
        capture = StagingCaptureCoordinator(
            TaggedFileCrypto(passphrase), drive, staging_root=self.base / "staging",
            pending_root=self.base / "pending", materialization_root=materialized)
        svc = ServerCaptureService(
            CATALOG, transport,
            environment={"SANDBOX_RECOVERY_DB_PASSWORD": SECRET, "RECOVERY_PASSPHRASE": passphrase},
            config={}, state_root=self.base / "operator", drive=drive, capture=capture)
        envelopes = [svc.start("fixture-remote", "set-a", PROFILES, confirm=True)]
        self.assertTrue(envelopes[0]["ok"], envelopes[0])
        self.h.wait()
        envelopes.append(svc.status("fixture-remote", "set-a"))
        self.assertEqual(envelopes[-1]["status"], "complete", envelopes[-1])
        envelopes.append(svc.retention("fixture-remote"))
        envelopes.append(svc.promote("fixture-remote", "set-a", confirm=True))
        self.assertTrue(envelopes[-1]["ok"], envelopes[-1])
        envelopes.append(svc.status("fixture-remote", "set-a"))
        self.assertTrue(envelopes[-1]["data"]["promoted"])
        for secret in (SECRET, passphrase):
            with self.subTest(secret=secret):
                for command, _stdin in ssh.calls:
                    self.assertNotIn(secret, command)
                self.assertNotIn(secret.encode(), self.h.all_bytes())
                self.assertNotIn(secret, json.dumps(envelopes))
                for key, value in drive.objects.items():
                    self.assertNotIn(secret.encode(), value, key)
                for path in self.base.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        self.assertNotIn(secret.encode(), path.read_bytes(), str(path))
                for call in self.h.docker_calls():
                    self.assertNotIn(secret, json.dumps(call.get("argv", call)))
        # Only the start call carries the credential, and only on stdin.
        carrying = [op for (command, stdin), op in zip(ssh.calls, ssh.ops)
                    if SECRET.encode() in stdin]
        self.assertEqual(carrying, ["start"])
        self.assertFalse((self.base / "operator" / "promote" / slot_for("fixture-remote", "set-a"))
                         .exists())
        self.assertTrue((self.h.slot_path() / "archive.tar").exists())


if __name__ == "__main__":
    unittest.main()

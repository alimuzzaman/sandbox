"""Readiness rows, the bounded check and its stored proof (spec 063 US2, T016)."""
import json
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.readiness import check, rows  # noqa: E402
from tests.test_remote_runtime_refusal import parse_with_cli  # noqa: E402

REVISION = "a" * 24
REMOTE = {"name": "vps", "provisioned": True, "capabilities": ["job.exec"],
          "mcp_service": {"runtime_revision": REVISION}}


def _target(kind="remote", name="vps", selection="explicit"):
    return SimpleNamespace(
        kind=kind, remote_name=name if kind == "remote" else None,
        project_root="/work/project", remote=REMOTE if kind == "remote" else None,
        sources={"identity": "project-identity", "remote_selection": selection})


def _ok(_remote, _command, timeout=None):
    return subprocess.CompletedProcess([], 0, "", "")


def _probes(home, **overrides):
    values = dict(
        resolve=lambda _project, _remote: _target(),
        remote_lookup=lambda _name: REMOTE,
        ssh_run=_ok,
        service_status=lambda _remote: {
            "installed_runtime_revision": REVISION,
            "compatibility": {"ok": True, "state": "compatible"}},
        capacity_decision=lambda _remote, *, remote_name: {"ok": True},
        sb_path=lambda _remote: "/home/u/sandbox/sb",
        propose=lambda _remote: {"proposed": "10.80.0.0/16", "subnet_prefix": 24,
                                 "assign_command": "./sb remote network-range assign vps "
                                                   "--cidr 10.80.0.0/16 --confirm"},
        clock=lambda: 1000.0,
        home=Path(home),
    )
    values.update(overrides)
    return check.Probes(**values)


def _rows(result):
    return {item["aspect"]: item for item in result["rows"]}


class RowStateTests(unittest.TestCase):
    def test_reachability_states(self):
        self.assertEqual(rows.reachability(_ok, REMOTE, "vps")["state"], "ready")
        refused = rows.reachability(
            lambda *_a, **_k: subprocess.CompletedProcess([], 255, "", ""), REMOTE, "vps")
        self.assertEqual((refused["state"], refused["reason"]), ("not_ready", "ssh_refused"))

        def slow(*_a, **_k):
            raise subprocess.TimeoutExpired("ssh", 10)
        timed = rows.reachability(slow, REMOTE, "vps")
        self.assertEqual((timed["state"], timed["probe_state"]), ("unknown", "timeout"))

    def test_runtime_compatibility_states(self):
        ready, revision = rows.runtime_compatibility(
            lambda _r: {"installed_runtime_revision": REVISION,
                        "compatibility": {"ok": True, "state": "compatible"}}, REMOTE, "vps")
        self.assertEqual((ready["state"], revision), ("ready", REVISION))
        refused, _ = rows.runtime_compatibility(
            lambda _r: {"runtime_revision_state": "mismatch"}, REMOTE, "vps")
        self.assertEqual(refused["state"], "not_ready")
        self.assertEqual(refused["remedy"], "./sb remote service migrate vps --confirm")
        unknown, _ = rows.runtime_compatibility(lambda _r: None, REMOTE, "vps")
        self.assertEqual((unknown["state"], unknown["probe_state"]), ("unknown", "unavailable"))

    def test_capacity_states(self):
        self.assertEqual(rows.capacity(
            lambda _r, *, remote_name: {"ok": True}, REMOTE, "vps")["state"], "ready")
        missing = rows.capacity(lambda _r, *, remote_name: {
            "ok": False, "evidence": {"reason": "missing_pool_evidence"}}, REMOTE, "vps")
        self.assertEqual((missing["state"], missing["reason"]),
                         ("not_ready", "missing_pool_evidence"))
        exhausted = rows.capacity(lambda _r, *, remote_name: {
            "ok": False, "code": "docker_network_subnet_exhausted"}, REMOTE, "vps")
        self.assertEqual(exhausted["reason"], "subnet_exhausted")
        partial = rows.capacity(lambda _r, *, remote_name: {
            "ok": False, "evidence": {"status": "partial"}}, REMOTE, "vps")
        self.assertEqual((partial["state"], partial["probe_state"]), ("unknown", "partial"))

    def test_ownership_repair_states(self):
        self.assertEqual(rows.ownership_repair(_ok, REMOTE, "vps", "/x/sb")["state"], "ready")
        missing = rows.ownership_repair(
            lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", ""), REMOTE, "vps", "/x/sb")
        self.assertEqual((missing["state"], missing["reason"]),
                         ("not_ready", "repair_helper_missing"))

    def test_handoff_needs_a_record_at_the_installed_revision(self):
        self.assertEqual(rows.handoff({"installed_runtime_revision": REVISION}, REVISION,
                                      "vps", "/p")["state"], "ready")
        stale = rows.handoff({"installed_runtime_revision": "b" * 24}, REVISION, "vps", "/p")
        self.assertEqual((stale["state"], stale["probe_state"]), ("unknown", "unrecorded"))

    def test_unsafe_reason_is_replaced(self):
        self.assertEqual(rows.row("capacity", "not_ready", reason="Bad Reason; rm")["reason"],
                         "unexpected")

    def test_every_remedy_parses_against_the_cli(self):
        remedies = {
            rows.registration("remote_not_provisioned", "vps")["remedy"],
            rows.registration("unknown_remote", "vps")["remedy"],
            rows.reachability(lambda *_a, **_k: subprocess.CompletedProcess([], 255, "", ""),
                              REMOTE, "vps")["remedy"],
            rows.runtime_compatibility(lambda _r: {"runtime_revision_state": "mismatch"},
                                       REMOTE, "vps")[0]["remedy"],
            rows.runtime_compatibility(lambda _r: None, REMOTE, "vps")[0]["remedy"],
            rows.capacity(lambda _r, *, remote_name: {
                "ok": False, "evidence": {"reason": "missing_pool_evidence"}},
                REMOTE, "vps")["remedy"],
            rows.capacity(lambda _r, *, remote_name: {
                "ok": False, "code": "docker_network_subnet_exhausted"}, REMOTE, "vps")["remedy"],
            rows.capacity(lambda _r, *, remote_name: None, REMOTE, "vps")["remedy"],
            rows.ownership_repair(lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", ""),
                                  REMOTE, "vps", "/x/sb")["remedy"],
            rows.handoff(None, REVISION, "vps", "/work/my project")["remedy"],
            "./sb remote readiness vps",
        }
        for remedy in sorted(remedies):
            with self.subTest(remedy=remedy):
                parse_with_cli(remedy)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_all_ready_except_unrecorded_handoff_and_proof_is_private(self):
        result = check.run("/work/project", "vps", probes=_probes(self.home))
        found = _rows(result)
        self.assertEqual(list(found), list(rows.ASPECTS))
        for aspect in rows.ASPECTS[:5]:
            self.assertEqual(found[aspect]["state"], "ready", aspect)
        self.assertEqual(found["handoff"]["state"], "unknown")
        self.assertEqual(result["remote_selection"], "explicit")
        self.assertEqual(result["installed_runtime_revision"], REVISION)
        self.assertEqual(result["reusable_until"], 1000 + check.REUSE_SECONDS)
        proof = check.readiness_dir(Path(self.home), "vps") / \
            f"{check.project_key('project-identity')}.json"
        self.assertEqual(stat.S_IMODE(proof.stat().st_mode), 0o600)
        self.assertEqual(json.loads(proof.read_text())["project"], "project-identity")

    def test_local_target_is_not_applicable_everywhere(self):
        result = check.run("/work/project", None, probes=_probes(
            self.home, resolve=lambda _p, _r: _target(kind="local", selection="local")))
        self.assertEqual({item["state"] for item in result["rows"]}, {"not_applicable"})
        self.assertEqual(result["remote_selection"], "local")

    def test_unregistered_remote_refuses_only_registration(self):
        class Refusal(Exception):
            code = "unknown_remote"

        def resolve(_project, _remote):
            raise Refusal("not registered")
        result = check.run("/work/project", "ghost", probes=_probes(self.home, resolve=resolve))
        found = _rows(result)
        self.assertEqual((found["registration"]["state"], found["registration"]["reason"]),
                         ("not_ready", "unknown_remote"))
        self.assertTrue(all(item["state"] == "not_applicable"
                            for item in result["rows"][1:]))

    def test_deadline_turns_unfinished_rows_unknown(self):
        release = threading.Event()

        def hang(_remote, *, remote_name):
            release.wait(5)
            return {"ok": True}
        try:
            with patch.object(check, "DEADLINE_SECONDS", 0.2):
                result = check.run("/work/project", "vps",
                                   probes=_probes(self.home, capacity_decision=hang))
        finally:
            release.set()
        found = _rows(result)
        self.assertEqual((found["capacity"]["state"], found["capacity"]["probe_state"]),
                         ("unknown", "timeout"))
        self.assertEqual(found["reachability"]["state"], "ready")

    def test_reachable_remote_completes_well_inside_thirty_seconds(self):
        import time
        started = time.monotonic()
        check.run("/work/project", "vps", probes=_probes(self.home))
        self.assertLess(time.monotonic() - started, 30)

    def test_proposed_range_only_when_pool_evidence_is_missing(self):
        missing = lambda _r, *, remote_name: {  # noqa: E731
            "ok": False, "evidence": {"reason": "missing_pool_evidence"}}
        result = check.run("/work/project", "vps",
                           probes=_probes(self.home, capacity_decision=missing))
        self.assertEqual(result["proposed_range"]["cidr"], "10.80.0.0/16")
        parse_with_cli(result["proposed_range"]["assign_command"])
        self.assertNotIn("proposed_range",
                         check.run("/work/project", "vps", probes=_probes(self.home)))

        def incomplete(_remote):
            raise RuntimeError("inventory incomplete")
        result = check.run("/work/project", "vps", probes=_probes(
            self.home, capacity_decision=missing, propose=incomplete))
        self.assertNotIn("proposed_range", result)

    def test_profile_selection_is_reported(self):
        result = check.run("/work/project", None, probes=_probes(
            self.home, resolve=lambda _p, _r: _target(selection="profile")))
        self.assertEqual((result["remote"], result["remote_selection"]), ("vps", "profile"))

    def test_handoff_ready_after_ensure_then_exec(self):
        probes = _probes(self.home)
        proof = check.run("/work/project", "vps", probes=probes, full=True)
        self.assertFalse(check.record_exec("vps", "project-identity", home=Path(self.home),
                                           proof=proof))
        check.record_ensure("vps", "project-identity", home=Path(self.home), proof=proof)
        self.assertTrue(check.record_exec("vps", "project-identity", home=Path(self.home),
                                          proof=proof))
        found = _rows(check.run("/work/project", "vps", probes=probes))
        self.assertEqual(found["handoff"]["state"], "ready")

    def test_invalid_remote_name_never_builds_a_path(self):
        with self.assertRaises(ValueError):
            check.readiness_dir(Path(self.home), "../escape")
        self.assertEqual(check.invalidate("../escape", Path(self.home)), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from sandbox.hosting.zone_coverage import (
    COVERAGE_DEPENDENTS_STALE,
    COVERAGE_DOCUMENT_CORRUPT,
    COVERAGE_GENERATION_STALE,
    COVERAGE_KIND_MISMATCH,
    COVERAGE_OPERATION_ACTIVE,
    COVERAGE_STORE_UNAVAILABLE,
    CoverageError,
)
from sandbox.hosting.zone_coverage import program
from sandbox.hosting.zone_coverage.client import ZoneCoverageClient


def _run_program(home: str, request: dict) -> tuple[int, dict | str]:
    payload = base64.b64encode(json.dumps(request).encode()).decode()
    proc = subprocess.run(
        [sys.executable, "-c", program.PROGRAM, home, payload],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return proc.returncode, proc.stderr or proc.stdout
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        return proc.returncode, data
    except Exception:
        return proc.returncode, proc.stdout


class TestZoneCoverageProgram(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.home = self.tmp_dir.name

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_f3_read_and_list_create_nothing_and_home_expansion(self):
        rc, out = _run_program(self.home, {"action": "read", "zone": "asb.bd"})
        self.assertEqual(rc, 0)
        self.assertEqual(out["state"], "absent")
        self.assertIsNone(out["document"])
        self.assertEqual(os.listdir(self.home), [])

        with tempfile.TemporaryDirectory() as fresh_home:
            rc, out = _run_program(fresh_home, {"action": "list"})
            self.assertEqual(rc, 0)
            self.assertEqual(out["zones"], [])
            self.assertEqual(os.listdir(fresh_home), [])

        cmd = program.remote_command({"action": "list"})
        import shlex
        test_cmd = cmd.replace("python3 -c " + shlex.quote(program.PROGRAM), "python3 -c 'import sys; print(sys.argv[1])'")
        env = {"HOME": "/tmp/fake-home-test", "PATH": os.environ.get("PATH", "/bin:/usr/bin")}
        proc = subprocess.run(["sh", "-c", test_cmd], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "/tmp/fake-home-test/sandbox")

    def test_f5_schema_and_structure_validation_and_byte_identical(self):
        zones_dir = os.path.join(self.home, "runtime", "zone-coverage", "zones")
        os.makedirs(zones_dir, exist_ok=True)
        doc_path = os.path.join(zones_dir, "asb.bd.json")

        # 1. Unsupported schema
        doc = {
            "schema_version": 2,
            "zone": "asb.bd",
            "state": "active",
            "generation": 0,
            "dependents_revision": 0,
            "dependents": {},
            "history": [],
        }
        raw_bytes = json.dumps(doc).encode("utf-8")
        with open(doc_path, "wb") as f:
            f.write(raw_bytes)

        rc, out = _run_program(self.home, {"action": "read", "zone": "asb.bd"})
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), "coverage_schema_unsupported")
        with open(doc_path, "rb") as f:
            self.assertEqual(f.read(), raw_bytes)

        # 2. Corrupt document: missing / wrong fields
        corrupt_cases = [
            {"schema_version": 1, "zone": "other.zone", "state": "active", "generation": 0, "dependents_revision": 0, "dependents": {}, "history": []},
            {"schema_version": 1, "zone": "asb.bd", "state": "bogus_state", "generation": 0, "dependents_revision": 0, "dependents": {}, "history": []},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "generation": "zero", "dependents_revision": 0, "dependents": {}, "history": []},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "generation": True, "dependents_revision": 0, "dependents": {}, "history": []},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "generation": 0, "dependents_revision": "zero", "dependents": {}, "history": []},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "generation": 0, "dependents_revision": 0, "dependents": "not-a-dict", "history": []},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "generation": 0, "dependents_revision": 0, "dependents": {}, "history": "not-a-list"},
            {"not_even_schema": 123},
        ]

        mutating_actions = [
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "ctrl"},
            {"action": "phase", "zone": "asb.bd", "operation_id": "op-1", "phase": "step"},
            {"action": "finish", "zone": "asb.bd", "operation_id": "op-1", "state": "active"},
            {"action": "dependent-bind", "zone": "asb.bd", "state_key": "a/b/c", "hostname": "x.asb.bd", "state": "bound"},
            {"action": "dependent-release", "zone": "asb.bd", "state_key": "a/b/c"},
        ]

        for bad_doc in corrupt_cases:
            bad_bytes = json.dumps(bad_doc).encode("utf-8")
            with open(doc_path, "wb") as f:
                f.write(bad_bytes)

            # read refuses coverage_document_corrupt
            rc, out = _run_program(self.home, {"action": "read", "zone": "asb.bd"})
            self.assertEqual(rc, 0)
            self.assertEqual(out.get("refused"), "coverage_document_corrupt")
            with open(doc_path, "rb") as f:
                self.assertEqual(f.read(), bad_bytes)

        # Test each mutating action leaves corrupt document byte-identical and refuses
        corrupt_bytes = b"{\"schema_version\": 1, \"zone\": \"wrong.zone\"}"
        for act in mutating_actions:
            with open(doc_path, "wb") as f:
                f.write(corrupt_bytes)
            rc, out = _run_program(self.home, act)
            self.assertEqual(rc, 0)
            self.assertEqual(out.get("refused"), "coverage_document_corrupt")
            with open(doc_path, "rb") as f:
                self.assertEqual(f.read(), corrupt_bytes)

    def test_read_absent_and_list(self):
        rc, out = _run_program(self.home, {"action": "read", "zone": "asb.bd"})
        self.assertEqual(rc, 0)
        self.assertEqual(out["state"], "absent")
        self.assertIsNone(out["document"])

        rc, out = _run_program(self.home, {"action": "list"})
        self.assertEqual(rc, 0)
        self.assertEqual(out["zones"], [])

    def test_begin_and_stale_generation(self):
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl1", "expected_generation": 5},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_GENERATION_STALE)

        # Successful begin
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl1", "expected_generation": 0},
        )
        self.assertEqual(rc, 0)
        self.assertNotIn("refused", out)
        op_id = out["operation_id"]
        doc = out["document"]
        self.assertEqual(doc["state"], "enabling")
        self.assertEqual(doc["generation"], 0)
        self.assertEqual(doc["operation"]["id"], op_id)

        # Check file mode 0600
        zone_file = os.path.join(self.home, "runtime", "zone-coverage", "zones", "asb.bd.json")
        self.assertTrue(os.path.exists(zone_file))
        mode = stat.S_IMODE(os.stat(zone_file).st_mode)
        self.assertEqual(mode, 0o600)

        # Stale generation check after creation
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl2", "expected_generation": 99},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_GENERATION_STALE)

    def test_live_lease_refusal_and_resume(self):
        # Initial begin
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl1"},
        )
        self.assertEqual(rc, 0)
        op1 = out["operation_id"]

        # Another controller tries to begin while lease is live
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl2"},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_OPERATION_ACTIVE)

        # Manually expire the lease in the file
        zone_file = os.path.join(self.home, "runtime", "zone-coverage", "zones", "asb.bd.json")
        with open(zone_file, "r") as f:
            data = json.load(f)
        data["operation"]["lease_expires_at"] = int(time.time()) - 10
        with open(zone_file, "w") as f:
            json.dump(data, f)

        # Without resume: true, still refused
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl2", "resume": False},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_OPERATION_ACTIVE)

        # Different kind with resume: true is refused (coverage_kind_mismatch)
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "disable", "controller": "h-ctrl2", "resume": True},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_KIND_MISMATCH)

        # Same kind with resume: true succeeds and gives new op id
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl2", "resume": True},
        )
        self.assertEqual(rc, 0)
        self.assertNotIn("refused", out)
        op2 = out["operation_id"]
        self.assertNotEqual(op1, op2)

        # Old op id now refused in phase
        rc, out = _run_program(
            self.home,
            {"action": "phase", "zone": "asb.bd", "operation_id": op1, "phase": "fallback"},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_OPERATION_ACTIVE)

    def test_stale_dependents_revision(self):
        # Create doc and finish active
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl1"},
        )
        op1 = out["operation_id"]
        _run_program(
            self.home,
            {"action": "finish", "zone": "asb.bd", "operation_id": op1, "state": "active", "raise_generation": True},
        )

        # Bind a dependent to raise dependents_revision to 1
        rc, out = _run_program(
            self.home,
            {
                "action": "dependent-bind",
                "zone": "asb.bd",
                "state_key": "app/svc/prod",
                "hostname": "site.asb.bd",
                "state": "pending",
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out["dependents_revision"], 1)

        # Calling begin with expected_dependents_revision=0 is refused
        rc, out = _run_program(
            self.home,
            {
                "action": "begin",
                "zone": "asb.bd",
                "kind": "disable",
                "controller": "h-ctrl1",
                "expected_dependents_revision": 0,
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_DEPENDENTS_STALE)

    def test_phase_and_finish(self):
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl1"},
        )
        op_id = out["operation_id"]

        # Phase update
        rc, out = _run_program(
            self.home,
            {
                "action": "phase",
                "zone": "asb.bd",
                "operation_id": op_id,
                "phase": "probe",
                "updates": {"fallback": {"marker": "xyz"}},
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out["document"]["fallback"]["marker"], "xyz")

        # Finish
        rc, out = _run_program(
            self.home,
            {
                "action": "finish",
                "zone": "asb.bd",
                "operation_id": op_id,
                "state": "active",
                "raise_generation": True,
            },
        )
        self.assertEqual(rc, 0)
        doc = out["document"]
        self.assertEqual(doc["state"], "active")
        self.assertEqual(doc["generation"], 1)
        self.assertIsNone(doc["operation"])
        self.assertEqual(len(doc["history"]), 1)
        self.assertEqual(doc["history"][0]["result"], "active")

    def test_dependent_bind_and_release(self):
        # Setup active zone
        rc, out = _run_program(
            self.home,
            {"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "h-ctrl1"},
        )
        op_id = out["operation_id"]
        _run_program(
            self.home,
            {"action": "finish", "zone": "asb.bd", "operation_id": op_id, "state": "active"},
        )

        # Bind dependent
        rc, out = _run_program(
            self.home,
            {
                "action": "dependent-bind",
                "zone": "asb.bd",
                "state_key": "app/svc/prod",
                "hostname": "site.asb.bd",
                "state": "bound",
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out["dependents_revision"], 1)
        self.assertIn("app/svc/prod", out["document"]["dependents"])

        # Release dependent
        rc, out = _run_program(
            self.home,
            {"action": "dependent-release", "zone": "asb.bd", "state_key": "app/svc/prod"},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out["dependents_revision"], 2)
        self.assertNotIn("app/svc/prod", out["document"]["dependents"])

    def test_corrupt_document_refused_and_unchanged(self):
        zone_dir = os.path.join(self.home, "runtime", "zone-coverage", "zones")
        os.makedirs(zone_dir, exist_ok=True)
        zone_file = os.path.join(zone_dir, "asb.bd.json")
        with open(zone_file, "w") as f:
            f.write("{invalid_json: true}")

        rc, out = _run_program(self.home, {"action": "read", "zone": "asb.bd"})
        self.assertEqual(rc, 0)
        self.assertEqual(out.get("refused"), COVERAGE_DOCUMENT_CORRUPT)

        with open(zone_file, "r") as f:
            content = f.read()
        self.assertEqual(content, "{invalid_json: true}")

    def test_size_bounds(self):
        # Huge request > 64 KiB
        huge_payload = base64.b64encode(json.dumps({"action": "read", "zone": "asb.bd", "padding": "x" * 70000}).encode()).decode()
        proc = subprocess.run(
            [sys.executable, "-c", program.PROGRAM, self.home, huge_payload],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 3)


class TestZoneCoverageClient(unittest.TestCase):
    def test_fake_ssh_run_success(self):
        def fake_ssh_run(entry, cmd, timeout=15):
            resp = {"ok": True, "action": "read", "state": "absent", "document": None, "now": 1700000000}
            res = MagicMock()
            res.returncode = 0
            res.stdout = json.dumps(resp) + "\n"
            return res

        client = ZoneCoverageClient({"name": "test"}, ssh_run=fake_ssh_run)
        out = client.read("asb.bd")
        self.assertEqual(out["state"], "absent")

    def test_fake_ssh_run_refusal_as_data(self):
        def fake_ssh_run(entry, cmd, timeout=15):
            resp = {"ok": True, "action": "begin", "refused": COVERAGE_OPERATION_ACTIVE, "now": 1700000000}
            res = MagicMock()
            res.returncode = 0
            res.stdout = json.dumps(resp) + "\n"
            return res

        client = ZoneCoverageClient({"name": "test"}, ssh_run=fake_ssh_run)
        out = client.begin("asb.bd", "enable", "h-123")
        self.assertEqual(out.get("refused"), COVERAGE_OPERATION_ACTIVE)

    def test_fake_ssh_run_timeout(self):
        def fake_ssh_run(entry, cmd, timeout=15):
            raise TimeoutError("timeout")

        client = ZoneCoverageClient({"name": "test"}, ssh_run=fake_ssh_run)
        with self.assertRaises(CoverageError) as ctx:
            client.read("asb.bd")
        self.assertEqual(ctx.exception.code, COVERAGE_STORE_UNAVAILABLE)

    def test_fake_ssh_run_nonzero_exit(self):
        def fake_ssh_run(entry, cmd, timeout=15):
            res = MagicMock()
            res.returncode = 255
            res.stdout = ""
            return res

        client = ZoneCoverageClient({"name": "test"}, ssh_run=fake_ssh_run)
        with self.assertRaises(CoverageError) as ctx:
            client.read("asb.bd")
        self.assertEqual(ctx.exception.code, COVERAGE_STORE_UNAVAILABLE)

    def test_fake_ssh_run_bad_json(self):
        def fake_ssh_run(entry, cmd, timeout=15):
            res = MagicMock()
            res.returncode = 0
            res.stdout = "This is not JSON!\n"
            return res

        client = ZoneCoverageClient({"name": "test"}, ssh_run=fake_ssh_run)
        with self.assertRaises(CoverageError) as ctx:
            client.read("asb.bd")
        self.assertEqual(ctx.exception.code, COVERAGE_STORE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()

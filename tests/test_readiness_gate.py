"""The submission gate refuses before any transfer (spec 063 US2, T019)."""
import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.jobs.models import JobSubmission, SourceIdentity  # noqa: E402
from sandbox.readiness import check, gate  # noqa: E402
from sandbox.readiness.errors import RemoteNotReadyError  # noqa: E402
from sandbox.transports.remote_jobs import RemoteJobTransport  # noqa: E402
from tests.test_readiness import REMOTE, REVISION, _probes, _target  # noqa: E402


class _Counting:
    """Counts fresh checks: every fresh run probes reachability once."""

    def __init__(self, returncode=0):
        self.calls = 0
        self.returncode = returncode

    def __call__(self, _remote, _command, timeout=None):
        self.calls += 1
        return subprocess.CompletedProcess([], self.returncode, "", "")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def probes(self, **overrides):
        return _probes(self.home, **overrides)

    def test_first_not_ready_row_refuses_with_no_bytes_transferred(self):
        refused = lambda *_a, **_k: subprocess.CompletedProcess([], 255, "", "")  # noqa: E731
        with self.assertRaises(RemoteNotReadyError) as caught:
            gate.require_ready("/work/project", "vps", probes=self.probes(ssh_run=refused))
        error = caught.exception
        self.assertEqual(error.code, "remote_not_ready_reachability")
        self.assertEqual(error.detail["bytes_transferred"], 0)
        self.assertEqual(error.detail["remedy"], "./sb remote list")
        payload = error.to_payload()
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["side_effects"],
                         {"staging_started": False, "bytes_transferred": 0})

    def test_unknown_and_not_applicable_never_refuse(self):
        unknown = lambda _remote: None  # noqa: E731
        proof = gate.require_ready("/work/project", "vps",
                                   probes=self.probes(service_status=unknown))
        states = {item["aspect"]: item["state"] for item in proof["rows"]}
        self.assertEqual(states["runtime_compatibility"], "unknown")
        self.assertEqual(states["handoff"], "unknown")
        local = gate.require_ready("/work/project", None, probes=self.probes(
            resolve=lambda _p, _r: _target(kind="local", selection="local")))
        self.assertEqual({item["state"] for item in local["rows"]}, {"not_applicable"})

    def test_fresh_all_ready_proof_is_reused_for_the_same_project_only(self):
        counter = _Counting()
        probes = self.probes(ssh_run=counter)
        gate.require_ready("/work/project", "vps", probes=probes)
        first = counter.calls
        proof = gate.require_ready("/work/project", "vps", probes=probes)
        self.assertEqual(counter.calls, first)
        self.assertEqual(proof["remote_selection"], "explicit")

        other = _target()
        other.sources = {**other.sources, "identity": "other-project"}
        gate.require_ready("/work/other", "vps",
                           probes=self.probes(ssh_run=counter, resolve=lambda _p, _r: other))
        self.assertGreater(counter.calls, first)

    def test_not_ready_proof_is_never_reused(self):
        counter = _Counting(returncode=255)
        probes = self.probes(ssh_run=counter)
        for _ in range(2):
            with self.assertRaises(RemoteNotReadyError):
                gate.require_ready("/work/project", "vps", probes=probes)
        self.assertGreaterEqual(counter.calls, 2)

    def test_expired_proof_is_rechecked(self):
        counter = _Counting()
        now = [1000.0]
        probes = self.probes(ssh_run=counter, clock=lambda: now[0])
        gate.require_ready("/work/project", "vps", probes=probes)
        first = counter.calls
        now[0] += check.REUSE_SECONDS
        gate.require_ready("/work/project", "vps", probes=probes)
        self.assertGreater(counter.calls, first)

    def test_revision_change_is_rechecked(self):
        counter = _Counting()
        gate.require_ready("/work/project", "vps", probes=self.probes(ssh_run=counter))
        first = counter.calls
        migrated = {**REMOTE, "mcp_service": {"runtime_revision": "b" * 24}}
        gate.require_ready("/work/project", "vps", probes=self.probes(
            ssh_run=counter, remote_lookup=lambda _name: migrated))
        self.assertGreater(counter.calls, first)

    def test_invalidation_forces_a_recheck(self):
        # Same-revision migrate, provision, up and range assign all invalidate.
        counter = _Counting()
        probes = self.probes(ssh_run=counter)
        gate.require_ready("/work/project", "vps", probes=probes)
        first = counter.calls
        self.assertEqual(check.invalidate("vps", Path(self.home)), 1)
        gate.require_ready("/work/project", "vps", probes=probes)
        self.assertGreater(counter.calls, first)

    def test_transport_refuses_before_deploy(self):
        def never(*_args, **_kwargs):
            self.fail("nothing may be transferred after a not_ready row")

        def not_ready(_root, _remote):
            raise RemoteNotReadyError({"aspect": "capacity", "state": "not_ready",
                                       "reason": "missing_pool_evidence",
                                       "remedy": "./sb remote network-range propose vps"},
                                      remote="vps")
        transport = RemoteJobTransport(
            deploy=never, ssh_run=never, readiness=not_ready,
            remote_lookup=lambda _name: {"provisioned": True,
                                         "capabilities": ["job.exec", "job.execution-policy.v1"]})
        submission = JobSubmission("test", "/project", "project:remote", "remote", "unit",
                                   ("echo", "x"), 60, SourceIdentity("caller"),
                                   remote_name="vps", workspace_mode="isolated")
        for kind, submit in (("single", lambda: transport.submit(submission)),
                             ("matrix", lambda: transport.submit_many([submission]))):
            with self.subTest(kind=kind), self.assertRaises(RemoteNotReadyError) as caught:
                submit()
            self.assertEqual(caught.exception.code, "remote_not_ready_capacity")


def _calls_named(node, name):
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)
            and getattr(item.func, "id", getattr(item.func, "attr", None)) == name]


class SubmissionSiteTests(unittest.TestCase):
    """Every function that constructs a transport and submits through it
    passes the gate; control-only constructions (status, retry) do not."""

    def test_every_submitting_transport_passes_readiness(self):
        files = list((ROOT / "sandbox").rglob("*.py")) + \
            list((ROOT / "mcp" / "wp-server").rglob("*.py"))
        submitting = 0
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                submits = [call for call in ast.walk(function) if isinstance(call, ast.Call)
                           and isinstance(call.func, ast.Attribute)
                           and call.func.attr in {"submit", "submit_many"}]
                for construction in _calls_named(function, "RemoteJobTransport"):
                    if not submits:
                        continue
                    submitting += 1
                    with self.subTest(path=str(path.relative_to(ROOT)),
                                      line=construction.lineno):
                        self.assertIn("readiness", [k.arg for k in construction.keywords])
        self.assertGreaterEqual(submitting, 10)

    def test_mcp_run_tests_factory_passes_readiness(self):
        tree = ast.parse((ROOT / "mcp" / "wp-server" / "tools" / "wp.py").read_text())
        factory = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                       and node.name == "_remote_job_transport")
        (construction,) = _calls_named(factory, "RemoteJobTransport")
        self.assertIn("readiness", [k.arg for k in construction.keywords])

    def test_ensure_remote_checks_readiness_before_deploying(self):
        source = (ROOT / "sandbox" / "commands" / "lifecycle.py").read_text()
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and node.name == "_remote_lifecycle")
        (gate_call,) = _calls_named(function, "require_ready")
        (deploy_call,) = _calls_named(function, "deploy_exact_working_tree")
        (legacy_guard,) = _calls_named(function, "_remote_ensure_reachability")
        self.assertLess(gate_call.lineno, deploy_call.lineno)
        # The gate answers an unreachable remote before the legacy guard does.
        self.assertLess(gate_call.lineno, legacy_guard.lineno)


class CliEnvelopeTests(unittest.TestCase):
    def test_remote_readiness_json_envelope(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest.mock import patch

        from sandbox.commands import remote as remote_cmd
        data = {"remote": "vps", "remote_selection": "explicit", "rows": [],
                "installed_runtime_revision": REVISION, "taken_at": 1, "reusable_until": 301}
        out = io.StringIO()
        with patch.object(check, "run", return_value=data), contextlib.redirect_stdout(out):
            remote_cmd._cmd_readiness(SimpleNamespace(name="vps", project_dir="/p"), True)
        payload = json.loads(out.getvalue())
        self.assertEqual((payload["ok"], payload["action"], payload["data"]),
                         (True, "readiness", data))



_ROW = {"aspect": "capacity", "state": "not_ready", "reason": "missing_pool_evidence",
        "remedy": "./sb remote network-range propose vps"}


class RefusalEnvelopeTests(unittest.TestCase):
    """The refusal is an admission refusal, so every caller that already
    returns an admission envelope returns this one unchanged."""

    def test_refusal_is_an_admission_refusal_with_its_remedy(self):
        from sandbox.transports.remote_jobs import RemoteJobAdmissionError
        error = RemoteNotReadyError(_ROW, remote="vps")
        self.assertIsInstance(error, RemoteJobAdmissionError)
        payload = error.to_payload()
        self.assertEqual((payload["code"], payload["status"], payload["remedy"]),
                         ("remote_not_ready_capacity", "blocked",
                          "./sb remote network-range propose vps"))
        self.assertEqual(payload["detail"]["reason"], "missing_pool_evidence")
        self.assertEqual(payload["side_effects"]["bytes_transferred"], 0)

    def test_cli_edge_renders_json_and_human_remedy(self):
        import contextlib
        import io
        from types import SimpleNamespace

        from sandbox import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli._dispatch_remote_admission_error(
                RemoteNotReadyError(_ROW, remote="vps"), SimpleNamespace(json=True))
        self.assertEqual(json.loads(out.getvalue())["code"], "remote_not_ready_capacity")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli._dispatch_remote_admission_error(
                RemoteNotReadyError(_ROW, remote="vps"), SimpleNamespace(json=False))
        self.assertIn("Remedy: ./sb remote network-range propose vps", err.getvalue())
        self.assertNotIn("docker-pool", err.getvalue())

    def test_test_command_human_failure_names_the_remedy(self):
        import contextlib
        import io
        from types import SimpleNamespace

        from sandbox.commands import jobs_runtime
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            jobs_runtime._remote_job_transport_failure(
                RemoteNotReadyError(_ROW, remote="vps"),
                SimpleNamespace(remote="vps", json=False), "test")
        self.assertIn("Nothing was transferred", err.getvalue())
        self.assertIn("./sb remote network-range propose vps", err.getvalue())

    def test_mcp_wrappers_pass_a_blocked_envelope_through(self):
        for name in ("e2e.py", "ci.py"):
            source = (ROOT / "mcp" / "wp-server" / "tools" / name).read_text()
            with self.subTest(tool=name):
                self.assertIn('.get("status") == "blocked"', source)


class FenceAndDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_check_in_flight_across_an_invalidation_is_never_reused(self):
        home = Path(self.home)
        check.invalidate("vps", home)  # an earlier generation exists

        def migrate_during_probe(_remote, _command, timeout=None):
            check.invalidate("vps", home)  # a same-revision migrate lands now
            return subprocess.CompletedProcess([], 0, "", "")
        probes = _probes(self.home, ssh_run=migrate_during_probe)
        check.run("/work/project", "vps", probes=probes)
        self.assertIsNone(check.reusable("vps", "project-identity", probes=_probes(self.home)))
        # A check that starts after the invalidation is reusable again.
        check.run("/work/project", "vps", probes=_probes(self.home))
        self.assertIsNotNone(check.reusable("vps", "project-identity",
                                            probes=_probes(self.home)))

    def test_process_exits_at_the_deadline_with_a_probe_still_running(self):
        import time

        from tests.subprocess_support import synthetic_environment
        program = (
            "import sys, threading; sys.path.insert(0, sys.argv[1]); "
            "from sandbox.readiness import check; "
            "from tests.test_readiness import _probes; "
            "check.DEADLINE_SECONDS = 0.5; "
            "hang = lambda *_a, **_k: threading.Event().wait(30); "
            "result = check.run('/work/project', 'vps', probes=_probes(sys.argv[2], "
            "capacity_decision=lambda _r, *, remote_name: hang())); "
            "print([r['state'] for r in result['rows'] if r['aspect'] == 'capacity'][0])"
        )
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-c", program, str(ROOT), self.home],
            capture_output=True, text=True, timeout=25, cwd=str(ROOT),
            env=synthetic_environment({"PYTHONPATH": str(ROOT)}))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "unknown")
        self.assertLess(time.monotonic() - started, 15)


class HandoffIsolationTests(unittest.TestCase):
    def test_exec_for_another_project_records_nothing(self):
        home = Path(tempfile.mkdtemp())
        check.run("/work/project", "vps", probes=_probes(home))
        check.record_ensure("vps", "other-project", home=home)
        self.assertFalse(check.record_exec("vps", "project-identity", home=home))
        self.assertFalse((check.readiness_dir(home, "vps") / "handoff.json").exists())


if __name__ == "__main__":
    unittest.main()

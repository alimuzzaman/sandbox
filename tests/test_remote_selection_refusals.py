"""A retired or ambiguous remote is told, not guessed (spec 063 US3, T022)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.application.target_service import TargetResolutionError, TargetService  # noqa: E402
from sandbox.jobs.models import TargetRequest  # noqa: E402
from sandbox.readiness import check, notice  # noqa: E402
from sandbox.readiness.errors import registration_refusal  # noqa: E402
from tests.test_readiness import _probes  # noqa: E402

REGISTERED = {
    "vps": {"name": "vps", "provisioned": True, "capabilities": ["job.exec"]},
    "old": {"name": "old", "provisioned": False},
}
HINT = "set runtime.remote in sandbox.config.json to a registered remote, or pass --remote NAME"


def _service(root, runtime=None, registered=REGISTERED):
    config = {"root": str(root)}
    if runtime is not None:
        config["runtime"] = runtime
    return TargetService(config_loader=lambda *_a, **_k: config,
                         remote_lookup=lambda name: registered.get(name),
                         remote_list=lambda: registered)


def _refusal(service, **request):
    with self_raises() as caught:
        service.resolve(TargetRequest(**request))
    return caught.exception


@contextlib.contextmanager
def self_raises():
    holder = SimpleNamespace(exception=None)
    try:
        yield holder
    except TargetResolutionError as exc:
        holder.exception = exc
    else:
        raise AssertionError("target resolution did not refuse")


class SelectionRefusalDataTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_unknown_declared_remote_names_source_registered_and_remedy(self):
        service = _service(self.root, {"default": "remote", "remote": "ghost"})
        exc = _refusal(service, project_dir=str(self.root), required_capability="job.exec")
        self.assertEqual((exc.code, exc.remote_name), ("unknown_remote", "ghost"))
        self.assertEqual(exc.data, {"name": "ghost", "name_source": "declaration",
                                    "registered": ["old", "vps"],
                                    "remedy": "./sb remote list", "hint": HINT})

    def test_unknown_explicit_remote_is_the_callers_name(self):
        service = _service(self.root)
        exc = _refusal(service, project_dir=str(self.root), remote="ghost")
        self.assertEqual(exc.data["name_source"], "caller")
        self.assertEqual(exc.data["registered"], ["old", "vps"])

    def test_ambiguous_remote_lists_candidates_and_how_to_choose(self):
        registered = {"a": {"name": "a", "provisioned": True},
                      "b": {"name": "b", "provisioned": True}}
        service = _service(self.root, registered=registered)
        exc = _refusal(service, project_dir=str(self.root))
        self.assertEqual(exc.code, "ambiguous_remote")
        self.assertEqual(exc.data, {"candidates": ["a", "b"], "remedy": "./sb remote list",
                                    "hint": HINT})

    def test_unprovisioned_remote_names_the_provision_command(self):
        service = _service(self.root, {"default": "remote", "remote": "old"})
        exc = _refusal(service, project_dir=str(self.root))
        self.assertEqual(exc.code, "remote_not_provisioned")
        self.assertEqual(exc.data, {"name": "old", "name_source": "declaration",
                                    "remedy": "./sb remote provision old"})

    def test_single_configured_inference_is_unchanged(self):
        registered = {"vps": REGISTERED["vps"]}
        target = _service(self.root, registered=registered).resolve(
            TargetRequest(project_dir=str(self.root)))
        self.assertEqual((target.kind, target.remote_name, target.sources["remote_selection"]),
                         ("remote", "vps", "single-configured"))


class RegistrationRefusalEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_envelope_carries_selection_data_and_remote_selection(self):
        cases = (
            ({"default": "remote", "remote": "ghost"}, {}, "profile"),
            (None, {"remote": "ghost"}, "explicit"),
        )
        for runtime, request, selection in cases:
            with self.subTest(selection=selection):
                exc = _refusal(_service(self.root, runtime),
                               project_dir=str(self.root), **request)
                payload = registration_refusal(exc).to_payload()
                self.assertEqual((payload["code"], payload["status"], payload["remote_selection"]),
                                 ("remote_not_ready_registration", "blocked", selection))
                detail = payload["detail"]
                self.assertEqual((detail["reason"], detail["name"], detail["registered"],
                                  detail["hint"]),
                                 ("unknown_remote", "ghost", ["old", "vps"], HINT))
                self.assertEqual(payload["side_effects"]["bytes_transferred"], 0)

    def test_ambiguous_remote_is_a_registration_refusal(self):
        registered = {"a": {"name": "a", "provisioned": True},
                      "b": {"name": "b", "provisioned": True}}
        exc = _refusal(_service(self.root, registered=registered), project_dir=str(self.root))
        payload = registration_refusal(exc).to_payload()
        self.assertEqual((payload["code"], payload["detail"]["reason"],
                          payload["detail"]["candidates"], payload["remote_selection"]),
                         ("remote_not_ready_registration", "ambiguous_remote", ["a", "b"], None))

    def test_selection_data_is_bounded_and_name_shaped(self):
        exc = TargetResolutionError("unknown_remote", "x", remote_name="ghost", data={
            "name": "ghost", "name_source": "declaration",
            "registered": ["ok", "bad name; rm -rf /", *[f"r{i}" for i in range(80)]],
            "remedy": "./sb remote list", "hint": HINT})
        detail = registration_refusal(exc).to_payload()["detail"]
        self.assertNotIn("bad name; rm -rf /", detail["registered"])
        self.assertLessEqual(len(detail["registered"]), 50)


class ReadinessRegistrationRowTests(unittest.TestCase):
    def test_readiness_reports_declared_unknown_remote_with_its_data(self):
        home = tempfile.mkdtemp()
        error = TargetResolutionError("unknown_remote", "not registered", remote_name="ghost", data={
            "name": "ghost", "name_source": "declaration", "registered": ["vps"],
            "remedy": "./sb remote list", "hint": HINT})

        def resolve(_project, _remote):
            raise error
        result = check.run("/work/project", None, probes=_probes(home, resolve=resolve))
        self.assertEqual((result["remote"], result["remote_selection"]), ("ghost", "profile"))
        row = result["rows"][0]
        self.assertEqual((row["aspect"], row["state"], row["reason"], row["name"],
                          row["name_source"], row["registered"]),
                         ("registration", "not_ready", "unknown_remote", "ghost",
                          "declaration", ["vps"]))

    def test_readiness_reports_ambiguous_candidates(self):
        home = tempfile.mkdtemp()
        error = TargetResolutionError("ambiguous_remote", "pick one", data={
            "candidates": ["a", "b"], "remedy": "./sb remote list", "hint": HINT})

        def resolve(_project, _remote):
            raise error
        result = check.run("/work/project", None, probes=_probes(home, resolve=resolve))
        row = result["rows"][0]
        self.assertEqual((row["state"], row["reason"], row["candidates"]),
                         ("not_ready", "ambiguous_remote", ["a", "b"]))
        self.assertIsNone(result["remote_selection"])


class NoLocalFallbackTests(unittest.TestCase):
    """MCP run_tests is the one submission path that used to fall back to a
    local run without a selector (research R9); it now refuses instead."""

    def _wp_tool(self):
        import importlib.util
        import types

        import sandbox.commands.jobs_runtime  # noqa: F401
        httpx = types.ModuleType("httpx")
        deps = types.ModuleType("dependencies")
        deps.ToolDependencies = object
        with patch.dict(sys.modules, {"httpx": httpx, "dependencies": deps}):
            path = ROOT / "mcp" / "wp-server" / "tools" / "wp.py"
            spec = importlib.util.spec_from_file_location("_selection_wp", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_mcp_run_tests_refuses_instead_of_running_locally(self):
        module = self._wp_tool()
        local_calls = []

        def local(*args, **_kwargs):
            local_calls.append(args)
            return {"ok": False, "error": "local path reached"}
        module._require_project_capability = local
        module._project_instance = local
        cases = {
            "ambiguous_remote": ("blocked", "remote_not_ready_registration"),
            "unsupported_capability": (None, "unsupported_capability"),
        }
        for code, (status, expected) in cases.items():
            def refuse(_request, code=code):
                raise TargetResolutionError(code, "cannot select", data={
                    "candidates": ["a", "b"], "remedy": "./sb remote list", "hint": HINT})
            with self.subTest(code=code), \
                    patch("sandbox.application.context.durable_job_dependencies",
                          return_value={"target_service": SimpleNamespace(resolve=refuse)}), \
                    patch.object(module, "_resolve_test_mode", return_value="unit"), \
                    patch.object(module.subprocess, "run", side_effect=AssertionError("ran")):
                result = module.run_tests(project_dir="/work/p")
                self.assertEqual((result["ok"], result["passed"], result["code"]),
                                 (False, False, expected))
                self.assertEqual(result.get("status"), status)
        self.assertEqual(local_calls, [])

        def not_sandbox(_request):
            raise TargetResolutionError("invalid_project", "not a sandbox project")
        with patch("sandbox.application.context.durable_job_dependencies",
                   return_value={"target_service": SimpleNamespace(resolve=not_sandbox)}), \
                patch.object(module, "_resolve_test_mode", return_value="unit"):
            result = module.run_tests(project_dir="/work/p")
        self.assertEqual(result, {"ok": False, "error": "local path reached"})


class LocalAfterNotReadyNoticeTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def _declared(self, name, resolve):
        return SimpleNamespace(declared_remote=lambda _project: name, resolve=resolve)

    def test_unregistered_declared_remote(self):
        def resolve(_request):
            raise TargetResolutionError("unknown_remote", "x", remote_name="ghost")
        found = notice.local_notice("/work/p", service=self._declared("ghost", resolve),
                                    home=self.home)
        self.assertEqual(found, {"remote_selection": "local", "declared_remote": "ghost",
                                 "failing_aspect": "registration", "reason": "unknown_remote"})

    def test_stored_not_ready_proof_names_its_first_failing_row(self):
        target = SimpleNamespace(kind="remote", remote_name="vps", project_root="/work/p",
                                 sources={"identity": "project-identity"})
        directory = check.readiness_dir(self.home, "vps")
        check._write_private(directory / f"{check.project_key('project-identity')}.json", {
            "remote": "vps", "project": "project-identity",
            "rows": [{"aspect": "registration", "state": "ready"},
                     {"aspect": "reachability", "state": "not_ready", "reason": "ssh_refused"}]})
        found = notice.local_notice("/work/p", service=self._declared(
            "vps", lambda _request: target), home=self.home)
        self.assertEqual(found, {"remote_selection": "local", "declared_remote": "vps",
                                 "failing_aspect": "reachability", "reason": "ssh_refused"})

    def test_no_notice_without_a_declaration_or_a_failing_proof(self):
        target = SimpleNamespace(kind="remote", remote_name="vps", project_root="/work/p",
                                 sources={"identity": "project-identity"})
        self.assertIsNone(notice.local_notice(
            "/work/p", service=self._declared(None, lambda _r: target), home=self.home))
        self.assertIsNone(notice.local_notice(
            "/work/p", service=self._declared("vps", lambda _r: target), home=self.home))

    def test_cli_prints_the_notice_for_an_explicit_local_submission(self):
        from sandbox import cli
        found = {"remote_selection": "local", "declared_remote": "vps",
                 "failing_aspect": "reachability", "reason": "ssh_refused"}
        err = io.StringIO()
        with patch.object(notice, "local_notice", return_value=found), \
                contextlib.redirect_stderr(err):
            cli._local_selection_notice(SimpleNamespace(cmd="test", local=True,
                                                        project_dir="/work/p"))
        line = err.getvalue()
        self.assertIn("remote_selection: local", line)
        self.assertIn("'vps'", line)
        self.assertIn("reachability (ssh_refused)", line)
        quiet = io.StringIO()
        with patch.object(notice, "local_notice", side_effect=AssertionError("looked up")), \
                contextlib.redirect_stderr(quiet):
            cli._local_selection_notice(SimpleNamespace(cmd="test", local=False,
                                                        project_dir="/work/p"))
            cli._local_selection_notice(SimpleNamespace(cmd="doctor", local=True,
                                                        project_dir="/work/p"))
        self.assertEqual(quiet.getvalue(), "")

    def test_mcp_local_results_carry_the_notice(self):
        module = NoLocalFallbackTests._wp_tool(self)
        found = {"remote_selection": "local", "declared_remote": "vps",
                 "failing_aspect": "reachability", "reason": "ssh_refused"}
        module._require_project_capability = lambda *_a, **_k: None
        module._project_instance = lambda *_a, **_k: ("inst", None)
        module._managed_execution_unavailable = lambda *_a, **_k: None
        module.SANDBOX_ROOT = ROOT
        completed = SimpleNamespace(returncode=0, stdout="OK (1 test, 1 assertion)", stderr="")
        with patch.object(notice, "local_notice", return_value=found), \
                patch.object(module, "_resolve_test_mode", return_value="unit"), \
                patch.object(module.subprocess, "run", return_value=completed) as run:
            result = module.run_tests(project_dir="/work/p", local=True)
        self.assertIn("--local", run.call_args.args[0])
        self.assertEqual({key: result[key] for key in found}, found)
        self.assertTrue(result["passed"])

    def test_mcp_local_job_start_carries_the_notice(self):
        import importlib.util
        import types

        import sandbox.commands.jobs_runtime  # noqa: F401
        deps = types.ModuleType("dependencies")
        deps.ToolDependencies = object
        with patch.dict(sys.modules, {"dependencies": deps}):
            path = ROOT / "mcp" / "wp-server" / "tools" / "jobs.py"
            spec = importlib.util.spec_from_file_location("_selection_jobs", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        found = {"remote_selection": "local", "declared_remote": "vps",
                 "failing_aspect": "reachability", "reason": "ssh_refused"}
        accepted = {"ok": True, "job_id": "job-1", "lifecycle": "accepted"}
        with patch.object(notice, "local_notice", return_value=found), \
                patch.object(module, "_submit_explicit_job", return_value=accepted):
            self.assertEqual(module.job_start(["true"], "/work/p", local=True),
                             {**accepted, **found})
        with patch.object(notice, "local_notice", side_effect=AssertionError("looked up")), \
                patch.object(module, "_submit_explicit_job", return_value=accepted):
            self.assertEqual(module.job_start(["true"], "/work/p"), accepted)


if __name__ == "__main__":
    unittest.main()

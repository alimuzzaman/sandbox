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


class RoundOneTests(unittest.TestCase):
    """Sol merge-gate round 1 for US3."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_human_refusal_names_the_selection_facts(self):
        from sandbox import cli
        registered = {"a": {"name": "a", "provisioned": True},
                      "b": {"name": "b", "provisioned": True}}
        cases = (
            (_service(self.root, registered=registered), {}, ("candidates: a, b", HINT)),
            (_service(self.root, {"default": "remote", "remote": "ghost"}), {},
             ("remote 'ghost' (from runtime.remote in sandbox.config.json)",
              "registered: old, vps", HINT)),
        )
        for service, request, expected in cases:
            exc = _refusal(service, project_dir=str(self.root), **request)
            err = io.StringIO()
            with self.subTest(code=exc.code), contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit):
                cli._dispatch_remote_admission_error(registration_refusal(exc),
                                                     SimpleNamespace(json=False))
            for text in expected:
                self.assertIn(text, err.getvalue())

    def test_gate_keeps_the_selection_of_the_callers_resolution(self):
        import threading
        from sandbox.readiness import gate
        service = _service(self.root, {"default": "remote", "remote": "vps"})
        target = service.resolve(TargetRequest(project_dir=str(self.root)))
        submission = SimpleNamespace(project_root=target.project_root, remote_name="vps",
                                     workspace_label="default", project_identity="id")
        self.assertEqual(gate._submission_target(submission).sources["remote_selection"],
                         "profile")
        other = SimpleNamespace(project_root="/elsewhere", remote_name="vps",
                                workspace_label="default", project_identity="id")
        self.assertIsNone(gate._submission_target(other).sources["remote_selection"])
        seen = []
        thread = threading.Thread(target=lambda: seen.append(
            gate._submission_target(submission).sources["remote_selection"]))
        thread.start()
        thread.join()
        self.assertEqual(seen, [None])

    def test_gate_refusal_reports_the_selection(self):
        from sandbox.readiness import gate
        from sandbox.readiness.errors import RemoteNotReadyError
        service = _service(self.root, {"default": "remote", "remote": "vps"})
        target = service.resolve(TargetRequest(project_dir=str(self.root)))
        submission = SimpleNamespace(project_root=target.project_root, remote_name="vps",
                                     workspace_label="default", project_identity="id")
        refused = SimpleNamespace(returncode=255)
        with self.assertRaises(RemoteNotReadyError) as caught:
            gate.require_ready(str(self.root), "vps", submission, probes=_probes(
                tempfile.mkdtemp(), ssh_run=lambda *_a, **_k: refused))
        payload = caught.exception.to_payload()
        self.assertEqual((payload["code"], payload["remote_selection"]),
                         ("remote_not_ready_reachability", "profile"))

    def test_readiness_unprovisioned_registration_row_through_the_resolver(self):
        service = _service(self.root, {"default": "remote", "remote": "old"})
        result = check.run(str(self.root), None, probes=_probes(
            tempfile.mkdtemp(), resolve=lambda project, remote: service.resolve(
                TargetRequest(project_dir=project, remote=remote,
                              required_capability="job.exec"))))
        first, *rest = result["rows"]
        self.assertEqual((first["state"], first["reason"], first["name"], first["name_source"],
                          first["remedy"]),
                         ("not_ready", "remote_not_provisioned", "old", "declaration",
                          "./sb remote provision old"))
        self.assertEqual((result["remote"], result["remote_selection"]), ("old", "profile"))
        self.assertEqual({row["state"] for row in rest}, {"not_applicable"})

    def test_cli_notice_uses_cwd_and_config_file_and_skips_durable_jobs(self):
        from sandbox import cli
        calls = []

        def record(project_dir, **kwargs):
            calls.append((project_dir, kwargs))
        # A synthetic environment: the parent's is never copied.
        environ = {}
        fake_os = SimpleNamespace(environ=environ, getcwd=lambda: "/work/cwd")
        with patch.object(notice, "local_notice", side_effect=record), \
                patch.object(cli, "os", fake_os):
            cli._local_selection_notice(SimpleNamespace(cmd="test", local=True, project_dir=None,
                                                        config_file="alt.json"))
            environ["SANDBOX_DURABLE_JOB_ID"] = "job-1"
            cli._local_selection_notice(SimpleNamespace(cmd="test", local=True,
                                                        project_dir="/work/p"))
        self.assertEqual(calls, [("/work/cwd", {"config_file": "alt.json"})])

    def test_notice_reads_the_selected_descriptor(self):
        seen = []

        def declared(project_dir, *, config_file=None):
            seen.append(config_file)
            return None
        service = SimpleNamespace(declared_remote=declared, resolve=None)
        self.assertIsNone(notice.local_notice("/work/p", service=service, config_file="alt.json"))
        self.assertEqual(seen, ["alt.json"])

    def test_mcp_run_tests_refuses_other_resolution_failures(self):
        module = NoLocalFallbackTests._wp_tool(self)
        module._require_project_capability = lambda *_a, **_k: {"ok": False, "error": "local"}

        def refuse(_request):
            raise TargetResolutionError("invalid_workspace", "bad workspace")
        with patch("sandbox.application.context.durable_job_dependencies",
                   return_value={"target_service": SimpleNamespace(resolve=refuse)}), \
                patch.object(module, "_resolve_test_mode", return_value="unit"):
            result = module.run_tests(project_dir="/work/p")
        self.assertEqual((result["ok"], result["code"]), (False, "invalid_workspace"))

    def test_mcp_remote_successes_report_the_selection(self):
        module = NoLocalFallbackTests._wp_tool(self)
        target = SimpleNamespace(kind="remote", remote_name="vps", workspace_label="default",
                                 project_root=str(self.root), runtime_policy={},
                                 sources={"remote_selection": "single-configured",
                                          "identity": "id"})
        resolves = []

        def resolve(request):
            resolves.append(request)
            return target
        transport = SimpleNamespace(submit=lambda _submission: {"job_id": "job-1"})
        with patch("sandbox.application.context.durable_job_dependencies",
                   return_value={"target_service": SimpleNamespace(resolve=resolve)}), \
                patch.object(module, "_resolve_test_mode", return_value="unit"), \
                patch.object(module, "_remote_job_transport", return_value=transport), \
                patch("sandbox.commands.jobs_runtime._source_identity", return_value=None), \
                patch("sandbox.commands.jobs_runtime._resolved_project_identity",
                      return_value="id"), \
                patch("sandbox.jobs.models.JobSubmission", side_effect=lambda *a, **k: a):
            result = module.run_tests(project_dir=str(self.root))
        self.assertEqual((result["ok"], result["remote_selection"], len(resolves)),
                         (True, "single-configured", 1))


    def test_mcp_remote_job_start_reports_the_selection(self):
        import importlib.util
        import types

        import sandbox.commands.jobs_runtime  # noqa: F401
        deps = types.ModuleType("dependencies")
        deps.ToolDependencies = object
        with patch.dict(sys.modules, {"dependencies": deps}):
            path = ROOT / "mcp" / "wp-server" / "tools" / "jobs.py"
            spec = importlib.util.spec_from_file_location("_selection_jobs_remote", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        target = SimpleNamespace(kind="remote", remote_name="vps", workspace_label="default",
                                 project_root=str(self.root), runtime_policy={},
                                 sources={"remote_selection": "explicit", "identity": "id"})
        module._target_service = SimpleNamespace(resolve=lambda _request: target)
        policy = SimpleNamespace(deadline_seconds=60, execution_profile=None,
                                 deadline_source=None, deadline_reminder=None,
                                 stall_seconds=None, cancel_grace_seconds=None,
                                 cancel_on_stall=None, cleanup_policy=None, provenance={})

        class Transport:
            def __init__(self, **_kwargs):
                pass

            def submit(self, _submission):
                return {"ok": True, "job_id": "job-1"}
        with patch.object(module, "_mcp_execution_policy", return_value=(policy, None)), \
                patch.object(module, "JobSubmission", side_effect=lambda *a, **k: a), \
                patch.object(module, "_resolved_project_identity", return_value="id"), \
                patch.object(module, "_source_identity", return_value=None), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", Transport):
            result = module.job_start(["true"], str(self.root), remote="vps")
        self.assertEqual(result, {"ok": True, "job_id": "job-1", "remote_selection": "explicit"})


class RoundTwoTests(unittest.TestCase):
    """Sol merge-gate round 2 for US3."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()

    def _tool(self, name):
        import importlib.util
        import types
        app = types.ModuleType("app")
        app.SANDBOX_ROOT = ROOT
        app._require_project_capability = lambda *_a, **_k: None
        app._safe_json = lambda line: json.loads(line)
        app.mcp = SimpleNamespace(tool=lambda *_a, **_k: (lambda function: function))
        with patch.dict(sys.modules, {"app": app}):
            path = ROOT / "mcp" / "wp-server" / "tools" / f"{name}.py"
            spec = importlib.util.spec_from_file_location(f"_selection_{name}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_mcp_e2e_and_ci_successes_report_remote_selection(self):
        service = _service(self.root, {"default": "remote", "remote": "vps"})
        deps = {"target_service": service}
        cases = (
            ("e2e", lambda m: m.run_e2e(str(self.root)), {"ok": True, "workers": []}),
            ("e2e", lambda m: m.run_e2e(str(self.root), async_=True), {"ok": True, "job_id": "j"}),
            ("ci", lambda m: m.ci_run(str(self.root), "ci.yml"),
             {"ok": True, "parent_job_id": "p", "children": []}),
        )
        for name, call, report in cases:
            module = self._tool(name)
            completed = SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="")
            with self.subTest(name=name, report=report), \
                    patch.object(module.subprocess, "run", return_value=completed), \
                    patch("sandbox.application.context.durable_job_dependencies",
                          return_value=deps):
                self.assertEqual(call(module), {**report, "remote_selection": "profile"})
        blocked = {"ok": False, "status": "blocked", "code": "remote_not_ready_capacity"}
        module = self._tool("e2e")
        completed = SimpleNamespace(returncode=1, stdout=json.dumps(blocked), stderr="")
        with patch.object(module.subprocess, "run", return_value=completed):
            self.assertEqual(module.run_e2e(str(self.root)), blocked)

    def test_with_selection_local_and_unresolvable(self):
        local = notice.with_selection({"ok": True}, "/work/p", local=True,
                                      service=SimpleNamespace(declared_remote=lambda _p: None))
        self.assertEqual(local, {"ok": True, "remote_selection": "local"})

        def refuse(_request):
            raise TargetResolutionError("ambiguous_remote", "x")
        self.assertEqual(notice.with_selection({"ok": True}, "/work/p",
                                               service=SimpleNamespace(resolve=refuse)),
                         {"ok": True, "remote_selection": None})

    def test_mcp_matrix_success_reports_remote_selection(self):
        import importlib.util
        import types

        import sandbox.commands.jobs_runtime  # noqa: F401
        deps = types.ModuleType("dependencies")
        deps.ToolDependencies = object
        with patch.dict(sys.modules, {"dependencies": deps}):
            path = ROOT / "mcp" / "wp-server" / "tools" / "jobs.py"
            spec = importlib.util.spec_from_file_location("_selection_jobs_matrix", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        target = SimpleNamespace(kind="remote", remote_name="vps", workspace_label="a",
                                 project_root=str(self.root), runtime_policy={},
                                 sources={"remote_selection": "single-configured"})
        module._target_service = SimpleNamespace(resolve=lambda _request: target)
        policy = SimpleNamespace(deadline_seconds=60, execution_profile=None,
                                 deadline_source=None, deadline_reminder=None,
                                 stall_seconds=None, cancel_grace_seconds=None,
                                 cancel_on_stall=None, cleanup_policy=None, provenance={})

        class Transport:
            def __init__(self, **_kwargs):
                pass

            def submit_many(self, _submissions):
                return {"ok": True, "parent_job_id": "p"}
        with patch.object(module, "_mcp_execution_policy", return_value=(policy, None)), \
                patch.object(module, "JobSubmission", side_effect=lambda *a, **k: a), \
                patch.object(module, "_resolved_project_identity", return_value="id"), \
                patch.object(module, "_source_identity", return_value=None), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", Transport):
            result = module.job_matrix(["true"], ["a", "b"], str(self.root), remote="vps")
        self.assertEqual(result, {"ok": True, "parent_job_id": "p",
                                  "remote_selection": "single-configured"})

    def test_gate_selection_after_interleaved_resolutions(self):
        import contextvars
        from sandbox.readiness import gate
        two = {"vps": REGISTERED["vps"],
               "edge": {"name": "edge", "provisioned": True, "capabilities": ["job.exec"]}}
        profile = _service(self.root, {"default": "remote", "remote": "vps"}, registered=two)

        def submission(remote):
            return SimpleNamespace(project_root=str(self.root.resolve()), remote_name=remote,
                                   workspace_label="default", project_identity="id")

        def selection(remote):
            return gate._submission_target(submission(remote)).sources["remote_selection"]

        def scenario():
            profile.resolve(TargetRequest(project_dir=str(self.root)))
            self.assertEqual(selection("vps"), "profile")
            # A different remote replaces it; the earlier remote no longer matches.
            profile.resolve(TargetRequest(project_dir=str(self.root), remote="edge"))
            self.assertEqual((selection("edge"), selection("vps")), ("explicit", None))
            # Re-selecting the same remote another way records the new way.
            profile.resolve(TargetRequest(project_dir=str(self.root), remote="vps"))
            self.assertEqual(selection("vps"), "explicit")
            # A local or failed resolution leaves the last remote selection.
            profile.resolve(TargetRequest(project_dir=str(self.root), local=True))
            with self.assertRaises(TargetResolutionError):
                profile.resolve(TargetRequest(project_dir=str(self.root), remote="ghost"))
            self.assertEqual(selection("vps"), "explicit")
        contextvars.copy_context().run(scenario)

    def test_run_tests_auto_target_keeps_descriptor_and_workspace(self):
        module = NoLocalFallbackTests._wp_tool(self)
        target = SimpleNamespace(kind="remote", remote_name="vps", workspace_label="php",
                                 project_root=str(self.root), runtime_policy={"workspace": "php"},
                                 sources={"remote_selection": "profile", "identity": "id"})
        requests, submitted = [], []
        (self.root / "sandbox.config.yml").write_text("runtime: {}\n")

        def resolve(request):
            requests.append(request)
            return target
        transport = SimpleNamespace(
            submit=lambda submission: submitted.append(submission) or {"job_id": "j"})
        with patch("sandbox.application.context.durable_job_dependencies",
                   return_value={"target_service": SimpleNamespace(resolve=resolve)}), \
                patch.object(module, "_resolve_test_mode", return_value="unit"), \
                patch.object(module, "_remote_job_transport", return_value=transport), \
                patch("sandbox.commands.jobs_runtime._source_identity", return_value=None), \
                patch("sandbox.commands.jobs_runtime._resolved_project_identity",
                      return_value="id"), \
                patch("sandbox.jobs.models.JobSubmission", side_effect=lambda *a, **k: a):
            result = module.run_tests(project_dir=str(self.root), config_file="sandbox.config.yml")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual([request.config_file for request in requests], ["sandbox.config.yml"])
        self.assertEqual((result["workspace"], result["remote_selection"]), ("php", "profile"))
        self.assertEqual(submitted[0][4], "php")


if __name__ == "__main__":
    unittest.main()

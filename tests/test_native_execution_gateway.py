from types import SimpleNamespace
import unittest
from unittest import mock


class TestNativeExecutionGateway(unittest.TestCase):
    def test_managed_async_wp_never_reaches_legacy_job_launcher(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="managed", passthrough=["option", "get", "siteurl"],
            run_async=True,
        )
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "managed_native_instance_selected",
                                  return_value=("/project", "default")), \
                mock.patch("sandbox.commands.jobs.launch_job") as launch, \
                self.assertRaises(SystemExit):
            command.cmd_wp({}, args)
        launch.assert_not_called()

    def test_managed_interactive_shell_never_reaches_compose(self):
        import sandbox.commands.lifecycle as command

        args = SimpleNamespace(resolved_instance="managed")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch("sandbox.application.context.managed_native_instance_selected",
                           return_value=("/project", "default")), \
                mock.patch.object(command, "compose") as compose, \
                self.assertRaises(SystemExit):
            command.cmd_shell({}, args)
        compose.assert_not_called()

    def test_managed_dashboard_terminal_never_reaches_compose(self):
        import sandbox.core._dash as dashboard

        outcomes = []

        def inline(_label, operation):
            outcomes.append(operation())
            return "fixture-job"

        with mock.patch.object(dashboard, "_start_job", side_effect=inline), \
                mock.patch.object(dashboard, "load_config", return_value={}), \
                mock.patch("sandbox.application.context.managed_native_instance_selected",
                           return_value=("/project", "default")), \
                mock.patch.object(dashboard, "compose") as compose:
            result = dashboard._web_do_action({
                "action": "term", "instance": "managed", "cmd": "id",
            })
        self.assertEqual(result["job_id"], "fixture-job")
        self.assertEqual(outcomes, [False])
        compose.assert_not_called()

    def test_execution_contract_validates_entry_path_and_sync_result(self):
        from sandbox.runtimes.base import ExecutionRequest, ExecutionResult
        request = ExecutionRequest("/project", "default", "wp_eval", ("wp", "eval", "1;"), 30)
        self.assertEqual(request.entry_path, "wp_eval")
        self.assertTrue(ExecutionResult(True, 0, "ready", {}).ok)
        with self.assertRaises(ValueError):
            ExecutionRequest("/project", "default", "host_shell", ("sh",), 30)

    def test_explicit_config_selector_reaches_managed_runtime_without_automatic_reload(self):
        from sandbox.application.context import execute_project, managed_native_instance_selected
        from sandbox.runtimes.base import ExecutionRequest, OperationResult
        import sandbox_core as sc

        calls = []

        class Service:
            def invoke(self, request):
                calls.append(request)
                return OperationResult(True, request.operation, request.project_root,
                                       "wordpress", {"state": "ready", "exit_code": 0})

        with mock.patch.object(sc, "registry_find_instance", return_value={
                "root": "/project", "label": "qa"}), \
                mock.patch.object(sc, "load_project_config", return_value={
                    "wordpressRuntime": {"mode": "managed_native"}}) as load, \
                mock.patch("sandbox.application.context.runtime_service", return_value=Service()):
            self.assertEqual(managed_native_instance_selected(
                "fixture", config_file="tooling/sandbox.config.json",
            ), ("/project", "qa"))
            result = execute_project({}, ExecutionRequest(
                "/project", "qa", "phpunit", ("php", "phpunit"), 30,
                "tooling/sandbox.config.json",
            ))

        self.assertTrue(result.ok)
        load.assert_called_once_with(
            "/project", label="qa", config_file="tooling/sandbox.config.json",
        )
        self.assertEqual(calls[0].arguments["config_file"],
                         "tooling/sandbox.config.json")
        self.assertEqual(calls[0].arguments["execution"].config_file,
                         "tooling/sandbox.config.json")

    def test_mcp_managed_execution_preserves_explicit_config_selector(self):
        import importlib.util
        from pathlib import Path
        import sys
        from sandbox.runtimes.base import ExecutionResult

        root = Path(__file__).parent.parent / "mcp/wp-server"
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        spec = importlib.util.spec_from_file_location(
            "managed_config_wp_tools_test", root / "tools/wp.py",
        )
        module = importlib.util.module_from_spec(spec)
        previous_httpx = sys.modules.get("httpx")
        sys.modules["httpx"] = mock.Mock()
        try:
            spec.loader.exec_module(module)
        finally:
            if previous_httpx is None:
                sys.modules.pop("httpx", None)
            else:
                sys.modules["httpx"] = previous_httpx
        captured = []
        with mock.patch("sandbox.application.context.managed_native_project_selected",
                        return_value=True) as selected, \
                mock.patch("sandbox.application.context.execute_project",
                           side_effect=lambda _cfg, request: captured.append(request) or
                           ExecutionResult(True, 0, "ready", {})):
            result = module._managed_execution_unavailable(
                "/project", "qa", "wordpress_cli", ("wp", "core", "version"), 60,
                "tooling/sandbox.config.json",
            )

        self.assertTrue(result["ok"])
        selected.assert_called_once_with(
            "/project", label="qa", config_file="tooling/sandbox.config.json",
        )
        self.assertEqual(captured[0].config_file, "tooling/sandbox.config.json")

    def test_managed_wpcli_is_blocked_before_compose_fallback(self):
        import sandbox.core._docker as docker
        import sandbox_core as sc
        error = SimpleNamespace(message="managed runtime is unavailable")
        with mock.patch.object(sc, "registry_find_instance", return_value={
                "root": "/project", "label": "default"}), \
                mock.patch.object(sc, "load_project_config", return_value={
                    "wordpressRuntime": {"mode": "managed_native"}}), \
                mock.patch.object(docker, "load_config", return_value={}), \
                mock.patch("sandbox.application.context.preflight_instance_capability", return_value=error), \
                mock.patch.object(docker, "compose") as compose:
            result = docker.wpcli(["eval", "echo 1;"], instance="demo", check=False)
        self.assertEqual(result.returncode, 126)
        compose.assert_not_called()

    def test_managed_durable_job_detection_never_allows_host_popen(self):
        from sandbox.jobs.supervisor import _managed_native_job
        self.assertTrue(_managed_native_job({"execution_runtime": "managed_native"}))
        self.assertFalse(_managed_native_job({"execution_runtime": "host"}))
        with self.assertRaisesRegex(RuntimeError, "runtime selection"):
            _managed_native_job({"cwd": "/project", "label": "default"})

    def test_managed_durable_job_uses_adapter_transport_and_persists_output(self):
        import json
        import tempfile
        from pathlib import Path
        from sandbox.application.job_service import JobService
        from sandbox.jobs.models import JobSubmission, SourceIdentity
        from sandbox.jobs.registry import JobRepository
        from sandbox.jobs.storage import JobStorage
        from sandbox.jobs.supervisor import run_descriptor
        from sandbox.runtimes.base import ExecutionResult

        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(
                repository, storage, components=None, launcher=lambda _path: None,
                runtime_selector=lambda _root, **_kwargs: True,
            )
            submitted = service.submit(JobSubmission(
                "exec", temp, "project", "local", "workspace",
                ("php", "probe.php"), 20, SourceIdentity("source"),
            ))
            descriptor_path = storage.job_dir(submitted["job_id"]) / "descriptor.json"
            with mock.patch("sandbox.application.context.execute_project", return_value=ExecutionResult(
                        True, 0, "ready", {"stdout": "guest-output", "stderr": "", "reason": {"code": "ready"}})), \
                    mock.patch("sandbox.jobs.supervisor.subprocess", SimpleNamespace(
                        Popen=mock.Mock(side_effect=AssertionError("managed argv reached host Popen")))):
                self.assertEqual(run_descriptor(descriptor_path), 0)
            state = repository.snapshot(submitted["job_id"])
            self.assertEqual(state["lifecycle"], "succeeded")
            self.assertEqual(service.read_output(submitted["job_id"])["data"], "guest-output")
            descriptor = json.loads(descriptor_path.read_text())
            self.assertEqual(descriptor["project_root"], temp)
            self.assertEqual(descriptor["execution_runtime"], "managed_native")
            repository.close()

    def test_runtime_selection_error_fails_submission_before_supervisor_launch(self):
        import tempfile
        from pathlib import Path
        from sandbox.application.job_service import JobService
        from sandbox.jobs.models import JobSubmission, SourceIdentity
        from sandbox.jobs.registry import JobRepository
        from sandbox.jobs.storage import JobStorage

        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0),
                                 components=None, launcher=mock.Mock(),
                                 runtime_selector=mock.Mock(
                                     side_effect=RuntimeError("config unreadable")))
            with self.assertRaisesRegex(RuntimeError, "supervisor_launch_failed"):
                service.submit(JobSubmission(
                    "exec", temp, "project", "local", "workspace", ("php", "probe.php"),
                    20, SourceIdentity("source"),
                ))
            service.launcher.assert_not_called()
            repository.close()

    def test_application_execution_maps_every_entry_path_to_adapter_operations(self):
        from sandbox.application.context import execute_project
        from sandbox.runtimes.base import ExecutionRequest, OperationRequest, OperationResult
        calls = []

        class Service:
            def invoke(self, request):
                calls.append(request)
                return OperationResult(True, request.operation, request.project_root,
                                       "wordpress", {"state": "ready", "exit_code": 0,
                                                     "stdout": "ok", "stderr": ""})

        expected = {"wordpress_cli": "wordpress_cli", "wp_eval": "wordpress_cli",
                    "exec": "exec", "composer": "exec", "plugin_activation": "exec",
                    "phpunit": "test", "durable_job": "exec"}
        with mock.patch("sandbox.application.context.runtime_service", return_value=Service()):
            for entry_path, operation in expected.items():
                result = execute_project({}, ExecutionRequest(
                    "/project", "default", entry_path, ("php", "probe.php"), 30))
                self.assertTrue(result.ok)
                self.assertEqual(calls[-1].operation, operation)
                self.assertIsInstance(calls[-1], OperationRequest)
                self.assertEqual(calls[-1].arguments["execution"].entry_path, entry_path)


if __name__ == "__main__": unittest.main()

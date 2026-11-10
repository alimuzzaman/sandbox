"""Spec 082: remote-only and Docker-less controllers never need local Docker."""

import io
import json
import subprocess
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

from sandbox.config import local_runtime as lr
from sandbox.config.manifest import MACHINE_CONFIG_PROVIDERS, apply_machine_config


class TestLocalRuntimeSelection(unittest.TestCase):
    def test_default_is_docker(self):
        selection = lr.resolve_local_runtime(env={}, machine={})
        self.assertEqual((selection.mode, selection.source), ("docker", "default"))
        self.assertFalse(selection.remote_only)

    def test_machine_switch_selects_none(self):
        selection = lr.resolve_local_runtime(
            env={}, machine={"controller": {"local_runtime": "none"}})
        self.assertEqual((selection.mode, selection.source), ("none", "machine_override"))
        self.assertTrue(selection.remote_only)

    def test_environment_wins_over_machine(self):
        selection = lr.resolve_local_runtime(
            env={lr.ENV_VARIABLE: "docker"},
            machine={"controller": {"local_runtime": "none"}})
        self.assertEqual((selection.mode, selection.source), ("docker", "environment"))
        selection = lr.resolve_local_runtime(env={lr.ENV_VARIABLE: " NONE "}, machine={})
        self.assertEqual(selection.mode, "none")

    def test_empty_environment_value_is_ignored(self):
        selection = lr.resolve_local_runtime(
            env={lr.ENV_VARIABLE: "  "},
            machine={"controller": {"local_runtime": "none"}})
        self.assertEqual(selection.source, "machine_override")

    def test_unknown_values_are_errors_not_docker(self):
        for env, machine in (
            ({lr.ENV_VARIABLE: "podman"}, {}),
            ({}, {"controller": {"local_runtime": "remote"}}),
            ({}, {"controller": {"local_runtime": 0}}),
            ({}, {"controller": "none"}),
            ({}, {"controller": None}),
        ):
            with self.subTest(env=env, machine=machine):
                with self.assertRaises(lr.LocalRuntimeConfigError):
                    lr.resolve_local_runtime(env=env, machine=machine)

    def test_refusal_names_the_switch_and_how_to_clear_it(self):
        message = lr.remote_only_message(
            lr.LocalRuntimeSelection("none", "machine_override"))
        self.assertIn("controller.local_runtime: none", message)
        self.assertIn("remove controller.local_runtime", message)
        self.assertIn(lr.ENV_VARIABLE, message)
        self.assertIn("--remote", message)


class TestMachineManifest(unittest.TestCase):
    def test_provider_is_registered(self):
        providers = {item[0]: item for item in MACHINE_CONFIG_PROVIDERS}
        _key, provider, owner, _order = providers["controller.local_runtime"]
        self.assertEqual(owner, "sandbox.config.local_runtime")
        self.assertEqual(provider({"controller": {"local_runtime": "none"}}), "none")

    def test_absent_block_stays_absent_and_declared_block_is_normalized(self):
        self.assertNotIn("controller", apply_machine_config({}))
        resolved = apply_machine_config({"controller": {"local_runtime": "NONE"}})
        self.assertEqual(resolved["controller"], {"local_runtime": "none"})
        with self.assertRaises(lr.LocalRuntimeConfigError):
            apply_machine_config({"controller": {"local_runtime": "maybe"}})


def _remote_only(value: bool = True):
    return mock.patch.dict("os.environ", {lr.ENV_VARIABLE: "none" if value else "docker"})


class TestRemoteOnlyChokePoints(unittest.TestCase):
    def test_compose_refuses_before_running_docker(self):
        from sandbox.core import _docker
        with _remote_only(), mock.patch.object(_docker, "run") as run, \
                mock.patch.object(_docker.subprocess, "run") as sub_run:
            with self.assertRaises(SystemExit):
                _docker.compose("ps", instance="demo", check=False, capture=True)
        run.assert_not_called()
        sub_run.assert_not_called()

    def test_ensure_preflight_refuses_with_typed_code(self):
        from sandbox.core import _docker
        with _remote_only(), mock.patch.object(_docker, "run") as run:
            result = _docker.docker_daemon_preflight()
        self.assertEqual(result["code"], "local_runtime_none")
        self.assertFalse(result["ok"])
        self.assertIn("controller.local_runtime", result["message"])
        run.assert_not_called()

    def test_inventory_and_proxy_probe_make_no_engine_call(self):
        from sandbox.core import _domains, _instances
        with _remote_only(), mock.patch("subprocess.run") as sub_run:
            self.assertFalse(_instances._instance_running("demo"))
            self.assertFalse(_domains._proxy_container_running())
            self.assertEqual(_domains.stop_proxy_if_idle({})["reason"],
                             "local_runtime_none")
        sub_run.assert_not_called()

    def test_url_proxy_setup_and_domains_up_do_not_start_docker(self):
        from sandbox.core import _domains
        with _remote_only(), mock.patch("subprocess.run") as sub_run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(_domains._ensure_url_proxy({}, quiet=True), (False, {}))
            self.assertFalse(_domains.proxy_up({}))
            _domains._ensure_proxy_up({})
        sub_run.assert_not_called()


class TestGenericComposeAdapter(unittest.TestCase):
    def test_remote_only_refuses_every_operation_before_the_engine(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.compose import ComposeAdapter
        dependencies = mock.Mock()
        adapter = ComposeAdapter(dependencies, mock.Mock())
        for operation in ("start", "stop", "status", "ensure"):
            with self.subTest(operation=operation), _remote_only():
                result = adapter.invoke(OperationRequest("/project", operation))
                self.assertFalse(result.ok)
                self.assertEqual(result.data["error"]["code"], "local_runtime_none")
                self.assertFalse(result.data["mutated"])
        dependencies.process.run.assert_not_called()

    def test_docker_mode_passes_through(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.compose import ComposeAdapter
        with _remote_only(False):
            self.assertIsNone(ComposeAdapter._remote_only_refusal(
                OperationRequest("/project", "start")))


class TestMcpHelpers(unittest.TestCase):
    def _app(self):
        import importlib
        import sys
        from pathlib import Path
        try:
            import httpx  # noqa: F401  (the MCP helper module needs it)
        except ImportError:
            self.skipTest("MCP dependencies are not installed in this environment")
        mcp_root = str(Path(__file__).resolve().parents[1] / "mcp" / "wp-server")
        had_app = "app" in sys.modules
        sys.path.insert(0, mcp_root)
        try:
            module = importlib.import_module("app")
        except ImportError as exc:
            self.skipTest(f"MCP helper module unavailable: {exc}")
        finally:
            sys.path.remove(mcp_root)
            if not had_app:
                self.addCleanup(sys.modules.pop, "app", None)
        return module

    def test_mcp_compose_and_proxy_probe_make_no_engine_call(self):
        app = self._app()
        compose_file = mock.Mock()
        compose_file.exists.return_value = True
        with _remote_only(), mock.patch.object(app, "_compose_file", return_value=compose_file), \
                mock.patch("subprocess.run") as sub_run:
            result = app._compose("ps", instance="demo")
            self.assertFalse(app._proxy_container_running())
        self.assertFalse(result["ok"])
        self.assertIn("controller.local_runtime", result["error"])
        sub_run.assert_not_called()


class TestMissingDocker(unittest.TestCase):
    def test_compose_without_docker_returns_127_or_refuses(self):
        from sandbox.core import _docker
        with _remote_only(False), mock.patch.object(_docker.shutil, "which", return_value=None), \
                mock.patch.object(_docker, "run") as run:
            result = _docker.compose("ps", instance="demo", check=False, capture=True)
            self.assertEqual(result.returncode, 127)
            self.assertIn("controller.local_runtime: none", result.stderr)
            with self.assertRaises(SystemExit):
                _docker.compose("up", "-d", instance="demo")
        run.assert_not_called()

    def test_proxy_probe_and_down_are_safe_without_docker(self):
        from sandbox.core import _domains
        with _remote_only(False), mock.patch.object(_domains.shutil, "which", return_value=None), \
                mock.patch.object(_domains, "_adoption_selected", return_value=False), \
                mock.patch("subprocess.run") as sub_run:
            self.assertFalse(_domains._proxy_container_running())
            self.assertTrue(_domains.proxy_down({}))
            self.assertEqual(_domains.stop_proxy_if_idle({})["reason"], "docker_unavailable")
        sub_run.assert_not_called()

    def test_proxy_probe_survives_os_error(self):
        from sandbox.core import _domains
        with _remote_only(False), mock.patch.object(_domains.shutil, "which", return_value="/x/docker"), \
                mock.patch.object(_domains.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(_domains._proxy_running_state())
            self.assertFalse(_domains._proxy_container_running())


class TestRemoteOnlyDoctor(unittest.TestCase):
    def _run(self, json_mode: bool):
        from sandbox.commands import lifecycle
        args = types.SimpleNamespace(resolved_instance=None, json=json_mode, report_only=False)
        remote = {"host": "example"}
        out = io.StringIO()
        with _remote_only(), mock.patch("subprocess.run") as sub_run, \
                mock.patch.object(lifecycle, "compose") as compose, \
                mock.patch("sandbox.core._remote.list_remotes", return_value={"r1": remote}), \
                mock.patch("sandbox.core._remote.remote_doctor_checks",
                           return_value=[{"label": "ssh reachable", "ok": True, "hint": ""}]), \
                mock.patch.object(lifecycle, "_storage_pressure_doctor_checks", return_value=[]), \
                redirect_stdout(out):
            lifecycle.cmd_doctor({}, args)
        sub_run.assert_not_called()
        compose.assert_not_called()
        return out.getvalue()

    def test_text_report_names_the_mode_and_passes_without_an_instance(self):
        text = self._run(False)
        self.assertIn("local runtime: none (remote-only", text)
        self.assertIn("r1: ssh reachable", text)
        self.assertIn("All checks passed", text)

    def test_json_report_carries_the_mode(self):
        payload = json.loads(self._run(True))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["local_runtime"]["mode"], "none")
        self.assertTrue(payload["local_runtime"]["remote_only"])

    def test_invalid_switch_fails_doctor_clearly(self):
        from sandbox.commands import lifecycle
        args = types.SimpleNamespace(resolved_instance=None, json=True, report_only=False)
        out = io.StringIO()
        with mock.patch.dict("os.environ", {lr.ENV_VARIABLE: "bogus"}), redirect_stdout(out):
            with self.assertRaises(SystemExit):
                lifecycle.cmd_doctor({}, args)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("controller.local_runtime", payload["checks"][0]["label"])


if __name__ == "__main__":
    unittest.main()

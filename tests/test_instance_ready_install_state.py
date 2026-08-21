"""Readiness contracts for a reachable but uninstalled WordPress instance."""
from __future__ import annotations

import contextlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.core import _instances  # noqa: E402


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _State:
    ConfigError = RuntimeError

    def __init__(self, root="/project"):
        self.root = root
        self.registry_put = mock.Mock()
        self.lock_events = []

    @contextlib.contextmanager
    def project_lock(self, value):
        kind = "ports" if str(value).endswith(".instance-ports") else "project"
        self.lock_events.append(f"{kind}:enter")
        try:
            yield
        finally:
            self.lock_events.append(f"{kind}:exit")

    def load_project_config(self, _project, label=None):
        return {"root": self.root, "server": "apache", "wpVersion": "6.7.1"}

    @staticmethod
    def registry_get(_root, label=None):
        return {
            "instance": "fixture", "status": "ready", "server": "apache",
            "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025,
            "url": "http://localhost:8088",
        }


class TestWpCoreInstallState(unittest.TestCase):
    def test_classifier_matrix_is_fail_closed(self):
        cases = (
            ([_Result(0)], _instances._WP_INSTALL_STATE_INSTALLED),
            ([_Result(1), _Result(0, stdout="1\n")],
             _instances._WP_INSTALL_STATE_UNINSTALLED),
            ([_Result(1)], _instances._WP_INSTALL_STATE_UNAVAILABLE),
            ([_Result(1, stdout="diagnostic")], _instances._WP_INSTALL_STATE_UNAVAILABLE),
            ([_Result(1, stderr="database unavailable")],
             _instances._WP_INSTALL_STATE_UNAVAILABLE),
            ([_Result(2)], _instances._WP_INSTALL_STATE_UNAVAILABLE),
            ([_Result(-9)], _instances._WP_INSTALL_STATE_UNAVAILABLE),
            ([_Result("1")], _instances._WP_INSTALL_STATE_UNAVAILABLE),
            ([_Result(1), _Result(1)], _instances._WP_INSTALL_STATE_UNAVAILABLE),
        )
        for results, expected in cases:
            with self.subTest(returncode=results[0].returncode), \
                    mock.patch.object(_instances, "wpcli", side_effect=results) as wpcli:
                self.assertEqual(_instances._wp_core_install_state("fixture"), expected)
            self.assertEqual(wpcli.call_args_list[0].args[0], ["core", "is-installed"])
            self.assertEqual(wpcli.call_args_list[0].kwargs["timeout"], 15)

    def test_db_probe_exception_and_malformed_result_are_unavailable(self):
        with mock.patch.object(
                _instances, "wpcli",
                side_effect=[_Result(1), TimeoutError("bounded timeout")]):
            self.assertEqual(
                _instances._wp_core_install_state("fixture"),
                _instances._WP_INSTALL_STATE_UNAVAILABLE,
            )
        with mock.patch.object(
                _instances, "wpcli",
                side_effect=[_Result(1), _Result(0, stdout=None)]):
            self.assertEqual(
                _instances._wp_core_install_state("fixture"),
                _instances._WP_INSTALL_STATE_UNAVAILABLE,
            )


class TestReadyEnsureInstallState(unittest.TestCase):
    def _ready_patches(self, state):
        return (
            mock.patch.object(_instances, "_core", return_value=state),
            mock.patch.object(_instances, "_desired_source_mounts", return_value=["/plugins"]),
            mock.patch.object(_instances, "attest_source_mounts", return_value={"ok": True}),
            mock.patch.object(_instances, "_instance_reachable", return_value=True),
        )

    def test_installed_site_keeps_fast_path_and_version_drift_is_only_warned(self):
        state = _State()
        existing = state.registry_get("/project")
        with contextlib.ExitStack() as stack:
            for patcher in self._ready_patches(state):
                stack.enter_context(patcher)
            wpcli = stack.enter_context(mock.patch.object(
                _instances, "wpcli", return_value=_Result(0),
            ))
            stack.enter_context(mock.patch.object(
                _instances, "_resolve_port_conflicts", side_effect=lambda cfg: cfg,
            ))
            stack.enter_context(mock.patch.object(
                _instances, "resolve_instances", return_value={"fixture": dict(existing)},
            ))
            warn = stack.enter_context(mock.patch.object(_instances, "_warn_version_drift"))
            stack.enter_context(mock.patch.object(_instances, "_auto_heal_wp_url", return_value=False))
            stack.enter_context(mock.patch.object(
                _instances, "_refresh_registered_url", return_value=existing,
            ))
            result = _instances.ensure_instance({}, "/project", wp_version="6.8.2")

        self.assertEqual(result, existing)
        self.assertEqual(wpcli.call_count, 1)
        warn.assert_called_once()
        self.assertEqual(warn.call_args.args[2]["wpVersion"], "6.8.2")
        state.registry_put.assert_not_called()

    def test_ambiguous_probe_refuses_before_every_write_capable_step(self):
        state = _State()
        writes = (
            "_resolve_port_conflicts", "_write_local_yaml", "write_compose_files",
            "prepare_php_extension_runtime", "_wire_project_plugins", "_wire_project_themes",
        )
        with contextlib.ExitStack() as stack:
            for patcher in self._ready_patches(state):
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                _instances, "wpcli", return_value=_Result(
                    1, stderr="database password=private",
                ),
            ))
            for name in writes:
                stack.enter_context(mock.patch.object(
                    _instances, name, side_effect=AssertionError(name),
                ))
            result = _instances.ensure_instance({}, "/project")

        self.assertEqual(result["error"]["code"], "instance_install_state_unavailable")
        self.assertFalse(result["mutated"])
        self.assertNotIn("private", result["error"]["message"])
        state.registry_put.assert_not_called()

    def test_probe_happens_before_global_port_lock(self):
        state = _State()
        existing = state.registry_get("/project")
        events = state.lock_events

        def record_probe(instance):
            events.append("probe")
            return _instances._WP_INSTALL_STATE_UNAVAILABLE

        with contextlib.ExitStack() as stack:
            for patcher in self._ready_patches(state):
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                _instances, "_wp_core_install_state", side_effect=record_probe,
            ))
            result = _instances.ensure_instance({}, "/project")

        self.assertEqual(result["error"]["code"], "instance_install_state_unavailable")
        self.assertLess(events.index("project:enter"), events.index("probe"))
        self.assertNotIn("ports:enter", events)

    def test_uninstalled_state_reuses_current_override_and_install_path(self):
        state = _State()

        class InstallReached(Exception):
            pass

        captured = {}

        def build(_cfg, _name, _root, pconf, _ports, _server):
            captured.update(pconf)
            return {}

        lifecycle = types.ModuleType("sandbox.commands.lifecycle")
        lifecycle.cmd_up = mock.Mock()
        lifecycle.cmd_install = mock.Mock(side_effect=InstallReached)
        with contextlib.ExitStack() as stack:
            for patcher in self._ready_patches(state):
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                _instances, "wpcli",
                side_effect=[_Result(1), _Result(0, stdout="1\n")],
            ))
            stack.enter_context(mock.patch.object(
                _instances, "_resolve_port_conflicts", side_effect=lambda cfg: cfg,
            ))
            stack.enter_context(mock.patch.object(
                _instances, "resolve_instances", return_value={
                    "fixture": state.registry_get("/project"),
                },
            ))
            stack.enter_context(mock.patch.object(_instances, "_build_instance_block", side_effect=build))
            stack.enter_context(mock.patch.object(
                _instances, "prepare_php_extension_runtime", return_value=None,
            ))
            stack.enter_context(mock.patch.object(_instances, "_local_yaml", return_value={}))
            stack.enter_context(mock.patch.object(_instances, "_write_local_yaml"))
            stack.enter_context(mock.patch.object(_instances, "write_compose_files"))
            stack.enter_context(mock.patch.object(_instances, "load_config", return_value={}))
            import sandbox.commands
            with mock.patch.dict(sys.modules, {"sandbox.commands.lifecycle": lifecycle}), \
                    mock.patch.object(sandbox.commands, "lifecycle", lifecycle, create=True):
                with self.assertRaises(InstallReached):
                    _instances.ensure_instance({}, "/project", wp_version="6.8.2")

        lifecycle.cmd_install.assert_called_once()
        self.assertEqual(captured["wpVersion"], "6.8.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)

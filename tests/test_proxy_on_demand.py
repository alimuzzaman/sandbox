"""Spec 082: the clean-URL proxy runs only while a local instance needs it."""

import io
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from sandbox.application.proxy_demand import ProxyDemandFacts, decide
from sandbox.application.clean_url_provider import ProviderSelection


DEFAULT = ProviderSelection("sandbox-caddy", "default", False, False)
DISABLED = ProviderSelection("disabled", "machine_override", False, True)
ADOPTED = ProviderSelection("herd-valet", "machine_override", True, False)


class TestDecision(unittest.TestCase):
    def test_table(self):
        rows = [
            (ProxyDemandFacts(False, (), 0), "none", "proxy_not_running"),
            (ProxyDemandFacts(None, (), 0), "keep", "proxy_state_unknown"),
            (ProxyDemandFacts(True, None, 0), "keep", "instances_unknown"),
            (ProxyDemandFacts(True, ("sandbox-a",), 0), "keep", "instances_running"),
            (ProxyDemandFacts(True, ("sandbox-a",), 0, True), "keep", "instances_running"),
            (ProxyDemandFacts(True, (), None, True), "stop", "clean_urls_disabled"),
            (ProxyDemandFacts(True, (), None), "keep", "wake_routes_unknown"),
            (ProxyDemandFacts(True, (), 2), "keep", "wake_routes"),
            (ProxyDemandFacts(True, (), 0), "stop", "idle"),
        ]
        for facts, action, reason in rows:
            with self.subTest(facts=facts):
                self.assertEqual(tuple(decide(facts)), (action, reason))


class TestRestartPolicy(unittest.TestCase):
    def test_proxy_does_not_restart_itself(self):
        from sandbox.core import _domains
        text = _domains.render_proxy_compose()
        self.assertIn('restart: "no"', text)
        self.assertNotIn("unless-stopped", text)

    def test_old_policy_compose_is_rewritten_and_recreated_on_apply(self):
        from sandbox.core import _domains
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "proxy.yml"
            compose_file.write_text(_domains.render_proxy_compose().replace(
                'restart: "no"', "restart: unless-stopped"))
            up = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(_domains, "PROXY_COMPOSE", compose_file), \
                    mock.patch.object(_domains, "_proxy_container_running", return_value=True), \
                    mock.patch.object(_domains, "_proxy_compose_up", return_value=up) as compose_up, \
                    mock.patch.object(_domains, "_dns_flush"), \
                    mock.patch.object(_domains.subprocess, "run") as sub_run:
                self.assertEqual(_domains.proxy_apply(), (True, ""))
            compose_up.assert_called_once_with()
            sub_run.assert_not_called()  # no hot reload of the old container
            self.assertIn('restart: "no"', compose_file.read_text())


def _env_docker():
    return mock.patch.dict("os.environ", {"SANDBOX_LOCAL_RUNTIME": "docker"})


class TestStopProxyIfIdle(unittest.TestCase):
    def setUp(self):
        from sandbox.core import _domains
        self.d = _domains
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [
            _env_docker(),
            mock.patch.object(_domains, "PROXY_DIR", Path(self.tmp.name)),
            mock.patch.object(_domains.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(_domains, "clean_url_selection", return_value=DEFAULT),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, *, running=True, projects=(), wake=0, stop_rc=0):
        stop = subprocess.CompletedProcess([], stop_rc, "", "")
        with mock.patch.object(self.d, "_proxy_running_state", return_value=running), \
                mock.patch.object(self.d, "_running_sandbox_projects", return_value=projects), \
                mock.patch.object(self.d, "_wake_route_count", return_value=wake), \
                mock.patch.object(self.d.subprocess, "run", return_value=stop) as sub_run, \
                redirect_stdout(io.StringIO()) as out:
            result = self.d.stop_proxy_if_idle({})
        return result, sub_run, out.getvalue()

    def test_stops_an_idle_proxy_once_and_says_so(self):
        result, sub_run, out = self._run()
        self.assertEqual(result, {"action": "stop", "reason": "idle", "stopped": True})
        argv = sub_run.call_args.args[0]
        self.assertEqual(argv[:3], ["docker", "compose", "-p"])
        self.assertEqual(argv[-2:], ["stop", "proxy"])
        self.assertIn("stopped the clean-URL proxy", out)

    def test_rerun_after_stop_is_a_silent_no_op(self):
        result, sub_run, out = self._run(running=False)
        self.assertEqual(result["action"], "none")
        sub_run.assert_not_called()
        self.assertEqual(out, "")

    def test_keeps_while_any_instance_runs(self):
        result, sub_run, _ = self._run(projects=("sandbox-demo",))
        self.assertEqual(result["reason"], "instances_running")
        sub_run.assert_not_called()

    def test_keeps_for_wake_routes_and_unknown_state(self):
        for kwargs, reason in (({"wake": 1}, "wake_routes"),
                               ({"wake": None}, "wake_routes_unknown"),
                               ({"projects": None}, "instances_unknown"),
                               ({"running": None}, "proxy_state_unknown")):
            with self.subTest(kwargs=kwargs):
                result, sub_run, _ = self._run(**kwargs)
                self.assertEqual((result["action"], result["reason"]), ("keep", reason))
                sub_run.assert_not_called()

    def test_failed_stop_reports_keep(self):
        result, _, out = self._run(stop_rc=1)
        self.assertEqual((result["action"], result["reason"]), ("keep", "stop_failed"))
        self.assertEqual(out, "")

    def test_disabled_clean_urls_count_as_no_demand(self):
        with mock.patch.object(self.d, "clean_url_selection", return_value=DISABLED):
            result, sub_run, _ = self._run(wake=3)
        self.assertEqual(result["reason"], "clean_urls_disabled")
        sub_run.assert_called_once()

    def test_never_touches_an_adopted_ingress(self):
        with mock.patch.object(self.d, "clean_url_selection", return_value=ADOPTED):
            result, sub_run, _ = self._run()
        self.assertEqual(result["reason"], "adopted_ingress")
        sub_run.assert_not_called()

    def test_start_in_progress_keeps_the_proxy(self):
        with self.d.proxy_start_lease():
            result, sub_run, _ = self._run()
        self.assertEqual((result["action"], result["reason"]), ("keep", "start_in_progress"))
        sub_run.assert_not_called()
        # Once the start finished, the next check may stop it.
        result, _, _ = self._run()
        self.assertTrue(result["stopped"])


class TestDemandFacts(unittest.TestCase):
    def test_running_projects_exclude_the_proxy_and_foreign_stacks(self):
        from sandbox.core import _domains
        ps = subprocess.CompletedProcess(
            [], 0, "sandbox-proxy\nsandbox-demo\n\nother-app\nsandbox-demo\n", "")
        with mock.patch.object(_domains.subprocess, "run", return_value=ps) as sub_run:
            self.assertEqual(_domains._running_sandbox_projects(), ("sandbox-demo",))
        self.assertIn("status=running", sub_run.call_args.args[0])

    def test_engine_errors_are_unknown(self):
        from sandbox.core import _domains
        for effect in (FileNotFoundError(), subprocess.TimeoutExpired("docker", 10),
                       subprocess.CompletedProcess([], 1, "", "boom")):
            with self.subTest(effect=effect):
                kwargs = ({"return_value": effect}
                          if isinstance(effect, subprocess.CompletedProcess)
                          else {"side_effect": effect})
                with mock.patch.object(_domains.subprocess, "run", **kwargs):
                    self.assertIsNone(_domains._running_sandbox_projects())

    def test_catalog_errors_are_unknown(self):
        from sandbox.core import _domains
        with mock.patch.object(_domains, "resolve_instances", side_effect=ValueError):
            self.assertIsNone(_domains._wake_route_count({}))


class TestEnsureProxyUpGates(unittest.TestCase):
    def test_only_the_default_provider_starts(self):
        from sandbox.core import _domains
        for selection, expected in ((DEFAULT, 1), (DISABLED, 0), (ADOPTED, 0)):
            with self.subTest(selection=selection.provider), _env_docker(), \
                    mock.patch.object(_domains.shutil, "which", return_value="/usr/bin/docker"), \
                    mock.patch.object(_domains, "clean_url_selection", return_value=selection), \
                    mock.patch.object(_domains, "_lo0_alias_present", return_value=True), \
                    mock.patch.object(_domains, "_distinct_tlds", return_value={"tst"}), \
                    mock.patch.object(_domains, "_resolver_present", return_value=True), \
                    mock.patch.object(_domains, "_proxy_container_running", return_value=False), \
                    mock.patch.object(_domains, "regen_caddyfile"), \
                    mock.patch.object(_domains, "reload_proxy") as reload_proxy:
                _domains._ensure_proxy_up({})
            self.assertEqual(reload_proxy.call_count, expected)

    def test_running_proxy_is_never_reloaded(self):
        from sandbox.core import _domains
        with _env_docker(), \
                mock.patch.object(_domains.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(_domains, "clean_url_selection", return_value=DEFAULT), \
                mock.patch.object(_domains, "_lo0_alias_present", return_value=True), \
                mock.patch.object(_domains, "_distinct_tlds", return_value={"tst"}), \
                mock.patch.object(_domains, "_resolver_present", return_value=True), \
                mock.patch.object(_domains, "_proxy_container_running", return_value=True), \
                mock.patch.object(_domains, "reload_proxy") as reload_proxy:
            _domains._ensure_proxy_up({})
        reload_proxy.assert_not_called()

    def test_disabled_never_provisions_the_url_proxy(self):
        from sandbox.core import _domains
        with _env_docker(), \
                mock.patch.object(_domains, "clean_url_selection", return_value=DISABLED), \
                mock.patch.object(_domains, "proxy_apply") as apply, \
                mock.patch.object(_domains.subprocess, "run") as sub_run:
            self.assertEqual(_domains._ensure_url_proxy({}, quiet=True), (False, {}))
        apply.assert_not_called()
        sub_run.assert_not_called()


class TestLifecycleWiring(unittest.TestCase):
    def test_down_runs_the_idle_check(self):
        from sandbox.commands import lifecycle
        args = types.SimpleNamespace(resolved_instance="demo")
        core = mock.Mock()
        core.registry_find_instance.return_value = None
        with mock.patch.object(lifecycle, "_core", return_value=core), \
                mock.patch.object(lifecycle, "_is_herd_instance", return_value=False), \
                mock.patch.object(lifecycle, "compose") as compose, \
                mock.patch.object(lifecycle, "stop_proxy_if_idle") as idle:
            lifecycle.cmd_down({"k": 1}, args)
        compose.assert_called_once_with("down", instance="demo")
        idle.assert_called_once_with({"k": 1})

    def test_generic_down_runs_the_idle_check(self):
        from sandbox.commands import lifecycle
        args = types.SimpleNamespace(resolved_instance="demo")
        core = mock.Mock()
        core.registry_find_instance.return_value = {"kind": "compose", "root": "/p"}
        service = mock.Mock()
        service.invoke.return_value = types.SimpleNamespace(ok=True, data={})
        with mock.patch.object(lifecycle, "_core", return_value=core), \
                mock.patch.object(lifecycle, "runtime_service", return_value=service), \
                mock.patch.object(lifecycle, "stop_proxy_if_idle") as idle, \
                redirect_stdout(io.StringIO()):
            lifecycle.cmd_down({}, args)
        idle.assert_called_once_with({})

    def test_herd_down_never_touches_the_proxy(self):
        from sandbox.commands import lifecycle
        args = types.SimpleNamespace(resolved_instance="demo")
        core = mock.Mock()
        core.registry_find_instance.return_value = None
        with mock.patch.object(lifecycle, "_core", return_value=core), \
                mock.patch.object(lifecycle, "_is_herd_instance", return_value=True), \
                mock.patch.object(lifecycle, "stop_proxy_if_idle") as idle, \
                redirect_stdout(io.StringIO()):
            lifecycle.cmd_down({}, args)
        idle.assert_not_called()

    def test_instance_delete_route_refresh_runs_the_idle_check(self):
        from sandbox.commands import instances_cmd
        with mock.patch.object(instances_cmd, "regen_caddyfile"), \
                mock.patch.object(instances_cmd, "load_config", return_value={}), \
                mock.patch.object(instances_cmd, "_proxy_container_running", return_value=False), \
                mock.patch.object(instances_cmd, "stop_proxy_if_idle") as idle:
            instances_cmd._refresh_caddy_routes_after_instance_delete()
        idle.assert_called_once_with()

    def test_failed_route_refresh_still_runs_the_idle_check(self):
        from sandbox.commands import instances_cmd
        with mock.patch.object(instances_cmd, "regen_caddyfile", side_effect=OSError), \
                mock.patch.object(instances_cmd, "load_config", return_value={}), \
                mock.patch.object(instances_cmd, "stop_proxy_if_idle") as idle, \
                redirect_stdout(io.StringIO()):
            instances_cmd._refresh_caddy_routes_after_instance_delete()
        idle.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

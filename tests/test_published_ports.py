"""Published ports: loopback by default, every interface only on opt-in.

Covers the three places the setting has to reach: the instance block built
from the project's `exposeToNetwork`, the rendered per-instance Compose file
(every published port of every server), and `sb status`.

    .cli-venv/bin/python -m unittest tests.test_published_ports -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.cli  # noqa: E402,F401  (registers command modules on import)
import sandbox.core as core  # noqa: E402
import sandbox.core._docker as docker  # noqa: E402
import sandbox.core._instances as instances  # noqa: E402
import sandbox_core  # noqa: E402
from sandbox.commands import lifecycle  # noqa: E402

SERVERS = ("apache", "nginx", "litespeed")
PORTS = {"wordpress_port": 8300, "db_port": 3500, "mailpit_port": 8301}


def _published(document: dict) -> dict[str, list[str]]:
    return {name: service["ports"] for name, service in document["services"].items()
            if service.get("ports")}


class _RenderCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-ports-")
        self.old = os.environ.get("SANDBOX_RUNTIME")
        os.environ["SANDBOX_RUNTIME"] = self.tmp
        docker._PUBLISH_GATEWAY_CACHE.clear()
        self.addCleanup(docker._PUBLISH_GATEWAY_CACHE.clear)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SANDBOX_RUNTIME", None)
        else:
            os.environ["SANDBOX_RUNTIME"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inst_cfg(self, server: str, **extra) -> dict:
        sandbox_core.registry_put(str(Path(self.tmp) / f"proj-{server}"), instance="ti",
                                  server=server, **PORTS)
        cfg = core.resolve_instances({})["ti"]
        cfg.update(extra)
        return cfg

    def _render(self, server: str, **extra) -> dict:
        return yaml.safe_load(core.render_compose(
            "ti", self._inst_cfg(server, **extra), Path("/tmp/plugins-host")))


class TestDefaultBindsLoopback(_RenderCase):
    def test_every_published_port_of_every_server_is_on_loopback(self):
        expected_web = {"apache": "wp", "nginx": "nginx", "litespeed": "wp"}
        with mock.patch.object(docker.sys, "platform", "darwin"):
            for server in SERVERS:
                with self.subTest(server=server):
                    published = _published(self._render(server))
                    self.assertEqual(set(published),
                                     {expected_web[server], "db", "mailpit"})
                    for ports in published.values():
                        for entry in ports:
                            self.assertTrue(entry.startswith("127.0.0.1:"), entry)
                    web = ["127.0.0.1:8300:80"]
                    if server == "litespeed":
                        web.append("127.0.0.1:9300:7080")
                    self.assertEqual(published[expected_web[server]], web)
                    self.assertEqual(published["db"], ["127.0.0.1:3500:3306"])
                    self.assertEqual(published["mailpit"],
                                     ["127.0.0.1:8301:8025", "127.0.0.1::1025"])

    def test_php_fpm_still_publishes_nothing(self):
        with mock.patch.object(docker.sys, "platform", "darwin"):
            self.assertNotIn("ports", self._render("nginx")["services"]["wp"])

    def test_containers_keep_host_gateway_and_service_names(self):
        """Loopback publishing does not change container-to-container or
        container-to-host wiring: services still reach db:3306 and
        mailpit:1025 on the Compose network, and host.docker.internal."""
        with mock.patch.object(docker.sys, "platform", "darwin"):
            for server in SERVERS:
                with self.subTest(server=server):
                    services = self._render(server)["services"]
                    self.assertIn("host.docker.internal:host-gateway",
                                  services["wp"]["extra_hosts"])
                    if server != "litespeed":
                        self.assertEqual(
                            services["wp"]["environment"]["WORDPRESS_DB_HOST"], "db:3306")

    def test_linux_adds_the_docker_bridge_gateway_and_nothing_wider(self):
        with mock.patch.object(docker.sys, "platform", "linux"), \
                mock.patch.object(docker, "local_runtime_remote_only", return_value=False), \
                mock.patch.object(docker, "_default_bridge_facts",
                                  return_value=("172.17.0.1", "docker0")), \
                mock.patch.object(docker, "_host_address_is_local", return_value=True):
            published = _published(self._render("nginx"))
        self.assertEqual(published["nginx"], ["127.0.0.1:8300:80", "172.17.0.1:8300:80"])
        self.assertEqual(published["db"], ["127.0.0.1:3500:3306", "172.17.0.1:3500:3306"])
        self.assertEqual(published["mailpit"], [
            "127.0.0.1:8301:8025", "127.0.0.1::1025",
            "172.17.0.1:8301:8025", "172.17.0.1::1025"])

    def test_linux_gateway_the_host_does_not_own_is_left_out(self):
        with mock.patch.object(docker.sys, "platform", "linux"), \
                mock.patch.object(docker, "local_runtime_remote_only", return_value=False), \
                mock.patch.object(docker, "_default_bridge_facts",
                                  return_value=("172.17.0.1", "docker0")), \
                mock.patch.object(docker, "_host_address_is_local", return_value=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(docker.publish_hosts({}), ["127.0.0.1"])
        self.assertIn("does not own the gateway 172.17.0.1", err.getvalue())

    def test_a_miss_expires_so_a_later_engine_is_found(self):
        with mock.patch.object(docker.sys, "platform", "linux"), \
                mock.patch.object(docker, "local_runtime_remote_only", return_value=False), \
                mock.patch.object(docker, "_host_address_is_local", return_value=True), \
                contextlib.redirect_stderr(io.StringIO()):
            with mock.patch.object(docker, "_default_bridge_facts", return_value=None):
                self.assertEqual(docker.publish_hosts({}), ["127.0.0.1"])
            docker._PUBLISH_GATEWAY_CACHE["at"] -= docker._GATEWAY_MISS_TTL + 1
            with mock.patch.object(docker, "_default_bridge_facts",
                                   return_value=("172.17.0.1", "docker0")):
                self.assertEqual(docker.publish_hosts({}), ["127.0.0.1", "172.17.0.1"])

    def test_remote_only_controller_never_asks_the_engine(self):
        with mock.patch.object(docker.sys, "platform", "linux"), \
                mock.patch.object(docker, "local_runtime_remote_only", return_value=True), \
                mock.patch.object(docker, "_default_bridge_facts") as facts:
            self.assertEqual(docker.publish_hosts({}), ["127.0.0.1"])
        facts.assert_not_called()


class TestExposeToNetworkOptIn(_RenderCase):
    def test_opt_in_publishes_as_before_on_every_interface(self):
        expected_web = {"apache": "wp", "nginx": "nginx", "litespeed": "wp"}
        with mock.patch.object(docker, "_loopback_publish_hosts") as loopback:
            for server in SERVERS:
                with self.subTest(server=server):
                    published = _published(self._render(server, expose_to_network=True))
                    web = ["8300:80"] + (["9300:7080"] if server == "litespeed" else [])
                    self.assertEqual(published[expected_web[server]], web)
                    self.assertEqual(published["db"], ["3500:3306"])
                    self.assertEqual(published["mailpit"], ["8301:8025", "1025"])
        loopback.assert_not_called()

    def test_only_an_explicit_true_counts(self):
        for value in (None, False, "true", 1):
            with self.subTest(value=value):
                self.assertFalse(core.instance_exposed_to_network(
                    {"expose_to_network": value}))
        self.assertTrue(core.instance_exposed_to_network({"expose_to_network": True}))

    def test_resolved_instance_carries_the_flag_only_when_on(self):
        sandbox_core.registry_put(str(Path(self.tmp) / "proj"), instance="ti",
                                  server="nginx", **PORTS)
        self.assertNotIn("expose_to_network", core.resolve_instances({})["ti"])
        cfg = {"instances": {"ti": {"expose_to_network": True}}}
        self.assertIs(core.resolve_instances(cfg)["ti"]["expose_to_network"], True)
        cfg = {"instances": {"ti": {"expose_to_network": False}}}
        self.assertNotIn("expose_to_network", core.resolve_instances(cfg)["ti"])


class TestInstanceBlock(unittest.TestCase):
    """`exposeToNetwork` is rebuilt from the project on every ensure/apply."""

    def _block(self, pconf, previous=None):
        local = {"instances": {"project": previous}} if previous else {}
        with mock.patch.object(instances, "_local_yaml", return_value=local):
            return instances._build_instance_block(
                {}, "project", "/tmp/project", {"root": "/tmp/project", **pconf},
                dict(PORTS), "nginx")

    def test_declared_true_lands_in_the_block(self):
        self.assertIs(self._block({"exposeToNetwork": True})["expose_to_network"], True)

    def test_omitted_or_false_leaves_it_out(self):
        self.assertNotIn("expose_to_network", self._block({}))
        self.assertNotIn("expose_to_network", self._block({"exposeToNetwork": False}))

    def test_removing_the_key_turns_exposure_off_again(self):
        block = self._block({}, previous={"expose_to_network": True})
        self.assertNotIn("expose_to_network", block)

    def test_a_non_boolean_value_never_exposes(self):
        """The loader refuses it first (TestOptInIsProjectOwned); the block
        builder itself only ever honours an explicit true."""
        for value in ("yes", 1, "0.0.0.0"):
            with self.subTest(value=value):
                self.assertNotIn("expose_to_network", self._block({"exposeToNetwork": value}))


def _ps_rows(*rows) -> str:
    """`compose ps --format json` lines: (service, url, published, target)."""
    out = []
    for service, url, published, target in rows:
        out.append(json.dumps({"Service": service, "State": "running", "Publishers": [
            {"URL": url, "TargetPort": target, "PublishedPort": published, "Protocol": "tcp"}]}))
    return "\n".join(out)


OLD_WILDCARD_ROWS = (("nginx", "0.0.0.0", 8300, 80), ("db", "0.0.0.0", 3500, 3306),
                     ("mailpit", "0.0.0.0", 8301, 8025))
LOOPBACK_ROWS = (("nginx", "127.0.0.1", 8300, 80), ("db", "127.0.0.1", 3500, 3306),
                 ("mailpit", "127.0.0.1", 8301, 8025))


class TestStatusReadsRunningContainers(unittest.TestCase):
    """M1: status reports what the running containers publish, not only config."""

    def _containers(self, rows):
        return [json.loads(line) for line in _ps_rows(*rows).splitlines()]

    def test_old_containers_on_every_interface_are_reported_not_hidden(self):
        exposure = lifecycle._port_exposure(
            {"server": "nginx", **PORTS}, self._containers(OLD_WILDCARD_ROWS), instance="fixture")
        self.assertEqual(exposure["configured"]["scope"], "loopback")
        self.assertEqual(exposure["published"]["scope"], "network")
        self.assertIs(exposure["in_sync"], False)
        self.assertEqual(exposure["next_command"], "./sb up --instance fixture")

    def test_matching_loopback_containers_are_in_sync(self):
        with mock.patch.object(lifecycle, "publish_hosts", return_value=["127.0.0.1"]):
            exposure = lifecycle._port_exposure(
                {"server": "nginx", **PORTS}, self._containers(LOOPBACK_ROWS), instance="fixture")
        self.assertEqual(exposure["published"]["scope"], "loopback")
        self.assertIs(exposure["in_sync"], True)
        self.assertIsNone(exposure["next_command"])

    def test_no_container_data_is_unknown_never_local_only(self):
        for containers in (None, []):
            with self.subTest(containers=containers):
                exposure = lifecycle._port_exposure(
                    {"server": "nginx", **PORTS}, containers, instance="fixture")
                self.assertEqual(exposure["published"]["scope"], "unknown")
                self.assertIsNone(exposure["in_sync"])

    def test_opt_in_not_yet_applied_is_reported(self):
        exposure = lifecycle._port_exposure(
            {"server": "nginx", "expose_to_network": True, **PORTS},
            self._containers(LOOPBACK_ROWS), instance="fixture")
        self.assertEqual(exposure["configured"]["scope"], "network")
        self.assertEqual(exposure["published"]["scope"], "loopback")
        self.assertIs(exposure["in_sync"], False)

    def test_opt_in_mismatch_names_the_ports_still_on_local_addresses(self):
        mixed = (("nginx", "0.0.0.0", 8300, 80), ("db", "127.0.0.1", 3500, 3306),
                 ("mailpit", "127.0.0.1", 8301, 8025))
        exposure = lifecycle._port_exposure(
            {"server": "nginx", "expose_to_network": True, **PORTS},
            self._containers(mixed), instance="fixture")
        line = lifecycle._port_exposure_line(exposure)
        self.assertIn("db 3500 (127.0.0.1)", line)
        self.assertIn("mailpit 8301 (127.0.0.1)", line)
        self.assertNotIn("nginx 8300", line)
        self.assertIn("./sb up --instance fixture", line)

    def test_json_status_uses_the_container_rows(self):
        ps = types.SimpleNamespace(stdout=_ps_rows(*OLD_WILDCARD_ROWS), returncode=0)
        with mock.patch.object(lifecycle, "_core", return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: None)), \
                mock.patch.object(lifecycle, "_is_herd_instance", return_value=False), \
                mock.patch.object(lifecycle, "resolve_instances",
                                  return_value={"fixture": {"server": "nginx", **PORTS}}), \
                mock.patch.object(lifecycle, "compose", return_value=ps), \
                mock.patch.object(lifecycle, "site_url", return_value="http://localhost:8300"), \
                mock.patch.object(lifecycle, "php_extension_status", return_value=None):
            payload = lifecycle._status_json_payload({}, "fixture")
        self.assertEqual(payload["port_exposure"]["published"]["scope"], "network")
        self.assertIs(payload["port_exposure"]["in_sync"], False)

    def _text(self, instance_cfg, ps_stdout):
        args = types.SimpleNamespace(json=False, resolved_instance="fixture")
        output = io.StringIO()
        missing = Path(tempfile.gettempdir()) / "sb-ports-no-such-focus-file"
        ps = types.SimpleNamespace(stdout=ps_stdout, returncode=0)
        with mock.patch.object(lifecycle, "_remote_lifecycle", return_value=None), \
                mock.patch.object(lifecycle, "_core", return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: None)), \
                mock.patch.object(lifecycle, "_is_herd_instance", return_value=False), \
                mock.patch.object(lifecycle, "resolve_instances", return_value=instance_cfg), \
                mock.patch.object(lifecycle, "compose", return_value=ps), \
                mock.patch.object(lifecycle, "active_project_file", return_value=missing), \
                mock.patch.object(lifecycle, "focus_file", return_value=missing), \
                mock.patch.object(lifecycle, "mcp_server_name", return_value="sandbox"), \
                mock.patch.object(lifecycle, "_bridge_token_for", return_value=None), \
                mock.patch.object(lifecycle, "php_extension_status", return_value=None), \
                mock.patch.object(lifecycle, "publish_hosts", return_value=["127.0.0.1"]), \
                contextlib.redirect_stdout(output):
            lifecycle.cmd_status({}, args)
        return output.getvalue()

    def test_text_status_names_the_mismatch_and_the_command(self):
        text = self._text({"fixture": {"server": "nginx", **PORTS}},
                          _ps_rows(*OLD_WILDCARD_ROWS))
        self.assertNotIn("Ports: local only", text)
        self.assertIn("still publish", text)
        self.assertIn("on every network interface", text)
        self.assertIn("./sb up --instance fixture", text)

    def test_text_status_says_not_known_without_container_data(self):
        text = self._text({"fixture": {"server": "nginx", **PORTS}}, "")
        self.assertNotIn("Ports: local only", text)
        self.assertIn("Ports: not known", text)


class TestStatusShowsExposure(unittest.TestCase):
    """In-sync cases: config and running containers agree."""

    def _instance(self, **extra):
        return {"fixture": {"server": "nginx", **PORTS, **extra}}

    def _json(self, instance_cfg, rows):
        ps = types.SimpleNamespace(stdout=_ps_rows(*rows), returncode=0)
        with mock.patch.object(lifecycle, "_core", return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: None)), \
                mock.patch.object(lifecycle, "_is_herd_instance", return_value=False), \
                mock.patch.object(lifecycle, "resolve_instances", return_value=instance_cfg), \
                mock.patch.object(lifecycle, "compose", return_value=ps), \
                mock.patch.object(lifecycle, "site_url", return_value="http://localhost:8300"), \
                mock.patch.object(lifecycle, "php_extension_status", return_value=None), \
                mock.patch.object(lifecycle, "publish_hosts", return_value=["127.0.0.1"]):
            return lifecycle._status_json_payload({}, "fixture")

    def test_json_reports_loopback_by_default(self):
        exposure = self._json(self._instance(), LOOPBACK_ROWS)["port_exposure"]
        self.assertEqual(exposure["configured"], {"scope": "loopback", "hosts": ["127.0.0.1"]})
        self.assertEqual(exposure["published"]["scope"], "loopback")
        self.assertEqual(len(exposure["published"]["ports"]), 3)
        self.assertIs(exposure["in_sync"], True)

    def test_json_reports_the_opt_in(self):
        exposure = self._json(self._instance(expose_to_network=True),
                              OLD_WILDCARD_ROWS)["port_exposure"]
        self.assertEqual(exposure["configured"], {"scope": "network", "hosts": ["*"]})
        self.assertEqual(exposure["published"]["scope"], "network")
        self.assertIs(exposure["in_sync"], True)

    def test_public_status_json_keeps_the_field(self):
        payload = self._json(self._instance(expose_to_network=True), OLD_WILDCARD_ROWS)
        public = lifecycle._public_status_json(payload)["port_exposure"]
        self.assertEqual(public["configured"]["scope"], "network")

    def test_herd_has_no_published_ports(self):
        self.assertIsNone(lifecycle._port_exposure({"server": "herd"}, [], instance="h"))

    def _text(self, instance_cfg, rows):
        return TestStatusReadsRunningContainers._text(self, instance_cfg, _ps_rows(*rows))

    def test_text_status_says_local_only_by_default(self):
        self.assertIn("Ports: local only (127.0.0.1)",
                      self._text(self._instance(), LOOPBACK_ROWS))

    def test_text_status_names_the_opt_in(self):
        text = self._text(self._instance(expose_to_network=True), OLD_WILDCARD_ROWS)
        self.assertIn("Ports: published on every network interface (exposeToNetwork: true)",
                      text)


class TestOptInIsProjectOwned(unittest.TestCase):
    """M2 + S1 + S4: where `exposeToNetwork` may come from, and when it is checked."""

    def _load(self, project_doc, *, user_doc=None, under=None, override_doc=None):
        base = Path(tempfile.mkdtemp(dir=Path.home(), prefix="sb-expose-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / (under or "") / "proj"
        root.mkdir(parents=True)
        (root / "sandbox.config.json").write_text(json.dumps(project_doc))
        if override_doc is not None:
            (root / "sandbox.config.override.json").write_text(json.dumps(override_doc))
        user = base / "user-global.json"
        user.write_text(json.dumps(user_doc or {}))
        from sandbox.config import wordpress as wordpress_schema
        wordpress_schema._EXPOSE_NOTICES.clear()
        err = io.StringIO()
        env = {"SANDBOX_USER_CONFIG": str(user), "SANDBOX_HOME": str(base / "home")}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(err):
            result = sandbox_core.load_project_config(root)
        return result, err.getvalue(), base

    def test_machine_wide_value_is_ignored_with_one_line(self):
        result, err, _ = self._load({}, user_doc={"exposeToNetwork": True})
        self.assertNotIn("exposeToNetwork", result)
        self.assertIn("exposeToNetwork", err)
        self.assertIn("ignored", err)

    def test_project_override_value_is_honoured(self):
        result, _, _ = self._load({}, override_doc={"exposeToNetwork": True})
        self.assertIs(result["exposeToNetwork"], True)

    def test_a_non_boolean_value_is_refused_by_the_loader(self):
        with self.assertRaises((sandbox_core.ConfigError, ValueError)) as raised:
            self._load({"exposeToNetwork": "yes"})
        self.assertEqual(getattr(raised.exception, "code", None), "expose_to_network_invalid")

    def test_ensure_refuses_a_bad_value_before_writing_anything(self):
        base = Path(tempfile.mkdtemp(dir=Path.home(), prefix="sb-expose-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "proj"
        root.mkdir()
        (root / "sandbox.config.json").write_text(json.dumps({"exposeToNetwork": "yes"}))
        user = base / "user-global.json"
        user.write_text("{}")
        runtime = base / "runtime"
        runtime.mkdir()
        env = {"SANDBOX_USER_CONFIG": str(user), "SANDBOX_RUNTIME": str(runtime)}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises((sandbox_core.ConfigError, ValueError)) as raised:
                core.ensure_instance({}, str(root))
            self.assertEqual(raised.exception.code, "expose_to_network_invalid")
            self.assertEqual(sandbox_core.registry_all(), {})

    def test_a_deployed_copy_never_exposes(self):
        base = Path(tempfile.mkdtemp(dir=Path.home(), prefix="sb-expose-"))
        self.addCleanup(shutil.rmtree, base, True)
        home = base / "home"
        root = home / "deploy-src" / "proj"
        root.mkdir(parents=True)
        (root / "sandbox.config.json").write_text(json.dumps({"exposeToNetwork": True}))
        user = base / "user-global.json"
        user.write_text("{}")
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"SANDBOX_USER_CONFIG": str(user),
                                          "SANDBOX_HOME": str(home)}), \
                contextlib.redirect_stderr(err):
            result = sandbox_core.load_project_config(root)
        self.assertNotIn("exposeToNetwork", result)
        self.assertIn("deployed", err.getvalue())


class TestRuntimeServiceKeepsTheCode(unittest.TestCase):
    """M4: `sb ensure --json` and the MCP tool get `expose_to_network_invalid`
    from the runtime service, not a generic `invalid_descriptor`."""

    def test_ensure_through_the_runtime_service_returns_the_specific_code(self):
        from sandbox.application.context import runtime_service
        from sandbox.runtimes.base import OperationError, OperationRequest
        base = Path(tempfile.mkdtemp(dir=Path.home(), prefix="sb-expose-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "proj"
        root.mkdir()
        (root / "sandbox.config.json").write_text(json.dumps({"exposeToNetwork": "yes"}))
        user = base / "user-global.json"
        user.write_text("{}")
        runtime = base / "runtime"
        runtime.mkdir()
        env = {"SANDBOX_USER_CONFIG": str(user), "SANDBOX_RUNTIME": str(runtime)}
        with mock.patch.dict(os.environ, env):
            result = runtime_service({}).invoke(OperationRequest(
                project_root=str(root), operation="ensure", label="default", arguments={}))
            self.assertEqual(sandbox_core.registry_all(), {})
        self.assertIsInstance(result, OperationError)
        self.assertEqual(result.code, "expose_to_network_invalid")


class TestLinuxGatewayLookup(_RenderCase):
    """S2 + S3 + S6: the gateway lookup is cached, said out loud, and Docker's own."""

    def _patches(self, facts, local=True):
        return (mock.patch.object(docker.sys, "platform", "linux"),
                mock.patch.object(docker, "local_runtime_remote_only", return_value=False),
                mock.patch.object(docker, "_default_bridge_facts", return_value=facts),
                mock.patch.object(docker, "_host_address_is_local", return_value=local))

    def test_a_miss_is_cached_and_said_once(self):
        p = self._patches(None)
        err = io.StringIO()
        with p[0], p[1], p[2] as facts, p[3], contextlib.redirect_stderr(err):
            self.assertEqual(docker.publish_hosts({}), ["127.0.0.1"])
            self.assertEqual(docker.publish_hosts({}), ["127.0.0.1"])
        self.assertEqual(facts.call_count, 1)
        self.assertEqual(err.getvalue().count("host.docker.internal"), 1)

    def test_a_gateway_on_a_non_docker_bridge_is_skipped(self):
        p = self._patches(("192.168.1.10", "br0"))
        err = io.StringIO()
        with p[0], p[1], p[2], p[3], contextlib.redirect_stderr(err):
            self.assertEqual(docker.publish_hosts({}), ["127.0.0.1"])
        self.assertIn("br0", err.getvalue())

    def test_docker0_gateway_is_used(self):
        p = self._patches(("172.17.0.1", "docker0"))
        with p[0], p[1], p[2], p[3]:
            self.assertEqual(docker.publish_hosts({}), ["127.0.0.1", "172.17.0.1"])


class TestExposureIsAnnounced(unittest.TestCase):
    """S7: an exposed instance is announced when it is started or recreated."""

    def test_exposed_instance_prints_one_line_on_stderr(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            docker.announce_port_binding("ti", {"expose_to_network": True})
        self.assertIn("ti", err.getvalue())
        self.assertIn("every network interface", err.getvalue())

    def test_loopback_instance_prints_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            docker.announce_port_binding("ti", {})
        self.assertEqual(err.getvalue(), "")


class TestLegacyComposeFile(unittest.TestCase):
    def test_checked_in_compose_publishes_on_loopback(self):
        document = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        for name, ports in _published(document).items():
            for entry in ports:
                self.assertTrue(entry.startswith("127.0.0.1:"), f"{name}: {entry}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

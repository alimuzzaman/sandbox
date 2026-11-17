"""Blue-green side-loading rules (sandbox/hosting/blue_green.py). No remote effects."""
from __future__ import annotations

import unittest

from sandbox.hosting import blue_green as bg

COMPOSE = {"service": "edge", "container_port": 80,
           "background_services": ["db", "hub", "mailpit", "wordpress", "wp-db", "wp-setup",
                                   "sandbox-login"],
           "init_services": []}


def validated(shared=("db", "wp-db", "wp-setup"), strategy="auto", colored=("wordpress",)):
    block = {key: list(value) for key, value in (("shared", shared), ("colored", colored)) if value}
    plan = bg.validate({"strategy": strategy, "blue_green": block} if block else {"strategy": strategy},
                       COMPOSE)
    return {"project": "xspeed-hub-demo", "environment": "sandbox", "compose": COMPOSE,
            "deploy": {"blue_green_plan": plan}}


def volume(source):
    return {"type": "volume", "source": source, "target": "/data"}


def model():
    return {"name": "sandbox-host-xspeed-hub-demo-sandbox", "services": {
        "edge": {"image": "x/edge:demo", "ports": [{"target": 80, "published": "18044", "host_ip": "127.0.0.1"}],
                 "depends_on": {"hub": {"condition": "service_healthy"},
                                "mailpit": {"condition": "service_healthy"},
                                "wordpress": {"condition": "service_healthy"}},
                 "networks": {"default": None}},
        "hub": {"build": {"context": "."}, "depends_on": {"db": {"condition": "service_healthy"}},
                "environment": {"DATABASE_URL": "postgres://db"}},
        "db": {"image": "postgres:16", "volumes": [volume("hub_pgdata")]},
        "mailpit": {"image": "axllent/mailpit", "ports": [{"target": 8025, "published": "18120"}]},
        "wordpress": {"image": "wordpress", "volumes": [volume("wp_html")],
                      "depends_on": {"wp-db": {"condition": "service_healthy"}}},
        "wp-db": {"image": "mariadb", "volumes": [volume("db_data")]},
        "wp-setup": {"image": "x/setup", "volumes": [volume("wp_html")]},
        "sandbox-login": {"image": "python", "ports": [{"target": 8080, "published": "21044"}]},
    }, "volumes": {"hub_pgdata": {"name": "sandbox-host-xspeed-hub-demo-sandbox_hub_pgdata"},
                   "wp_html": {"name": "sandbox-host-xspeed-hub-demo-sandbox_wp_html"},
                   "db_data": {}}}


class ValidateTests(unittest.TestCase):
    def test_defaults_and_refusals(self):
        self.assertEqual(bg.validate({}, COMPOSE), {"strategy": "auto", "shared": [], "colored": []})
        for deploy, message in (
                ({"strategy": "rolling"}, "deploy.strategy"),
                ({"blue_green": {"shared": ["nope"]}}, "undeclared"),
                ({"blue_green": {"shared": ["edge"]}}, "primary"),
                ({"blue_green": {"shared": ["db", "db"]}}, "list of service names"),
                ({"blue_green": {"other": ["db"]}}, "accepts only shared and colored"),
                ({"blue_green": {"shared": ["db"], "colored": ["db"]}}, "both"),
                ({"blue_green": {"colored": ["nope"]}}, "undeclared"),
                ({"strategy": "in-place", "blue_green": {"shared": ["db"]}}, "auto or blue-green")):
            with self.subTest(deploy=deploy), self.assertRaisesRegex(ValueError, message):
                bg.validate(deploy, COMPOSE)

    def test_only_shared_services_skip_the_revision_check(self):
        self.assertEqual(bg.revision_exempt(validated()), {"db", "wp-db", "wp-setup"})
        self.assertEqual(bg.revision_exempt(validated(shared=(), colored=())), set())
        self.assertEqual(bg.revision_exempt({"deploy": {}}), set())


class ObservationTests(unittest.TestCase):
    """A shared service carries no revision stamp; the apply's ready check agrees."""

    def target(self, shared):
        return {"compose": {"service": "web", "background_services": ["db"]},
                "deploy": {"derived_environment": {"APP_SOURCE_REVISION": "pushed_commit_sha"},
                           "blue_green_plan": {"strategy": "auto", "shared": list(shared),
                                               "colored": []}}}

    def observation(self, revision):
        return {"complete": True, "configured_services": ["web", "db"],
                "rows": [{"Service": name, "State": "running", "Health": "healthy"}
                         for name in ("web", "db")],
                "revision_checks": [{"service": "web", "key": "APP_SOURCE_REVISION",
                                     "observed": revision},
                                    {"service": "db", "key": "APP_SOURCE_REVISION",
                                     "observed": None}]}

    def test_an_unstamped_shared_service_is_exact_ready(self):
        from sandbox.commands import hosting
        revision = "a" * 40
        target = self.target(["db"])
        classified = hosting._classify_host_observation(target, self.observation(revision), revision)
        self.assertTrue(hosting._host_observation_is_exact_ready(target, classified, revision))
        # Without the exemption the same runtime is not ready: db must carry the stamp.
        target = self.target([])
        classified = hosting._classify_host_observation(target, self.observation(revision), revision)
        self.assertFalse(hosting._host_observation_is_exact_ready(target, classified, revision))


class SplitTests(unittest.TestCase):
    def test_declared_and_port_publishing_services_stay_single(self):
        split = bg.split(model(), validated(), gate_service="sandbox-login")
        self.assertEqual(split["colored"], ["edge", "hub", "wordpress"])
        self.assertEqual(split["shared"], ["db", "mailpit", "sandbox-login", "wp-db", "wp-setup"])

    def test_undeclared_stateful_services_are_never_doubled(self):
        with self.assertRaisesRegex(bg.Ineligible, "db, wordpress, wp-db, wp-setup mount data"):
            bg.split(model(), validated(shared=(), colored=()))
        # A shared list alone does not license other data mounts (a forgotten redis).
        with self.assertRaisesRegex(bg.Ineligible, "^wordpress mount data"):
            bg.split(model(), validated(colored=()))

    def test_a_writable_bind_mount_is_data_and_a_read_only_one_is_not(self):
        for read_only, eligible in ((False, False), (True, True)):
            with self.subTest(read_only=read_only):
                changed = model()
                changed["services"]["hub"]["volumes"] = [
                    {"type": "bind", "source": "/srv/data", "target": "/var/lib/x", "read_only": read_only}]
                if eligible:
                    bg.split(changed, validated())
                else:
                    with self.assertRaisesRegex(bg.Ineligible, "hub mount data"):
                        bg.split(changed, validated())

    def test_a_dependency_apply_does_not_start_is_ineligible(self):
        changed = model()
        changed["services"]["hub"]["depends_on"]["cache"] = {"condition": "service_started"}
        with self.assertRaisesRegex(bg.Ineligible, "hub depends on services apply does not start: cache"):
            bg.split(changed, validated())

    def test_colored_services_that_cannot_run_twice_are_ineligible(self):
        for change, message in ((("hub", "container_name", "x"), "container_name"),
                                (("hub", "network_mode", "host"), "network_mode"),
                                (("hub", "networks", {"front": None}), "declared network"),
                                (("hub", "links", ["db"]), "links"),
                                (("hub", "volumes_from", ["db"]), "volumes_from"),
                                (("hub", "secrets", [{"source": "s"}]), "secrets"),
                                (("hub", "pid", "service:db"), "pid namespace")):
            with self.subTest(change=change):
                bad = model()
                bad["services"][change[0]][change[1]] = change[2]
                with self.assertRaisesRegex(bg.Ineligible, message):
                    bg.split(bad, validated())


class RenderTests(unittest.TestCase):
    def test_the_side_shares_volumes_and_publishes_only_its_port(self):
        split = bg.split(model(), validated(), gate_service="sandbox-login")
        side = bg.render_side(model(), validated(), base_project="sandbox-host-xspeed-hub-demo-sandbox",
                              colored=split["colored"], shared=split["shared"], port=19044)
        self.assertEqual(side["name"], "sandbox-hostside-xspeed-hub-demo-sandbox")
        self.assertEqual(sorted(side["services"]), ["edge", "hub", "wordpress"])
        self.assertEqual(side["services"]["edge"]["ports"],
                         [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 80,
                           "published": "19044", "protocol": "tcp"}])
        self.assertNotIn("ports", side["services"]["hub"])
        # Only colored dependencies survive; shared ones are reached by alias.
        self.assertEqual(sorted(side["services"]["edge"]["depends_on"]), ["hub", "wordpress"])
        self.assertNotIn("depends_on", side["services"]["hub"])
        self.assertEqual(side["services"]["hub"]["image"], "sandbox-host-xspeed-hub-demo-sandbox-hub")
        self.assertNotIn("build", side["services"]["hub"])
        self.assertEqual(side["volumes"], {"wp_html": {
            "name": "sandbox-host-xspeed-hub-demo-sandbox_wp_html", "external": True}})
        self.assertEqual(side["networks"], {"default": {
            "name": "sandbox-hostside-xspeed-hub-demo-sandbox_net", "external": True}})
        # Built images exist locally; image-only ones are pulled when missing.
        self.assertEqual({name: svc["pull_policy"] for name, svc in side["services"].items()},
                         {"edge": "missing", "hub": "never", "wordpress": "missing"})

    def test_side_names_never_collide_with_base_projects(self):
        self.assertFalse(bg.side_project(validated()).startswith("sandbox-host-"))
        self.assertEqual(bg.side_port(18044), 19044)


class SwapTests(unittest.TestCase):
    NGINX = ("server {\n    location / {\n        proxy_pass http://127.0.0.1:18044;\n    }\n"
             "    location /s/ { proxy_pass http://127.0.0.1:21044; }\n"
             "    # 127.0.0.1:180445 and 10.127.0.0.1:18044 are not upstreams\n}\n")

    def test_only_the_primary_upstream_moves(self):
        swapped = bg.swap_upstream(self.NGINX, 18044, 19044)
        self.assertIn("proxy_pass http://127.0.0.1:19044;", swapped)
        self.assertIn("127.0.0.1:21044", swapped)
        self.assertIn("127.0.0.1:180445", swapped)
        self.assertIn("10.127.0.0.1:18044", swapped)
        self.assertEqual(swapped.replace("19044", "18044"), self.NGINX)  # no marker here
        self.assertTrue(bg.serves_port(swapped, 19044))
        self.assertFalse(bg.serves_port(swapped, 18044))

    def test_the_nginx_route_marker_changes_with_the_upstream(self):
        text = ('server {\n    location = /.well-known/sandbox-edge-route {\n'
                '        default_type text/plain;\n        return 200 "0123456789abcdef01234567\\n";\n    }\n'
                '    location / { proxy_pass http://127.0.0.1:18044; }\n}\n'
                'server {\n    location = /.well-known/sandbox-edge-route { return 200 "0123456789abcdef01234567\\n"; }\n}\n')
        swapped = bg.swap_upstream(text, 18044, 19044)
        from sandbox.hosting.front_door import nginx
        marker = nginx.route_marker(swapped)
        self.assertIsNotNone(marker)
        self.assertNotEqual(marker, "0123456789abcdef01234567")
        self.assertEqual(swapped.count(marker), 2)
        self.assertNotIn("0123456789abcdef01234567", swapped)

    def test_caddy_upstream(self):
        text = "a.example {\n    reverse_proxy 127.0.0.1:18044\n}\n"
        self.assertEqual(bg.swap_upstream(text, 18044, 19044),
                         "a.example {\n    reverse_proxy 127.0.0.1:19044\n}\n")

    def test_a_route_without_the_upstream_refuses(self):
        for text in ("", None, "reverse_proxy 127.0.0.1:18045\n"):
            with self.subTest(text=text), self.assertRaises(bg.Ineligible):
                bg.swap_upstream(text, 18044, 19044)


class DestroyTests(unittest.TestCase):
    def test_destroy_removes_a_leftover_side(self):
        from sandbox.hosting import destroy
        command = destroy._remote_teardown_command(
            "sandbox-host-site-sandbox", {"runtime": "/h/runtime/hosts/site/sandbox",
                                          "project_runtime": "/h/runtime/hosts/site"})
        self.assertIn("label=com.docker.compose.project=sandbox-hostside-site-sandbox", command)
        self.assertIn("label=sandbox.hostside=sandbox-host-site-sandbox", command)


class SideLoadTests(unittest.TestCase):
    """_SideLoad.start with every remote call faked."""

    ROUTE = "server { location / { proxy_pass http://127.0.0.1:18044; } }\n"

    def run_start(self, *, healthy=True, route=ROUTE, side_running=False, strategy="auto",
                  stopped=(), side_start_fails=None, side_services="edge\nhub\nwordpress\n"):
        import json
        import subprocess
        from unittest import mock
        from sandbox.commands import hosting as hosting_cmd
        edge = mock.Mock()
        edge.read_previous.return_value = route
        checked = []

        def remote_checked(entry, command, timeout=180, **kwargs):
            checked.append(command)
            if command.startswith("docker ps -aq --filter"):
                return "abc\n" if side_running else ""
            if command.startswith("docker ps -a --filter") and "--format" in command:
                return side_services if side_running else ""
            if " ps -q --status running " in command or command.startswith("docker ps -q --filter"):
                return "" if any(f"service={name}" in command for name in stopped) else "base\n"
            return ""
        target = validated(strategy=strategy)
        runtime = {"loopback_port": 18044, "compose_project": "sandbox-host-xspeed-hub-demo-sandbox",
                   "healthcheck": {"path": "/healthz", "statuses": [200]}}
        side = hosting_cmd._SideLoad({}, {**target, "login_service": None}, runtime, edge=edge,
                                     caddy_name="sandbox-host-xspeed-hub-demo-sandbox",
                                     previous_route=route, previous_entry={"loopback_port": 18044},
                                     runtime_dir="/rt")
        self.side = side
        original = remote_checked

        def remote_checked(entry, command, timeout=180, **kwargs):
            if side_start_fails is not None and kwargs.get("log_phase") == "blue_green_side_start":
                checked.append(command)
                raise side_start_fails
            return original(entry, command, timeout, **kwargs)
        health = mock.Mock(side_effect=None if healthy else RuntimeError("remote healthcheck failed"))
        with mock.patch.object(hosting_cmd, "_remote_checked", side_effect=remote_checked), \
                mock.patch.object(hosting_cmd.remote, "ssh_run_bounded", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(model()), "")), \
                mock.patch.object(hosting_cmd, "_write_remote_text") as write, \
                mock.patch.object(hosting_cmd, "_locked_host_compose_up", side_effect=lambda v, r, c: c) as locked, \
                mock.patch.object(hosting_cmd, "_verify_remote_health", health):
            try:
                result = side.start("BASE", "/rt", "/rt/environment.env")
                error = None
            except Exception as exc:  # noqa: BLE001 - asserted by callers
                result, error = None, exc
        return side, edge, checked, write, locked, result, error

    def test_a_healthy_side_switches_only_the_upstream(self):
        side, edge, checked, write, locked, result, error = self.run_start()
        self.assertIsNone(error)
        self.assertTrue(result and side.serving)
        edge.configure.assert_called_once()
        name, content, previous = edge.configure.call_args.args
        self.assertEqual(content, self.ROUTE.replace("18044", "19044"))
        self.assertEqual(previous, self.ROUTE)
        self.assertEqual(side.route, content)
        self.assertEqual(write.call_args.args[1], "/rt/side.compose.json")
        self.assertEqual(write.call_args.args[3], "0600")
        # The side starts under the port recheck for its own project and port.
        _, side_runtime, command = locked.call_args.args
        self.assertEqual((side_runtime["compose_project"], side_runtime["loopback_port"]),
                         ("sandbox-hostside-xspeed-hub-demo-sandbox", 19044))
        self.assertIn("up -d --no-build --pull missing --no-deps edge hub wordpress", command)
        self.assertIn("--alias db", command)
        self.assertNotIn("--alias sandbox-login", command)  # the gate stays off the side
        base = side.base_up_command("BASE", "")
        self.assertIn("BASE up -d --no-build --no-deps --remove-orphans db mailpit sandbox-login wp-db wp-setup && ", base)
        # Recreated shared containers rejoin the side network before colored ones go.
        self.assertLess(base.index("--alias db"), base.index("--force-recreate"))
        self.assertTrue(base.endswith("--force-recreate --renew-anon-volumes edge hub wordpress"))

    def test_a_refused_port_recheck_never_runs_the_side_up(self):
        import os
        import subprocess
        import tempfile
        *_, locked, _, _ = self.run_start()
        _, _, up = locked.call_args.args
        with tempfile.TemporaryDirectory() as root:
            log = os.path.join(root, "calls")
            fake = os.path.join(root, "docker")
            with open(fake, "w") as handle:
                handle.write(f"#!/bin/sh\necho \"$*\" >> {log}\necho id1\n")
            os.chmod(fake, 0o755)
            from tests.subprocess_support import synthetic_environment
            env = synthetic_environment({"PATH": root + os.pathsep + os.environ["PATH"]})
            for check in ("false", "true"):
                with self.subTest(check=check):
                    open(log, "w").close()
                    result = subprocess.run(["sh", "-c", check + " && " + up], env=env,
                                            capture_output=True, text=True, timeout=30)
                    calls = open(log).read()
                    if check == "false":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn("compose", calls)
                        self.assertNotIn("network", calls)
                    else:
                        self.assertIn("compose", calls)

    def test_a_side_that_cannot_start_falls_back_under_auto(self):
        from sandbox.commands import hosting as hosting_cmd
        refused = hosting_cmd.HostRemoteCommandFailed("port recheck refused",
                                                      phase="blue_green_side_start", exit_code=1)
        side, edge, checked, *_, result, error = self.run_start(side_start_fails=refused)
        self.assertIsNone(error)
        self.assertIs(result, False)
        self.assertIn("could not start", side.skipped)
        edge.configure.assert_not_called()
        self.assertTrue(any("docker network rm" in command for command in checked))

    def test_a_lost_link_during_side_start_is_not_a_known_failure(self):
        # It may still run under the ingress locks: no cleanup race, no fallback.
        side, edge, checked, *_, result, error = self.run_start(
            side_start_fails=RuntimeError("SSH connection lost during remote phase (exit 255)"))
        self.assertIn("SSH connection lost", str(error))
        self.assertFalse(any("docker network rm" in command for command in checked))
        edge.configure.assert_not_called()

    def test_a_stopped_shared_service_falls_back(self):
        side, edge, checked, write, *_, result, error = self.run_start(stopped=("wp-db",))
        self.assertIs(result, False)
        self.assertIn("shared services are not running: wp-db", side.skipped)
        write.assert_not_called()

    def test_base_up_never_runs_unscoped(self):
        side, *_ = self.run_start(route="x")  # ineligible: nothing loaded
        with self.assertRaisesRegex(Exception, "no colored services"):
            side.base_up_command("BASE", "")

    def test_an_unhealthy_side_leaves_route_and_base_untouched(self):
        side, edge, checked, *_, error = self.run_start(healthy=False)
        self.assertIn("blue_green_side_unhealthy", str(error))
        edge.configure.assert_not_called()
        self.assertFalse(side.serving)
        self.assertTrue(any("docker network rm" in command for command in checked))
        self.assertFalse(any("BASE up" in command and "force-recreate" in command for command in checked))

    def test_a_side_left_serving_is_reused_with_its_split(self):
        route = self.ROUTE.replace("18044", "19044")
        side, edge, checked, write, *_ = self.run_start(route=route, side_running=True)
        self.assertTrue(side.serving)
        self.assertEqual(side.route, route)
        write.assert_not_called()
        edge.configure.assert_not_called()
        self.assertTrue(any("--alias db" in command for command in checked))  # reconnected
        base = side.base_up_command("BASE", "")
        self.assertTrue(base.endswith("--force-recreate --renew-anon-volumes edge hub wordpress"))
        self.assertNotIn("--force-recreate --renew-anon-volumes db", base)

    def test_an_unusable_leftover_side_recreates_in_place_under_both_strategies(self):
        # The route points at a dead side: refusing would keep the target down.
        route = self.ROUTE.replace("18044", "19044")
        for strategy in ("auto", "blue-green"):
            for case, expected in (({"healthy": False}, "unhealthy"),
                                   ({"side_running": False}, "gone"),
                                   ({"side_services": "edge\nhub\nwordpress\ndb\n"}, "other services")):
                with self.subTest(strategy=strategy, case=case):
                    options = {"side_running": True, **case}
                    side, edge, checked, *_, result, error = self.run_start(
                        route=route, strategy=strategy, **options)
                    self.assertIsNone(error)
                    self.assertIs(result, False)
                    self.assertFalse(side.serving)
                    self.assertIn(expected, side.skipped)
                    self.assertTrue(any("docker network rm" in command for command in checked))

    def test_finish_clears_the_rollback_target_before_teardown(self):
        from unittest import mock
        from sandbox.commands import hosting as hosting_cmd
        side, *_ = self.run_start()
        self.assertTrue(side.serving)
        with mock.patch.object(hosting_cmd, "_remote_checked",
                               side_effect=RuntimeError("network rm failed")) as checked:
            side.finish(None)
        self.assertIn("rm -f /rt/side.compose.json", checked.call_args.args[1])
        self.assertFalse(side.serving)
        self.assertIsNone(side.route)

    def test_auto_falls_back_and_explicit_blue_green_refuses(self):
        route = "server { proxy_pass http://127.0.0.1:18999; }\n"
        side, edge, *_, result, error = self.run_start(route=route)
        self.assertIs(result, False)
        self.assertIn("does not serve 127.0.0.1:18044", side.skipped)
        *_, error = self.run_start(route=route, strategy="blue-green")
        self.assertIn("blue_green_ineligible", str(error))


if __name__ == "__main__":
    unittest.main()

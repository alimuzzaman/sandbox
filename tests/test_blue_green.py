"""Blue-green side-loading rules (sandbox/hosting/blue_green.py). No remote effects."""
from __future__ import annotations

import unittest

from sandbox.hosting import blue_green as bg

COMPOSE = {"service": "edge", "container_port": 80,
           "background_services": ["db", "hub", "mailpit", "wordpress", "wp-db", "wp-setup",
                                   "sandbox-login"],
           "init_services": []}


def validated(shared=("db", "wp-db", "wp-setup"), strategy="auto"):
    plan = bg.validate({"strategy": strategy, "blue_green": {"shared": list(shared)}}
                       if shared else {"strategy": strategy}, COMPOSE)
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
        self.assertEqual(bg.validate({}, COMPOSE), {"strategy": "auto", "shared": []})
        for deploy, message in (
                ({"strategy": "rolling"}, "deploy.strategy"),
                ({"blue_green": {"shared": ["nope"]}}, "undeclared"),
                ({"blue_green": {"shared": ["edge"]}}, "primary"),
                ({"blue_green": {"shared": ["db", "db"]}}, "list of service names"),
                ({"blue_green": {"colored": ["db"]}}, "accepts only shared"),
                ({"strategy": "in-place", "blue_green": {"shared": ["db"]}}, "auto or blue-green")):
            with self.subTest(deploy=deploy), self.assertRaisesRegex(ValueError, message):
                bg.validate(deploy, COMPOSE)

    def test_only_shared_services_skip_the_revision_check(self):
        self.assertEqual(bg.revision_exempt(validated()), {"db", "wp-db", "wp-setup"})
        self.assertEqual(bg.revision_exempt(validated(shared=())), set())
        self.assertEqual(bg.revision_exempt({"deploy": {}}), set())


class SplitTests(unittest.TestCase):
    def test_declared_and_port_publishing_services_stay_single(self):
        split = bg.split(model(), validated(), gate_service="sandbox-login")
        self.assertEqual(split["colored"], ["edge", "hub", "wordpress"])
        self.assertEqual(split["shared"], ["db", "mailpit", "sandbox-login", "wp-db", "wp-setup"])

    def test_undeclared_stateful_services_are_never_doubled(self):
        with self.assertRaisesRegex(bg.Ineligible, "db, wordpress, wp-db, wp-setup mount named volumes"):
            bg.split(model(), validated(shared=()))

    def test_colored_services_that_cannot_run_twice_are_ineligible(self):
        for change, message in ((("hub", "container_name", "x"), "container_name"),
                                (("hub", "network_mode", "host"), "network_mode"),
                                (("hub", "networks", {"front": None}), "declared network")):
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
        self.assertTrue(all(s["pull_policy"] == "never" for s in side["services"].values()))

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
        self.assertEqual(swapped.replace("19044", "18044"), self.NGINX)
        self.assertTrue(bg.serves_port(swapped, 19044))
        self.assertFalse(bg.serves_port(swapped, 18044))

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

    def run_start(self, *, healthy=True, route=ROUTE, side_running=False, strategy="auto"):
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
            if " ps -q --status running " in command:
                return "base\n"
            return ""
        target = validated(strategy=strategy)
        runtime = {"loopback_port": 18044, "compose_project": "sandbox-host-xspeed-hub-demo-sandbox",
                   "healthcheck": {"path": "/healthz", "statuses": [200]}}
        side = hosting_cmd._SideLoad({}, {**target, "login_service": None}, runtime, edge=edge,
                                     caddy_name="sandbox-host-xspeed-hub-demo-sandbox",
                                     previous_route=route, previous_entry={"loopback_port": 18044})
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
        self.assertIn("up -d --no-build --pull never --no-deps edge hub wordpress", command)
        self.assertIn("--alias db", command)
        base = side.base_up_command("BASE", "")
        self.assertIn("BASE up -d --no-build --no-deps --remove-orphans db mailpit sandbox-login wp-db wp-setup && ", base)
        self.assertTrue(base.endswith("--force-recreate --renew-anon-volumes edge hub wordpress"))

    def test_an_unhealthy_side_leaves_route_and_base_untouched(self):
        side, edge, checked, *_, error = self.run_start(healthy=False)
        self.assertIn("blue_green_side_unhealthy", str(error))
        edge.configure.assert_not_called()
        self.assertFalse(side.serving)
        self.assertTrue(any("docker network rm" in command for command in checked))
        self.assertFalse(any("BASE up" in command and "force-recreate" in command for command in checked))

    def test_a_side_left_serving_is_reused(self):
        route = self.ROUTE.replace("18044", "19044")
        side, edge, checked, write, *_ = self.run_start(route=route, side_running=True)
        self.assertTrue(side.serving)
        self.assertEqual(side.route, route)
        write.assert_not_called()
        edge.configure.assert_not_called()

    def test_auto_falls_back_and_explicit_blue_green_refuses(self):
        route = "server { proxy_pass http://127.0.0.1:18999; }\n"
        side, edge, *_, result, error = self.run_start(route=route)
        self.assertIs(result, False)
        self.assertIn("does not serve 127.0.0.1:18044", side.skipped)
        *_, error = self.run_start(route=route, strategy="blue-green")
        self.assertIn("blue_green_ineligible", str(error))


if __name__ == "__main__":
    unittest.main()

"""The panel's stacks and hosted sites view model (spec 073)."""
from __future__ import annotations

import json
import unittest

from sandbox.hosting import panel_records as pr
from sandbox.hosting import panel_view as pv

COMMIT = "b" * 40
SECRET = "planted-secret-value-9876543210"


def summary(project="xspeed-hub-demo", environment="staging", *, slug="demo", remote="xspeed-dev",
            routes=None, status="succeeded", ttl=None, ports=None):
    stack = None
    if slug:
        stack = {"slug": slug, "ref": "feature/demo", "ref_branch": "feature/demo", "commit": COMMIT,
                 "template": "deploy/sandbox.hosting.yml", "ports": ports or {"mailpit": 18142},
                 "created_at": 1_700_000_000}
        if ttl:
            stack["ttl_expires_at"] = ttl
    return pr.parse_target_summary({
        "schema": 1, "remote": remote, "project": project, "environment": environment,
        "compose_project": f"sandbox-host-{project}-{environment}", "loopback_port": 18017,
        "healthcheck_path": "/health",
        "routes": routes or [
            {"hostname": f"renderer-{slug}.example.test", "mode": "serve", "primary": False},
            {"hostname": f"hub-{slug}.example.test", "mode": "serve", "primary": True},
            {"hostname": f"admin-{slug}.example.test", "mode": "serve", "primary": False},
        ],
        "commit": COMMIT,
        "last_apply": {"status": status, "started_at": 10, "finished_at": 20, "request_id": "req-1"},
        "stack": stack,
    })


def link(hostname="wp-demo.example.test", slug="demo", instance="sandbox-wp-demo", remote="xspeed-dev"):
    return pr.build_site_link(remote=remote, slug=slug, hostname=hostname, instance=instance,
                              label="default", commit=COMMIT, recorded_at=30)


def server(targets=(), links=(), legacy=(), partial=()):
    return pv.server_source({"targets": list(targets), "links": list(links), "legacy": list(legacy),
                             "partial_reasons": list(partial)})


class StackViewTest(unittest.TestCase):
    def test_stack_rows_in_order_with_links(self):
        view = pv.build_view([server([summary()])], clock=lambda: 99)
        self.assertEqual(view["sources"], ["this-server"])
        (stack,) = view["stacks"]
        self.assertEqual([row["hostname"] for row in stack["servers"]],
                         ["hub-demo.example.test", "admin-demo.example.test", "renderer-demo.example.test"])
        self.assertEqual(stack["servers"][0]["url"], "https://hub-demo.example.test/")
        self.assertTrue(stack["servers"][0]["primary"])
        self.assertEqual({row["state"] for row in stack["servers"]}, {"serving"})
        self.assertEqual(stack["servers"][0]["ref"], {"branch": "feature/demo", "commit": COMMIT})
        self.assertEqual(stack["mailpit_port"], 18142)
        self.assertEqual(stack["template"], "deploy/sandbox.hosting.yml")
        self.assertEqual(stack["last_apply"]["request_id"], "req-1")
        self.assertIsNone(stack["ttl_expires_at"])
        self.assertEqual(view["targets"], [])

    def test_redirect_and_wildcard_rows(self):
        routes = [{"hostname": "hub-demo.example.test", "mode": "serve", "primary": True},
                  {"hostname": "old-demo.example.test", "mode": "redirect", "primary": False,
                   "target": "https://hub-demo.example.test"},
                  {"hostname": "*.demo.example.test", "mode": "serve", "primary": False}]
        probes = []
        view = pv.build_view([server([summary(routes=routes)])],
                             health=lambda targets: probes.extend(targets) or {})
        rows = {row["hostname"]: row for row in view["stacks"][0]["servers"]}
        self.assertEqual(rows["old-demo.example.test"]["state"], "redirecting")
        self.assertEqual(rows["old-demo.example.test"]["target"], "https://hub-demo.example.test")
        self.assertIsNone(rows["*.demo.example.test"]["url"])
        self.assertEqual(rows["*.demo.example.test"]["health"]["status"], "not_checked")
        self.assertIn(("hub-demo.example.test", "/health"), probes)
        self.assertIn(("old-demo.example.test", "/"), probes)
        self.assertNotIn("*.demo.example.test", [host for host, _ in probes])

    def test_apply_states_reach_rows(self):
        for status, state in (("applying", "applying"), ("failed", "failed")):
            view = pv.build_view([server([summary(status=status)])])
            self.assertEqual({row["state"] for row in view["stacks"][0]["servers"]}, {state})

    def test_ttl_when_present(self):
        view = pv.build_view([server([summary(ttl=1_800_000_000)])])
        self.assertEqual(view["stacks"][0]["ttl_expires_at"], 1_800_000_000)

    def test_plain_and_legacy_targets(self):
        plain = summary("marketing", "production", slug=None, routes=[
            {"hostname": "www.example.test", "mode": "serve", "primary": True}])
        view = pv.build_view([server([plain], legacy=[{"project": "legacy", "environment": "production"}],
                                     partial=["target_summary_unreadable:x/y:JSONDecodeError"])])
        self.assertEqual(view["stacks"], [])
        self.assertEqual([target["project"] for target in view["targets"]], ["marketing", "legacy"])
        self.assertTrue(view["targets"][0]["recorded"])
        self.assertFalse(view["targets"][1]["recorded"])
        self.assertEqual(view["targets"][1]["servers"], [])
        self.assertEqual(view["partial_reasons"], ["target_summary_unreadable:x/y:JSONDecodeError"])

    def test_server_source_wins_over_controller(self):
        controller = {"source": "controller", "targets": [summary(status="failed")], "links": [],
                      "legacy": [], "partial_reasons": []}
        view = pv.build_view([controller, server([summary()])])
        (stack,) = view["stacks"]
        self.assertEqual(stack["source"], "this-server")
        self.assertEqual(stack["last_apply"]["status"], "succeeded")

    def test_health_is_applied_to_rows(self):
        def lookup(targets):
            return {host: {"status": "up", "http_status": 200, "checked_at": 5} for host, _ in targets}
        view = pv.build_view([server([summary()])], health=lookup)
        self.assertEqual({row["health"]["status"] for row in view["stacks"][0]["servers"]}, {"up"})


class SiteRowsTest(unittest.TestCase):
    def test_linked_site_under_stack(self):
        known = pv.known_sites([{"instance": "sandbox-wp-demo", "url": "https://wp-demo.example.test"}], [])
        view = pv.build_view([server([summary()], links=[link()])], known=known)
        row = view["stacks"][0]["servers"][-1]
        self.assertEqual(row["origin"], "linked")
        self.assertEqual(row["state"], "serving")
        self.assertEqual(row["instance"], "sandbox-wp-demo")
        self.assertEqual(row["ref"], {"branch": None, "commit": COMMIT})
        self.assertEqual(len(view["stacks"][0]["servers"]), 4)

    def test_linked_site_whose_instance_is_gone(self):
        view = pv.build_view([server([summary()], links=[link()])], known=[])
        self.assertEqual(view["stacks"][0]["servers"][-1]["state"], "instance_not_found")

    def test_name_match_fallback_is_labelled(self):
        known = pv.known_sites([{"instance": "i1", "url": "https://wp-demo.example.test/"},
                                {"instance": "i2", "url": "https://wp-demo.other.test/"},
                                {"instance": "i3", "url": "http://localhost:8080"}],
                               ["sandbox-instance-mail-demo.example.test.caddy", "unrelated.conf"])
        view = pv.build_view([server([summary()])], known=known)
        matched = [row for row in view["stacks"][0]["servers"] if row["origin"] == "matched_by_name"]
        self.assertEqual([row["hostname"] for row in matched],
                         ["mail-demo.example.test", "wp-demo.example.test"])
        self.assertIsNone(matched[0]["instance"])

    def test_explicit_link_beats_name_match(self):
        known = pv.known_sites([{"instance": "sandbox-wp-demo", "url": "https://wp-demo.example.test"}], [])
        view = pv.build_view([server([summary()], links=[link()])], known=known)
        origins = [row["origin"] for row in view["stacks"][0]["servers"]
                   if row["hostname"] == "wp-demo.example.test"]
        self.assertEqual(origins, ["linked"])

    def test_longest_slug_wins(self):
        short = summary("hub-two", "staging", slug="two")
        longer = summary("hub-demo-two", "staging", slug="demo-two")
        known = pv.known_sites([{"instance": "i", "url": "https://wp-demo-two.example.test"}], [])
        view = pv.build_view([server([short, longer])], known=known)
        by_slug = {stack["slug"]: [row["origin"] for row in stack["servers"]] for stack in view["stacks"]}
        self.assertIn("matched_by_name", by_slug["demo-two"])
        self.assertNotIn("matched_by_name", by_slug["two"])

    def test_route_hosts_are_never_name_matched(self):
        known = pv.known_sites([{"instance": "i", "url": "https://hub-demo.example.test"}], [])
        view = pv.build_view([server([summary()])], known=known)
        self.assertEqual(len(view["stacks"][0]["servers"]), 3)


class ControllerSourceTest(unittest.TestCase):
    def test_stack_registry_and_hosts_state(self):
        stack_data = {"version": 1, "stacks": {"xspeed-dev/demo": {
            "remote": "xspeed-dev", "slug": "demo", "project": "xspeed-hub-demo",
            "environment": "staging", "template": "/home/u/checkout/deploy/sandbox.hosting.yml",
            "checkout": "/home/u/checkout", "ref": "feature/demo", "ref_branch": "feature/demo",
            "commit": COMMIT, "hostnames": ["hub-demo.example.test", "admin-demo.example.test"],
            "ports": {"mailpit": 18142}, "created_at": 1_700_000_000,
            "last_apply": {"request_id": "req-7", "job_id": "job-1", "submitted_at": 50, "status": "submitted"},
            "sites": [{"hostname": "wp-demo.example.test", "instance": "sandbox-wp-demo",
                       "label": "default", "commit": COMMIT, "recorded_at": 60},
                      {"hostname": "bad host", "instance": "x"}],
            "manifest": "/home/u/sandbox/runtime/stacks/demo/sandbox.hosting.yml",
            "worktrees": ["/home/u/sandbox/runtime/stacks/demo/source/abc"],
        }}}
        host_state = {"version": 1, "hosts": {
            "xspeed-dev/xspeed-hub-demo/staging": {"hostnames": ["hub-demo.example.test"],
                                                   "loopback_port": 18017, "commit": COMMIT,
                                                   "runtime": {"state": "ready"}, "edge": {"state": "ready"},
                                                   "secret_fingerprint": SECRET},
            "xspeed-dev/marketing/production": {"hostnames": ["www.example.test"], "commit": COMMIT,
                                                "runtime": {"state": "ready"}, "edge": {"state": "ready"}},
        }}

        def last_apply(record):
            return {**record["last_apply"], "status": "succeeded", "finished_at": 70, "at": 70}
        records = pv.controller_records(stack_data, host_state, last_apply)
        self.assertEqual(records["partial_reasons"], ["site_link_unreadable:demo:RecordError"])
        view = pv.build_view([records])
        self.assertEqual(view["sources"], ["controller"])
        (stack,) = view["stacks"]
        self.assertEqual(stack["template"], "deploy/sandbox.hosting.yml")
        self.assertEqual(stack["last_apply"], {"status": "succeeded", "started_at": 50,
                                               "finished_at": 70, "request_id": "req-7"})
        self.assertEqual(stack["mailpit_port"], 18142)
        self.assertEqual(stack["loopback_port"], 18017)
        hosts = [row["hostname"] for row in stack["servers"]]
        self.assertEqual(hosts, ["admin-demo.example.test", "hub-demo.example.test", "wp-demo.example.test"])
        self.assertEqual(stack["servers"][-1]["state"], "unknown")
        self.assertEqual([target["project"] for target in view["targets"]], ["marketing"])
        encoded = json.dumps(view)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("/home/u", encoded)

    def test_empty_inputs(self):
        records = pv.controller_records({}, {}, lambda record: {})
        self.assertEqual(pv.build_view([records])["stacks"], [])


if __name__ == "__main__":
    unittest.main()

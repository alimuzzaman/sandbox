import json
import threading
import time
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from io import StringIO


def _records(*, mode="idle_stop", wake=True, kind="wordpress", host="site.tst",
             route_id="ar_0123456789abcdef0123456789abcdef", token="x" * 43,
             aliases=None, cron_disabled=True):
    record = {"instance": "site", "root": "/opaque/project", "label": "default",
              "kind": kind, "domain": host, "http_port": 8080,
              "instanceLifecycle": {"mode": mode, "wakeOnRequest": wake},
              "activation_route": {"id": route_id, "token": token}}
    block = {"domain": host, "wordpress_port": 8080,
             "instance_lifecycle": {"mode": mode, "wakeOnRequest": wake},
             "activation_route": {"id": route_id, "token": token},
             "wp_config": {"DISABLE_WP_CRON": cron_disabled}}
    if aliases is not None:
        block["aliases"] = aliases
    return {"key": record}, {"site": block}


class ActivationRaceTests(unittest.TestCase):
    def test_twenty_concurrent_requests_resume_once_and_release_claims(self):
        from sandbox.activation import ActivationPolicy, ActivationService
        service = ActivationService()
        service.register("route", ActivationPolicy(mode="idle_stop", wake_on_request=True))
        start = threading.Barrier(21)
        release = threading.Event()
        calls, results = [], []

        def resume(*_args):
            calls.append(1)
            release.wait(2)
            return True

        def invoke():
            start.wait(2)
            results.append(service.activate("route", resume=resume))

        workers = [threading.Thread(target=invoke) for _ in range(20)]
        for worker in workers:
            worker.start()
        start.wait(2)
        time.sleep(.05)
        release.set()
        for worker in workers:
            worker.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 20)
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(service.coordinator.snapshot("route")["pending"], 0)

    def test_failure_generation_is_not_reused_and_exception_is_retryable(self):
        from sandbox.activation import ActivationPolicy, ActivationService
        service = ActivationService()
        service.register("route", ActivationPolicy(mode="idle_stop", wake_on_request=True))
        first = service.activate("route", resume=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")))
        second = service.activate("route", resume=lambda *_: True)
        self.assertFalse(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(service.coordinator.snapshot("route")["generation"], 2)
        self.assertEqual(service.coordinator.snapshot("route")["pending"], 0)

    def test_request_cancels_stale_drain_claim(self):
        from sandbox.activation import ActivationCoordinator, ActivationPolicy, ActivationState
        now = [1000.0]
        coordinator = ActivationCoordinator(clock=lambda: now[0])
        coordinator.register("route", ActivationPolicy(mode="idle_stop", wake_on_request=True,
                                                        idle_after_seconds=60), state=ActivationState.READY)
        now[0] += 61
        claim = coordinator.claim_due_for_suspend()[0]
        self.assertTrue(coordinator.begin_request("route"))
        current, called = coordinator.run_if_drain_current(claim, lambda: True)
        self.assertFalse(current)
        self.assertFalse(called)
        self.assertEqual(coordinator.snapshot("route")["state"], "ready")
        coordinator.end_request("route")


class ActivationCatalogTests(unittest.TestCase):
    def test_catalog_allowlist_and_exclusions(self):
        from sandbox.activation.catalog import build_catalog
        records, wp = _records()
        self.assertEqual(len(build_catalog(records, wp).routes()), 1)
        records, wp = _records(wake=False)
        self.assertEqual(build_catalog(records, wp).routes(), ())
        records, wp = _records(mode="always_on", wake=True)
        self.assertEqual(build_catalog(records, wp).routes(), ())

    def test_catalog_excludes_cron_enabled_and_legacy_unmarked_entries(self):
        from sandbox.activation.catalog import build_catalog

        records, wp = _records(cron_disabled=False)
        self.assertEqual(build_catalog(records, wp).routes(), ())
        records, wp = _records()
        records["key"].pop("instanceLifecycle")
        wp["site"].pop("instance_lifecycle")
        self.assertEqual(build_catalog(records, wp).routes(), ())

    def test_catalog_rejects_aliases_and_collisions(self):
        from sandbox.activation.catalog import ActivationCatalogError, build_catalog
        records, wp = _records(aliases=["alias.tst"])
        with self.assertRaises(ActivationCatalogError):
            build_catalog(records, wp)
        records, wp = _records()
        records["other"] = dict(records["key"], root="/other")
        with self.assertRaises(ActivationCatalogError):
            build_catalog(records, wp)

    def test_catalog_quarantines_stale_metadata_without_disabling_other_routes(self):
        from sandbox.activation.catalog import build_catalog
        bad_records, bad_wp = _records()
        bad_records["key"]["instance"] = "bad-site"
        bad_wp["site"]["domain"] = None
        catalog = build_catalog({"bad": bad_records["key"]}, {"bad-site": bad_wp["site"]})
        self.assertEqual(catalog.routes(), ())
        self.assertEqual(catalog.issues(), ("activation route metadata is invalid",))

        valid_records, valid_wp = _records()
        # A separate malformed row must not prevent a valid route from being
        # represented. Use a distinct instance name to avoid a route collision.
        valid_records["key"]["instance"] = "valid-site"
        valid_records["key"]["domain"] = "valid.tst"
        combined = {"bad": bad_records["key"], "valid": valid_records["key"]}
        combined_wp = {
            "bad-site": bad_wp["site"],
            "valid-site": dict(valid_wp["site"], domain="valid.tst"),
        }
        catalog = build_catalog(combined, combined_wp)
        self.assertEqual([route.hostname for route in catalog.routes()], ["valid.tst"])
        self.assertEqual(catalog.issues(), ("activation route metadata is invalid",))

    def test_scan_reports_invalid_catalog_without_traceback_or_scheduler(self):
        from sandbox.commands.activation import cmd_activation

        output = StringIO()
        malformed = {
            "bad": {
                "instance": "site", "root": "/opaque/project", "label": "default",
                "kind": "wordpress", "domain": "site.tst", "http_port": 8080,
                "instanceLifecycle": {"mode": "idle_stop", "wakeOnRequest": True},
            },
        }
        with mock.patch("sandbox_core.registry_all", return_value=malformed), \
             mock.patch("sandbox.core.resolve_instances", return_value={
                 "site": {
                     "domain": "site.tst", "wordpress_port": 8080,
                     "instance_lifecycle": {"mode": "idle_stop", "wakeOnRequest": True},
                     "wp_config": {"DISABLE_WP_CRON": True},
                 },
             }), \
             redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                cmd_activation({}, type("Args", (), {"action": "scan", "dry_run": True})())
        self.assertEqual(raised.exception.code, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "activation_catalog_invalid")
        self.assertEqual(payload["results"], [])


class ActivationHTTPTests(unittest.TestCase):
    def setUp(self):
        from sandbox.activation import ActivationService
        from sandbox.activation.catalog import build_catalog
        from sandbox.activation.http import ActivationHTTPApplication
        records, wp = _records()
        self.route = build_catalog(records, wp).routes()[0]
        self.calls = []
        self.app = ActivationHTTPApplication(
            build_catalog(records, wp), ActivationService(),
            lambda route, timeout: self.calls.append((route.route_id, timeout)) or True,
        )
        self.headers = {"X-Sandbox-Route-ID": self.route.route_id,
                        "Authorization": f"Bearer {self.route.token}"}

    def test_strict_contract_and_no_request_replay(self):
        self.assertEqual(self.app.handle("GET", "/v1/activate", self.headers).status, 204)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.app.handle("POST", "/v1/activate", self.headers, b"payload").status, 405)
        self.assertEqual(self.app.handle("GET", "/v1/activate?target=x", self.headers).status, 404)
        self.assertEqual(self.app.handle("GET", "/v1/activate", {}).status, 404)
        self.assertEqual(len(self.calls), 1)

    def test_failure_is_sanitized_retryable_and_never_cached(self):
        from sandbox.activation import ActivationService
        from sandbox.activation.catalog import build_catalog
        from sandbox.activation.http import ActivationHTTPApplication
        records, wp = _records()
        app = ActivationHTTPApplication(build_catalog(records, wp), ActivationService(),
                                        lambda *_: False)
        response = app.handle("GET", "/v1/activate", self.headers)
        self.assertEqual(response.status, 503)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Retry-After", response.headers)

    def test_health_and_unrelated_endpoints_never_activate(self):
        self.assertEqual(self.app.handle("GET", "/healthz", {}).status, 204)
        self.assertEqual(self.app.handle("GET", "/api/instances", {}).status, 404)
        self.assertEqual(self.calls, [])


class ActivationCaddyTests(unittest.TestCase):
    def test_forward_auth_is_only_rendered_for_explicit_route(self):
        from sandbox.activation.catalog import build_catalog
        from sandbox.core._domains import _caddy_block
        records, wp = _records()
        route = build_catalog(records, wp).routes()[0]
        direct = _caddy_block("site.tst", 8080)
        wake = _caddy_block("site.tst", 8080, activation_route=route)
        self.assertNotIn("forward_auth", direct)
        self.assertIn("forward_auth host.docker.internal:8766", wake)
        self.assertLess(wake.index("forward_auth"), wake.index("reverse_proxy"))

    def test_gateway_enable_failure_is_reported_without_route_mutation(self):
        from sandbox.core._domains import _ensure_activation_gateway
        records, wp = _records()
        with mock.patch("sandbox.core._domains.registry_all", return_value=records), \
             mock.patch("sandbox.core._domains.resolve_instances", return_value=wp), \
             mock.patch("sandbox.core._domains._activation_gateway_healthy", return_value=False), \
             mock.patch("sandbox.activation.supervision.enable", return_value={"ok": False}):
            self.assertFalse(_ensure_activation_gateway({}))

    def test_unhealthy_gateway_strips_stale_wake_middleware(self):
        """A dead authority must not leave an old forward_auth Caddyfile live."""
        from sandbox.core import _domains

        records, wp = _records()
        with tempfile.TemporaryDirectory() as td:
            proxy_dir = Path(td) / "proxy"
            caddyfile = proxy_dir / "Caddyfile"
            caddyfile.parent.mkdir(parents=True)
            caddyfile.write_text(
                "forward_auth host.docker.internal:8766 {\n"
                "    header_up Authorization \"Bearer redacted-test-token\"\n"
                "}\n",
                encoding="utf-8",
            )
            with mock.patch.object(_domains, "PROXY_DIR", proxy_dir), \
                 mock.patch.object(_domains, "PROXY_CADDYFILE", caddyfile), \
                 mock.patch.object(_domains, "registry_all", return_value=records), \
                 mock.patch.object(_domains, "resolve_instances", return_value=wp), \
                 mock.patch.object(_domains, "_activation_gateway_healthy",
                                   return_value=False), \
                 mock.patch.object(_domains, "_cert_paths", return_value=(
                     proxy_dir / "missing.pem", proxy_dir / "missing-key.pem")):
                _domains.regen_caddyfile({})
            rendered = caddyfile.read_text(encoding="utf-8")

        self.assertNotIn("forward_auth host.docker.internal:8766", rendered)
        self.assertIn("reverse_proxy host.docker.internal:8080", rendered)


class ActivationSchedulerTests(unittest.TestCase):
    def make_scheduler(self, *, state="ready", safe=(True, "idle"), stop=True):
        from sandbox.activation import ActivationService
        from sandbox.activation.catalog import build_catalog
        from sandbox.activation.scheduler import ActivationScheduler
        records, wp = _records()
        catalog = build_catalog(records, wp)
        calls = []
        scheduler = ActivationScheduler(
            catalog, ActivationService(), observe_state=lambda _route: state,
            activity_safe=lambda _route: safe,
            suspend=lambda route, timeout: calls.append((route.route_id, timeout)) or stop,
        )
        scheduler.reconcile()
        route = catalog.routes()[0]
        return scheduler, route, calls

    def test_reconciles_live_ready_then_suspends_due_route(self):
        scheduler, route, calls = self.make_scheduler()
        snapshot = scheduler.service.coordinator.snapshot(route.route_id)
        due = float(snapshot["last_activity"]) + int(snapshot["policy"]["idle_after_seconds"]) + 1
        results = scheduler.scan(now=due)
        self.assertEqual(results[0].action, "suspended")
        self.assertEqual(len(calls), 1)
        self.assertEqual(scheduler.service.coordinator.snapshot(route.route_id)["state"], "asleep")

    def test_dry_run_and_active_evidence_never_stop(self):
        scheduler, route, calls = self.make_scheduler()
        snapshot = scheduler.service.coordinator.snapshot(route.route_id)
        due = float(snapshot["last_activity"]) + 1801
        self.assertEqual(scheduler.scan(now=due, dry_run=True)[0].action, "would_suspend")
        self.assertEqual(calls, [])
        scheduler, route, calls = self.make_scheduler(safe=(False, "active_job"))
        snapshot = scheduler.service.coordinator.snapshot(route.route_id)
        self.assertEqual(scheduler.scan(now=float(snapshot["last_activity"]) + 1801)[0].reason,
                         "active_job")
        self.assertEqual(calls, [])

    def test_uncertain_restart_is_error_and_never_suspends(self):
        scheduler, route, calls = self.make_scheduler(state="unknown")
        self.assertEqual(scheduler.service.coordinator.snapshot(route.route_id)["state"], "error")
        self.assertEqual(scheduler.scan(now=10**12), ())
        self.assertEqual(calls, [])

    def test_scheduler_source_never_uses_destructive_compose_operations(self):
        source = Path("sandbox/activation/scheduler.py").read_text(encoding="utf-8")
        for forbidden in ("docker pause", "compose down", "recreate", "volume rm"):
            self.assertNotIn(forbidden, source.lower())


class ActivationLeaseTests(unittest.TestCase):
    def test_instance_activity_is_visible_and_released(self):
        from sandbox.activation import leases
        with TemporaryDirectory() as temp, \
             mock.patch.object(leases, "_root", return_value=Path(temp)):
            self.assertFalse(leases.has_active_instance_lease("site"))
            with leases.instance_activity("site", "wordpress_cli", ttl_seconds=30):
                self.assertTrue(leases.has_active_instance_lease("site"))
            self.assertFalse(leases.has_active_instance_lease("site"))

    def test_malformed_lease_pins_fail_closed(self):
        from sandbox.activation import leases
        with TemporaryDirectory() as temp, \
             mock.patch.object(leases, "_root", return_value=Path(temp)):
            folder = Path(temp) / "site"
            folder.mkdir()
            (folder / "bad.json").write_text("{}", encoding="utf-8")
            self.assertTrue(leases.has_active_instance_lease("site"))


if __name__ == "__main__":
    unittest.main()

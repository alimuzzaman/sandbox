import threading
import time
import unittest
from unittest import mock


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


if __name__ == "__main__":
    unittest.main()

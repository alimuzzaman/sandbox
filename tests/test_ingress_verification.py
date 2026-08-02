from __future__ import annotations
import unittest


class Http:
    def __init__(self, failing=()): self.failing = set(failing); self.calls = []
    def probe(self, url, *, timeout): self.calls.append((url, timeout)); return url not in self.failing
    def probe_route(self, address, port, host, *, timeout):
        target = (address, port, host)
        self.calls.append((target, timeout))
        return target not in self.failing


class TestIngressVerification(unittest.TestCase):
    def test_new_hostname_and_every_baseline_route_must_remain_healthy(self):
        from sandbox.ingress.verification import IngressVerifier
        http = Http(); verifier = IngressVerifier(
            http=http, baseline_urls=lambda _plan: ({
                "address": "127.0.0.1", "port": 8123, "host": "localhost",
            },))
        plan = {"hostname": "demo.test", "protocols": ("http",),
                "listen": {"address": "127.0.0.1", "port": 80}}
        self.assertTrue(verifier.baseline(plan)["ok"])
        self.assertTrue(verifier.route(
            plan, {"present": True, "hostname": "demo.test"})["ok"])
        self.assertEqual([call[0] for call in http.calls], [
            ("127.0.0.1", 8123, "localhost"),
            ("127.0.0.1", 80, "demo.test"),
        ])

    def test_observation_failure_or_baseline_regression_fails_closed(self):
        from sandbox.ingress.verification import IngressVerifier
        verifier = IngressVerifier(
            http=Http({("127.0.0.1", 8123, "localhost")}),
            baseline_urls=lambda _plan: ({
                "address": "127.0.0.1", "port": 8123, "host": "localhost",
            },),
        )
        self.assertFalse(verifier.baseline({})["ok"])
        self.assertFalse(verifier.route(
            {"hostname": "demo.test", "protocols": ("http",)},
            {"present": False})["ok"])

    def test_route_identity_and_staged_content_must_match_before_http_probe(self):
        from sandbox.ingress.verification import IngressVerifier
        http = Http(); verifier = IngressVerifier(http=http)
        result = verifier.route({
            "route_id": "owned", "hostname": "demo.test",
            "backend": {"port": 8123}, "content_digest": "expected",
        }, {
            "route_id": "owned", "hostname": "demo.test",
            "backend": {"port": 8123}, "content_digest": "foreign",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["mismatches"], ["content_digest"])
        self.assertEqual(http.calls, [])

    def test_required_foreign_baseline_cannot_pass_vacuously(self):
        from sandbox.ingress.verification import IngressVerifier
        verifier = IngressVerifier(
            http=Http(), baseline_urls=lambda plan: plan.get("_baseline_urls", ()),
        )
        result = verifier.baseline({"_baseline_required": True, "_baseline_urls": ()})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "baseline_samples_unavailable")


if __name__ == "__main__": unittest.main()

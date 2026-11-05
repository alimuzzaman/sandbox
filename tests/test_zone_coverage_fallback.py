from __future__ import annotations

import unittest

from sandbox.hosting.zone_coverage import (
    COVERAGE_FALLBACK_UNVERIFIED,
    CoverageError,
)
from sandbox.hosting.zone_coverage.fallback import (
    FALLBACK_BODY,
    render_fallback_conf,
    verify_origin_probe_result,
)


class TestZoneCoverageFallback(unittest.TestCase):
    def test_render_fallback_conf_exact_contents(self):
        conf = render_fallback_conf(
            "asb.bd",
            cert_path="/etc/sandbox-edge/certs/coverage-asb.bd/origin.pem",
            key_path="/etc/sandbox-edge/certs/coverage-asb.bd/origin.key",
            ipv6=False,
            http2_directive=True,
        )
        self.assertIn("server_name *.asb.bd;", conf)
        self.assertIn("listen 80;", conf)
        self.assertIn("listen 443 ssl;", conf)
        self.assertIn("http2 on;", conf)
        self.assertIn("ssl_certificate /etc/sandbox-edge/certs/coverage-asb.bd/origin.pem;", conf)
        self.assertIn("ssl_certificate_key /etc/sandbox-edge/certs/coverage-asb.bd/origin.key;", conf)
        self.assertIn("default_type text/html;", conf)
        self.assertIn('add_header X-Robots-Tag "noindex, nofollow" always;', conf)
        self.assertIn('add_header X-Sandbox-Fallback "1" always;', conf)
        self.assertIn(f'return 404 "{FALLBACK_BODY}";', conf)

    def test_render_fallback_conf_ipv6_and_legacy_http2(self):
        conf = render_fallback_conf(
            "asb.bd",
            cert_path="/etc/cert.pem",
            key_path="/etc/key.pem",
            ipv6=True,
            http2_directive=False,
        )
        self.assertIn("listen [::]:80;", conf)
        self.assertIn("listen 443 ssl http2;", conf)
        self.assertIn("listen [::]:443 ssl http2;", conf)
        self.assertNotIn("http2 on;", conf)

    def test_edge_fallback_marking_and_content_isolation(self):
        # FR-006, FR-023: noindex, error-only, never content of another site
        conf = render_fallback_conf("asb.bd", cert_path="/c.pem", key_path="/k.pem")
        self.assertIn('add_header X-Robots-Tag "noindex, nofollow" always;', conf)
        self.assertIn('add_header X-Sandbox-Fallback "1" always;', conf)
        self.assertIn(FALLBACK_BODY, conf)
        # Ensure no proxy_pass or root directive exists in fallback
        self.assertNotIn("proxy_pass", conf)
        self.assertNotIn("root /", conf)

    def test_origin_probe_success(self):
        valid = {
            "status": 404,
            "fallback_header": "1",
            "body": FALLBACK_BODY,
        }
        res = verify_origin_probe_result(valid)
        self.assertTrue(res)

    def test_origin_probe_rejects_non_404_status(self):
        for status in (200, 301, 403, 500, 502):
            with self.subTest(status=status):
                with self.assertRaises(CoverageError) as ctx:
                    verify_origin_probe_result({
                        "status": status,
                        "fallback_header": "1",
                        "body": FALLBACK_BODY,
                    })
                self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

    def test_origin_probe_rejects_missing_or_wrong_header(self):
        for header in (None, "", "0", "true"):
            with self.subTest(header=header):
                with self.assertRaises(CoverageError) as ctx:
                    verify_origin_probe_result({
                        "status": 404,
                        "fallback_header": header,
                        "body": FALLBACK_BODY,
                    })
                self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

    def test_origin_probe_rejects_altered_body(self):
        for body in ("", "Not found", "<html><body>Other site</body></html>"):
            with self.subTest(body=body):
                with self.assertRaises(CoverageError) as ctx:
                    verify_origin_probe_result({
                        "status": 404,
                        "fallback_header": "1",
                        "body": body,
                    })
                self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

    def test_origin_probe_rejects_tls_or_connection_error(self):
        with self.assertRaises(CoverageError) as ctx:
            verify_origin_probe_result({"error": "SSLError", "message": "certificate verify failed"})
        self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()

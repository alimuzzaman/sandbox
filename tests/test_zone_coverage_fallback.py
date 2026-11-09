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
    def test_f9_origin_probe_sni_and_port_argument(self):
        import os
        import socket
        import ssl
        import subprocess
        import sys
        import tempfile
        import threading
        from sandbox.hosting.zone_coverage.fallback import _PROBE_SCRIPT, FALLBACK_BODY

        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "key.pem")
            cert_path = os.path.join(td, "cert.pem")
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key_path,
                 "-out", cert_path, "-days", "1", "-nodes", "-subj", "/CN=probe-sub.asb.bd"],
                check=True, capture_output=True,
            )

            received_sni = []
            def sni_cb(sock, req_hostname, ctx):
                received_sni.append(req_hostname)
                return None

            server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
            server_ctx.sni_callback = sni_cb

            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind(("127.0.0.1", 0))
            server_sock.listen(1)
            port = server_sock.getsockname()[1]

            def run_server():
                try:
                    conn, _ = server_sock.accept()
                    with server_ctx.wrap_socket(conn, server_side=True) as tls_conn:
                        _ = tls_conn.recv(1024)
                        resp = (
                            b"HTTP/1.1 404 Not Found\r\n"
                            b"Content-Type: text/html\r\n"
                            b"X-Sandbox-Fallback: 1\r\n"
                            b"Content-Length: " + str(len(FALLBACK_BODY)).encode() + b"\r\n"
                            b"Connection: close\r\n\r\n" + FALLBACK_BODY.encode()
                        )
                        tls_conn.sendall(resp)
                except Exception:
                    pass
                finally:
                    server_sock.close()

            th = threading.Thread(target=run_server, daemon=True)
            th.start()

            # Execute _PROBE_SCRIPT via subprocess passing target host and port
            proc = subprocess.run(
                [sys.executable, "-c", _PROBE_SCRIPT, "probe-sub.asb.bd", str(port)],
                capture_output=True,
                text=True,
            )
            th.join(timeout=3)

            self.assertEqual(proc.returncode, 0, "Probe script failed: " + str(proc.stderr))
            self.assertEqual(received_sni, ["probe-sub.asb.bd"], f"Expected SNI probe-sub.asb.bd, got {received_sni}")

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

    def test_render_fallback_conf_refuses_hosts_outside_the_zone_before_answering(self):
        conf = render_fallback_conf("my-zone.asb.bd", cert_path="/c.pem", key_path="/k.pem")
        guard = 'if ($host !~* "^([^.*]+\\.)+my\\-zone\\.asb\\.bd$") {\n        return 444;\n    }'
        self.assertIn(guard, conf)
        self.assertLess(conf.index(guard), conf.index("return 404"))

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


    def test_g7_origin_probe_errors_never_leak_message_or_secret_marker(self):
        import subprocess, sys, json
        from sandbox.hosting.zone_coverage.fallback import _PROBE_SCRIPT, verify_origin_probe_result

        # 1. Run probe script with an exception holding SECRET-MARKER
        harness = (
            "import socket; "
            "socket.create_connection = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('internal SECRET-MARKER error')); "
        ) + _PROBE_SCRIPT
        proc = subprocess.run([sys.executable, '-c', harness, 'test.asb.bd', '443'], capture_output=True, text=True)
        self.assertNotIn('SECRET-MARKER', proc.stdout)
        self.assertNotIn('SECRET-MARKER', proc.stderr)

        # 2. Verify origin probe result never exposes message in raised CoverageError
        data = {'error': 'CustomError', 'message': 'sensitive SECRET-MARKER detail'}
        with self.assertRaises(CoverageError) as ctx:
            verify_origin_probe_result(data)
        self.assertNotIn('SECRET-MARKER', str(ctx.exception))
        self.assertIn('CustomError', str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

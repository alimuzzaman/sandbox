from __future__ import annotations
import unittest


class TestIngressNginx(unittest.TestCase):
    def test_owned_fragment_has_exact_listener_backend_and_no_tls_claim(self):
        from sandbox.ingress.adapters.nginx import render_nginx
        from sandbox.ingress.manifest import built_in_ingress_registry
        text = render_nginx("a" * 64, "demo.test", "127.0.0.1", 8123,
                            {"address": "127.0.0.1", "port": 80})
        self.assertIn("listen 127.0.0.1:80;", text)
        self.assertIn("proxy_pass http://127.0.0.1:8123;", text)
        self.assertNotIn("ssl", text)
        self.assertNotIn("https", built_in_ingress_registry().get("system-nginx").declaration.capabilities)


if __name__ == "__main__": unittest.main()

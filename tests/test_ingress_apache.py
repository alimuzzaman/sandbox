from __future__ import annotations
import unittest


class TestIngressApache(unittest.TestCase):
    def test_fragment_uses_proxy_modules_and_graceful_fixed_service_contract(self):
        from sandbox.ingress.adapters.apache import render_apache
        from pathlib import Path
        text = render_apache("b" * 64, "demo.test", "127.0.0.1", 8123,
                             {"address": "127.0.0.1", "port": 80})
        self.assertIn("ProxyPass / http://127.0.0.1:8123/", text)
        helper = (Path(__file__).parent.parent / "tools/ingress-helper.sh").read_text()
        self.assertIn("apache2ctl configtest", helper)
        self.assertIn("systemctl reload apache2.service", helper)


if __name__ == "__main__": unittest.main()

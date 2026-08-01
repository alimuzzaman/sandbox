from __future__ import annotations
import unittest


class TestIngressCaddy(unittest.TestCase):
    def test_persistent_fragment_requires_explicit_import_and_validation(self):
        from sandbox.ingress.adapters.caddy import render_caddy
        from pathlib import Path
        text = render_caddy("c" * 64, "demo.test", "127.0.0.1", 8123,
                            {"address": "0.0.0.0", "port": 80})
        self.assertIn("reverse_proxy 127.0.0.1:8123", text)
        helper = (Path(__file__).parent.parent / "tools/ingress-helper.sh").read_text()
        self.assertIn("Caddyfile does not import", helper)
        self.assertIn("caddy validate --config /etc/caddy/Caddyfile", helper)


if __name__ == "__main__": unittest.main()

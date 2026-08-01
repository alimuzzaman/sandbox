from __future__ import annotations
import unittest


class TestIngressTraefik(unittest.TestCase):
    def test_dynamic_fragment_requires_enabled_file_provider_and_bounded_schema(self):
        from sandbox.ingress.adapters.traefik import render_traefik
        from pathlib import Path
        text = render_traefik("d" * 64, "demo.test", "127.0.0.1", 8123,
                              {"address": "0.0.0.0", "port": 80})
        self.assertIn("loadBalancer", text)
        helper = (Path(__file__).parent.parent / "tools/ingress-helper.sh").read_text()
        self.assertIn("Traefik file provider is not enabled", helper)
        self.assertIn("candidate contains a forbidden key", helper)


if __name__ == "__main__": unittest.main()

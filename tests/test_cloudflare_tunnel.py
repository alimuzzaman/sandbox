from __future__ import annotations

import unittest

from sandbox.core import _cloudflare_tunnel as tunnel


class TestTunnelValidation(unittest.TestCase):
    def test_exact_ingress_and_terminal_catch_all_are_required(self):
        config = {"config": {"ingress": [
            {"hostname": "hermes.asb.bd", "service": "http://127.0.0.1:9120"},
            {"service": "http_status:404"},
        ]}}
        self.assertEqual(tunnel.validate_configuration(config, "hermes.asb.bd", "http://127.0.0.1:9120")["hostname"], "hermes.asb.bd")
        config["config"]["ingress"][-1] = {"service": "http://127.0.0.1:9999"}
        with self.assertRaises(tunnel.TunnelError):
            tunnel.validate_configuration(config, "hermes.asb.bd", "http://127.0.0.1:9120")

    def test_connector_unit_uses_only_a_token_file(self):
        rendered = tunnel.service_unit("hermes-cloudflared.service", "%h/.hermes/cloudflared-token")
        self.assertIn("--token-file %h/.hermes/cloudflared-token", rendered)
        self.assertIn("--no-autoupdate", rendered)
        self.assertNotIn("--token ey", rendered)
        self.assertIn("NoNewPrivileges=true", rendered)

from __future__ import annotations
import unittest


class TestIngressCaddy(unittest.TestCase):
    def test_persistent_fragment_requires_explicit_import_and_validation(self):
        from sandbox.ingress.adapters.caddy import render_caddy
        from pathlib import Path
        text = render_caddy("c" * 64, "demo.test", "127.0.0.1", 8123,
                            {"address": "127.0.0.1", "port": 80})
        self.assertIn("bind 127.0.0.1", text)
        self.assertIn("reverse_proxy 127.0.0.1:8123", text)
        helper = (Path(__file__).parent.parent / "tools/ingress-helper.sh").read_text()
        self.assertIn("Caddyfile does not import", helper)
        self.assertIn("caddy validate --config /etc/caddy/Caddyfile", helper)

    def test_current_build_composes_only_proof_scoped_exact_http_system_caddy(self):
        from pathlib import Path
        from unittest import mock
        import sandbox_core as sc
        from sandbox.application.context import ingress_service
        from sandbox.ingress.manifest import IngressProofAttestation
        with mock.patch.object(sc, "sandbox_base", return_value=Path("/tmp/ingress-context")):
            service = ingress_service({}, platform="linux")
        caddy = service.registry.get("system-caddy")
        self.assertEqual(type(caddy.adapter).__name__, "CaddyAdapter")
        self.assertFalse(caddy.adoptable)
        with mock.patch.object(sc, "sandbox_base", return_value=Path("/tmp/ingress-context")):
            proof = ingress_service(
                {}, platform="linux",
                proof_attestation=IngressProofAttestation(
                    "system-caddy", "ubuntu-live-http-exact",
                ),
            )
        self.assertTrue(proof.registry.get("system-caddy").adoptable)
        self.assertEqual(caddy.declaration.capabilities, frozenset({"http"}))
        for adapter_id in ("sandbox-caddy", "herd-valet", "system-nginx",
                           "system-apache", "traefik"):
            self.assertFalse(service.registry.get(adapter_id).adoptable)


if __name__ == "__main__": unittest.main()

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


class TestWildcardBindIsRestrictedToLoopbackClients(unittest.TestCase):
    """Binding the incumbent's wildcard socket must not publish the instance."""

    def test_loopback_bind_renders_a_plain_reverse_proxy(self):
        from sandbox.ingress.adapters.caddy import render_caddy

        content = render_caddy("route1", "demo.test", "127.0.0.1", 8188,
                               {"address": "127.0.0.1", "port": 80})
        self.assertIn("bind 127.0.0.1", content)
        self.assertIn("reverse_proxy 127.0.0.1:8188", content)
        self.assertNotIn("remote_ip", content)

    def test_wildcard_bind_serves_loopback_clients_only(self):
        from sandbox.ingress.adapters.caddy import render_caddy

        content = render_caddy("route1", "demo.test", "127.0.0.1", 8188,
                               {"address": "::", "port": 80,
                                "loopback_clients_only": True})
        self.assertIn("bind ::", content)
        self.assertIn("@loopback remote_ip 127.0.0.0/8 ::1", content)
        self.assertIn("respond 403", content)
        proxy_line = [line for line in content.splitlines() if "reverse_proxy" in line][0]
        self.assertIn("127.0.0.1:8188", proxy_line)

    def test_plan_refuses_a_routable_listen_address(self):
        from sandbox.ingress.adapters.file_fragment import FileFragmentAdapter

        adapter = FileFragmentAdapter(
            process=None, helper="/usr/local/libexec/sandbox-ingress-helper",
            network_root="/tmp", render=lambda *args, **kwargs: "",
        )
        with self.assertRaises(ValueError):
            adapter.plan_route(
                {"listen": {"address": "203.0.113.5", "port": 80},
                 "protocols": ("http",), "authority": {"socket": "1"}},
                {"hostname": "demo.test", "owner": "/tmp/project::default"},
                {"address": "127.0.0.1", "port": 8188},
            )

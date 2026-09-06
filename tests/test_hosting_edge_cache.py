from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from sandbox.hosting import edge_cache
from sandbox.core import _cloudflare as cloudflare
from sandbox.hosting.images.activation.models import activation_digest
from sandbox.hosting.images.activation.v2_models import GenerationBoundEdgeReceiptV2

DIGEST_A = "sha256:" + "a" * 64


class EdgeCachePolicyTests(unittest.TestCase):
    routes = [
        {"hostname": "app.example.test", "mode": "serve"},
        {"hostname": "*.example.test", "mode": "serve"},
        {"hostname": "www.example.test", "mode": "redirect", "target": "https://app.example.test"},
    ]

    def test_policy_is_explicit_and_deduplicated(self):
        policy = edge_cache.validate_policy(
            {"on_deploy": True, "scope": "zone_all", "zones": ["example.test"]},
            routes=self.routes, proxied=True,
        )
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["zones"], ["example.test"])
        self.assertEqual(policy["routes"], ["*.example.test", "app.example.test", "www.example.test"])
        self.assertRegex(policy["policy_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_policy_requires_every_route_to_be_in_allowlist(self):
        with self.assertRaisesRegex(edge_cache.EdgeCacheError, "policy_route_mismatch"):
            edge_cache.validate_policy(
                {"on_deploy": True, "scope": "zone_all", "zones": ["example.test"]},
                routes=[{"hostname": "app.other.test"}], proxied=True,
            )

    def test_disabled_policy_preserves_non_opt_in_behavior(self):
        policy = edge_cache.validate_policy(None, routes=self.routes, proxied=True)
        self.assertFalse(policy["enabled"])
        with self.assertRaisesRegex(edge_cache.EdgeCacheError, "provider_not_configured"):
            edge_cache.build_purge_plan(
                policy=policy, project="demo", environment="production",
                request_id="request-1",
            )


class EdgeCacheProviderTests(unittest.TestCase):
    def _plan(self):
        policy = edge_cache.validate_policy(
            {"on_deploy": True, "scope": "zone_all", "zones": ["example.test", "other.test"]},
            routes=[{"hostname": "app.example.test"}, {"hostname": "app.other.test"}],
            proxied=True,
        )
        return edge_cache.build_purge_plan(
            policy=policy, project="demo", environment="production",
            request_id="request-1", deployment_revision="a" * 40,
        )

    def test_resolves_every_zone_before_first_post_and_returns_closed_receipt(self):
        client = Mock()
        client.zone.side_effect = [
            {"id": "zone-a", "name": "example.test"},
            {"id": "zone-b", "name": "other.test"},
        ]
        client.purge_cache.side_effect = [{"id": "purge-a"}, {"id": "purge-b"}]
        receipt = edge_cache.purge_plan(client=client, plan=self._plan())
        self.assertEqual(client.zone.call_count, 2)
        self.assertEqual(client.purge_cache.call_count, 2)
        self.assertEqual([item["state"] for item in receipt["zones"]], ["acknowledged", "acknowledged"])
        self.assertEqual(receipt["scope"], "zone_all")
        self.assertRegex(receipt["receipt_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_v2_receipt_binds_purge_to_activation_request(self):
        plan = self._plan()
        plan["activation_request_digest"] = DIGEST_A
        # Recompute the plan identity exactly as the production builder does.
        from sandbox.hosting.edge_cache import _digest
        identity = {key: plan[key] for key in (
            "schema_version", "project", "environment", "request_id",
            "deployment_revision", "generation_subject_digest",
            "activation_request_digest", "policy_digest", "routes", "zones", "scope")}
        plan["request_digest"] = _digest(identity)
        client = Mock()
        client.zone.side_effect = [{"id": "zone-a", "name": "example.test"},
                                   {"id": "zone-b", "name": "other.test"}]
        client.purge_cache.side_effect = [{"id": "purge-a"}, {"id": "purge-b"}]
        purge = edge_cache.purge_plan(client=client, plan=plan)
        body = {"schema_version": 2, "request_digest": DIGEST_A,
                "target": {"machine_identity": "m", "target_identity": "t",
                           "daemon_identity": "d"}, "generation": 1,
                "generation_subject_digest": DIGEST_A, "route_digest": DIGEST_A,
                "observation_digest": DIGEST_A, "terminal": True,
                "cache_purge_receipt": purge}
        receipt = {**body, "receipt_digest": activation_digest(
            "sandbox.hosting.images.generation-bound-edge-receipt.v2", body)}
        self.assertEqual(GenerationBoundEdgeReceiptV2.from_mapping(receipt).cache_purge_receipt,
                         purge)

    def test_zone_mismatch_refuses_without_any_purge(self):
        client = Mock()
        client.zone.side_effect = [
            {"id": "zone-a", "name": "wrong.test"},
        ]
        with self.assertRaisesRegex(edge_cache.EdgeCacheError, "zone_mismatch"):
            edge_cache.purge_plan(client=client, plan=self._plan())
        client.purge_cache.assert_not_called()

    def test_provider_error_is_bounded_and_does_not_search_parent(self):
        client = Mock()
        client.zone.side_effect = cloudflare.CloudflareError("denied", "provider_auth")
        with self.assertRaisesRegex(edge_cache.EdgeCacheError, "provider_auth"):
            edge_cache.purge_plan(client=client, plan=self._plan())
        self.assertEqual(client.zone.call_count, 1)
        client.purge_cache.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_cloudflare_client_uses_zone_wide_purge_endpoint(self, urlopen):
        response = Mock()
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: None
        response.read.return_value = b'{"success":true,"result":{"id":"purge-1"}}'
        urlopen.return_value = response
        client = cloudflare.Client("token")
        self.assertEqual(client.purge_cache("zone-1"), {"id": "purge-1"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/zones/zone-1/purge_cache"))
        self.assertEqual(request.data, b'{"purge_everything": true}')


if __name__ == "__main__":
    unittest.main()

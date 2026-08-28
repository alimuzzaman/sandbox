"""MCP contract coverage for manual tiered resource reclamation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


MCP_ROOT = Path(__file__).parent.parent / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_ROOT))


class _ScopeService:
    def __init__(self):
        self.calls = []

    def plan(self, scope, *, thorough, budget_seconds):
        self.calls.append(("plan", scope, thorough, budget_seconds))
        return {"ok": True, "action": "plan", "status": "planned",
                "data": {"plan_id": "scope-plan", "scope": scope}}

    def cleanup(self, plan_id, *, confirm):
        self.calls.append(("cleanup", plan_id, confirm))
        return {"ok": True, "action": "cleanup", "status": "completed",
                "data": {"plan_id": plan_id}}


class _ReclaimService:
    def __init__(self):
        self.calls = []

    def plan(self, tier, *, budget_seconds):
        self.calls.append(("plan", tier, budget_seconds))
        # This is deliberately already the public ReclaimService envelope:
        # the MCP adapter must not re-shape it or leak an alternate path.
        return {"ok": True, "action": "plan", "status": "planned",
                "data": {"plan_id": "tier-plan", "tier": tier,
                         "candidates": [], "skipped": [],
                         "estimated_reclaimable_bytes": 0,
                         "tier_totals": {"safe": 0, "tmp": 0, "all": 0},
                         "requires_confirmation": True}}

    def cleanup(self, *, tier, confirm):
        self.calls.append(("cleanup", tier, confirm))
        return {"ok": True, "action": "cleanup", "status": "completed",
                "data": {"tier": tier, "observed_reclaimed_bytes": 0}}


class _NodeStoreService:
    def __init__(self):
        self.calls = []

    def plan(self, family, *, budget_seconds):
        self.calls.append(("plan", family, budget_seconds))
        return {"ok": True, "action": "node_store_plan", "status": "planned",
                "data": {"plan_id": "a" * 32, "family": family}}

    def apply(self, plan_id, *, family, confirm):
        self.calls.append(("apply", plan_id, family, confirm))
        return {"ok": True, "action": "node_store_cleanup", "status": "removed",
                "data": {"plan_id": plan_id, "family": family}}


class _Server:
    @staticmethod
    def tool():
        return lambda function: function


class TestMcpResourceTier(unittest.TestCase):
    def setUp(self):
        from dependencies import ToolDependencies
        from tools import resources

        self.resources = resources
        self.scope = _ScopeService()
        self.reclaim = _ReclaimService()
        self.node_store = _NodeStoreService()
        resources.register(_Server(), ToolDependencies({
            "resource_service_factory": lambda _remote: self.scope,
            "reclaim_service_factory": lambda _remote: self.reclaim,
            "node_store_service_factory": lambda _remote: self.node_store,
        }))

    def test_node_store_plan_and_apply_use_exact_registered_service(self):
        planned = self.resources.resource_cleanup_plan(
            node_store_family="lenzora", remote="fixture", budget_seconds=12,
        )
        applied = self.resources.resource_cleanup_apply(
            plan_id="a" * 32, node_store_family="lenzora",
            remote="fixture", confirm=True,
        )
        self.assertTrue(planned["ok"] and applied["ok"])
        self.assertEqual(self.node_store.calls, [
            ("plan", "lenzora", 12),
            ("apply", "a" * 32, "lenzora", True),
        ])
        self.assertEqual(self.scope.calls, [])
        self.assertEqual(self.reclaim.calls, [])

    def test_tier_plan_routes_to_reclaim_service_with_its_public_payload(self):
        payload = self.resources.resource_cleanup_plan(
            tier="tmp", remote="fixture", thorough=False, budget_seconds=37,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["tier"], "tmp")
        self.assertEqual(self.reclaim.calls, [("plan", "tmp", 37)])
        self.assertEqual(self.scope.calls, [])

    def test_scope_plan_keeps_the_legacy_resource_service_route(self):
        payload = self.resources.resource_cleanup_plan(
            scope="cache", remote="fixture", thorough=False, budget_seconds=37,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(self.scope.calls, [("plan", "cache", False, 37)])
        self.assertEqual(self.reclaim.calls, [])

    def test_plan_refuses_ambiguous_missing_and_unknown_modes_before_services(self):
        cases = (
            ({"scope": "cache", "tier": "safe"}, "invalid_mode"),
            ({}, "invalid_scope"),
            ({"tier": "unsafe"}, "invalid_tier"),
        )
        for kwargs, code in cases:
            with self.subTest(kwargs=kwargs):
                payload = self.resources.resource_cleanup_plan(**kwargs)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], code)
        self.assertEqual(self.scope.calls, [])
        self.assertEqual(self.reclaim.calls, [])

    def test_apply_checks_confirmation_before_any_mode_or_service(self):
        payload = self.resources.resource_cleanup_apply(
            plan_id="scope-plan", tier="safe", confirm=False,
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "confirmation_required")
        self.assertEqual(self.scope.calls, [])
        self.assertEqual(self.reclaim.calls, [])

    def test_tier_apply_uses_manual_reclaim_service_path(self):
        payload = self.resources.resource_cleanup_apply(tier="safe", confirm=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["tier"], "safe")
        self.assertEqual(self.reclaim.calls, [("cleanup", "safe", True)])
        self.assertEqual(self.scope.calls, [])

    def test_plan_id_apply_keeps_the_legacy_resource_service_path(self):
        payload = self.resources.resource_cleanup_apply(
            plan_id="scope-plan", confirm=True,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(self.scope.calls, [("cleanup", "scope-plan", True)])
        self.assertEqual(self.reclaim.calls, [])

    def test_apply_refuses_invalid_xor_and_unknown_tier_before_services(self):
        cases = (
            ({"plan_id": "scope-plan", "tier": "safe"}, "invalid_mode"),
            ({}, "invalid_mode"),
            ({"tier": "unsafe"}, "invalid_tier"),
        )
        for kwargs, code in cases:
            with self.subTest(kwargs=kwargs):
                payload = self.resources.resource_cleanup_apply(confirm=True, **kwargs)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["status"], "refused")
                self.assertEqual(payload["error"]["code"], code)
        self.assertEqual(self.scope.calls, [])
        self.assertEqual(self.reclaim.calls, [])

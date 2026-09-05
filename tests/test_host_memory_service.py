from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from sandbox.resources.host_memory.service import HostMemoryService
from tests.host_memory_fixtures import MARKER, NOW, REVISION, status_state
from tests.host_memory_fixtures import sample
from tests.host_memory_assertions import assert_privacy_bounded

class FakeRemote:
    name="r"; marker=MARKER; revision=REVISION; record={"identity":"host"}
    def __init__(self): self.calls=[]; self.apply_status="applied"
    def call(self,action,**fields):
        self.calls.append((action,fields))
        if action=="host_memory_status": return status_state(target_identity="host")
        if action=="host_memory_history": return {"samples":[],"counts":{"returned":0},"complete":True,"truncated":False}
        return {"status":self.apply_status,"data":{"operation_id":fields["operation_id"]},"error":None}

class HostMemoryServiceTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.remote=FakeRemote(); self.service=HostMemoryService(self.remote,now=lambda:NOW)
    def tearDown(self): self.tmp.cleanup()
    def test_status_rejects_known_only_or_sensitive_fake_adapter_results(self):
        for state in ({"evidence_state":"known"},
                      {**status_state(), "source_path":"/proc/meminfo"}):
            self.remote.call=lambda action, **fields: state
            result=self.service.status()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"],"response_invalid")

    def test_status_composes_freshness_warning_and_projection(self):
        state = status_state(target_identity="host", monitor={
            **status_state()["monitor"], "service_state":"active", "timer_state":"active",
            "latest_sample_at":"2026-08-30T11:55:00Z", "age_seconds":300,
            "interval_seconds":300, "freshness":"fresh", "sustained_swap_use":True,
            "pressure_state":"normal", "next_sample_at":"2026-08-30T12:00:00Z"})
        self.remote.call = lambda action, **fields: state
        result = self.service.status()
        self.assertEqual(result["data"]["monitor"]["freshness"], "fresh")
        self.assertTrue(result["data"]["monitor"]["sustained_swap_use"])
        projection = self.service.projection(result["data"])
        self.assertTrue(projection.sustained_swap_use)
        assert_privacy_bounded(self, projection.to_dict(), maximum=64*1024)

    def test_unknown_status_is_partial_and_non_authorizing(self):
        self.remote.call = lambda action, **fields: status_state(evidence_state="unknown",
                                                                 target_identity="host")
        result = self.service.status()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "partial")

    def test_plan_is_deterministic_and_controller_owned(self):
        first = self.service.plan()
        second = self.service.plan()
        self.assertTrue(first["ok"])
        self.assertEqual(first["data"]["plan_id"], second["data"]["plan_id"])
        self.assertEqual(first["data"]["effective_policy"]["size_gib"], 4)
        actions = [call[0] for call in self.remote.calls]
        self.assertTrue(actions)
        self.assertEqual(set(actions), {"host_memory_status"})

    def test_plan_propagates_valid_sizes_and_refuses_invalid(self):
        for size in (1, 8):
            result = self.service.plan(size_gib=size)
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["requested_policy"], {"size_gib": size})
        for size in (0, 9):
            result = self.service.plan(size_gib=size)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_size")

    def test_plan_binds_confirmation_without_mutation(self):
        result = self.service.plan(size_gib=4)
        self.assertTrue(result["data"]["requires_confirmation"])
        self.assertEqual(result["data"]["state"], "planned")
        for action, _fields in self.remote.calls:
            self.assertEqual(action, "host_memory_status")

    def test_apply_requires_exact_confirmation(self):
        plan_res = self.service.plan(size_gib=4)
        plan_id = plan_res["data"]["plan_id"]
        for bad in (False, None, "true", 1):
            result = self.service.apply(plan_id, confirmed=bad)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["error"]["code"], "confirmation_required")
        apply_calls = [c for c in self.remote.calls if c[0] == "host_memory_apply"]
        self.assertEqual(apply_calls, [])

    def test_apply_refuses_unknown_or_expired_plan(self):
        result = self.service.apply("f" * 64, confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["error"]["code"], "plan_not_found")

        plan_res = self.service.plan(size_gib=4)
        plan = dict(plan_res["data"])
        plan["expires_at"] = "2026-08-30T11:59:00Z"
        expired_res = self.service.apply(plan, confirmed=True)
        self.assertFalse(expired_res["ok"])
        self.assertEqual(expired_res["status"], "refused")
        self.assertEqual(expired_res["error"]["code"], "plan_expired")

    def test_apply_dispatches_canonical_plan_and_binds_operation_id(self):
        plan_res = self.service.plan(size_gib=4)
        plan_id = plan_res["data"]["plan_id"]
        result = self.service.apply(plan_id, confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "applied")
        apply_calls = [c for c in self.remote.calls if c[0] == "host_memory_apply"]
        self.assertEqual(len(apply_calls), 1)
        action, kwargs = apply_calls[0]
        self.assertEqual(action, "host_memory_apply")
        self.assertTrue(kwargs["confirmed"])
        self.assertEqual(kwargs["plan"]["plan_id"], plan_id)
        self.assertEqual(kwargs["plan"]["effective_policy"]["size_gib"], 4)
        assert_privacy_bounded(self, result, maximum=64 * 1024)

    def test_apply_already_current_propagates_cleanly(self):
        self.remote.apply_status = "already_current"
        plan_res = self.service.plan(size_gib=4)
        plan_id = plan_res["data"]["plan_id"]
        result = self.service.apply(plan_id, confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already_current")

    def test_apply_remote_unreachable_returns_failed(self):
        def bad_call(action, **fields):
            if action == "host_memory_status":
                return status_state(target_identity="host")
            raise RuntimeError("network down")
        self.remote.call = bad_call
        plan_res = self.service.plan(size_gib=4)
        plan_id = plan_res["data"]["plan_id"]
        result = self.service.apply(plan_id, confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")

    def test_history_retrieves_bounded_window_and_enforces_limits(self):
        result = self.service.history(limit=100)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "swap-history")
        self.assertIn("samples", result["data"])
        self.assertIn("counts", result["data"])
        assert_privacy_bounded(self, result, maximum=1024 * 1024)

    def test_disable_planning_and_apply_orchestration(self):
        area = {
            "area_id": "a" * 24, "type": "file", "total_bytes": 4 * 1024 ** 3,
            "used_bytes": 0, "active": True, "persistent": True, "priority": -2, "ownership": "owned",
        }
        self.remote.call = lambda action, **fields: {
            **status_state(target_identity="host", ownership="owned", swap_areas=[area]),
            "evidence_state": "known",
        } if action == "host_memory_status" else {"status": "applied", "data": {"operation_id": fields.get("operation_id")}, "error": None}
        # disable_plan builds a plan with operation="disable"
        plan_res = self.service.disable_plan()
        self.assertTrue(plan_res["ok"])
        self.assertEqual(plan_res["action"], "swap-plan")
        plan = plan_res["data"]
        self.assertEqual(plan["operation"], "disable")
        plan_id = plan["plan_id"]

        # unconfirmed apply is refused
        refused = self.service.apply(plan_id, confirmed=False)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "refused")

    def test_service_replay_operation_blocks_and_ambiguous_outcomes(self):
        # 1. Refusal when operation_block is active
        self.remote.call = lambda action, **fields: {
            **status_state(target_identity="host", operation_block={"operation_id": "a" * 64, "reason": "operation_in_progress"}),
            "evidence_state": "known",
        }
        plan_res = self.service.plan(size_gib=4)
        self.assertFalse(plan_res["ok"])
        self.assertEqual(plan_res["status"], "refused")
        self.assertEqual(plan_res["error"]["code"], "operation_in_progress")

        # 2. Refusal when rollback_incomplete
        self.remote.call = lambda action, **fields: {
            **status_state(target_identity="host", operation_block={"operation_id": "a" * 64, "reason": "rollback_incomplete"}),
            "evidence_state": "known",
        }
        plan_res = self.service.plan(size_gib=4)
        self.assertFalse(plan_res["ok"])
        self.assertEqual(plan_res["status"], "refused")
        self.assertEqual(plan_res["error"]["code"], "rollback_incomplete")

        # 3. Same-identity replay returns terminal outcome
        self.remote.call = lambda action, **fields: status_state(target_identity="host") if action == "host_memory_status" else {
            "status": "applied", "data": {"operation_id": fields.get("operation_id")}, "error": None,
        }
        plan_res = self.service.plan(size_gib=4)
        plan_id = plan_res["data"]["plan_id"]
        first_apply = self.service.apply(plan_id, confirmed=True)
        self.assertTrue(first_apply["ok"])
        # Replay same plan_id
        second_apply = self.service.apply(plan_id, confirmed=True)
        self.assertTrue(second_apply["ok"])
        self.assertIn(second_apply["status"], {"applied", "already_current"})

        # 4. Service with repository records operation and ledger lookup avoids duplicate remote call
        from sandbox.resources.host_memory.repository import HostMemoryRepository
        repo = HostMemoryRepository(Path(self.tmp.name))
        service_with_repo = HostMemoryService(self.remote, repo=repo, now=lambda: NOW)
        remote_calls = []
        self.remote.call = lambda action, **fields: remote_calls.append(action) or (
            status_state(target_identity="host") if action == "host_memory_status" else {
                "status": "applied", "data": {"operation_id": fields.get("operation_id")}, "error": None,
            }
        )
        p_res = service_with_repo.plan(size_gib=4)
        pid = p_res["data"]["plan_id"]
        app1 = service_with_repo.apply(pid, confirmed=True)
        self.assertTrue(app1["ok"])
        calls_count = len(remote_calls)
        app2 = service_with_repo.apply(pid, confirmed=True)
        self.assertTrue(app2["ok"])
        # Second apply must use ledger lookup and not call remote again
        self.assertEqual(len(remote_calls), calls_count)


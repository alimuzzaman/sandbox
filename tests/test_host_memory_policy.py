from __future__ import annotations
import unittest
from datetime import timedelta
from sandbox.resources.host_memory.policy import (
    GIB, PolicyRefusal, build_plan, disable_calculations, enable_calculations,
    freshness, sustained_swap_use,
)
from tests.host_memory_fixtures import NOW, eligible_state, sample, service_evidence


class HostMemoryPolicyTest(unittest.TestCase):
    def test_default_and_override_bounds(self):
        state=eligible_state()
        for size in (1,4,8): self.assertEqual(len(enable_calculations(state,size)),4)
        for size in (0,9,True):
            with self.assertRaises(PolicyRefusal): enable_calculations(state,size)
        with self.assertRaisesRegex(PolicyRefusal,"capacity"):
            enable_calculations(eligible_state(memory={"total_bytes":8*GIB,"available_bytes":7*GIB}),8)

    def test_plan_is_deterministic_and_controller_owned(self):
        target={"remote_name":"r","target_identity":"host","service_ownership_marker":"a"*24,"runtime_revision":"b"*24}
        one=build_plan("enable",target,eligible_state(),now=NOW)
        two=build_plan("enable",target,eligible_state(),now=NOW)
        self.assertEqual(one["plan_id"],two["plan_id"]); self.assertEqual(one["effective_policy"]["size_gib"],4)

    def test_disable_is_strictly_greater_and_owned(self):
        state=eligible_state(ownership="owned",swap_areas=[{"ownership":"owned","used_bytes":GIB,"total_bytes":4*GIB}])
        state["memory"]={"total_bytes":16*GIB,"available_bytes":3*GIB}
        self.assertTrue(disable_calculations(state)[0]["passed"])
        state["memory"]["available_bytes"]=2*GIB
        with self.assertRaises(PolicyRefusal): disable_calculations(state)

    def test_freshness_and_warning_exact_boundaries(self):
        self.assertEqual(freshness("2026-08-30T11:49:00Z",NOW),"fresh")
        self.assertEqual(freshness("2026-08-30T11:48:59Z",NOW),"stale")
        rows=[sample(f"2026-08-30T11:{minute}:00Z",512*1024*1024) for minute in (40,45,50)]
        self.assertTrue(sustained_swap_use(rows)); self.assertFalse(sustained_swap_use(rows[:2]))

    def test_unmanaged_and_operation_blocks_refuse(self):
        for state in (eligible_state(swap_areas=[{"ownership":"unmanaged"}]), eligible_state(operation_block={"reason":"active"})):
            with self.assertRaises(PolicyRefusal): build_plan("enable",{},state,now=NOW)

    def test_enable_accepts_exact_capacity_boundaries(self):
        state = eligible_state(
            memory={"total_bytes": 16 * GIB, "available_bytes": 12 * GIB},
            filesystem={"total_bytes": 80 * GIB, "free_bytes": 20 * GIB},
        )
        rows = enable_calculations(state, 8)
        self.assertTrue(all(row["passed"] for row in rows))
        self.assertEqual(rows[-1]["observed_bytes"], rows[-1]["threshold_bytes"])

    def test_negative_unknown_and_boolean_capacity_fail_closed(self):
        bad_states = (
            eligible_state(memory={"total_bytes": -1, "available_bytes": 0}),
            eligible_state(filesystem={"total_bytes": 100 * GIB, "free_bytes": True}),
            eligible_state(evidence_state="unknown"),
        )
        for state in bad_states:
            with self.subTest(state=state):
                with self.assertRaises(PolicyRefusal):
                    build_plan("enable", service_evidence(), state, now=NOW)

    def test_warning_ignores_partial_breaks_and_clock_regression(self):
        rows = [sample("2026-08-30T11:40:00Z", 512 * 1024 ** 2),
                sample("2026-08-30T11:45:00Z", 512 * 1024 ** 2, "partial"),
                sample("2026-08-30T11:50:00Z", 512 * 1024 ** 2),
                sample("2026-08-30T11:55:00Z", 512 * 1024 ** 2)]
        self.assertFalse(sustained_swap_use(rows))
        self.assertEqual(freshness("2026-08-30T12:00:01Z", NOW), "unknown")

    def test_plan_requested_and_effective_policy_for_every_size(self):
        from sandbox.resources.host_memory.policy import ACTIVE_ARTIFACTS
        target = service_evidence()
        for size in (1, 2, 4, 8):
            plan = build_plan("enable", target, eligible_state(), size_gib=size, now=NOW)
            self.assertEqual(plan["requested_policy"], {"size_gib": size})
            self.assertEqual(plan["effective_policy"]["size_gib"], size)
            self.assertEqual(plan["intended_changes"], list(ACTIVE_ARTIFACTS))
            self.assertEqual(plan["rollback_scope"], list(ACTIVE_ARTIFACTS))
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(plan["state"], "planned")

    def test_plan_expiry_is_created_plus_fifteen_minutes(self):
        plan = build_plan("enable", service_evidence(), eligible_state(), now=NOW)
        self.assertEqual(plan["expires_at"], "2026-08-30T12:15:00Z")

    def test_plan_current_refuses_expired_and_drifted(self):
        from sandbox.resources.host_memory.policy import plan_current
        target = service_evidence()
        plan = build_plan("enable", target, eligible_state(), now=NOW)
        with self.assertRaises(PolicyRefusal) as expired:
            plan_current(plan, eligible_state(), now=NOW + timedelta(minutes=16))
        self.assertEqual(expired.exception.code, "plan_expired")
        with self.assertRaises(PolicyRefusal) as drifted:
            plan_current(plan, eligible_state(
                memory={"total_bytes": 32 * GIB, "available_bytes": 24 * GIB}), now=NOW)
        self.assertEqual(drifted.exception.code, "plan_drifted")

    def test_plan_invalid_sizes_and_modes_refuse(self):
        target = service_evidence()
        for size in (0, 9, True, "4"):
            with self.subTest(size=size):
                with self.assertRaises(PolicyRefusal):
                    build_plan("enable", target, eligible_state(), size_gib=size, now=NOW)
        with self.assertRaises(PolicyRefusal) as mode:
            build_plan("format", target, eligible_state(), now=NOW)
        self.assertEqual(mode.exception.code, "invalid_mode")

    def test_already_enabled_converges_without_duplicate_mutation(self):
        target = service_evidence()
        state = eligible_state(
            ownership="owned",
            swap_areas=[{"ownership": "owned", "used_bytes": 0, "total_bytes": 4 * GIB}],
        )
        plan = build_plan("enable", target, state, size_gib=4, now=NOW)
        self.assertEqual(plan["state"], "already_current")

    def test_plan_refuses_unregistered_or_unsafe_targets(self):
        state = eligible_state()
        for target in (
            {},
            {"remote_name": "r"},
            {"remote_name": "r", "target_identity": "host"},
            {"remote_name": "r", "target_identity": "host",
             "service_ownership_marker": "short", "runtime_revision": "b" * 24},
            {"remote_name": "r", "target_identity": "../escape",
             "service_ownership_marker": "a" * 24, "runtime_revision": "b" * 24},
        ):
            with self.subTest(target=target):
                with self.assertRaises(PolicyRefusal) as refused:
                    build_plan("enable", target, state, now=NOW)
                self.assertEqual(refused.exception.code, "unregistered_target")

    def test_plan_refuses_stale_observations(self):
        old = eligible_state(observed_at="2026-08-30T09:00:00Z")
        with self.assertRaises(PolicyRefusal) as refused:
            build_plan("enable", service_evidence(), old, now=NOW)
        self.assertEqual(refused.exception.code, "evidence_stale")

    def test_plan_refuses_ambiguous_ownership(self):
        for ownership in ("ambiguous", "contested", "foreign"):
            with self.subTest(ownership=ownership):
                with self.assertRaises(PolicyRefusal) as refused:
                    build_plan("enable", service_evidence(),
                               eligible_state(ownership=ownership), now=NOW)
                self.assertEqual(refused.exception.code, "ownership_unknown")

    def test_disable_plan_policy_rules(self):
        target = service_evidence()
        # Refuses foreign, unmanaged, ambiguous ownership
        for ownership in ("foreign", "unmanaged", "ambiguous"):
            with self.subTest(ownership=ownership):
                with self.assertRaises(PolicyRefusal) as refused:
                    build_plan("disable", target, eligible_state(ownership=ownership), now=NOW)
                self.assertEqual(refused.exception.code, "ownership_unknown")

        # Refuses unmanaged swap area
        with self.assertRaises(PolicyRefusal) as refused:
            build_plan("disable", target, eligible_state(
                ownership="owned",
                swap_areas=[{"ownership": "unmanaged", "used_bytes": 0, "total_bytes": 1 * GIB}],
            ), now=NOW)
        self.assertIn(refused.exception.code, {"ownership_unknown", "unmanaged_swap"})

        # Headroom: strictly greater than
        # total 16 GiB -> max(1 GiB, 1.6 GiB) = 1.6 GiB; used = 1 GiB -> required = 2.6 GiB
        # available = 2.6 GiB -> available <= required -> Refuses
        state = eligible_state(
            ownership="owned",
            memory={"total_bytes": 16 * GIB, "available_bytes": 2 * GIB + 600 * 1024 * 1024},
            swap_areas=[{"ownership": "owned", "used_bytes": 1 * GIB, "total_bytes": 4 * GIB}],
        )
        with self.assertRaises(PolicyRefusal) as refused:
            build_plan("disable", target, state, now=NOW)
        self.assertEqual(refused.exception.code, "insufficient_disable_headroom")

        # Available = 4 GiB -> strictly greater -> success
        state["memory"]["available_bytes"] = 4 * GIB
        plan = build_plan("disable", target, state, now=NOW)
        self.assertEqual(plan["operation"], "disable")
        self.assertEqual(plan["state"], "planned")
        self.assertTrue(plan["requires_confirmation"])
        # Reverse teardown ordering required
        self.assertEqual(plan["intended_changes"][0], "monitor_timer")
        self.assertEqual(plan["intended_changes"][-1], "swap_file")

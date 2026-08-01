import unittest

from tests.test_managed_package_plan import TestManagedPackagePlan, VERSIONS


class TestManagedPackageApply(unittest.TestCase):
    def test_noninteractive_is_zero_mutation_and_never_confirms(self):
        from sandbox.runtimes.managed.packages import ManagedPackageService
        plan = TestManagedPackagePlan().planner().plan(); calls = []
        service = ManagedPackageService(
            replanner=lambda: plan,
            apply_transaction=lambda current: calls.append(current) or {"ok": True, "mutated": True},
            baseline_observer=lambda: {}, confirmation=lambda value: calls.append(value) or True,
        )
        result = service.apply(plan, interactive=False)
        self.assertEqual(result["state"], "pending_confirmation"); self.assertEqual(calls, [])

    def test_digest_drift_refuses_apply(self):
        from sandbox.runtimes.managed.packages import ManagedPackageService
        helper = TestManagedPackagePlan(); plan = helper.planner().plan()
        changed = helper.planner(versions={**VERSIONS, "nginx": "1.24.1"}).plan()
        calls = []
        service = ManagedPackageService(
            replanner=lambda: changed,
            apply_transaction=lambda current: calls.append(current),
            baseline_observer=lambda: {}, confirmation=lambda value: True,
        )
        result = service.apply(plan, interactive=True)
        self.assertEqual(result["reason"]["code"], "package_plan_drift")
        self.assertEqual(calls, [])

    def test_host_service_baseline_must_be_byte_for_byte_unchanged(self):
        from sandbox.runtimes.managed.packages import ManagedPackageService
        plan = TestManagedPackagePlan().planner().plan(); states = iter(({"nginx": "active"}, {"nginx": "stopped"}))
        service = ManagedPackageService(
            replanner=lambda: plan,
            apply_transaction=lambda current: {"ok": True, "mutated": True},
            baseline_observer=lambda: next(states), confirmation=lambda value: True,
        )
        result = service.apply(plan, interactive=True)
        self.assertEqual(result["reason"]["code"], "host_service_baseline_changed")


if __name__ == "__main__": unittest.main()

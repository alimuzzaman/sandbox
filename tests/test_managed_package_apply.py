import unittest
from pathlib import Path
import tempfile

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

    def test_staged_plan_and_root_helper_call_are_digest_bound_and_cleaned(self):
        from sandbox.runtimes.managed.packages import (
            NativeHostPackageApplier, PackagePlanStager,
        )
        plan = TestManagedPackagePlan().planner().plan(); calls = []

        class Process:
            def run(self, argv, **kwargs):
                calls.append((argv, kwargs))
                return type("Result", (), {"returncode": 0})()

        with tempfile.TemporaryDirectory() as directory:
            stager = PackagePlanStager(directory)
            result = NativeHostPackageApplier(
                process=Process(), repository_helper="/repo/native-helper.py",
                installed_helper="/installed/native-helper", stager=stager,
            ).apply(plan)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][0], ("sudo", "/repo/native-helper.py", "install"))
        self.assertEqual(calls[1][0][:4],
                         ("sudo", "-n", "/installed/native-helper", "host-packages-apply"))
        self.assertEqual(calls[1][0][-1], plan.simulation_digest)

    def test_foreign_service_config_and_data_baseline_is_content_free_and_stable(self):
        from sandbox.runtimes.managed.packages import HostServiceBaseline

        class Process:
            def run(self, argv, **kwargs):
                return type("Result", (), {"stdout": "LoadState=loaded\nActiveState=active\n"})()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = root / "config"; data = root / "data"
            config.mkdir(); data.mkdir(); (config / "foreign.conf").write_text("secret-value")
            (data / "ibdata1").write_bytes(b"database-bytes")
            observer = HostServiceBaseline(process=Process(), config_roots=(config,),
                                           data_roots=(data,))
            before = observer.observe(); after = observer.observe()
            self.assertEqual(before, after)
            self.assertNotIn("secret-value", str(before))
            (config / "foreign.conf").write_text("changed")
            self.assertNotEqual(before, observer.observe())


if __name__ == "__main__": unittest.main()

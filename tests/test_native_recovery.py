"""Drift, unavailable, retry, and idempotent cleanup contracts."""

import unittest
import contextlib
import io
from types import SimpleNamespace
from unittest import mock



class TestNativeRecovery(unittest.TestCase):
    def test_cleanup_progress_uses_later_proven_removals_to_reach_true_residual(self):
        from tests.test_native_destroy import TestNativeDestroy

        helper = TestNativeDestroy(
            methodName="test_owned_resources_are_compared_and_removed_in_safe_order_before_state",
        )
        helper.setUp(); self.addCleanup(helper.doCleanups)
        helper.put_owned("policies")
        machine_id = helper.policy.machine_id
        helper.repository.put_recovery(f"cleanup-progress:{machine_id}", {
            "owner": helper.owner, "object_type": "cleanup_progress",
            "identity": machine_id, "reason_code": "cleanup_in_progress",
            "retry_state": "pending", "removed": ("machine", "image"),
        })
        observed = []
        cleaner = helper.cleaner()

        def observe(name, _plan):
            observed.append(name)
            return None

        cleaner.observe = observe
        result = cleaner.cleanup(helper.request(), helper.plan())
        self.assertFalse(result["ok"])
        self.assertEqual(result["cleanup"]["residual"], ("network",))
        # Every resource is asked about, including the ones progress calls
        # removed: progress only decides what to do when the observer cannot
        # answer, which is the case here. It stops at the true residual either
        # way, and a progress record that turned out to be wrong cannot make
        # cleanup skip a resource the host still has.
        self.assertEqual(observed, ["services", "database", "machine", "network"])

    def test_drift_retains_residual_without_calling_remove_and_retry_converges(self):
        from tests.test_native_destroy import TestNativeDestroy
        helper = TestNativeDestroy(methodName="test_owned_resources_are_compared_and_removed_in_safe_order_before_state")
        helper.setUp(); self.addCleanup(helper.doCleanups)
        for section in ("backends", "policies", "networks"):
            helper.put_owned(section)
        cleaner = helper.cleaner()
        # A changed observation is retained at the exact resource and no later
        # image/policy action is attempted. Restoring it permits a retry.
        cleaner.observe = lambda name, _plan: ({"owned": "network", "generation": 2}
                                                if name == "network" else helper.expected[name])
        drifted = cleaner.cleanup(helper.request(), helper.plan())
        self.assertFalse(drifted["ok"]); self.assertEqual(drifted["cleanup"]["residual"], ("network",))
        self.assertNotIn(("network", "remove", "network"), helper.calls)
        key = f"cleanup:{helper.policy.machine_id}:network"
        self.assertIn(key, helper.repository.snapshot()["recovery"])
        cleaner.observe = lambda name, _plan: helper.expected[name]
        retried = cleaner.cleanup(helper.request(), helper.plan())
        self.assertTrue(retried["ok"])
        recovery = helper.repository.snapshot()["recovery"]
        self.assertNotIn(key, recovery)
        self.assertNotIn(f"cleanup-progress:{helper.policy.machine_id}", recovery)

    def _destroy_helper(self):
        from tests.test_native_destroy import TestNativeDestroy
        helper = TestNativeDestroy(
            methodName="test_owned_resources_are_compared_and_removed_in_safe_order_before_state")
        helper.setUp(); self.addCleanup(helper.doCleanups)
        return helper

    def test_absent_resource_converges_and_later_resources_are_still_removed(self):
        # A resource provisioning never created has nothing to remove, so cleanup
        # must pass it and keep going. Before this, the observation of a missing
        # resource looked like drift, cleanup stopped there for good, and the
        # surviving policy record then failed every later provisioning.
        helper = self._destroy_helper()
        for section in ("backends", "policies", "networks"):
            helper.put_owned(section)
        cleaner = helper.cleaner()
        cleaner.observe = lambda name, _plan: (
            {**helper.expected[name], "state": "absent"} if name in {"database", "network"}
            else helper.expected[name])

        result = cleaner.cleanup(helper.request(), helper.plan())

        self.assertTrue(result["ok"]); self.assertTrue(result["cleanup"]["complete"])
        self.assertEqual([(name, action) for name, action, _ in helper.calls], [
            ("services", "stop"), ("machine", "stop"), ("image", "unmount"),
            ("image", "remove"), ("policy", "remove"),
        ])
        self.assertIn("policy", result["cleanup"]["removed"])
        self.assertFalse(helper.repository.snapshot()["policies"])

    def test_absence_must_be_read_not_inferred_from_an_unreadable_resource(self):
        # The distinction the whole fix rests on: an observer that could not
        # answer is a residual, never a resource proven gone.
        helper = self._destroy_helper()
        for section in ("backends", "policies", "networks"):
            helper.put_owned(section)
        cleaner = helper.cleaner()

        def observe(name, _plan):
            if name == "network":
                raise RuntimeError("observer unavailable")
            return helper.expected[name]

        cleaner.observe = observe
        result = cleaner.cleanup(helper.request(), helper.plan())

        self.assertFalse(result["ok"])
        self.assertEqual(result["cleanup"]["residual"], ("network",))
        self.assertNotIn(("network", "remove", "network"), helper.calls)
        self.assertIn(f"cleanup:{helper.policy.machine_id}:network",
                      helper.repository.snapshot()["recovery"])

    def test_an_unknown_observation_state_is_malformed_not_a_hint(self):
        from sandbox.runtimes.managed.helper import ManagedCleanupObserver

        class Process:
            def run(self, _argv, **_kwargs):
                payload = ('{"machine_id":"sb-1","policy_digest":"d","resource":"image",'
                           '"resource_digest":"d","state":"probably-gone"}')
                return SimpleNamespace(returncode=0, stdout=payload, stderr="")

        observer = ManagedCleanupObserver(process=Process(), helper="/helper")
        with self.assertRaises(RuntimeError):
            observer("image", {"machine_id": "sb-1", "policy_digest": "d"})

    def test_a_retained_failure_never_reports_untouched_resources_as_removed(self):
        # Machine ids are reused across attempts, so an earlier attempt's image
        # and network are real host objects. Recording steps this attempt did not
        # reach as already-removed told cleanup to skip exactly those and strand
        # them; only removals that actually happened count as progress.
        from sandbox.runtimes.managed.adapter import ManagedProvisioner

        helper = self._destroy_helper()
        machine_id = helper.policy.machine_id
        plan = {"policy": helper.policy, "machine_id": machine_id,
                "cleanup": helper.plan()["cleanup"],
                "record": {"owner": helper.owner}}
        provisioner = ManagedProvisioner.__new__(ManagedProvisioner)
        provisioner.repository = helper.repository
        provisioner._persist_incomplete_plan(plan, [])

        progress = helper.repository.snapshot()["recovery"][f"cleanup-progress:{machine_id}"]
        self.assertEqual(tuple(progress["removed"]), ())

    def test_a_wrong_progress_record_cannot_strand_a_resource_the_host_still_has(self):
        # The exact record a released bug left on the proof host: six steps
        # claimed removed while an 8.5 GB image and a network record were still
        # there. Cleanup must believe the host, not the claim.
        helper = self._destroy_helper()
        for section in ("backends", "policies", "networks"):
            helper.put_owned(section)
        machine_id = helper.policy.machine_id
        helper.repository.put_recovery(f"cleanup-progress:{machine_id}", {
            "owner": helper.owner, "object_type": "cleanup_progress",
            "identity": machine_id, "reason_code": "cleanup_in_progress",
            "retry_state": "pending",
            "removed": ("services", "database", "machine", "network", "mount", "image"),
        })

        result = helper.cleaner().cleanup(helper.request(), helper.plan())

        self.assertTrue(result["ok"])
        self.assertEqual([(name, action) for name, action, _ in helper.calls], [
            ("services", "stop"), ("database", "remove"), ("machine", "stop"),
            ("network", "remove"), ("image", "unmount"), ("image", "remove"),
            ("policy", "remove"),
        ])

    def test_early_absent_cleanup_retires_only_matching_stale_entries(self):
        from tests.test_native_destroy import TestNativeDestroy

        helper = TestNativeDestroy(methodName="test_owned_resources_are_compared_and_removed_in_safe_order_before_state")
        helper.setUp(); self.addCleanup(helper.doCleanups)
        machine_id = helper.policy.machine_id
        matching = f"cleanup:{machine_id}:services"
        foreign = f"cleanup:{machine_id}:database"
        helper.repository.put_recovery(matching, {
            "owner": helper.owner, "identity": machine_id,
            "object_type": "services", "reason_code": "cleanup_failed",
        })
        helper.repository.put_recovery(foreign, {
            "owner": {"project_root": "/foreign", "label": "default"},
            "identity": machine_id, "object_type": "database",
            "reason_code": "cleanup_failed",
        })
        helper.repository.put_recovery(f"cleanup-progress:{machine_id}", {
            "owner": helper.owner, "identity": machine_id,
            "object_type": "cleanup_progress", "removed": ("services",),
        })

        result = helper.cleaner().cleanup(helper.request(), helper.plan())

        self.assertTrue(result["ok"])
        recovery = helper.repository.snapshot()["recovery"]
        self.assertNotIn(matching, recovery)
        self.assertNotIn(f"cleanup-progress:{machine_id}", recovery)
        self.assertIn(foreign, recovery)

    def test_unavailable_cleanup_and_missing_cleanup_dependency_keep_secret_free_recovery(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        from pathlib import Path
        import tempfile

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        adapter = ManagedNativeAdapter(preflight=object(), repository=repository, dependencies=None,
                                       evidence_id="live-proof")
        result = adapter.invoke(OperationRequest("/tmp/project", "destroy"))
        self.assertFalse(result.ok); self.assertEqual(result.data["state"], "cleanup_incomplete")
        status = adapter.invoke(OperationRequest("/tmp/project", "status"))
        self.assertTrue(status.data["recovery"])
        self.assertNotIn("secret", repr(status.data).lower())

    def test_native_status_and_cleanup_use_runtime_contract_without_package_uninstall(self):
        import sandbox.commands.native as native
        from sandbox.runtimes.base import OperationResult

        service = mock.Mock()
        service.invoke.side_effect = lambda request: OperationResult(
            request.operation != "destroy", request.operation, request.project_root, "wordpress",
            {"state": "ready" if request.operation == "status" else "cleanup_incomplete",
             "mutated": request.operation == "destroy",
             "recovery": ({"object_type": "image", "reason_code": "runtime_unavailable",
                           "retry_state": "pending"},)},
        )
        args = SimpleNamespace(action="status", project_dir="/project", label="default",
                               web_server="nginx", json=True)
        output = io.StringIO()
        with mock.patch("sandbox.application.context.runtime_service", return_value=service), \
                contextlib.redirect_stdout(output):
            native.cmd_native({}, args)
        self.assertIn("runtime_unavailable", output.getvalue())
        self.assertNotIn("package", output.getvalue().lower())
        args.action = "cleanup"
        with mock.patch("sandbox.application.context.runtime_service", return_value=service), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            native.cmd_native({}, args)
        self.assertEqual([call.args[0].operation for call in service.invoke.call_args_list], ["status", "destroy"])


if __name__ == "__main__": unittest.main()

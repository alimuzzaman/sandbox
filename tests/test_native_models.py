import unittest


class TestNativeModels(unittest.TestCase):
    def test_runtime_selection_reports_truthful_isolation(self):
        from sandbox.runtimes.base import RuntimeSelection
        selected = RuntimeSelection(
            "/tmp/project", "default", "managed_native", "ubuntu-nspawn",
            "machine_override", {"php": "8.3"}, "managed_container",
            frozenset({"ensure", "exec"}),
        )
        self.assertEqual(selected.isolation_level, "managed_container")
        with self.assertRaises(ValueError):
            RuntimeSelection("/tmp/project", "default", "managed_native", "bad",
                             "machine_override", {}, "trusted_shared_host")

    def test_policy_digest_is_canonical_and_tamper_evident(self):
        from sandbox.isolation.models import ManagedIsolationPolicy
        values = dict(
            policy_version=1, machine_id="sb-0123456789ab",
            uid_map={"base": 200000, "count": 65536},
            root_image={"path": "/var/lib/sandbox/native/demo.img", "bytes": 1024},
            read_only_mounts=({"source": "/src", "target": "/workspace"},),
            writable_mounts=({"source": "/state", "target": "/state"},),
            network={"egress": "deny", "veth": "ve-sb-demo"},
            syscalls={"no_new_privileges": True, "seccomp": "managed-v1"},
            devices=frozenset({"null", "zero"}), resources={"memory_max": 1024},
            credentials=("db-password-ref",),
        )
        policy = ManagedIsolationPolicy(**values)
        self.assertEqual(len(policy.digest), 64)
        with self.assertRaises(ValueError):
            ManagedIsolationPolicy(**values, digest="0" * 64)

    def test_private_metadata_and_loopback_egress_are_rejected(self):
        from sandbox.isolation.models import EgressGrant
        for destination in ("127.0.0.1/32", "10.0.0.0/8", "169.254.169.254/32"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                EgressGrant("g", "owner", "public_cidr_tcp", (destination,), (443,), "later")
        grant = EgressGrant("g", "owner", "hostname_https", ("api.wordpress.org",),
                            (443,), "later")
        self.assertFalse(grant.revoked)

    def test_package_plan_digest_and_secret_redaction(self):
        from sandbox.runtimes.managed.models import PackageTransactionPlan
        plan = PackageTransactionPlan(
            "ubuntu-24.04", ({"name": "systemd-container", "version": "255"},),
            ({"name": "php8.3", "version": "8.3"},),
            ({"uri": "http://archive.ubuntu.com"},), (), ("/var/lib/sandbox",),
            ("image-create",),
        )
        self.assertEqual(len(plan.simulation_digest), 64)
        with self.assertRaises(ValueError):
            PackageTransactionPlan("x", ({"password": "leak"},), (), (), (), (), ())

    def test_backend_health_transitions_and_cleanup_records_are_structured(self):
        from sandbox.runtimes.managed.models import NativeBackendRecord
        from sandbox.isolation.models import NativeCleanupRecovery
        values = dict(owner={"root": "/tmp/project", "label": "default"},
                      mode="managed_native", adapter="ubuntu-nspawn",
                      backend={"address": "10.77.0.2", "port": 8080},
                      machine={"id": "sb-demo"}, php={"web": "8.3", "cli": "8.3"},
                      database={"reference": "db-credential"}, files={"image": "digest"})
        self.assertEqual(NativeBackendRecord(**values, health="ready").health, "ready")
        with self.assertRaises(ValueError):
            NativeBackendRecord(**values, health="running-ish")
        recovery = NativeCleanupRecovery("owner", "image", "sb-demo", "a" * 64,
                                         None, "runtime_unavailable", "pending")
        self.assertEqual(recovery.reason_code, "runtime_unavailable")


if __name__ == "__main__": unittest.main()

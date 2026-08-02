import unittest


def policy():
    from sandbox.isolation.models import ManagedIsolationPolicy
    return ManagedIsolationPolicy(
        1, "sb-0123456789ab", {"base": 200000, "count": 65536},
        {"path": "/images/demo.img"},
        ({"source": "/src", "target": "/workspace"},),
        ({"source": "/state", "target": "/state"},),
        {"egress": "deny", "guest_address": "10.203.0.2/30"},
        {"no_new_privileges": True}, frozenset(),
        {"memory_max": 1024, "pids_max": 64}, (),
    )


def healthy(target):
    return {"policy_digest": target.digest,
            "private_namespaces": {name: True for name in
                ("user", "mount", "pid", "ipc", "uts", "network")},
            "no_new_privileges": True, "capabilities": [], "seccomp": True,
            "apparmor_profile": f"sandbox-native-{target.machine_id}//payload",
            "nested_userns": False, "ambient_capabilities": [],
            "dangerous_capabilities": [], "devices": ["null", "zero", "urandom"],
            "nft_default_drop": True, "default_route": False,
            "guest_address": target.network["guest_address"],
            "reachability": {"host": False, "sibling": False,
                             "metadata": False, "public": False},
            "cgroup_limits": dict(target.resources),
            "read_only_mounts": ["/workspace"], "writable_mounts": ["/state"],
            "unexpected_host_mounts": [], "leaked_fds": [],
            "leaked_environment": [], "control_sockets": []}


class TestIsolationVerification(unittest.TestCase):
    def test_all_effective_boundaries_must_match(self):
        from sandbox.isolation.verification import IsolationVerifier
        target = policy(); result = IsolationVerifier(observe=lambda _id: healthy(target)).verify(target)
        self.assertTrue(result["ok"])

    def test_any_namespace_network_mount_resource_or_leak_drift_blocks(self):
        from sandbox.isolation.verification import IsolationVerifier
        target = policy()
        for key, value in (
            ("default_route", True), ("nft_default_drop", False),
            ("unexpected_host_mounts", ["/home"]), ("leaked_fds", [3]),
            ("cgroup_limits", {}), ("apparmor_profile", "unconfined"),
            ("nested_userns", True), ("reachability", {}),
        ):
            with self.subTest(key=key):
                observed = healthy(target); observed[key] = value
                result = IsolationVerifier(observe=lambda _id, value=observed: value).verify(target)
                self.assertFalse(result["ok"]); self.assertEqual(result["state"], "blocked")

    def test_active_grant_cannot_report_ready_before_production_broker_exists(self):
        from sandbox.isolation.models import EgressGrant, EgressGrantSet
        from sandbox.isolation.verification import IsolationVerifier
        target = policy()
        # Egress is a separate control-plane document and intentionally does
        # not alter the stable policy digest.
        target = type("Policy", (), {**target.__dict__, "network": {
            **dict(target.network), "host_address": "10.203.0.1/30", "veth": "ve-sb-demo",
        }})()
        grant = EgressGrant("api", target.machine_id, "public_cidr_tcp",
                            ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z")
        granted = EgressGrantSet(target.machine_id, target.digest, (grant,))
        result = IsolationVerifier(observe=lambda _id: healthy(target)).verify(target, grants=granted)
        self.assertFalse(result["ok"])
        self.assertIn("egress_broker", result["reason"]["failed_gates"])

        observed = healthy(target)
        observed["egress_broker"] = {
            "ok": True, "policy_digest": target.digest, "grant_digest": granted.digest,
            "listener": {"address": "10.203.0.1", "port": 18443,
                         "interface": "ve-sb-demo"},
            "grants": {"api": {"accepted": 0, "rejected": 0, "bytes": 0,
                                "active": 0}},
        }
        ready = IsolationVerifier(observe=lambda _id: observed).verify(target, grants=granted)
        self.assertTrue(ready["ok"])


if __name__ == "__main__": unittest.main()

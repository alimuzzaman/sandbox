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
            "apparmor_profile": f"sandbox-native-{target.machine_id}//guest",
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


if __name__ == "__main__": unittest.main()

import unittest


class Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode; self.stdout = stdout


class Process:
    def __init__(self, result=None): self.calls = []; self.result = result or Result()
    def run(self, argv, **kwargs): self.calls.append((argv, kwargs)); return self.result


class Policy:
    machine_id = "sb-0123456789ab"
    digest = "a" * 64
    network = {"egress": "deny", "default_route": False,
               "host_address": "10.203.0.1/30", "guest_address": "10.203.0.2/30",
               "veth": "ve-sb-demo", "ingress_port": 8080, "grants": []}


class TestManagedNetwork(unittest.TestCase):
    def test_fixed_helper_verbs_are_digest_bound(self):
        from sandbox.isolation.network import ManagedNetwork
        process = Process(); network = ManagedNetwork(process=process, helper="/fixed/helper")
        plan = network.plan(Policy())
        network.apply(plan); network.status(plan); network.remove(plan)
        self.assertEqual([call[0][3] for call in process.calls],
                         ["network-apply", "network-status", "network-remove"])
        for argv, kwargs in process.calls:
            self.assertEqual(argv[:3], ("sudo", "-n", "/fixed/helper"))
            self.assertEqual(argv[-2:], (Policy.machine_id, Policy.digest))
            self.assertEqual(kwargs["timeout"], 120)

    def test_default_route_or_allow_policy_is_rejected_before_helper(self):
        from sandbox.isolation.network import ManagedNetwork
        process = Process(); network = ManagedNetwork(process=process, helper="helper")
        for changed in ({**Policy.network, "default_route": True},
                        {**Policy.network, "egress": "allow"}):
            instance = type("Changed", (), {"machine_id": Policy.machine_id,
                "digest": Policy.digest, "network": changed})()
            with self.assertRaises(ValueError): network.plan(instance)
        self.assertEqual(process.calls, [])


if __name__ == "__main__": unittest.main()

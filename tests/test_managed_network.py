import unittest


class Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode; self.stdout = stdout


class Process:
    def __init__(self, result=None): self.calls = []; self.result = result or Result()
    def run(self, argv, **kwargs): self.calls.append((argv, kwargs)); return self.result


class SequenceProcess(Process):
    def __init__(self, results): super().__init__(); self.results = list(results)
    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs)); return self.results.pop(0)


class Policy:
    machine_id = "sb-0123456789ab"
    digest = "a" * 64
    network = {"egress": "deny", "default_route": False,
               "host_address": "10.203.0.1/30", "guest_address": "10.203.0.2/30",
               "veth": "ve-sb-demo", "ingress_port": 8080,
               "grant_authority": "staged-v1", "grants": []}


class TestManagedNetwork(unittest.TestCase):
    def test_fixed_helper_verbs_are_digest_bound(self):
        from sandbox.isolation.network import ManagedNetwork
        process = Process(); network = ManagedNetwork(process=process, helper="/fixed/helper")
        plan = network.plan(Policy())
        network.apply(plan); network.status(plan); network.remove(plan)
        self.assertEqual([call[0][3] for call in process.calls],
                         ["egress-remove", "network-apply", "network-status",
                          "egress-remove", "network-remove"])
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

    def test_status_parses_digest_bound_helper_counters(self):
        from sandbox.isolation.network import ManagedNetwork
        payload = ('{"ok":true,"policy_digest":"' + Policy.digest + '",'
                   '"counters":{"guest_host_drop":{"packets":3,"bytes":192}}}')
        process = Process(Result(stdout=payload))
        network = ManagedNetwork(process=process, helper="helper")
        result = network.status(network.plan(Policy()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["counters"]["guest_host_drop"]["packets"], 3)

    def test_status_rejects_malformed_or_wrong_digest_observation(self):
        from sandbox.isolation.network import ManagedNetwork
        for stdout in ("not-json", '{"ok":true,"policy_digest":"wrong"}'):
            with self.subTest(stdout=stdout):
                process = Process(Result(stdout=stdout))
                result = ManagedNetwork(process=process, helper="helper").status(
                    ManagedNetwork(process=process, helper="helper").plan(Policy()))
                self.assertFalse(result["ok"])

    def test_embedded_grants_are_rejected_before_helper(self):
        from sandbox.isolation.network import ManagedNetwork
        process = Process(); network = ManagedNetwork(process=process, helper="helper")
        policy = type("Changed", (), {"machine_id": Policy.machine_id,
                       "digest": Policy.digest,
                       "network": {**Policy.network, "grants": [{"grant_id": "api"}]}})()
        with self.assertRaises(ValueError):
            network.plan(policy)
        self.assertEqual(process.calls, [])

    def test_grant_reconcile_stages_digest_bound_document_without_argv_payload(self):
        import tempfile
        from sandbox.isolation.models import EgressGrant, EgressGrantSet, ManagedIsolationPolicy
        from sandbox.isolation.network import EgressGrantReconciler

        policy = ManagedIsolationPolicy(
            1, Policy.machine_id, {"base": 200000, "count": 65536},
            {"path": "/images/demo.img"}, (), (),
            {key: value for key, value in Policy.network.items() if key != "grants"},
            {"no_new_privileges": True}, frozenset(), {"memory_max": 1024}, (),
        )
        grant = EgressGrant("api", policy.machine_id, "public_cidr_tcp",
                            ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z")
        desired = EgressGrantSet(policy.machine_id, policy.digest, (grant,))
        expected = "0" * 64
        process = Process()
        with tempfile.TemporaryDirectory() as staging:
            result = EgressGrantReconciler(
                process=process, helper="/fixed/helper", staging_root=staging,
            ).reconcile(policy, desired, expected_digest=expected)
            self.assertTrue(result["ok"])
            self.assertFalse(list(__import__("pathlib").Path(staging).iterdir()))
        argv, kwargs = process.calls[0]
        self.assertEqual(argv, ("sudo", "-n", "/fixed/helper", "grant-reconcile",
                                policy.machine_id, policy.digest, expected,
                                desired.digest))
        self.assertEqual(kwargs["timeout"], 120)


if __name__ == "__main__": unittest.main()

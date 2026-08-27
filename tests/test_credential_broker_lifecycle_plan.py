import json
import unittest
from types import SimpleNamespace


class Process:
    def __init__(self, output="", code=0): self.output, self.code, self.calls = output, code, []
    def run(self, argv, timeout):
        self.calls.append((argv, timeout))
        return SimpleNamespace(returncode=self.code, stdout=self.output, stderr="")


class TestCredentialBrokerLifecyclePlan(unittest.TestCase):
    DIGESTS = {"broker_digest": "1" * 64, "executable_digest": "2" * 64,
               "config_digest": "3" * 64}

    def compiler(self):
        from sandbox.runtimes.managed.services import CredentialBrokerPlanCompiler
        return CredentialBrokerPlanCompiler(**self.DIGESTS)

    def plan(self):
        from sandbox.runtimes.managed.services import CredentialBrokerPlanCompiler
        from tests.test_isolation_verification import policy
        base = policy()
        target = SimpleNamespace(
            machine_id=base.machine_id, digest=base.digest,
            network={"veth": "ve-sb01234567", "host_address": "10.203.0.1/30",
                     "guest_address": "10.203.0.2/30"},
        )
        return self.compiler().compile(target, egress_digest="4" * 64)

    def test_plan_is_secret_free_canonical_and_fixed(self):
        plan = self.plan()
        self.assertEqual(plan["guest_port"], 18443)
        self.assertEqual(plan["limits"]["active_requests"], 16)
        self.assertEqual(plan["host_address"], "10.203.0.1")
        self.assertEqual(plan["guest_address"], "10.203.0.2")
        self.assertEqual(plan["subnet"], "10.203.0.0/30")
        text = json.dumps(plan)
        for forbidden in ("source_reference", "body", "header", "lease_id", "operation_id"):
            self.assertNotIn(forbidden, text)

    def test_supervisor_uses_only_fixed_argv_and_exact_closed_status(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        plan = self.plan()
        status = {"ok": True, "state": "credential_pending", "admission_open": False,
                  "broker_epoch": "5" * 64, "pid": 123,
                  "process_start_identity": "123:991827", "service_uid": 991,
                  "unit_identity": f"sandbox-credential-broker@{plan['machine_id']}.service",
                  "cgroup_identity": f"/sandbox.slice/credential-broker/{plan['machine_id']}",
                  **{key: plan[key] for key in
                     ("machine_id", "policy_digest", "egress_digest", "broker_digest",
                      "executable_digest", "config_digest")}}
        process = Process(json.dumps(status))
        supervisor = CredentialBrokerSupervisor(process=process, helper="/fixed/helper",
                                                **self.DIGESTS)
        self.assertTrue(supervisor.status(plan)["ok"])
        self.assertEqual(process.calls[0][0], (
            "sudo", "-n", "/fixed/helper", "credential-broker-status",
            plan["machine_id"], plan["policy_digest"], plan["egress_digest"], plan["broker_digest"]))
        process.output = json.dumps({**status, "endpoint": "private"})
        self.assertEqual(supervisor.status(plan)["state"], "drifted")

        stopped = {**status, "state": "stopped"}
        process.output = json.dumps(stopped)
        self.assertEqual(supervisor.status(plan)["state"], "stopped")
        process.output = json.dumps({**stopped, "ok": False})
        self.assertEqual(supervisor.status(plan)["state"], "unavailable")
        process.output = json.dumps({**stopped, "process_start_identity": "999:991827"})
        self.assertEqual(supervisor.status(plan)["state"], "drifted")

    def test_start_output_is_discarded_and_plan_drift_refused(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        process = Process("credential-material-must-not-surface")
        supervisor = CredentialBrokerSupervisor(process=process, helper="/fixed/helper",
                                                **self.DIGESTS)
        result = supervisor.start(self.plan())
        self.assertNotIn("credential-material", json.dumps(result))
        changed = self.plan(); changed["guest_port"] = 443
        with self.assertRaises(ValueError): supervisor.start(changed)

    def test_network_and_broadcast_addresses_are_refused(self):
        from tests.test_isolation_verification import policy
        base = policy()
        for host, guest in (("10.203.0.0/30", "10.203.0.2/30"),
                            ("10.203.0.1/30", "10.203.0.3/30")):
            target = SimpleNamespace(
                machine_id=base.machine_id, digest=base.digest,
                network={"veth": "ve-sb01234567", "host_address": host,
                         "guest_address": guest})
            with self.subTest(host=host, guest=guest), self.assertRaises(ValueError):
                self.compiler().compile(target, egress_digest="4" * 64)


if __name__ == "__main__": unittest.main()

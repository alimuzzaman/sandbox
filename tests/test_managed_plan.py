from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


class Component:
    def plan(self, policy):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest}


class Network(Component):
    pass


class Database:
    def plan(self, *, owner, machine_id):
        return {"owner": owner, "machine_id": machine_id, "production": "sb_prod",
                "tests": "sb_tests", "user": "sbu_user",
                "credential_refs": (f"native/{machine_id}/db-credential",),
                "socket": "/run/mysqld/mysqld.sock", "network_exposed": False}


class Services:
    def compile(self, policy, *, web_server):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "web_server": web_server, "backend": {"address": "10.203.0.2", "port": 8080},
                "files": {}, "file_digests": {}, "units": (), "digest": "a" * 64}


class TestManagedPlan(unittest.TestCase):
    def test_plan_is_deterministic_secret_free_and_allocations_do_not_overlap(self):
        from sandbox.isolation.resources import ResourcePolicyCompiler
        from sandbox.runtimes.managed.plan import ManagedPlanBuilder
        from sandbox.runtimes.managed.repository import NativeRepository
        with tempfile.TemporaryDirectory() as temp:
            repository = NativeRepository(Path(temp) / "state.json")
            packages = SimpleNamespace(plan=lambda **_kwargs: SimpleNamespace(simulation_digest="p" * 64))
            builder = ManagedPlanBuilder(
                repository=repository, packages=packages, resources=ResourcePolicyCompiler(),
                network=Network(), image=Component(), apparmor=Component(), machine=Component(),
                database=Database(), services=Services(),
            )
            one = builder(SimpleNamespace(project_root=temp, label="default", arguments={}))
            again = builder(SimpleNamespace(project_root=temp, label="default", arguments={}))
            other_root = Path(temp) / "other"; other_root.mkdir()
            other = builder(SimpleNamespace(project_root=str(other_root), label="default", arguments={}))
            self.assertEqual(one["policy"].digest, again["policy"].digest)
            self.assertNotEqual(one["policy"].network["guest_address"],
                                other["policy"].network["guest_address"])
            self.assertNotEqual(one["policy"].uid_map["base"], other["policy"].uid_map["base"])
            self.assertNotIn("password", repr(one).lower())
            self.assertFalse(one["policy"].network["default_route"])
            self.assertEqual(one["policy"].read_only_mounts[0]["target"], "/workspace")

    def test_apply_egress_argument_changes_only_the_separate_grant_set(self):
        from sandbox.isolation.resources import ResourcePolicyCompiler
        from sandbox.runtimes.managed.plan import ManagedPlanBuilder
        from sandbox.runtimes.managed.repository import NativeRepository
        with tempfile.TemporaryDirectory() as temp:
            builder = ManagedPlanBuilder(
                repository=NativeRepository(Path(temp) / "state.json"),
                packages=SimpleNamespace(plan=lambda **_kwargs: SimpleNamespace(simulation_digest="p" * 64)),
                resources=ResourcePolicyCompiler(), network=Network(), image=Component(),
                apparmor=Component(), machine=Component(), database=Database(), services=Services(),
            )
            baseline = builder(SimpleNamespace(project_root=temp, label="default", arguments={}))
            granted = builder(SimpleNamespace(project_root=temp, label="default", arguments={
                "egress": [{"grant_id": "api", "kind": "public_cidr_tcp",
                            "destinations": ["8.8.8.8/32"], "ports": [443],
                            "expires_at": "2999-01-01T00:00:00Z"}],
            }))
            self.assertEqual(baseline["policy"].digest, granted["policy"].digest)
            self.assertNotIn("grants", granted["policy"].network)
            self.assertNotEqual(baseline["grant_set"].digest, granted["grant_set"].digest)
            with self.assertRaises(ValueError):
                builder(SimpleNamespace(project_root=temp, label="default", arguments={"egress": "all"}))

    def test_policy_store_never_puts_policy_json_on_privileged_argv(self):
        from sandbox.runtimes.managed.plan import ManagedPolicyStore
        from tests.test_isolation_verification import policy
        calls = []
        process = SimpleNamespace(run=lambda argv, timeout: calls.append(argv) or
                                  SimpleNamespace(returncode=0, stdout=""))
        with tempfile.TemporaryDirectory() as temp:
            target = policy()
            store = ManagedPolicyStore(process=process, helper="/fixed/helper", staging_root=temp)
            store.install(target)
            self.assertEqual(calls[0][:5], ("sudo", "-n", "/fixed/helper", "policy-install",
                                            target.machine_id))
            self.assertNotIn(target.digest, calls[0])


if __name__ == "__main__": unittest.main()

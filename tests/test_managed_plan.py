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

    def test_managed_php_extensions_map_only_to_catalogued_signed_apt_rows(self):
        from sandbox.runtimes.managed.plan import ManagedExtensionPackagePlanner

        versions = {"php8.3-imagick": "3.7.0", "php8.3-common": "8.3.6", **__import__(
            "tests.test_managed_package_plan", fromlist=["VERSIONS"]
        ).VERSIONS}
        base = __import__("tests.test_managed_package_plan",
                          fromlist=["TestManagedPackagePlan"]).TestManagedPackagePlan().planner(
                              versions=versions)
        package_plan, extension_plan = ManagedExtensionPackagePlanner(base).resolve(
            requirements={"profile": "wordpress@1", "extensions": {
                "gd": True,
                "imagick": {"state": "enabled", "version": "3.7.0"},
            }}
        )
        self.assertEqual(extension_plan.profile, "wordpress@1")
        self.assertEqual(len(extension_plan.digest), 64)
        image_rows = [dict(row) for row in package_plan.image_packages
                      if row.get("php_extensions")]
        self.assertTrue(image_rows)
        imagick = next(row for row in image_rows
                       if any(item["name"] == "imagick" for item in row["php_extensions"]))
        self.assertEqual(imagick["name"], "php8.3-imagick")
        evidence = next(item for item in imagick["php_extensions"] if item["name"] == "imagick")
        self.assertEqual(evidence["package_version"], "3.7.0")
        self.assertTrue(evidence["catalog_digest"].startswith("sha256:"))
        self.assertEqual(evidence["source"], "official-distribution")
        self.assertTrue(all(item["source"] == "official-distribution"
                            for item in imagick["php_extensions"]))

    def test_profile_only_request_selects_deterministic_gd_capability(self):
        from sandbox.runtimes.managed.plan import ManagedExtensionPackagePlanner

        package_tests = __import__("tests.test_managed_package_plan",
                                   fromlist=["TestManagedPackagePlan", "VERSIONS"])
        base = package_tests.TestManagedPackagePlan().planner(
            versions={"php8.3-common": "8.3.6", **package_tests.VERSIONS},
        )
        package_plan, extension_plan = ManagedExtensionPackagePlanner(base).resolve(
            requirements={"profile": "wordpress@1"},
        )
        names = {item["name"] for item in extension_plan.requirements}
        self.assertIn("gd", names)
        self.assertNotIn("imagick", names)
        gd_rows = [row for row in package_plan.image_packages
                   if any(item["name"] == "gd"
                          for item in row.get("php_extensions", ()))]
        self.assertTrue(gd_rows)

    def test_managed_php_extension_requirements_reject_packages_sources_and_observation_only(self):
        from sandbox.runtimes.managed.plan import ManagedExtensionPackagePlanner
        base = __import__("tests.test_managed_package_plan",
                          fromlist=["TestManagedPackagePlan"]).TestManagedPackagePlan().planner()
        planner = ManagedExtensionPackagePlanner(base)
        for requirements in (
            {"extensions": {"gd": {"state": "enabled", "package": "apt-evil"}}},
            {"extensions": {"gd": "8.3; rm -rf /"}},
            {"extensions": {"bcmath": True}},
        ):
            with self.subTest(requirements=requirements), self.assertRaises(ValueError):
                planner.resolve(requirements=requirements)

    def test_extension_requirement_or_catalog_drift_invalidates_existing_approval(self):
        from sandbox.runtimes.managed.packages import ManagedPackageService
        from sandbox.runtimes.managed.plan import ManagedExtensionPackagePlanner
        base = __import__("tests.test_managed_package_plan",
                          fromlist=["TestManagedPackagePlan"]).TestManagedPackagePlan().planner()
        planner = ManagedExtensionPackagePlanner(base)
        approved, _ = planner.resolve(requirements={"extensions": {"gd": True}})
        changed, _ = planner.resolve(requirements={"extensions": {"gd": "8.3.*"}})
        applied = []
        service = ManagedPackageService(
            replanner=lambda: changed,
            apply_transaction=lambda plan: applied.append(plan) or {"ok": True, "mutated": True},
            baseline_observer=lambda: {"digest": "same"},
            confirmation=lambda _plan: True,
        )
        result = service.apply(approved, interactive=True)
        self.assertEqual(result["state"], "drifted")
        self.assertFalse(applied)

    def test_invalid_php_extensions_are_rejected_before_network_reservation(self):
        from sandbox.isolation.resources import ResourcePolicyCompiler
        from sandbox.runtimes.managed.plan import ManagedPlanBuilder
        from sandbox.runtimes.managed.repository import NativeRepository

        package_tests = __import__(
            "tests.test_managed_package_plan", fromlist=["TestManagedPackagePlan"]
        )
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            repository = NativeRepository(state_path)
            repository.put_owned("backends", "unrelated", {
                "owner": "unrelated", "value": "preserve",
            })
            before_bytes = state_path.read_bytes()
            before_state = repository.snapshot()
            builder = ManagedPlanBuilder(
                repository=repository,
                packages=package_tests.TestManagedPackagePlan().planner(),
                resources=ResourcePolicyCompiler(), network=Network(), image=Component(),
                apparmor=Component(), machine=Component(), database=Database(),
                services=Services(),
            )
            request = SimpleNamespace(
                project_root=temp,
                label="default",
                arguments={"phpExtensions": {
                    "extensions": {"gd": {"state": "enabled", "package": "apt-foreign"}},
                }},
            )
            with self.assertRaises(ValueError):
                builder(request)
            self.assertEqual(state_path.read_bytes(), before_bytes)
            after_state = repository.snapshot()
            self.assertEqual(after_state, before_state)
            self.assertEqual(len(after_state["networks"]), 0)


if __name__ == "__main__": unittest.main()

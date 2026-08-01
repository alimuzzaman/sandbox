import unittest


class Step:
    def __init__(self, events, name): self.events = events; self.name = name
    def install(self, value): self.events.append(self.name)
    def create(self, value): self.events.append("image-create")
    def mount(self, value): self.events.append("image-mount")
    def unmount(self, value): self.events.append("image-unmount"); return {"ok": True}
    def configure(self, value): self.events.append(self.name)
    def start_minimal(self, value): self.events.append("machine-minimal")
    def stop(self, value): self.events.append(self.name + "-stop"); return {"ok": True}
    def apply(self, value): self.events.append("network")
    def remove(self, value): self.events.append(self.name + "-remove"); return {"ok": True}
    def initialize(self, value): self.events.append("database")
    def activate(self, value): self.events.append("services")
    def put_owned(self, *args): self.events.append("persist")


class Verifier:
    def __init__(self, events, ok=True): self.events = events; self.ok = ok
    def verify(self, policy): self.events.append("verify"); return {"ok": self.ok}


class TestManagedProvisioner(unittest.TestCase):
    def provisioner(self, events, verify=True):
        from sandbox.runtimes.managed.adapter import ManagedProvisioner
        return ManagedProvisioner(
            policy=Step(events, "policy"), apparmor=Step(events, "apparmor"),
            image=Step(events, "image"),
            rootfs=Step(events, "rootfs"), machine=Step(events, "machine"),
            network=Step(events, "network"), verifier=Verifier(events, verify),
            database=Step(events, "database"), services=Step(events, "services"),
            health=lambda plan: events.append("health") or {"ok": True},
            repository=Step(events, "repository"),
        )

    def plan(self):
        return {"machine_id": "sb-0123456789ab", "policy": object(), "image": {},
                "apparmor": {},
                "network": {}, "database": {}, "services": {"backend": {"port": 8080}},
                "record": {"owner": "owner"}}

    def test_no_project_service_can_start_before_effective_verification(self):
        events = []; result = self.provisioner(events).ensure(self.plan())
        self.assertTrue(result["ok"])
        self.assertLess(events.index("verify"), events.index("database"))
        self.assertLess(events.index("verify"), events.index("services"))
        self.assertLess(events.index("image-unmount"), events.index("machine-minimal"))
        self.assertLess(events.index("apparmor"), events.index("machine-minimal"))
        self.assertEqual(events[-1], "persist")

    def test_verification_failure_rolls_back_without_database_or_services(self):
        events = []; result = self.provisioner(events, verify=False).ensure(self.plan())
        self.assertFalse(result["ok"])
        self.assertNotIn("database", events); self.assertNotIn("services", events)
        self.assertIn("network-remove", events); self.assertIn("machine-stop", events)
        self.assertIn("apparmor-remove", events)
        self.assertIn("image-unmount", events)


if __name__ == "__main__": unittest.main()

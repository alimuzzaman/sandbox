import subprocess
from pathlib import Path
import tempfile
import unittest


class Process:
    def __init__(self): self.calls = []
    def run(self, argv, *, timeout):
        self.calls.append((argv, timeout)); return subprocess.CompletedProcess(argv, 0, "", "")


class Policy:
    machine_id = "sb-0123456789ab"; digest = "a" * 64
    root_image = {"path": "/var/lib/sandbox/native/instances/sb-0123456789ab/root.img",
                  "bytes": 8 * 1024**3, "inodes": 500000}


class TestManagedImage(unittest.TestCase):
    def test_plan_is_fixed_size_inode_bounded_and_uses_hardened_mount_options(self):
        from sandbox.runtimes.managed.image import ManagedImage
        plan = ManagedImage(process=Process(), helper="/helper").plan(Policy())
        self.assertEqual(plan["bytes"], 8 * 1024**3)
        self.assertIn("nodev", plan["mount_options"]); self.assertIn("nosuid", plan["mount_options"])

    def test_lifecycle_uses_only_fixed_helper_verbs(self):
        from sandbox.runtimes.managed.image import ManagedImage
        process = Process(); image = ManagedImage(
            process=process, helper="/helper",
            observer=lambda _machine: {"policy_digest": "a" * 64, "mounted": False},
        )
        plan = image.plan(Policy()); image.create(plan); image.mount(plan); image.unmount(plan); image.remove(plan)
        self.assertEqual([call[0][3] for call in process.calls],
                         ["image-create", "image-mount", "image-unmount", "image-remove"])

    def test_drift_or_mounted_state_prevents_destructive_cleanup(self):
        from sandbox.runtimes.managed.image import ManagedImage
        process = Process(); plan = ManagedImage(process=process, helper="/h").plan(Policy())
        drift = ManagedImage(process=process, helper="/h", observer=lambda _m: {
            "policy_digest": "b" * 64, "mounted": False}).remove(plan)
        mounted = ManagedImage(process=process, helper="/h", observer=lambda _m: {
            "policy_digest": "a" * 64, "mounted": True}).remove(plan)
        self.assertFalse(drift["ok"]); self.assertFalse(mounted["ok"])
        self.assertEqual(process.calls, [])

    def test_rootfs_bootstrap_uses_staged_exact_plan_and_cleans_it(self):
        from sandbox.runtimes.managed.image import ManagedRootfs

        class PackagePlan:
            simulation_digest = "b" * 64
        class Stager:
            def __init__(self, path): self.path = path
            def stage(self, _plan): self.path.write_text("staged"); return self.path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"; process = Process()
            rootfs = ManagedRootfs(process=process, helper="/fixed/helper",
                                   stager=Stager(path))
            rootfs.configure({"machine_id": Policy.machine_id,
                "policy_digest": Policy.digest, "package_plan": PackagePlan(),
                "web_server": "nginx"})
            self.assertFalse(path.exists())
        argv, timeout = process.calls[0]
        self.assertEqual(argv[:4], ("sudo", "-n", "/fixed/helper", "image-bootstrap"))
        self.assertEqual(argv[-2:], (PackagePlan.simulation_digest, "nginx"))
        self.assertEqual(timeout, 1900)


if __name__ == "__main__": unittest.main()

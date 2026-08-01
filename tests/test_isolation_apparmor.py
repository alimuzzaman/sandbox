import shutil
import subprocess
import tempfile
import unittest


class Policy:
    machine_id = "sb-0123456789ab"
    digest = "a" * 64


class TestIsolationAppArmor(unittest.TestCase):
    def test_supervisor_must_transition_and_guest_lacks_escape_primitives(self):
        from sandbox.isolation.apparmor import AppArmorCompiler
        result = AppArmorCompiler().compile(Policy())
        content = result["content"]
        supervisor, guest = content.split("  profile guest", 1)
        self.assertIn("userns,", supervisor)
        self.assertIn("cx -> guest", supervisor)
        self.assertNotIn("userns,", guest)
        self.assertNotIn("mount,", guest)
        self.assertNotIn("ptrace,", guest)
        self.assertNotIn("network netlink", guest)
        self.assertNotIn("network packet", guest)
        self.assertNotIn("capability sys_admin", guest)

    def test_profile_is_accepted_by_the_supported_apparmor_parser(self):
        parser = shutil.which("apparmor_parser")
        if not parser: self.skipTest("AppArmor parser is unavailable")
        from sandbox.isolation.apparmor import AppArmorCompiler
        with tempfile.NamedTemporaryFile(mode="w", suffix=".profile") as profile:
            profile.write(AppArmorCompiler().compile(Policy())["content"]); profile.flush()
            result = subprocess.run((parser, "--skip-kernel-load", "--skip-cache",
                                     profile.name), text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profile_lifecycle_uses_only_fixed_digest_bound_helper_verbs(self):
        from sandbox.isolation.apparmor import ManagedAppArmor

        class Result:
            returncode = 0; stdout = "{}"
        class Process:
            def __init__(self): self.calls = []
            def run(self, argv, **kwargs): self.calls.append((argv, kwargs)); return Result()

        process = Process(); manager = ManagedAppArmor(process=process, helper="/fixed/helper")
        plan = manager.plan(Policy())
        manager.install(plan); manager.status(plan); manager.remove(plan)
        self.assertEqual([argv[3] for argv, _kwargs in process.calls],
                         ["apparmor-install", "apparmor-status", "apparmor-remove"])
        self.assertTrue(all(argv[-2:] == (Policy.machine_id, Policy.digest)
                            for argv, _kwargs in process.calls))


if __name__ == "__main__": unittest.main()

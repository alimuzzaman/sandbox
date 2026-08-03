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
        supervisor, remainder = content.split("  profile guest", 1)
        guest, remainder = remainder.split("  profile bwrap", 1)
        bwrap, payload = remainder.split("  profile payload", 1)
        self.assertIn("userns,", supervisor)
        self.assertIn("cx -> guest", supervisor)
        self.assertNotIn("userns,", guest)
        # No BLANKET mount primitive. The guest may mount only the enumerated
        # API filesystems its own init needs inside the machine's namespace.
        self.assertNotIn("\n    mount,\n", guest)
        self.assertNotIn("\n    remount,\n", guest)
        self.assertIn("mount fstype=tmpfs -> /run/lock/,", guest)
        # A blanket ptrace would reach outside the machine; a read-only,
        # same-profile peer rule cannot. The peer must name the CHILD profile
        # and grant both directions: the kernel checks `read` on the reader and
        # `readby` on the target, and an unresolvable `@variable` peer matches
        # nothing at all.
        self.assertNotIn("\n    ptrace,\n", guest)
        self.assertIn("ptrace (read, readby) peer=sandbox-native-sb-0123456789ab//guest,",
                      guest)
        self.assertNotIn("peer=@", guest)
        self.assertNotIn("network netlink", guest)
        self.assertNotIn("network packet", guest)
        # The guest's PID 1 needs sys_admin for its typed API mounts; every
        # service that runs project code strips it in its own unit, and the
        # payload profile below denies it outright.
        self.assertIn("capability sys_admin", guest)
        self.assertIn("userns,", bwrap)
        self.assertIn("mount,", bwrap)
        self.assertIn("capability sys_admin", bwrap)
        self.assertIn("/** cx -> payload", bwrap)
        self.assertNotIn("userns,", payload)
        self.assertNotIn("mount,", payload)
        self.assertNotIn("capability sys_admin", payload)

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

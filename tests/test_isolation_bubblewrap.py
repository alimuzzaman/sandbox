import unittest


class TestIsolationBubblewrap(unittest.TestCase):
    def test_profile_clears_environment_privilege_and_private_process_state(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        argv = BubblewrapCompiler().argv(
            environment={"PATH": "/usr/bin"}, command=("php", "probe.php"),
        )
        for option in ("--clearenv", "--die-with-parent", "--new-session",
                       "--unshare-user", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                       "--cap-drop", "--tmpfs"):
            self.assertIn(option, argv)
        self.assertNotIn("--unshare-net", argv)  # retain only nspawn's already-filtered veth
        # Measured on Ubuntu 24.04: these three cannot work inside a machine and
        # made every payload fail before it ran. `--disable-userns` writes
        # /proc/sys, which nspawn mounts read-only; `--unshare-pid` forces a
        # fresh procfs, which a non-initial user namespace may only mount when
        # /proc is fully visible, and nspawn masks it (FR-045, FR-046).
        for option in ("--disable-userns", "--assert-userns-disabled", "--unshare-pid"):
            self.assertNotIn(option, argv)
        self.assertIn("--proc", argv)
        self.assertEqual(argv[argv.index("--uid") + 1], "33")
        self.assertEqual(argv[argv.index("--gid") + 1], "33")
        self.assertEqual(argv[-2:], ("php", "probe.php"))

    def test_the_payload_profile_is_stacked_onto_the_final_exec(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        profile = "sandbox-native-sb-0123456789ab//payload"
        argv = BubblewrapCompiler().argv(
            command=("php", "probe.php"), payload_profile=profile,
        )
        boundary = argv.index("--")
        self.assertEqual(argv[boundary + 1:boundary + 3], ("/bin/sh", "-c"))
        script = argv[boundary + 3]
        self.assertIn(f"stack {profile}", script)
        self.assertIn("/proc/self/attr/apparmor/exec", script)
        # A failed stack must not fall through to the weaker bwrap profile.
        self.assertIn("exit 126", script)
        self.assertEqual(argv[-2:], ("php", "probe.php"))

    def test_a_profile_name_that_could_break_out_of_the_script_is_refused(self):
        from sandbox.isolation.bubblewrap import stacked_command
        for name in ("", "a'b", "a b", 'a"b', "a\nb", "a\\b", None):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    stacked_command(name, ("id",))

    def test_roots_and_argv_are_bounded_not_project_controlled(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        compiler = BubblewrapCompiler()
        with self.assertRaises(ValueError): compiler.argv(root="/host", command=("id",))
        with self.assertRaises(ValueError): compiler.argv(command=("x",) * 257)
        with self.assertRaises(ValueError): compiler.argv(command=("a" * 65537,))

    def test_only_declared_writable_targets_are_rebound_after_read_only_root(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        argv = BubblewrapCompiler().argv(
            writable_targets=("/var/lib/sandbox", "/workspace/generated"),
            command=("php", "probe.php"),
        )
        root_index = argv.index("--ro-bind")
        writable_indexes = [index for index, value in enumerate(argv) if value == "--bind"]
        self.assertTrue(writable_indexes)
        self.assertTrue(all(index > root_index for index in writable_indexes))
        self.assertIn(("--bind", "/var/lib/sandbox", "/var/lib/sandbox"),
                      tuple(zip(argv, argv[1:], argv[2:])))
        with self.assertRaises(ValueError):
            BubblewrapCompiler().argv(writable_targets=("/proc/escape",), command=("id",))

    def test_requested_credentials_bind_from_hidden_root_before_source_mask(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        argv = BubblewrapCompiler().argv(
            credential_names=("api-token", "db-credential"), command=("php", "probe.php"),
        )
        db_bind = argv.index("/run/sandbox-native-credentials/db-credential")
        api_bind = argv.index("/run/sandbox-native-credentials/api-token")
        source_mask = argv.index("/run/sandbox-native-credentials", db_bind)
        self.assertLess(api_bind, db_bind)  # stable exact-request ordering
        self.assertLess(db_bind, source_mask)
        self.assertEqual(
            argv[db_bind - 1:db_bind + 2],
            ("--ro-bind", "/run/sandbox-native-credentials/db-credential",
             "/run/credentials/sandbox/db-credential"),
        )
        self.assertEqual(argv[source_mask - 1], "--tmpfs")
        self.assertNotIn(
            ("--ro-bind", "/run/credentials/sandbox/db-credential",
             "/run/credentials/sandbox/db-credential"),
            tuple(zip(argv, argv[1:], argv[2:])),
        )


if __name__ == "__main__": unittest.main()

import unittest


class TestIsolationBubblewrap(unittest.TestCase):
    def test_profile_clears_environment_privilege_and_private_process_state(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        argv = BubblewrapCompiler().argv(
            environment={"PATH": "/usr/bin"}, command=("php", "probe.php"),
        )
        for option in ("--clearenv", "--die-with-parent", "--new-session",
                       "--unshare-user", "--disable-userns", "--assert-userns-disabled",
                       "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                       "--cap-drop", "--tmpfs"):
            self.assertIn(option, argv)
        self.assertNotIn("--unshare-net", argv)  # retain only nspawn's already-filtered veth
        self.assertEqual(argv[argv.index("--uid") + 1], "33")
        self.assertEqual(argv[argv.index("--gid") + 1], "33")
        self.assertEqual(argv[-2:], ("php", "probe.php"))

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

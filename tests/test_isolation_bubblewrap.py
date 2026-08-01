import unittest


class TestIsolationBubblewrap(unittest.TestCase):
    def test_profile_clears_environment_privilege_and_private_process_state(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        argv = BubblewrapCompiler().argv(
            environment={"PATH": "/usr/bin"}, command=("php", "probe.php"),
        )
        for option in ("--clearenv", "--die-with-parent", "--new-session", "--unshare-user",
                       "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                       "--cap-drop", "--tmpfs"):
            self.assertIn(option, argv)
        self.assertNotIn("--unshare-net", argv)  # retain only nspawn's already-filtered veth
        self.assertEqual(argv[-2:], ("php", "probe.php"))

    def test_roots_and_argv_are_bounded_not_project_controlled(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        compiler = BubblewrapCompiler()
        with self.assertRaises(ValueError): compiler.argv(root="/host", command=("id",))
        with self.assertRaises(ValueError): compiler.argv(command=("x",) * 257)
        with self.assertRaises(ValueError): compiler.argv(command=("a" * 65537,))


if __name__ == "__main__": unittest.main()

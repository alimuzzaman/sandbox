"""Isolation preflight probes must not fail on kernel bookkeeping (039 T047)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _helper():
    path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
            / "native-helper.py")
    spec = importlib.util.spec_from_file_location("native_helper_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIpv6DefaultRouteDetection(unittest.TestCase):
    UNREACHABLE = ("0" * 32 + " 00 " + "0" * 32 + " 00 " + "0" * 32
                   + " ffffffff 00000001 00000000 00200200       lo")
    REAL_DEFAULT = ("0" * 32 + " 00 " + "0" * 32 + " 00 "
                    + "fe800000000000000000000000000001"
                    + " 00000400 00000001 00000000 00000003       eth0")
    LOOPBACK_HOST = ("0" * 31 + "1 80 " + "0" * 32 + " 00 " + "0" * 32
                     + " 00000000 00000002 00000000 80200001       lo")

    def test_kernel_unreachable_default_is_not_connectivity(self):
        self.assertFalse(_helper().ipv6_default_route(self.UNREACHABLE))

    def test_a_usable_default_route_is_detected(self):
        self.assertTrue(_helper().ipv6_default_route(self.REAL_DEFAULT))

    def test_non_default_prefixes_are_ignored(self):
        self.assertFalse(_helper().ipv6_default_route(self.LOOPBACK_HOST))

    def test_malformed_rows_are_ignored(self):
        for row in ("", "garbage", "0" * 32 + " 00"):
            with self.subTest(row=row):
                self.assertFalse(_helper().ipv6_default_route(row))


if __name__ == "__main__":
    unittest.main()


class TestScopeProbeCommandShape(unittest.TestCase):
    """systemd refuses `--wait` together with `--scope`, so the
    cgroup-delegation gate could never pass while both were passed."""

    def test_scope_probe_does_not_pass_wait(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        block = source.split("if probe == \"private-network\":", 1)[1].split("command.extend((str(", 1)[0]
        scope_branch = block.split("else:", 1)[1]
        arguments = [line for line in scope_branch.splitlines()
                     if "command.extend" in line]
        self.assertTrue(arguments)
        self.assertIn("--scope", arguments[0])
        self.assertNotIn("--wait", arguments[0])

    def test_waiting_probes_still_wait(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        block = source.split("if probe == \"private-network\":", 1)[1].split("else:", 1)[0]
        self.assertIn("--wait", block)


class TestManagedRuntimeAcceptsRealProjectRoots(unittest.TestCase):
    """Projects live in the developer's own directories; restricting the runtime
    to the checkout and the state base rejected every real project."""

    def test_home_is_among_the_default_roots(self):
        from pathlib import Path

        import sandbox.core as core
        from sandbox.application.context import wordpress_runtime_dependencies

        dependencies = wordpress_runtime_dependencies(None)
        roots = set(dependencies.paths.roots)
        self.assertIn(Path.home().resolve(), roots)
        self.assertIn(Path(core.ROOT).resolve(), roots)

    def test_an_explicit_override_still_wins(self):
        from pathlib import Path
        import tempfile

        from sandbox.application.context import wordpress_runtime_dependencies

        with tempfile.TemporaryDirectory() as tmp:
            dependencies = wordpress_runtime_dependencies(None, allowed_roots=(tmp,))
            self.assertEqual(set(dependencies.paths.roots), {Path(tmp).resolve()})


class TestManagedAdapterRootsInTheRuntimeService(unittest.TestCase):
    """The composed runtime service must accept project directories too."""

    def test_managed_dependencies_include_home(self):
        from pathlib import Path

        from sandbox.application import context

        captured = {}
        original = context.managed_native_dependencies

        def spy(cfg, **kwargs):
            captured["roots"] = kwargs.get("allowed_roots")
            return original(cfg, **kwargs)

        context.managed_native_dependencies = spy
        try:
            context.runtime_service(None)
        except Exception:
            pass
        finally:
            context.managed_native_dependencies = original

        self.assertIn("roots", captured)
        self.assertIn(Path.home().resolve(), {Path(r).resolve() for r in captured["roots"]})


class TestManagedFailuresAreLegible(unittest.TestCase):
    """A failing bootstrap and a refusing runtime must say why."""

    def test_rootfs_failure_carries_the_helper_output(self):
        from types import SimpleNamespace

        from sandbox.runtimes.managed.image import ManagedRootfs

        class Process:
            @staticmethod
            def run(argv, **_kwargs):
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="native-helper: package plan digest mismatch")

        class Stager:
            @staticmethod
            def stage(_plan):
                import tempfile
                from pathlib import Path

                handle = tempfile.NamedTemporaryFile(delete=False)
                handle.close()
                return Path(handle.name)

        rootfs = ManagedRootfs(process=Process(), helper="/usr/local/libexec/x",
                               stager=Stager())
        plan = {"package_plan": SimpleNamespace(simulation_digest="d"),
                "machine_id": "m", "policy_digest": "p", "web_server": "nginx",
                "services": {"digest": "s"}}
        with self.assertRaises(RuntimeError) as caught:
            rootfs.configure(plan)
        self.assertIn("package plan digest mismatch", str(caught.exception))

    def test_ensure_guards_against_a_missing_instance_record(self):
        """Source-level guard: a refusing runtime must not crash the CLI."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "sandbox" / "commands"
                  / "instances_cmd.py").read_text()
        guard = source.split("entry = dict(result.data)", 1)[1].split("if getattr(args", 1)[0]
        self.assertIn('"instance" not in entry', guard)
        self.assertIn("instance is not ready", guard)

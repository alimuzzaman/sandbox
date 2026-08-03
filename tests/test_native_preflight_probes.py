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


class TestImageMountAllowsDeviceNodes(unittest.TestCase):
    """debootstrap's first act is a /dev/null probe inside the new rootfs, so a
    `nodev` mount made bootstrap impossible on every host. Device access for the
    running guest is governed by cgroup DeviceAllow and nspawn, not this flag."""

    def _mount_line(self) -> str:
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        return next(line for line in source.splitlines()
                    if '"mount", "-o"' in line)

    def test_nodev_is_not_used_for_the_owned_image(self):
        self.assertNotIn("nodev", self._mount_line())

    def test_nosuid_is_still_enforced(self):
        self.assertIn("nosuid", self._mount_line())


class TestWebConfigTestRunsInANamespace(unittest.TestCase):
    """nginx and Apache bind their listen sockets during a config test, and the
    machine's address does not exist yet at image-configure time."""

    def _source(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                / "native-helper.py").read_text()

    def test_config_test_is_wrapped_in_a_private_namespace(self):
        source = self._source()
        self.assertIn("def _namespaced_config_test", source)
        self.assertIn('"unshare", "--net"', source)

    def test_it_enables_nonlocal_bind_only_inside_that_namespace(self):
        source = self._source()
        block = source.split("def _namespaced_config_test", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("ip_nonlocal_bind=1", block)
        self.assertIn("unshare", block)

    def test_the_web_servers_use_it(self):
        source = self._source()
        self.assertIn('_namespaced_config_test(mountpoint, "/usr/sbin/nginx", "-t")', source)
        self.assertIn('_namespaced_config_test(mountpoint, "/usr/sbin/apache2ctl", "configtest")',
                      source)


class TestMachineUnitArguments(unittest.TestCase):
    """systemd-nspawn rejected the machine unit outright on an invalid flag."""

    def test_link_journal_uses_a_valid_mode(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        self.assertIn('"--link-journal=no"', source)
        self.assertNotIn("--link-journal=no-host", source)


class TestApparmorProfileAllowsTheRootDirectory(unittest.TestCase):
    """`/**` matches paths BELOW the root, never the root directory entry, so
    systemd-nspawn was denied `open /` while pinning the outer mount namespace
    and no managed machine could start."""

    def _profile(self) -> str:
        import importlib.util
        from pathlib import Path

        path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                / "native-helper.py")
        spec = importlib.util.spec_from_file_location("native_helper_profile", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.compile_apparmor_profile("sb-test", "d" * 64)

    def test_root_directory_is_readable(self):
        self.assertIn("\n  / r,\n", self._profile())

    def test_guest_profile_also_allows_it(self):
        self.assertIn("\n    / r,\n", self._profile())

    def test_the_broad_rule_is_still_present(self):
        profile = self._profile()
        self.assertIn("/** rwklm,", profile)


class TestImageShipsAnInit(unittest.TestCase):
    """The image is booted with `systemd-nspawn --boot`, and debootstrap's
    minbase variant ships no init, so the machine died immediately."""

    def test_systemd_is_in_the_image_package_set(self):
        from sandbox.runtimes.managed.packages import IMAGE_COMMON

        for package in ("systemd", "systemd-sysv"):
            self.assertIn(package, IMAGE_COMMON)

    def test_the_web_server_is_still_chosen_per_instance(self):
        from sandbox.runtimes.managed.packages import IMAGE_COMMON

        self.assertNotIn("nginx", IMAGE_COMMON)
        self.assertNotIn("apache2", IMAGE_COMMON)


class TestNoNewPrivilegesLivesOnTheGuestUnits(unittest.TestCase):
    """Under NoNewPrivileges the kernel refuses an AppArmor domain transition,
    so the flag on the MACHINE blocked the guest init from entering the tighter
    //guest profile. It belongs on the guest's own service units, which are
    every untrusted execution path inside the machine."""

    def _files(self):
        from sandbox.runtimes.managed.services import compile_service_files

        files, units = compile_service_files(
            "10.1.2.3", 64, 60, web_server="nginx", backend_port=8080)
        return files, units

    def test_machine_command_no_longer_sets_it(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        machine_block = source.split("nspawn = [", 1)[1].split("]", 1)[0]
        self.assertNotIn("--no-new-privileges", machine_block)

    def test_every_guest_service_unit_sets_it(self):
        files, units = self._files()
        for unit in units:
            path = f"/etc/systemd/system/{unit}.d/sandbox-no-new-privileges.conf"
            with self.subTest(unit=unit):
                self.assertIn(path, files)
                self.assertIn("NoNewPrivileges=yes", files[path])

    def test_the_php_pool_unit_keeps_it_too(self):
        files, _units = self._files()
        self.assertIn(
            "NoNewPrivileges=yes",
            files["/etc/systemd/system/php8.3-fpm.service.d/sandbox-isolation.conf"])

    def test_transient_exec_payloads_keep_it(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        self.assertIn('"--property=NoNewPrivileges=yes"', source)


class TestGuestProfileAllowsItsOwnApiMounts(unittest.TestCase):
    """The guest's PID 1 mounts /run/lock and friends inside the machine's own
    mount namespace; without a mount rule it died before any service started."""

    def _profile(self) -> str:
        from sandbox.isolation.apparmor import compile_apparmor_profile

        return compile_apparmor_profile("sb-test", "d" * 64)

    def test_guest_may_mount_only_the_api_filesystems(self):
        guest = self._profile().split("profile guest", 1)[1].split("profile bwrap", 1)[0]
        for rule in ("mount fstype=tmpfs -> /run/lock/,",
                     "mount fstype=cgroup2 -> /sys/fs/cgroup/,",
                     "mount fstype=tmpfs -> /dev/shm/,"):
            self.assertIn(rule, guest)

    def test_guest_has_no_blanket_mount_primitive(self):
        guest = self._profile().split("profile guest", 1)[1].split("profile bwrap", 1)[0]
        self.assertNotIn("\n    mount,\n", guest)
        self.assertNotIn("\n    remount,\n", guest)

    def test_helper_and_control_plane_agree(self):
        import importlib.util
        from pathlib import Path

        path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                / "native-helper.py")
        spec = importlib.util.spec_from_file_location("native_helper_profile2", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.compile_apparmor_profile("sb-test", "d" * 64),
                         self._profile())


class TestPayloadsNeverHoldSysAdmin(unittest.TestCase):
    """CAP_SYS_ADMIN is namespaced to the machine so its init can boot; the code
    that runs project payloads must still be unable to use it."""

    def _profile(self) -> str:
        from sandbox.isolation.apparmor import compile_apparmor_profile

        return compile_apparmor_profile("sb-test", "d" * 64)

    def test_payload_profile_denies_the_escape_primitives(self):
        payload = self._profile().split("profile payload", 1)[1]
        for rule in ("userns,", "mount,", "capability sys_admin"):
            self.assertNotIn(f"\n    {rule}", payload)

    def test_machine_runs_in_a_private_user_namespace(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        machine = source.split("nspawn = [", 1)[1].split("\n    for mount in", 1)[0]
        self.assertIn("--private-users=", machine)
        self.assertIn("--private-users-ownership=", machine)

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HELPER = Path(__file__).parent.parent / "tools/native-helper/native-helper.py"


def module():
    spec = importlib.util.spec_from_file_location("native_helper_test", HELPER)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


class TestNativeHelper(unittest.TestCase):
    def policy(self, root, identity="sb-0123456789ab"):
        project = root / "project"; project.mkdir(exist_ok=True)
        state = root / "state"; state.mkdir(exist_ok=True)
        helper = module(); value = {
            "policy_version": 1, "machine_id": identity,
            "uid_map": {"base": 196608, "count": 65536},
            "root_image": {"path": f"/var/lib/sandbox/native/instances/{identity}/root.img",
                           "bytes": 8 * 1024**3, "inodes": 500000},
            "read_only_mounts": [{"source": str(project), "target": "/workspace"}],
            "writable_mounts": [],
            "network": {"egress": "deny", "veth": "ve-sb-demo",
                        "host_address": "10.203.0.1/30", "guest_address": "10.203.0.2/30",
                        "default_route": False, "ingress_port": 8080, "grants": []},
            "syscalls": {"no_new_privileges": True, "seccomp": "managed-v1"},
            "devices": [], "resources": {"cpu_percent": 200,
                "memory_bytes": 2 * 1024**3, "pids": 512, "runtime_seconds": 3600,
                "disk_bytes": 8 * 1024**3, "inodes": 500000, "fds": 4096,
                "connections": 512, "io_weight": 100},
            "credentials": [],
        }
        value["digest"] = helper.canonical_digest(value)
        path = root / f"{identity}.json"; path.write_text(json.dumps(value)); path.chmod(0o600)
        return helper, path

    def test_fixed_verbs_and_invalid_identity_are_rejected(self):
        helper = module()
        with self.assertRaises(SystemExit): helper.main(["check-policy", "../../host", "/tmp/x"])
        with self.assertRaises(SystemExit): helper.main(["shell", "anything"])

    def test_privileged_subprocess_discards_the_caller_environment(self):
        helper = module()
        result = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(helper.subprocess, "run", return_value=result) as run:
            helper.run_fixed(("nft", "list", "ruleset"), "failed")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment, helper.FIXED_ENVIRONMENT)
        self.assertNotIn("HOME", environment)
        self.assertNotIn("LD_PRELOAD", environment)

    def test_policy_path_symlink_mode_owner_and_fixed_root_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                helper.checked_policy(path, "sb-0123456789ab")
                with mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid() + 1)}):
                    with self.assertRaises(SystemExit):
                        helper.checked_policy(path, "sb-0123456789ab")
                path.chmod(0o666)
                with self.assertRaises(SystemExit): helper.checked_policy(path, "sb-0123456789ab")
                path.chmod(0o600); link = root / "link.json"; link.symlink_to(path)
                with self.assertRaises(SystemExit): helper.checked_policy(link, "sb-0123456789ab")

    def test_digest_or_default_allow_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                value = json.loads(path.read_text()); value["network"]["egress"] = "allow"
                path.write_text(json.dumps(value))
                with self.assertRaises(SystemExit): helper.checked_policy(path, "sb-0123456789ab")

    def test_host_mount_and_missing_resource_ceiling_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                for mutate in (
                    lambda value: value["read_only_mounts"].append(
                        {"source": "/etc", "target": "/host"}),
                    lambda value: value["resources"].pop("pids"),
                ):
                    value = json.loads(path.read_text()); mutate(value)
                    value["digest"] = helper.canonical_digest(
                        {key: item for key, item in value.items() if key != "digest"})
                    path.write_text(json.dumps(value))
                    with self.assertRaises(SystemExit):
                        helper.checked_policy(path, "sb-0123456789ab")
                    _helper, path = self.policy(root)

    def test_policy_install_uses_validated_bytes_not_a_reopened_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            installed = root / "installed"
            original = path.read_bytes()
            replacement = b'{"attacker":"replacement"}'
            real_install = helper.atomic_install_bytes

            def replace_then_install(payload, destination):
                path.unlink(); path.write_bytes(replacement)
                return real_install(payload, destination)

            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "POLICY_ROOT", installed), \
                    mock.patch.object(helper, "require_root"), \
                    mock.patch.object(helper, "atomic_install_bytes", replace_then_install):
                helper.main(["policy-install", "sb-0123456789ab", str(path)])
            self.assertEqual((installed / "sb-0123456789ab.json").read_bytes(), original)

    def test_network_apply_compiles_only_instance_drop_rules(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        policy_value = json.loads(path.read_text()); calls = []
        with mock.patch.object(helper, "applied_policy", return_value=(path, policy_value)), \
                mock.patch.object(helper, "observed_link",
                                  return_value={"ifalias": ""}), \
                mock.patch.object(helper, "observed_nft_table", return_value=None), \
                mock.patch.object(helper, "machine_leader", return_value=4242), \
                mock.patch.object(helper, "run_fixed",
                                  side_effect=lambda argv, message, **kw: calls.append((argv, kw))):
            helper.network_apply("sb-0123456789ab", policy_value["digest"])
        nft = next(kwargs["input_text"] for argv, kwargs in calls if argv[:2] == ("nft", "-f"))
        self.assertIn('input iifname "ve-sb-demo"', nft)
        self.assertIn('forward iifname "ve-sb-demo"', nft)
        self.assertEqual(nft.count("counter drop"), 2)
        self.assertNotIn("counter drop\nip saddr", nft)
        self.assertNotIn("masquerade", nft)
        self.assertNotIn("policy accept; }\nadd rule inet sb_0123456789ab forward", nft)
        namespace_calls = [argv for argv, _kwargs in calls if argv[:5] ==
                           ("nsenter", "--target", "4242", "--net", "--")]
        self.assertTrue(any(argv[-4:] == ("route", "flush", "table", "main")
                            for argv in namespace_calls))

    def test_network_apply_refuses_unbrokered_egress_grants(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        value["network"]["grants"] = [{"grant_id": "wordpress-org",
            "destinations": ["8.8.8.8/32"], "ports": [443],
            "expires_at": "later", "revoked": False}]
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                self.assertRaises(SystemExit):
            helper.network_apply("sb-0123456789ab", value["digest"])

    def test_machine_command_is_fixed_digest_bound_and_systemd_255_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, path = self.policy(Path(directory)); value = json.loads(path.read_text())
            command, description = helper.machine_command(value)
        self.assertEqual(command[:5], ("systemd-run", "--no-block", "--collect",
                                       "--unit=sandbox-native-sb-0123456789ab.service",
                                       "--service-type=notify"))
        self.assertIn("--settings=no", command)
        self.assertIn("--private-network", command)
        self.assertIn("--network-veth-extra=ve-sb-demo:host0", command)
        self.assertIn("--no-new-privileges=yes", command)
        self.assertTrue(any(value.startswith("--drop-capability=") and "CAP_SYS_ADMIN" in value
                            for value in command))
        self.assertFalse(any("private-users-delegate" in value for value in command))
        self.assertFalse(any(value.startswith("--restrict-address-families=")
                             for value in command))
        self.assertIn(value["digest"], description)

    def test_machine_start_refuses_when_apparmor_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, path = self.policy(Path(directory)); value = json.loads(path.read_text())
            with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                    mock.patch.object(helper, "apparmor_loaded", return_value=False), \
                    mock.patch.object(helper, "run_fixed") as run, self.assertRaises(SystemExit):
                helper.machine_start_minimal("sb-0123456789ab", value["digest"])
            run.assert_not_called()

    def test_helper_and_control_plane_compile_identical_apparmor_profiles(self):
        from sandbox.isolation.apparmor import compile_apparmor_profile
        helper = module(); identity = "sb-0123456789ab"; digest = "a" * 64
        self.assertEqual(helper.compile_apparmor_profile(identity, digest),
                         compile_apparmor_profile(identity, digest))

    def test_apparmor_install_writes_only_the_fixed_owned_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path = self.policy(root)
            value = json.loads(policy_path.read_text()); calls = []
            with mock.patch.object(helper, "APPARMOR_ROOT", root / "profiles"), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, value)), \
                    mock.patch.object(helper, "apparmor_loaded", return_value=True), \
                    mock.patch.object(helper, "run_fixed",
                                      side_effect=lambda argv, message, **kw: calls.append(argv)):
                helper.apparmor_install("sb-0123456789ab", value["digest"])
            installed = root / "profiles" / "sandbox-native-sb-0123456789ab"
            self.assertEqual(installed.read_text(),
                             helper.compile_apparmor_profile("sb-0123456789ab", value["digest"]))
            self.assertEqual(calls[0][:3],
                             ("apparmor_parser", "--replace", "--skip-cache"))


if __name__ == "__main__": unittest.main()

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
                        "default_route": False},
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


if __name__ == "__main__": unittest.main()

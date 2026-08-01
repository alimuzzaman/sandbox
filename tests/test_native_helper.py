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
        helper = module(); value = {
            "policy_version": 1, "machine_id": identity,
            "uid_map": {"base": 200000, "count": 65536},
            "root_image": {"path": f"/var/lib/sandbox/native/instances/{identity}/root.img"},
            "read_only_mounts": [], "writable_mounts": [],
            "network": {"egress": "deny", "veth": "ve-sb-demo"},
            "syscalls": {"no_new_privileges": True, "seccomp": "managed-v1"},
            "devices": [], "resources": {"memory_max": 1024}, "credentials": [],
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


if __name__ == "__main__": unittest.main()

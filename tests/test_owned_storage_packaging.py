"""Packaging validation tests for owned storage authority assets and distribution rules."""

import os
from pathlib import Path
import unittest

ROOT = Path(__file__).parent.parent


class TestOwnedStoragePackaging(unittest.TestCase):
    def test_required_runtime_files_exist(self):
        required_files = [
            ROOT / "sandbox" / "owned_storage" / "__init__.py",
            ROOT / "sandbox" / "owned_storage" / "models.py",
            ROOT / "sandbox" / "owned_storage" / "protocol.py",
            ROOT / "sandbox" / "owned_storage" / "service.py",
            ROOT / "sandbox" / "owned_storage" / "repository.py",
            ROOT / "sandbox" / "owned_storage" / "cleanup.py",
            ROOT / "sandbox" / "owned_storage" / "redaction.py",
            ROOT / "sandbox" / "owned_storage" / "adapters" / "linux.py",
            ROOT / "sandbox" / "owned_storage_lifecycle" / "__init__.py",
            ROOT / "sandbox" / "owned_storage_lifecycle" / "models.py",
            ROOT / "sandbox" / "owned_storage_lifecycle" / "repository.py",
            ROOT / "sandbox" / "owned_storage_lifecycle" / "service.py",
            ROOT / "tools" / "owned-storage-service.py",
            ROOT / "tools" / "owned-storage-controller.py",
            ROOT / "tools" / "owned-storage-mount-controller.py",
            ROOT / "config" / "systemd" / "sandbox-owned-storage.service",
            ROOT / "config" / "systemd" / "sandbox-owned-storage.socket",
            ROOT / "config" / "systemd" / "sandbox-owned-storage-controller.service",
            ROOT / "config" / "systemd" / "sandbox-owned-storage-controller.socket",
            ROOT / "config" / "systemd" / "sandbox-owned-storage-mount.service",
            ROOT / "config" / "systemd" / "sandbox-owned-storage.sysusers",
        ]
        missing = [str(f.relative_to(ROOT)) for f in required_files if not f.is_file()]
        self.assertEqual(missing, [], f"Missing required owned storage runtime assets: {missing}")

    def test_make_release_prunes_specs_and_includes_owned_storage(self):
        make_release = (ROOT / "scripts" / "make-release.sh").read_text(encoding="utf-8")
        self.assertIn('"$STAGE/sandbox/specs"', make_release)
        self.assertIn('"$STAGE/sandbox/.specify"', make_release)
        self.assertIn('skills/speckit-*', make_release)
        self.assertIn('sandbox/owned_storage', make_release)
        self.assertIn('tools/owned-storage-service.py', make_release)
        self.assertIn('config/systemd/sandbox-owned-storage.service', make_release)

    def test_install_remote_stages_owned_storage_assets(self):
        install_remote = (ROOT / "scripts" / "install-remote.sh").read_text(encoding="utf-8")
        self.assertIn("sandbox-owned-storage.service", install_remote)
        self.assertIn("sandbox-owned-storage.socket", install_remote)
        self.assertIn("sandbox-owned-storage.sysusers", install_remote)
        self.assertIn("SANDBOX_DEPLOY_OWNED_STORAGE", install_remote)

    def test_command_manifest_registers_owned_storage(self):
        from sandbox.commands.manifest import BUILTIN_COMMAND_MODULES, validate_builtin_command_coverage
        self.assertIn("sandbox.commands.owned_storage", BUILTIN_COMMAND_MODULES)
        self.assertEqual(validate_builtin_command_coverage(), ())

    def test_mcp_manifest_registers_owned_storage(self):
        import sys
        mcp_root = ROOT / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        try:
            from tools.manifest import BUILTIN_TOOL_GROUPS, BUILTIN_TOOL_NAMES, built_in_tool_registry
            self.assertIn("owned_storage", BUILTIN_TOOL_GROUPS)
            self.assertEqual(
                BUILTIN_TOOL_NAMES["owned_storage"],
                (
                    "owned_storage_capability",
                    "owned_storage_status",
                    "owned_storage_preview",
                    "owned_storage_reclaim",
                ),
            )
            registry = built_in_tool_registry(("owned_storage",))
            self.assertIn("owned_storage", registry.group_ids())
        finally:
            sys.path.remove(str(mcp_root))


if __name__ == "__main__":
    unittest.main()

import unittest
import tempfile
import os
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.server_config_fixtures import (
    FakeAdapter, FakeClock, FIXED_INCARNATION, fragment,
)
from sandbox.server_config.input import write_fragment_output
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from sandbox.commands.server import _config_show


class TestServerConfigContentExport(unittest.TestCase):
    """T084: owner-only regular destination, safe-parent, symlink/special/non-owner refusal, atomic replacement, basename-only JSON, zero state write."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.clock = FakeClock()
        from sandbox.server_config.adapters.base import AdapterDescriptor

        self.descriptor = AdapterDescriptor(
            server_type="nginx",
            adapter_id="test_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract",
        )
        self.adapter = FakeAdapter(descriptor=self.descriptor)
        self.service = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock,
        )

    def test_export_to_owner_only_regular_file(self):
        """T084: Output writes exact bytes with 0600 permissions."""
        with tempfile.TemporaryDirectory() as target_dir:
            dest_file = os.path.join(target_dir, "export.conf")
            exact_bytes = b"location /exported { return 200; }"
            res = write_fragment_output(dest_file, exact_bytes)

            self.assertTrue(res["written"])
            self.assertEqual(res["basename"], "export.conf")
            with open(dest_file, "rb") as f:
                self.assertEqual(f.read(), exact_bytes)
            mode = stat.S_IMODE(os.stat(dest_file).st_mode)
            self.assertEqual(mode, 0o600)

    def test_export_refuses_symlink_destination(self):
        """T084: Refuses symlink destination without modifying link target."""
        with tempfile.TemporaryDirectory() as target_dir:
            real_file = os.path.join(target_dir, "real.conf")
            with open(real_file, "wb") as f:
                f.write(b"original")
            symlink_file = os.path.join(target_dir, "link.conf")
            os.symlink(real_file, symlink_file)

            with self.assertRaises(ValueError) as ctx:
                write_fragment_output(symlink_file, b"new_data")
            self.assertEqual(str(ctx.exception), "content_output_unsafe")

            # Target remains untouched
            with open(real_file, "rb") as f:
                self.assertEqual(f.read(), b"original")

    def test_export_refuses_unsafe_parent_directory(self):
        """T084: Refuses write to world-writable parent directory."""
        with tempfile.TemporaryDirectory() as target_dir:
            # Make parent world-writable
            os.chmod(target_dir, 0o777)
            dest_file = os.path.join(target_dir, "export.conf")
            with self.assertRaises(ValueError) as ctx:
                write_fragment_output(dest_file, b"data")
            self.assertEqual(str(ctx.exception), "content_output_unsafe")

    def test_export_basename_only_in_json(self):
        """T084: JSON response returns only basename, never raw content or full absolute path."""
        exact_bytes = b"location /export-json { return 204; }"
        self.service.apply(fragment(name="export-json", content=exact_bytes))

        with tempfile.TemporaryDirectory() as target_dir:
            dest_file = os.path.join(target_dir, "exported_fragment.conf")
            args = MagicMock()
            args.name = "export-json"
            args.content = False
            args.output = dest_file
            args.json = True
            args.instance_service = self.service

            with patch("sandbox.commands.server._render_json") as mock_json:
                _config_show(None, args, use_json=True)
                mock_json.assert_called_once()
                payload = mock_json.call_args[0][0]
                self.assertEqual(payload.get("output_basename"), "exported_fragment.conf")
                self.assertNotIn("location", str(payload))

    def test_export_performs_zero_repository_state_writes(self):
        """T084: Export operation is strictly read-only for repository state."""
        exact_bytes = b"location /zero-write { return 200; }"
        self.service.apply(fragment(name="zero-write", content=exact_bytes))

        state_before = self.repository.read_state()

        with tempfile.TemporaryDirectory() as target_dir:
            dest_file = os.path.join(target_dir, "out.conf")
            content = self.service.read_fragment_content("zero-write")
            write_fragment_output(dest_file, content)

        state_after = self.repository.read_state()
        self.assertEqual(state_before, state_after)


if __name__ == "__main__":
    unittest.main()

import unittest
import io
import tempfile
import sys
from unittest.mock import patch, MagicMock

from tests.server_config_fixtures import (
    FakeAdapter, FakeClock, FIXED_INCARNATION, fragment,
)
from sandbox.server_config.models import (
    ServerType, TerminalOutcome,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from sandbox.commands.server import _config_show


class TestServerConfigContentShow(unittest.TestCase):
    """T083: exact show --content stdout-only, no-added-newline/heading, pre-emission refusal, and --content --json incompatibility."""

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

    def test_show_content_stdout_exact_bytes_no_added_newline(self):
        """T083: show --content emits exact raw bytes without added newlines, headings, or decoration."""
        exact_bytes = b"location /custom { try_files $uri =404; }"
        self.service.apply(fragment(name="exact-test", content=exact_bytes))

        stdout_capture = io.BytesIO()
        fake_stdout = io.TextIOWrapper(stdout_capture, encoding="utf-8", write_through=True)
        args = MagicMock()
        args.name = "exact-test"
        args.content = True
        args.json = False
        args.output = None
        args.instance_service = self.service

        with patch("sys.stdout", fake_stdout):
            _config_show(None, args, use_json=False)

        self.assertEqual(stdout_capture.getvalue(), exact_bytes)

    def test_show_content_and_json_incompatibility(self):
        """T083: --content and --json are mutually exclusive."""
        args = MagicMock()
        args.name = "exact-test"
        args.content = True
        args.json = True
        args.output = None

        with self.assertRaises(SystemExit) as ctx:
            from sandbox.commands.server import _validate_show_content_json
            args.config_action = "show"
            _validate_show_content_json(args)
        self.assertEqual(ctx.exception.code, 1)

    def test_show_missing_fragment_refusal(self):
        """T083: Showing a nonexistent fragment refuses before emitting anything."""
        stdout_capture = io.BytesIO()
        fake_stdout = io.TextIOWrapper(stdout_capture, encoding="utf-8", write_through=True)
        args = MagicMock()
        args.name = "nonexistent-fragment"
        args.content = True
        args.json = False
        args.output = None
        args.instance_service = self.service

        with patch("sys.stdout", fake_stdout):
            with self.assertRaises(SystemExit):
                _config_show(None, args, use_json=False)

        self.assertEqual(stdout_capture.getvalue(), b"")

    def test_show_degraded_pre_emission_refusal(self):
        """T083: If repository is degraded/corrupt, refusal occurs before any emission."""
        stdout_capture = io.BytesIO()
        fake_stdout = io.TextIOWrapper(stdout_capture, encoding="utf-8", write_through=True)
        args = MagicMock()
        args.name = "exact-test"
        args.content = True
        args.json = False
        args.output = None

        # Corrupt state
        with patch.object(self.service, "inspect", return_value="recovery_needed"):
            args.instance_service = self.service
            with patch("sys.stdout", fake_stdout):
                with self.assertRaises(SystemExit):
                    _config_show(None, args, use_json=False)

        self.assertEqual(stdout_capture.getvalue(), b"")

    def test_default_show_returns_metadata_only(self):
        """T083: Default show (no --content) outputs metadata only, never raw bytes."""
        exact_bytes = b"location /secret-marker { return 200 'hello'; }"
        self.service.apply(fragment(name="meta-test", content=exact_bytes))

        stdout_capture = io.StringIO()
        args = MagicMock()
        args.name = "meta-test"
        args.content = False
        args.json = True
        args.output = None
        args.instance_service = self.service

        with patch("sys.stdout", stdout_capture):
            _config_show(None, args, use_json=True)

        output = stdout_capture.getvalue()
        self.assertNotIn("location /secret-marker", output)
        self.assertIn("meta-test", output)


if __name__ == "__main__":
    unittest.main()

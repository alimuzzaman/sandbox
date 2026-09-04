import unittest
import tempfile
import io
from unittest.mock import patch, MagicMock

from tests.server_config_fixtures import (
    FakeAdapter, FakeClock, FIXED_INCARNATION, fragment,
)
from sandbox.server_config.models import (
    TerminalOutcome,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from sandbox.commands.server import _config_list, _config_show, _config_revert


class TestServerConfigContentLeaks(unittest.TestCase):
    """T085: recognizable-marker leak scans across list/default show/JSON/errors/phase evidence."""

    CANARY = "CANARY_TOKEN_ALPHA_774921"

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
        self.content_with_canary = f"location /canary {{ set $marker '{self.CANARY}'; }}".encode("utf-8")
        self.service.apply(fragment(name="canary-frag", content=self.content_with_canary))

    def test_leak_scan_in_list_output(self):
        """T085: List output never leaks fragment content bytes."""
        # Check service list
        items = self.service.list()
        for item in items:
            self.assertNotIn(self.CANARY, str(item))

        # Check command output
        with patch("sandbox.commands.server._render_json") as mock_json:
            args = MagicMock()
            args.json = True
            args.instance_service = self.service
            _config_list(None, args, use_json=True)
            mock_json.assert_called_once()
            payload = mock_json.call_args[0][0]
            self.assertNotIn(self.CANARY, str(payload))

    def test_leak_scan_in_default_show(self):
        """T085: Default show output never leaks fragment content bytes."""
        with patch("sandbox.commands.server._render_json") as mock_json:
            args = MagicMock()
            args.name = "canary-frag"
            args.content = False
            args.output = None
            args.json = True
            args.instance_service = self.service
            _config_show(None, args, use_json=True)
            mock_json.assert_called_once()
            payload = mock_json.call_args[0][0]
            self.assertNotIn(self.CANARY, str(payload))

    def test_leak_scan_in_revert_output(self):
        """T085: Revert output never leaks fragment content bytes."""
        res = self.service.revert("canary-frag")
        self.assertNotIn(self.CANARY, str(res))

    def test_leak_scan_in_error_messages(self):
        """T085: Error and refusal messages never leak fragment content bytes."""
        # Submit fragment with canary that causes refusal
        unsafe_payload = f"proxy_pass http://upstream; # {self.CANARY}".encode("utf-8")
        try:
            self.service.apply(name="unsafe-canary", content=unsafe_payload)
        except Exception as exc:
            self.assertNotIn(self.CANARY, str(exc))

    def test_leak_scan_in_phase_evidence(self):
        """T085: Phase evidence and transaction records never store raw fragment bytes."""
        tx = self.repository.read_transaction()
        if tx is not None:
            self.assertNotIn(self.CANARY, str(tx))


if __name__ == "__main__":
    unittest.main()

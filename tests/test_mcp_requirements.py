"""Dependency contract for the remote FastMCP transport (Spec 014)."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class TestMcpRequirements(unittest.TestCase):
    def test_remote_transport_pins_the_v1_fastmcp_sdk_contract(self):
        requirements = (ROOT / "mcp" / "wp-server" / "requirements.txt").read_text()
        self.assertIn("mcp==1.25.0", requirements)


if __name__ == "__main__":
    unittest.main()

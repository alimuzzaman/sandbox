"""Migration plans describe credential custody without opening credentials."""
import unittest
from unittest.mock import patch

from sandbox.core import _remote as remote


class JoinedMigrationPlanTests(unittest.TestCase):
    def test_joined_owner_plan_preserves_existing_credentials_without_remote_effects(self):
        record = {"joined": True, "control_transport": "tailscale", "control_access": "tailnet"}
        with patch.object(remote, "ssh_run", side_effect=AssertionError("plan executed SSH")):
            plan = remote.migrate_remote_mcp_service(record, "100.64.1.2", 9174,
                None, confirm=False, preserve_credentials=True)
        self.assertIn("preserve existing owner-only remote credential file", plan["steps"])
        self.assertNotIn("write owner-only remote credential file", plan["steps"])

    def test_ordinary_plan_keeps_existing_credential_install_behavior(self):
        with patch.object(remote, "ssh_run", side_effect=AssertionError("plan executed SSH")):
            plan = remote.migrate_remote_mcp_service({}, "127.0.0.1", 9174,
                None, confirm=False)
        self.assertIn("write owner-only remote credential file", plan["steps"])
        self.assertNotIn("preserve existing owner-only remote credential file", plan["steps"])

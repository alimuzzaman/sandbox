import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from sandbox.recovery.catalog import RecoveryCatalog
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.inventory import SandboxRemoteInventory
from sandbox.recovery.service import RecoveryService


class FakeInventory:
    def __init__(self): self.calls = []
    def discover(self, remote):
        self.calls.append(remote)
        return {"host_projects": ["fixture"], "managed_containers": []}


class TestRecoveryInventory(unittest.TestCase):
    def test_remote_plan_uses_read_only_inventory_dependency(self):
        inventory = FakeInventory()
        payload = RecoveryService(RecoveryCatalog(1, ()), inventory=inventory).plan(remote="test")
        self.assertTrue(payload["ok"])
        self.assertEqual(inventory.calls, ["test"])
        self.assertEqual(payload["data"]["remote_inventory"]["host_projects"], ["fixture"])

    @patch("sandbox.core._remote.ssh_run")
    @patch("sandbox.core._remote.resolve_sandbox_home", return_value="/home/sandbox")
    @patch("sandbox.core._remote.get_remote", return_value={"provisioned": True})
    def test_remote_inventory_rejects_malformed_schema(self, get_remote, resolve_home, ssh_run):
        ssh_run.return_value = CompletedProcess([], 0, '{"host_projects": "not-a-list"}\n', "")
        with self.assertRaisesRegex(RecoveryError, "invalid data"):
            SandboxRemoteInventory().discover("test")


if __name__ == "__main__": unittest.main()

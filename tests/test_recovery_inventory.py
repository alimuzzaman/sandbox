import unittest

from sandbox.recovery.catalog import RecoveryCatalog
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


if __name__ == "__main__": unittest.main()

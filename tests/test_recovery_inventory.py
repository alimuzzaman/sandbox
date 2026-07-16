import json
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

    @patch("sandbox.core._remote.ssh_run")
    @patch("sandbox.core._remote.resolve_sandbox_home", return_value="/home/sandbox")
    @patch("sandbox.core._remote.get_remote", return_value={"provisioned": True})
    def test_remote_inventory_rejects_malformed_nested_records(self, get_remote, resolve_home, ssh_run):
        payload = {
            "host_projects": ["site"],
            "runtime_environments": {"site": ["default"]},
            "managed_containers": ["sandbox-host-site"],
            "mounts": {"sandbox-host-site": [{
                "type": "bind", "name": "/srv/site", "destination": "/var/www", "rw": True,
            }]},
            "repositories": {"site": {
                "head": "abc", "branch": "main", "dirty_count": 0, "untracked_count": 0,
            }},
            "warnings": [],
        }
        for mutate in (
            lambda value: value["mounts"]["sandbox-host-site"][0].update(rw="yes"),
            lambda value: value["repositories"]["site"].update(dirty_count=-2),
            lambda value: value["runtime_environments"].update(site=["bad\nname"]),
        ):
            candidate = json.loads(json.dumps(payload))
            mutate(candidate)
            ssh_run.return_value = CompletedProcess([], 0, json.dumps(candidate) + "\n", "")
            with self.subTest(candidate=candidate), self.assertRaisesRegex(RecoveryError, "invalid data"):
                SandboxRemoteInventory().discover("test")


if __name__ == "__main__": unittest.main()

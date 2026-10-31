"""Spec 066: sb host heal restarts restart-exhausted hosted services."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.hosting import heal

GB = 1024 ** 3


def container(cid, project, status="exited", policy="on-failure", maximum=5,
              count=5, exit_code=1, name=None):
    return {"Id": cid, "Name": "/" + (name or cid),
            "Config": {"Labels": {"com.docker.compose.project": project}},
            "State": {"Status": status, "ExitCode": exit_code},
            "RestartCount": count,
            "HostConfig": {"RestartPolicy": {"Name": policy, "MaximumRetryCount": maximum}}}


INVENTORY = [
    container("a10000000000", "sandbox-host-lenzora-production", name="worker"),
    container("a20000000000", "sandbox-host-lenzora-production", status="running"),
    container("a30000000000", "sandbox-host-lenzora-production", count=2),
    container("a40000000000", "sandbox-host-lenzora-production", policy="unless-stopped"),
    container("a50000000000", "sandbox-proj-workspace-0123456789abcd"),
    container("a60000000000", "sandbox-host-other-production", exit_code=0),
    container("a70000000000", "sandbox-host-lenzora-development", maximum=0),
]


class SelectTest(unittest.TestCase):
    def test_selects_only_exhausted_hosted(self):
        picked = heal.select_candidates(INVENTORY)
        self.assertEqual([c["id"] for c in picked], ["a10000000000"])
        self.assertEqual(picked[0]["container"], "worker")

    def test_target_filter(self):
        picked = heal.select_candidates(INVENTORY, project="lenzora", environment="production")
        self.assertEqual([c["id"] for c in picked], ["a10000000000"])


class Runner:
    def __init__(self, inventory, free):
        self.inventory = inventory
        self.free = free
        self.started = []

    def ssh(self, remote, command, timeout=30):
        if command.startswith("docker start"):
            self.started.extend(command.split()[2:])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "docker inspect" in command and "--format" in command:
            return SimpleNamespace(returncode=0, stdout="running\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.inventory), stderr="")


class ExecuteTest(unittest.TestCase):
    def run_heal(self, free, floor=30, confirm=False, inventory=INVENTORY):
        runner = Runner(inventory, free)
        space = {"status": "complete" if free is not None else "unavailable", "free_bytes": free}
        with mock.patch.object(heal, "ssh_run", side_effect=runner.ssh), \
                mock.patch.object(heal, "remote_free_space", return_value=space):
            result = heal.run({"storage_floor_gb": floor}, confirm=confirm)
        return result, runner

    def test_dry_run_restarts_nothing(self):
        result, runner = self.run_heal(80 * GB)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(runner.started, [])

    def test_confirm_above_floor_restarts_listed_only(self):
        result, runner = self.run_heal(80 * GB, confirm=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(sorted(runner.started), ["a10000000000"])
        self.assertEqual([r["state"] for r in result["restarted"]], ["running"])

    def test_confirm_at_or_below_or_unknown_refuses(self):
        for free in (30 * GB, 10 * GB, None):
            result, runner = self.run_heal(free, confirm=True)
            self.assertFalse(result["ok"], free)
            self.assertEqual(result["code"], "storage_floor_not_clear")
            self.assertEqual(runner.started, [])

    def test_no_floor_configured_requires_measurable_space(self):
        result, runner = self.run_heal(1 * GB, floor=0, confirm=True)
        self.assertTrue(result["ok"])
        result, runner = self.run_heal(None, floor=0, confirm=True)
        self.assertFalse(result["ok"])

    def test_inventory_failure_refuses(self):
        with mock.patch.object(heal, "ssh_run", return_value=SimpleNamespace(
                returncode=1, stdout="", stderr="x")), \
                mock.patch.object(heal, "remote_free_space",
                                  return_value={"status": "complete", "free_bytes": 80 * GB}):
            result = heal.run({"storage_floor_gb": 30}, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "inventory_unavailable")


if __name__ == "__main__":
    unittest.main()

class SafetyRegressionTest(unittest.TestCase):
    def test_successful_exit_is_not_exhausted(self):
        self.assertEqual(heal.select_candidates([INVENTORY[5]]), [])

    def test_inventory_list_failure_is_not_empty_success(self):
        self.assertIn('|| exit', heal._INVENTORY)

    def test_space_is_rechecked_after_candidate_reselection(self):
        runner = Runner(INVENTORY, 80 * GB)
        with mock.patch.object(heal, 'ssh_run', side_effect=runner.ssh), \
                mock.patch.object(heal, 'remote_free_space', side_effect=[
                    {'status':'complete','free_bytes':80 * GB},
                    {'status':'complete','free_bytes':30 * GB}]):
            result = heal.run({'storage_floor_gb':30}, confirm=True)
        self.assertFalse(result['ok'])
        self.assertEqual(runner.started, [])

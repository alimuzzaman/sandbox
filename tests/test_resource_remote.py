from __future__ import annotations

import json
import unittest

from sandbox.resources.remote import RemoteResourceAdapter, _program
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import NOW
from tests.resource_fixtures import observation
from sandbox.resources.models import CleanupCandidate


class TestRemoteResourceAdapter(unittest.TestCase):
    def test_unknown_and_unprovisioned_remotes_fail_before_probe(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "unknown remote"):
            RemoteResourceAdapter(
                "missing", remote_lookup=lambda _name: None,
                ssh_process=lambda *_args, **_kwargs: calls.append("ssh"),
            ).target()
        with self.assertRaisesRegex(RuntimeError, "not provisioned"):
            RemoteResourceAdapter(
                "remote-a",
                remote_lookup=lambda _name: {"ssh": "host", "provisioned": False},
                ssh_process=lambda *_args, **_kwargs: calls.append("ssh"),
            ).target()
        self.assertEqual(calls, [])

    def test_remote_probe_returns_compact_snapshot_without_deployment(self):
        calls = []
        payload = {
            "identity": "remote-identity",
            "capacity": {
                "total_bytes": 100, "used_bytes": 80,
                "available_bytes": 20, "reserved_bytes": 0,
            },
            "resources": [],
            "category_outcomes": [{"category": "paths", "status": "complete"}],
            "drift": None,
        }

        def ssh(_remote, command, *, input_data=None, timeout=0):
            calls.append((command, input_data, timeout))
            return ProcessResult(("ssh",), 0, json.dumps(payload), "")

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=30)
        self.assertEqual(snapshot.target.identity, "remote-identity")
        self.assertEqual(snapshot.capacity["used_bytes"], 80)
        self.assertIn('PYTHONPATH="$sandbox_runtime" python3 -', calls[0][0])
        self.assertIn("disk_usage", calls[0][1])
        self.assertNotIn("deploy_exact_working_tree", str(calls))
        self.assertIn('"managed_host":true', calls[0][1])
        self.assertIn('"remote_name":"remote-a"', calls[0][1])

    def test_remote_program_covers_lifecycle_host_engine_and_exact_cache_evidence(self):
        program = _program({
            "action": "observe",
            "thorough": True,
            "budget_seconds": 30,
            "managed_host": True,
            "remote_name": "remote-a",
        })
        compile(program, "<remote-resource-probe>", "exec")
        for evidence in (
            "registry.sqlite3",
            "registry.json",
            "JsonRegistryRepository",
            "read_resource_index",
            "docker_build_cache",
            "host_filesystem",
            "Docker overlay layers",
            "registry_and_job_absence",
            "live_compose_project",
            "capacity_accounted",
        ):
            self.assertIn(evidence, program)
        self.assertNotIn("docker system prune", program)
        self.assertNotIn("docker volume prune", program)
        self.assertNotIn("sqlite3.connect", program)
        self.assertNotIn("registry.read_text", program)

    def test_targeted_remote_revalidation_uses_exact_kind_and_locator(self):
        calls = []
        item = observation(
            "cache", kind="build_cache", locator="a" * 24,
            evidence=(
                "buildx_disk_usage", "provisioned_sandbox_remote",
                "engine_reports_reclaimable", "immutable",
            ),
        )
        payload = {
            "identity": "remote-identity",
            "capacity": {
                "total_bytes": 100, "used_bytes": 80,
                "available_bytes": 20, "reserved_bytes": 0,
            },
            "resources": [item.internal_dict()],
            "category_outcomes": [],
        }

        def ssh(_remote, command, *, input_data=None, timeout=0):
            calls.append(input_data)
            return ProcessResult(("ssh",), 0, json.dumps(payload), "")

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        )
        current = adapter.revalidate(CleanupCandidate.from_observation(item))
        self.assertEqual(current.resource_id, "cache")
        self.assertIn('"target_kind":"build_cache"', calls[0])
        self.assertIn('"target_locator":"aaaaaaaaaaaaaaaaaaaaaaaa"', calls[0])

    def test_remote_timeout_is_partial_and_not_retried(self):
        calls = []

        def ssh(_remote, command, *, input_data=None, timeout=0):
            calls.append(command)
            return ProcessResult(("ssh",), 124, "", "timed out")

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=2)
        self.assertIsNone(snapshot.capacity)
        self.assertEqual(snapshot.category_outcomes[0]["status"], "timed_out")
        self.assertEqual(len(calls), 1)

    def test_remote_cleanup_timeout_has_one_indeterminate_receipt_and_no_retry(self):
        calls = []

        def ssh(_remote, command, *, input_data=None, timeout=0):
            calls.append((command, input_data))
            return ProcessResult(("ssh",), 124, "", "timed out")

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        )
        outcome = adapter.remove(
            CleanupCandidate.from_observation(observation()),
        )
        self.assertEqual(outcome.status, "timed_out")
        self.assertEqual(outcome.reason, "cleanup_timed_out")
        self.assertEqual(len(calls), 1)

    def test_remote_build_cache_cleanup_is_exactly_id_filtered(self):
        calls = []

        def ssh(_remote, command, *, input_data=None, timeout=0):
            calls.append(input_data)
            return ProcessResult(
                ("ssh",), 0,
                json.dumps({"status": "removed", "reason": "removed"}), "",
            )

        item = observation(
            "cache", kind="build_cache", locator="b" * 24,
            evidence=(
                "buildx_disk_usage", "provisioned_sandbox_remote",
                "engine_reports_reclaimable", "immutable",
            ),
        )
        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        )
        outcome = adapter.remove(CleanupCandidate.from_observation(item))
        self.assertEqual(outcome.status, "removed")
        self.assertIn('"kind":"build_cache"', calls[0])
        self.assertIn('"locator":"bbbbbbbbbbbbbbbbbbbbbbbb"', calls[0])
        self.assertIn('"--filter", "id=" + locator', calls[0])


if __name__ == "__main__":
    unittest.main()

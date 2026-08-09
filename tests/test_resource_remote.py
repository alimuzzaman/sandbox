from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from sandbox.resources.remote import RemoteResourceAdapter, _program
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import NOW
from tests.resource_fixtures import deep_attribution
from tests.resource_fixtures import observation
from sandbox.resources.models import CleanupCandidate


class TestRemoteResourceAdapter(unittest.TestCase):
    def _probe_namespace(self):
        source = _program({
            "action": "observe", "thorough": True, "deep": True,
            "budget_seconds": 300, "managed_host": True,
            "remote_name": "remote-a",
        }).split("\ntry:\n    output = remove()", 1)[0]
        namespace = {}
        exec(source, namespace)
        return namespace

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
            "capacity_scope_id": "capacity-root",
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
        self.assertEqual(snapshot.capacity_scope_id, "capacity-root")
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

    def test_remote_deep_probe_is_read_only_and_uses_installed_tool_fallbacks(self):
        program = _program({
            "action": "observe",
            "thorough": True,
            "deep": True,
            "budget_seconds": 300,
            "managed_host": True,
            "remote_name": "remote-a",
        })
        compile(program, "<remote-resource-probe>", "exec")
        for evidence in (
            'shutil.which("gdu")',
            '["du", "-x", "-k", "-d", "4"]',
            '[lsof, "-nP", "-FpcfDitsn", "+L1"]',
            '["docker", "system", "df", "-v", "--format", "json"]',
            '"deep_attribution": deep',
            '["sudo", "-n", "true"]',
            "exc.stdout or",
            "directory_measurement_timed_out_with_partial",
            "resource_thorough = thorough and not deep_requested",
        ):
            self.assertIn(evidence, program)
        for forbidden in (
            "apt install",
            "apt-get install",
            "dnf install",
            "yum install",
            "docker system prune",
            "docker volume prune",
        ):
            self.assertNotIn(forbidden, program)

    def test_remote_deep_payload_is_validated_and_returned(self):
        calls = []
        payload = {
            "identity": "remote-identity",
            "capacity": {
                "total_bytes": 100, "used_bytes": 80,
                "available_bytes": 20, "reserved_bytes": 0,
            },
            "resources": [],
            "category_outcomes": [],
            "drift": None,
            "deep_attribution": deep_attribution().to_dict(),
        }

        def ssh(_remote, _command, *, input_data=None, timeout=0):
            calls.append((input_data, timeout))
            return ProcessResult(("ssh",), 0, json.dumps(payload), "")

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        )
        snapshot = adapter.observe(
            thorough=True, deep=True, budget_seconds=300,
        )
        self.assertEqual(
            snapshot.deep_attribution.reconciliation.accounted_bytes, 80,
        )
        self.assertIn('"deep":true', calls[0][0])

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

    def test_remote_timeout_retains_delivered_valid_partial_payload(self):
        payload = {
            "identity": "remote-identity",
            "capacity": {
                "total_bytes": 100, "used_bytes": 80,
                "available_bytes": 20, "reserved_bytes": 0,
            },
            "resources": [],
            "category_outcomes": [
                {"category": "paths", "status": "complete"},
            ],
            "drift": None,
        }

        def ssh(_remote, _command, *, input_data=None, timeout=0):
            self.assertLessEqual(timeout, 7)
            return ProcessResult(
                ("ssh",), 124, json.dumps(payload), "timed out",
            )

        snapshot = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=ssh,
            clock=lambda: NOW,
        ).observe(thorough=True, budget_seconds=2)
        self.assertEqual(snapshot.target.identity, "remote-identity")
        self.assertEqual(snapshot.capacity["used_bytes"], 80)
        self.assertEqual(snapshot.category_outcomes[0]["status"], "complete")
        self.assertEqual(snapshot.category_outcomes[-1]["status"], "timed_out")

    def test_remote_pre_cancelled_probe_does_not_start_transport(self):
        calls = []
        snapshot = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=lambda *_args, **_kwargs: calls.append("ssh"),
            clock=lambda: NOW,
        ).observe(thorough=True, budget_seconds=30, cancelled=True)
        self.assertEqual(calls, [])
        self.assertIsNone(snapshot.capacity)
        self.assertEqual(snapshot.category_outcomes[0]["status"], "cancelled")

    def test_remote_nonzero_interruption_retains_delivered_valid_payload(self):
        payload = {
            "identity": "remote-identity",
            "capacity": None,
            "resources": [],
            "category_outcomes": [
                {"category": "directory", "status": "partial"},
            ],
        }

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            ssh_process=lambda *_args, **_kwargs: ProcessResult(
                ("ssh",), 130, json.dumps(payload), "interrupted",
            ),
            clock=lambda: NOW,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=30)
        self.assertEqual(snapshot.target.identity, "remote-identity")
        self.assertEqual(snapshot.category_outcomes[0]["status"], "partial")
        self.assertEqual(snapshot.category_outcomes[-1]["status"], "disconnected")

    def test_remote_program_models_converged_deep_evidence(self):
        program = _program({
            "action": "observe", "thorough": True, "deep": True,
            "budget_seconds": 300, "managed_host": True,
            "remote_name": "remote-a",
        })
        for evidence in (
            'Path("/proc/self/mountinfo")',
            '"parent_mount_id"',
            '"filesystem_type"',
            '"nested_mount_excluded"',
            'gdu_failed_fell_back_to_du',
            'record.get("a")',
            'deleted_open_visibility_requires_elevation',
            'potentially_reclaimable',
            'shared_bytes',
            'drift_material',
            '64 * 1024 * 1024',
        ):
            self.assertIn(evidence, program)

    def test_remote_deleted_open_uses_allocated_blocks_and_device_mapping(self):
        namespace = self._probe_namespace()
        original_stat = namespace["os"].stat
        namespace["os"].stat = lambda _path: SimpleNamespace(st_blocks=8)
        try:
            findings, total = namespace["deleted_open_findings"](
                "p42\nf7\ntREG\nD0x801\ni9\ns99999\nn/redacted (deleted)\n",
                [{
                    "filesystem_id": "fs-root", "device": "8:1",
                    "selected": True,
                }],
            )
        finally:
            namespace["os"].stat = original_stat
        self.assertEqual(total, 4096)
        self.assertEqual(findings[0]["filesystem_id"], "fs-root")
        self.assertIn("allocated_blocks", findings[0]["evidence"])
        self.assertNotIn("/redacted", json.dumps(findings))

    def test_remote_deleted_open_on_unselected_filesystem_is_diagnostic_only(self):
        namespace = self._probe_namespace()
        original_stat = namespace["os"].stat
        namespace["os"].stat = lambda _path: SimpleNamespace(st_blocks=16)
        try:
            findings, total = namespace["deleted_open_findings"](
                "p9\nf3\ntREG\nD0x802\ni4\ns8192\n",
                [{
                    "filesystem_id": "fs-unrelated", "device": "8:2",
                    "selected": False,
                }],
            )
        finally:
            namespace["os"].stat = original_stat
        self.assertEqual(total, 0)
        self.assertFalse(findings[0]["capacity_accounted"])
        self.assertIn("unselected_filesystem", findings[0]["limitations"])

    def test_remote_docker_diagnostics_separate_unique_shared_and_activity(self):
        namespace = self._probe_namespace()
        findings, logical = namespace["docker_deep_findings"](json.dumps({
            "Images": [{
                "ID": "sha256:a", "Repository": "safe/image",
                "UniqueSize": "10MiB", "SharedSize": "20MiB",
                "Containers": "1", "Reclaimable": True,
            }],
            "BuildCache": [{
                "ID": "cache-a", "Size": "5MiB", "InUse": False,
                "Reclaimable": True,
            }],
            "LocalVolumes": [{
                "Name": "volume-a", "Size": "7MiB", "Links": "0",
            }],
        }))
        self.assertEqual(logical, 22 * 1024 * 1024)
        self.assertTrue(all(not item["capacity_accounted"] for item in findings))
        self.assertEqual(
            {item["activity"] for item in findings}, {"active", "inactive"},
        )
        image = next(item for item in findings if item["kind"] == "container_image")
        self.assertEqual(image["unique_bytes"], 10 * 1024 * 1024)
        self.assertEqual(image["shared_bytes"], 20 * 1024 * 1024)
        self.assertEqual(image["owner"]["id"], "sha256:a")
        volume = next(item for item in findings if item["kind"] == "volume")
        self.assertEqual(volume["observed_bytes"], 7 * 1024 * 1024)
        self.assertEqual(
            volume["potentially_reclaimable_bytes"], 7 * 1024 * 1024,
        )
        self.assertTrue(any(
            "potentially_reclaimable" in item["evidence"] for item in findings
        ))
        surviving, surviving_total = namespace["docker_deep_findings"](
            json.dumps({
                "Images": {"unexpected": "shape"},
                "LocalVolumes": [{
                    "Name": "survivor", "Size": "3MiB", "Links": "1",
                }],
            }),
        )
        self.assertEqual(surviving_total, 3 * 1024 * 1024)
        self.assertEqual([item["kind"] for item in surviving], ["volume"])

    def test_remote_deep_scan_falls_back_and_reports_material_drift(self):
        namespace = self._probe_namespace()
        base = {
            "source": "/dev/root", "mount_point": "/", "mount_id": "1",
            "parent_mount_id": "1", "device": "8:1",
            "filesystem_type": "ext4", "writable": True,
            "total_bytes": 512 * 1024 * 1024,
            "available_bytes": 128 * 1024 * 1024,
        }
        nested = dict(
            base, source="/dev/other", mount_point="/mnt", mount_id="2",
            parent_mount_id="1", device="8:2", used_bytes=10 * 1024 * 1024,
        )
        inventories = iter([
            ([dict(base, used_bytes=100 * 1024 * 1024), nested], "complete"),
            ([dict(base, used_bytes=180 * 1024 * 1024), nested], "complete"),
        ])
        namespace["df_rows"] = lambda: next(inventories)
        original_which = namespace["shutil"].which
        namespace["shutil"].which = lambda name: "/" + name

        commands = []
        behavior = {"fail_recheck": False}

        def run(argv, _timeout):
            commands.append(tuple(argv))
            if argv[:3] == ["docker", "info", "--format"]:
                return 0, '"/var/lib/docker"', ""
            if argv[:3] == ["sudo", "-n", "true"]:
                return 0, "", ""
            if argv[:2] == ["/gdu", "--version"]:
                return 0, "gdu 5", ""
            if "/gdu" in argv:
                return 2, "", "incompatible"
            if "du" in argv:
                if behavior["fail_recheck"] and "-s" in argv:
                    return 124, "", "timed out"
                if "-s" in argv:
                    return 0, "133120\t/\n", ""
                return 0, "10240\t/var\n51200\t/\n", ""
            if "/lsof" in argv:
                return 0, "", ""
            if argv[:3] == ["docker", "system", "df"]:
                return 0, "{}", ""
            raise AssertionError(argv)

        namespace["run"] = run
        namespace["docker_deep_findings"] = lambda _output: (_ for _ in ()).throw(
            RuntimeError("provider parser failed"),
        )
        try:
            deep = namespace["deep_attribution"]({
                "total_bytes": base["total_bytes"],
                "used_bytes": 100 * 1024 * 1024,
                "available_bytes": base["available_bytes"],
            })
        finally:
            namespace["shutil"].which = original_which
        capability = next(
            item for item in deep["capabilities"]
            if item["category"] == "directory"
        )
        self.assertTrue(capability["fallback"])
        self.assertIn("gdu_failed_fell_back_to_du", capability["limitations"])
        self.assertEqual(
            deep["reconciliation"]["drift_bytes"], 80 * 1024 * 1024,
        )
        self.assertEqual(
            deep["reconciliation"]["capacity_drift_bytes"],
            80 * 1024 * 1024,
        )
        self.assertEqual(
            deep["reconciliation"]["attributed_drift_bytes"],
            80 * 1024 * 1024,
        )
        self.assertTrue(
            deep["reconciliation"]["attributed_drift_material"],
        )
        self.assertTrue(deep["reconciliation"]["drift_material"])
        self.assertTrue(any(
            item["kind"] == "directory" for item in deep["findings"]
        ))
        docker_coverage = next(
            item for item in deep["coverage"]
            if item["category"] == "container_storage"
        )
        self.assertEqual(docker_coverage["status"], "unavailable")
        self.assertEqual(
            docker_coverage["reason"], "docker_accounting_parser_failure",
        )
        du_command = next(argv for argv in commands if "du" in argv)
        self.assertIn("--exclude=/mnt", du_command)
        root = next(item for item in deep["filesystems"] if item["selected"])
        unrelated = next(item for item in deep["filesystems"] if not item["selected"])
        self.assertIn("nested_mount_excluded", root["limitations"])
        self.assertNotIn("nested_mount_excluded", unrelated["limitations"])

        second_inventories = iter([
            ([dict(base, used_bytes=100 * 1024 * 1024), nested], "complete"),
            ([], "unavailable"),
        ])
        namespace["df_rows"] = lambda: next(second_inventories)
        namespace["shutil"].which = lambda name: "/" + name
        behavior["fail_recheck"] = True
        try:
            deep_unknown = namespace["deep_attribution"]({
                "total_bytes": base["total_bytes"],
                "used_bytes": 100 * 1024 * 1024,
                "available_bytes": base["available_bytes"],
            })
        finally:
            namespace["shutil"].which = original_which
        coverage = {item["category"]: item for item in deep_unknown["coverage"]}
        self.assertEqual(coverage["capacity_drift"]["status"], "partial")
        self.assertEqual(
            coverage["capacity_drift"]["reason"], "capacity_recheck_unknown",
        )
        self.assertEqual(coverage["attributed_drift"]["status"], "partial")
        self.assertEqual(
            coverage["attributed_drift"]["reason"],
            "attributed_recheck_unknown",
        )

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

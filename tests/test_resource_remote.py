from __future__ import annotations

import json
import hashlib
import subprocess
import time
from types import SimpleNamespace
import unittest

from sandbox.resources.remote import (
    RemoteResourceAdapter,
    _program,
    parse_job_list_payload,
)
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import NOW
from tests.resource_fixtures import deep_attribution
from tests.resource_fixtures import observation
from sandbox.resources.models import CleanupCandidate, NetworkLifecycle


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
                service_request=lambda *_args, **_kwargs: calls.append("service"),
            ).target()
        with self.assertRaisesRegex(RuntimeError, "not provisioned"):
            RemoteResourceAdapter(
                "remote-a",
                remote_lookup=lambda _name: {"ssh": "host", "provisioned": False},
                service_request=lambda *_args, **_kwargs: calls.append("service"),
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
            service_request=ssh,
            clock=lambda: NOW,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=30)
        self.assertEqual(snapshot.target.identity, "remote-identity")
        self.assertEqual(snapshot.capacity["used_bytes"], 80)
        self.assertEqual(snapshot.capacity_scope_id, "capacity-root")
        self.assertEqual(calls[0][0], "POST /resources")
        self.assertNotIn("python3", calls[0][0])
        self.assertNotIn("deploy_exact_working_tree", str(calls))
        self.assertIn('"managed_host":true', calls[0][1])
        self.assertIn('"remote_name":"remote-a"', calls[0][1])

    def test_authoritative_target_uses_bounded_cache_only_probe(self):
        calls = []
        payload = {
            "identity": "remote-identity",
            "capacity": {
                "total_bytes": 100, "used_bytes": 80,
                "available_bytes": 20, "reserved_bytes": 0,
            },
            "resources": [],
            "category_outcomes": [],
        }

        def service(_remote, _command, *, input_data=None, timeout=0):
            calls.append((json.loads(input_data), timeout))
            return ProcessResult(("control",), 0, json.dumps(payload), "")

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            service_request=service,
            clock=lambda: NOW,
        )
        target = adapter.authoritative_target(budget_seconds=4)
        self.assertEqual(target.identity, "remote-identity")
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][0]["deep"])
        self.assertEqual(calls[0][0]["directory_cache"], "cache_only")
        self.assertEqual(calls[0][1], 9)

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

    def test_job_list_consumer_accepts_only_top_level_ok_jobs(self):
        self.assertEqual(parse_job_list_payload({"ok": True, "jobs": []}), [])
        with self.assertRaisesRegex(ValueError, "top-level jobs"):
            parse_job_list_payload({"ok": True, "data": {"jobs": []}})
        with self.assertRaisesRegex(ValueError, "top-level list"):
            parse_job_list_payload({"ok": True, "jobs": {}})
        with self.assertRaisesRegex(ValueError, "top-level ok"):
            parse_job_list_payload({"ok": False, "jobs": []})

    def test_remote_program_embeds_strict_job_list_parser(self):
        namespace = self._probe_namespace()
        self.assertEqual(
            namespace["parse_job_list_payload"]({"ok": True, "jobs": []}),
            [],
        )
        with self.assertRaisesRegex(ValueError, "top-level jobs"):
            namespace["parse_job_list_payload"]({
                "ok": True, "data": {"jobs": []},
            })

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
            '["du", "-x", "-k", "-d", str(DIRECTORY_DEPTH)]',
            '[lsof, "-nP", "-FpcfDitsn", "+L1"]',
            '["docker", "system", "df", "-v", "--format", "json"]',
            '"deep_attribution": deep',
            '["sudo", "-n", "true"]',
            # A killed sudo leaves the real worker holding the pipe, so the
            # probe must end the whole process group to keep its budget.
            "os.killpg(os.getpgid(process.pid), signal.SIGKILL)",
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

    def test_remote_program_batches_and_retains_partial_docker_inventory(self):
        program = _program({
            "action": "observe",
            "thorough": True,
            "deep": True,
            "budget_seconds": 30,
            "managed_host": True,
            "remote_name": "remote-a",
        })
        compile(program, "<remote-resource-probe>", "exec")
        for evidence in (
            "DOCKER_INSPECT_BATCH_SIZE = 10",
            "for offset in range(0, len(identifiers), DOCKER_INSPECT_BATCH_SIZE)",
            "inspect_timed_out = False",
            'status = "timed_out" if inspect_timed_out else',
            '"timed_out" if inspect_timed_out or code == 124',
            "build_rows = []",
        ):
            self.assertIn(evidence, program)

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
            service_request=ssh,
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
            service_request=ssh,
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
            service_request=ssh,
            clock=lambda: NOW,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=2)
        self.assertIsNone(snapshot.capacity)
        self.assertEqual(snapshot.category_outcomes[0]["status"], "timed_out")
        self.assertEqual(len(calls), 1)

    def test_remote_transport_timeout_exception_is_structured_partial_evidence(self):
        calls = []

        def ssh(_remote, command, *, input_data=None, timeout=0):
            calls.append(command)
            raise subprocess.TimeoutExpired(("ssh", "fixture"), timeout)

        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            service_request=ssh,
            clock=lambda: NOW,
        )

        snapshot = adapter.observe(thorough=True, budget_seconds=2)

        self.assertIsNone(snapshot.capacity)
        self.assertEqual(snapshot.category_outcomes, ({
            "category": "remote_probe", "status": "timed_out",
        },))
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
            service_request=ssh,
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
            service_request=lambda *_args, **_kwargs: calls.append("service"),
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
            service_request=lambda *_args, **_kwargs: ProcessResult(
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

        def walk_rows(argv, _timeout, multiplier, _keep_prefixes=()):
            commands.append(tuple(argv))
            if "/gdu" in argv:
                return [], False
            return [
                (10240 * multiplier, "/var"), (51200 * multiplier, "/"),
            ], True

        namespace["run"] = run
        namespace["walk_rows"] = walk_rows
        namespace["directory_cache_read"] = lambda: None
        namespace["directory_cache_write"] = lambda _payload: True
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
            service_request=ssh,
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
            service_request=ssh,
            clock=lambda: NOW,
        )
        outcome = adapter.remove(CleanupCandidate.from_observation(item))
        self.assertEqual(outcome.status, "removed")
        self.assertIn('"kind":"build_cache"', calls[0])
        self.assertIn('"locator":"bbbbbbbbbbbbbbbbbbbbbbbb"', calls[0])
        self.assertIn(f'"expected_resource_id":"{item.resource_id}"', calls[0])
        self.assertNotIn("python3", calls[0])
        self.assertNotIn("docker rm", calls[0])

    def test_remote_workspace_lifecycle_keeps_one_owner_identity_and_refs(self):
        namespace = self._probe_namespace()
        details = namespace["workspace_owner_details"](
            {
                "records": [{
                    "workspace_id": "ws_remote", "owner_kind": "workspace",
                    "lifecycle": "destroyed", "status": "destroyed",
                    "observed_at": "2026-07-28T12:00:00Z", "index_generation": 1,
                    "active_references": {"leases": 0, "containers": 0, "jobs": 0},
                    "bindings": [{
                        "resource_type": "compose_project",
                        "resource_id": "sandbox-unit", "status": "owned",
                    }],
                }],
                "index_generation": 1,
                "counts": {"total": 1, "unresolved": 0, "conflict": 0, "incomplete": 0},
            },
            "compose_project", "sandbox-unit",
        )
        self.assertEqual(
            (details["owner_kind"], details["owner_id"]),
            ("workspace", "ws_remote"),
        )
        self.assertEqual(details["lifecycle"], "destroyed")
        self.assertEqual(details["active_references"]["leases"], 0)

    def test_remote_network_release_refuses_active_references_without_transport(self):
        calls = []
        adapter = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            service_request=lambda *_args, **_kwargs: calls.append("service"),
            clock=lambda: NOW,
        )
        decision = adapter.release_network(NetworkLifecycle(
            network_id="network-1", owner_kind="workspace", owner_id="ws_remote",
            lifecycle="orphaned",
            active_references={"leases": 1, "containers": 0, "jobs": 0},
            allocation_state="allocated",
        ))
        self.assertEqual(decision["status"], "refused")
        self.assertEqual(decision["reason"], "active_references")
        self.assertEqual(calls, [])


class TestRemoteProbeResilience(unittest.TestCase):
    """A full disk must never degrade the report to "unmeasurable"."""

    def _probe_namespace(self, request=None):
        source = _program(request or {
            "action": "observe", "thorough": True, "deep": True,
            "budget_seconds": 300, "managed_host": True,
            "remote_name": "remote-a",
        }).split("\ntry:\n    output = remove()", 1)[0]
        namespace = {}
        exec(source, namespace)
        return namespace

    @staticmethod
    def _envelope_line(**overrides):
        payload = {
            "stage": "envelope",
            "identity": "remote-identity",
            "capacity": {
                "total_bytes": 200, "used_bytes": 190,
                "available_bytes": 10, "reserved_bytes": 0,
            },
            "capacity_scope_id": "capacity-root",
            "resources": [],
            "category_outcomes": [{
                "category": "remote_probe", "status": "partial",
                "reason": "probe_incomplete_capacity_only",
            }],
            "drift": None,
            "deep_attribution": None,
        }
        payload.update(overrides)
        return json.dumps(payload)

    def test_probe_publishes_capacity_before_bounded_work(self):
        program = _program({
            "action": "observe", "thorough": True, "deep": True,
            "budget_seconds": 300, "managed_host": True,
            "remote_name": "remote-a",
        })
        envelope_at = program.index('"stage": "envelope"')
        self.assertLess(envelope_at, program.index('PHASE = "lifecycle_evidence"'))
        self.assertIn("emit(ENVELOPE)", program)

    def test_remote_volume_remove_refuses_recreated_same_name_identity(self):
        locator = "sandbox-nodestore-lenzora"
        expected = "volume-" + hashlib.sha256(
            (locator + "\0" + "old-created-at").encode()
        ).hexdigest()[:20]
        namespace = self._probe_namespace({
            "action": "remove", "kind": "volume", "locator": locator,
            "expected_resource_id": expected, "budget_seconds": 60,
        })
        calls = []
        def run(argv, _timeout):
            calls.append(tuple(argv))
            return 0, json.dumps([{
                "Name": locator, "CreatedAt": "new-created-at",
            }]), ""
        namespace["run"] = run
        self.assertEqual(namespace["remove"](), {
            "status": "failed", "reason": "volume_identity_changed",
        })
        self.assertEqual(calls, [("docker", "volume", "inspect", locator)])

    def test_remote_volume_remove_rechecks_identity_immediately_before_exact_remove(self):
        locator = "sandbox-nodestore-lenzora"
        created = "created-at"
        expected = "volume-" + hashlib.sha256(
            (locator + "\0" + created).encode()
        ).hexdigest()[:20]
        namespace = self._probe_namespace({
            "action": "remove", "kind": "volume", "locator": locator,
            "expected_resource_id": expected, "budget_seconds": 60,
        })
        calls = []
        def run(argv, _timeout):
            calls.append(tuple(argv))
            if argv[:3] == ["docker", "volume", "inspect"]:
                return 0, json.dumps([{"Name": locator, "CreatedAt": created}]), ""
            return 0, "", ""
        namespace["run"] = run
        self.assertEqual(namespace["remove"](), {
            "status": "removed", "reason": "removed",
        })
        self.assertEqual(calls, [
            ("docker", "volume", "inspect", locator),
            ("docker", "volume", "rm", locator),
        ])

    def test_truncated_final_record_still_reports_capacity(self):
        stdout = self._envelope_line() + "\n" + '{"identity":"remote-ide'

        def ssh(_remote, _command, *, input_data=None, timeout=0):
            return ProcessResult(("ssh",), 124, stdout, "timed out")

        snapshot = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            service_request=ssh, clock=lambda: NOW,
        ).observe(thorough=True, budget_seconds=2, deep=True)
        self.assertEqual(snapshot.capacity["used_bytes"], 190)
        self.assertEqual(snapshot.capacity_scope_id, "capacity-root")
        self.assertEqual(
            snapshot.category_outcomes[0]["reason"],
            "probe_incomplete_capacity_only",
        )
        self.assertEqual(snapshot.category_outcomes[-1]["status"], "timed_out")

    def test_final_record_supersedes_the_envelope(self):
        final = self._envelope_line(
            stage="final",
            resources=[],
            category_outcomes=[{"category": "paths", "status": "complete"}],
        )

        def ssh(_remote, _command, *, input_data=None, timeout=0):
            return ProcessResult(("ssh",), 0, self._envelope_line() + "\n" + final, "")

        snapshot = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            service_request=ssh, clock=lambda: NOW,
        ).observe(thorough=True, budget_seconds=2)
        self.assertEqual(
            snapshot.category_outcomes[0],
            {"category": "paths", "status": "complete"},
        )

    def test_probe_failure_reports_capacity_and_failing_phase(self):
        failure = self._envelope_line(
            stage="error", error="resource probe failed",
            error_phase="docker_inventory", error_type="RuntimeError",
            category_outcomes=[{
                "category": "remote_probe", "status": "unavailable",
                "reason": "probe_failed_in_docker_inventory",
            }],
        )

        def ssh(_remote, _command, *, input_data=None, timeout=0):
            return ProcessResult(("ssh",), 1, failure, "")

        snapshot = RemoteResourceAdapter(
            "remote-a",
            remote_lookup=lambda _name: {"ssh": "host", "provisioned": True},
            service_request=ssh, clock=lambda: NOW,
        ).observe(thorough=True, budget_seconds=2)
        self.assertEqual(snapshot.capacity["used_bytes"], 190)
        self.assertEqual(
            snapshot.category_outcomes[0]["reason"],
            "probe_failed_in_docker_inventory",
        )

    def test_run_kills_the_whole_process_group_within_its_budget(self):
        namespace = self._probe_namespace()
        started = time.monotonic()
        # A backgrounded grandchild inherits stdout; killing only the direct
        # child would block the read until the grandchild exits.
        code, _out, _err = namespace["run"](
            ["sh", "-c", "sleep 30 & sleep 30"], 1,
        )
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - started, 10)

    def test_walk_rows_keeps_managed_paths_and_partial_output(self):
        namespace = self._probe_namespace()
        namespace["DIRECTORY_MIN_BYTES"] = 1024 * 1024
        rows, complete = namespace["walk_rows"](
            ["printf", "1\t/home/alim/sandbox/deploy-src/ws\n2048\t/var\n1\t/tmp\n"],
            5, 1024, ("/home/alim/sandbox",),
        )
        self.assertIn((1024, "/home/alim/sandbox/deploy-src/ws"), rows)
        self.assertIn((2048 * 1024, "/var"), rows)
        self.assertNotIn((1024, "/tmp"), rows)
        self.assertTrue(complete)

    def test_directory_index_reuses_cache_and_reports_provenance(self):
        namespace = self._probe_namespace()
        stored = {}
        namespace["directory_cache_write"] = lambda payload: stored.update(payload) or True
        namespace["directory_cache_read"] = lambda: stored or None
        namespace["walk_rows"] = lambda *_args, **_kwargs: (
            [(4096, "/var"), (8192, "/")], True,
        )
        fresh = namespace["directory_index"]("/", ["du"], 1024, 5, ())
        self.assertEqual(fresh["source"], "scan")
        self.assertTrue(fresh["complete"])
        namespace["walk_rows"] = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached index must not walk the disk"),
        )
        cached = namespace["directory_index"]("/", ["du"], 1024, 5, ())
        self.assertEqual(cached["source"], "cache")
        self.assertEqual(cached["rows"], [(4096, "/var"), (8192, "/")])
        self.assertFalse(cached["stale"])

    def test_fast_mode_never_walks_and_says_the_index_is_missing(self):
        namespace = self._probe_namespace({
            "action": "observe", "thorough": False, "deep": True,
            "budget_seconds": 10, "managed_host": True,
            "remote_name": "remote-a", "directory_cache": "cache_only",
        })
        self.assertTrue(namespace["FAST"])
        namespace["directory_cache_read"] = lambda: None
        namespace["walk_rows"] = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fast mode must not walk the disk"),
        )
        index = namespace["directory_index"]("/", ["du"], 1024, 5, ())
        self.assertEqual(index["source"], "cache_missing")
        self.assertEqual(index["rows"], [])

    def test_managed_paths_are_named_and_unmanaged_paths_are_not(self):
        namespace = self._probe_namespace()
        home = str(namespace["HOME"])
        findings, total = namespace["rank_directory_rows"](
            [
                (90, home + "/deploy-src/feature-workspace-1"),
                (50, "/srv/private-thing"),
                (200, "/"),
            ],
            "filesystem-1", "/", None, None,
        )
        names = [item["display_name"] for item in findings]
        self.assertIn("Sandbox home/deploy-src/feature-workspace-1", names)
        self.assertTrue(any(name.startswith("entry ") for name in names))
        self.assertEqual(total, 200)

    def test_engine_storage_includes_the_containerd_content_store(self):
        namespace = self._probe_namespace()
        namespace["INDEX_ROWS"].update({
            "/var/lib/containerd": 25_000_000_000,
            "/var/lib/docker/overlay2": 35_000_000_000,
        })
        namespace["Path"] = namespace["Path"]
        resources, outcomes = namespace["docker_storage_resources"](True)
        stores = {
            item["display_name"]: item["size_bytes"] for item in resources
        }
        self.assertEqual(
            stores.get("containerd content store"), 25_000_000_000,
        )
        self.assertEqual(outcomes[0]["category"], "docker_storage")

if __name__ == "__main__":
    unittest.main()

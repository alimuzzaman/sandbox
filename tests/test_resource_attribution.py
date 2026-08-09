from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.resources.attribution import (
    AttributionFinding,
    CapabilityObservation,
    CoverageObservation,
    DeepAttributionCollector,
    DeepAttribution,
    FilesystemObservation,
    apply_cleanup_guidance,
    parse_df_output,
    parse_docker_disk_usage,
    parse_du_output,
    parse_gdu_output,
    parse_lsof_fields,
    parse_mount_output,
    reconcile_attribution,
    select_filesystem_mounts,
)
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import observation


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, argv, *, timeout=None, **_kwargs):
        command = tuple(argv)
        self.calls.append((command, timeout))
        for prefix, result in self.responses:
            if command[:len(prefix)] == prefix:
                return ProcessResult(command, *result)
        return ProcessResult(command, 127, "", "unavailable")


class TestDeepAttributionModels(unittest.TestCase):
    def test_reconciliation_excludes_overlap_and_never_goes_negative(self):
        value = reconcile_attribution(
            used_bytes=1000,
            directory_allocated_bytes=700,
            deleted_open_bytes=200,
            observable_overhead_bytes=50,
            overlapping_logical_bytes=800,
            drift_bytes=10,
        )
        self.assertEqual(value.accounted_bytes, 950)
        self.assertEqual(value.residual_unexplained_bytes, 50)
        self.assertEqual(value.overage_bytes, 0)
        self.assertFalse(value.drift_material)

        over = reconcile_attribution(
            used_bytes=100,
            directory_allocated_bytes=120,
            deleted_open_bytes=30,
        )
        self.assertEqual(over.accounted_bytes, 100)
        self.assertEqual(over.residual_unexplained_bytes, 0)
        self.assertEqual(over.overage_bytes, 50)

    def test_reconciliation_reports_capacity_and_attributed_drift(self):
        value = reconcile_attribution(
            used_bytes=10 * 1024 ** 3,
            directory_allocated_bytes=8 * 1024 ** 3,
            capacity_drift_bytes=80 * 1024 ** 2,
            attributed_drift_bytes=120 * 1024 ** 2,
        )
        self.assertEqual(value.capacity_drift_bytes, 80 * 1024 ** 2)
        self.assertEqual(value.attributed_drift_bytes, 120 * 1024 ** 2)
        self.assertFalse(value.capacity_drift_material)
        self.assertTrue(value.attributed_drift_material)
        self.assertTrue(value.drift_material)

    def test_deep_payload_validates_and_redacts_public_values(self):
        filesystem = FilesystemObservation(
            filesystem_id="fs-root",
            display_name="root",
            filesystem_type="ext4",
            total_bytes=1000,
            used_bytes=800,
            available_bytes=200,
            writable=True,
            selected=True,
            selection_reason="root",
            status="complete",
            observed_allocated_bytes=700,
            hardlink_deduplication="confirmed",
            limitations=(),
        )
        finding = AttributionFinding(
            finding_id="finding-a",
            kind="directory",
            display_name="password=hunter2",
            filesystem_id="fs-root",
            owner_kind="host",
            owner_id=None,
            observed_bytes=700,
            capacity_accounted=True,
            overlap="none",
            activity="unknown",
            guidance="monitoring_only",
            evidence=("allocated_blocks",),
            limitations=(),
        )
        deep = DeepAttribution(
            status="complete",
            filesystems=(filesystem,),
            findings=(finding,),
            capabilities=(CapabilityObservation(
                category="directory",
                name="gdu",
                version="v1",
                fallback=False,
                privilege="unprivileged",
                status="complete",
            ),),
            coverage=(CoverageObservation(
                category="directory",
                boundary_id="fs-root",
                status="complete",
                duration_ms=1,
                confidence="high",
                privilege_sufficient=True,
            ),),
            reconciliation=reconcile_attribution(
                used_bytes=800,
                directory_allocated_bytes=700,
            ),
        )
        payload = deep.to_dict()
        self.assertNotIn("hunter2", str(payload))
        self.assertEqual(
            payload["reconciliation"]["residual_unexplained_bytes"],
            100,
        )

    def test_cleanup_guidance_only_references_existing_exact_eligibility(self):
        base = AttributionFinding(
            finding_id="finding-a",
            kind="container",
            display_name="temporary-worker",
            filesystem_id=None,
            owner_kind="container_engine",
            owner_id="temporary-worker-id",
            observed_bytes=10,
            capacity_accounted=False,
            overlap="directory_root",
            activity="inactive",
            guidance="monitoring_only",
        )
        manual = AttributionFinding(
            finding_id="finding-b",
            kind="deleted_open",
            display_name="process 42",
            filesystem_id="fs-root",
            owner_kind="process",
            owner_id="42",
            observed_bytes=20,
            capacity_accounted=True,
            overlap="none",
            activity="active",
            guidance="manual",
        )
        deep = DeepAttribution(
            status="complete",
            filesystems=(),
            findings=(base, manual),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=30,
                directory_allocated_bytes=10,
                deleted_open_bytes=20,
            ),
        )
        eligible = observation(
            resource_id="container-" + hashlib.sha256(
                b"temporary-worker-id",
            ).hexdigest()[:20],
            kind="container",
            classification="disposable_cache",
            locator="temporary-worker-id",
        )
        eligible = replace(eligible, display_name="temporary-worker")
        guided = apply_cleanup_guidance(deep, (eligible,))
        self.assertEqual(
            [item.guidance for item in guided.findings],
            ["existing_cache_scope", "manual"],
        )
        self.assertEqual(
            apply_cleanup_guidance(deep, ()).findings[0].guidance,
            "monitoring_only",
        )

        same_name_wrong_locator = replace(
            eligible, locator="different-id", resource_id="different-resource",
        )
        self.assertEqual(
            apply_cleanup_guidance(deep, (same_name_wrong_locator,)).findings[0].guidance,
            "monitoring_only",
        )


class TestDeepAttributionParsers(unittest.TestCase):
    def test_mount_parser_reports_safe_topology_and_normalized_flags(self):
        rows = parse_mount_output(
            "/dev/root on / type ext4 (rw,relatime)\n"
            "/dev/data on /srv/data type xfs (ro,nosuid,nodev)\n",
            capacity_rows=parse_df_output(
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 1000 600 400 60% /\n"
                "/dev/data 2000 1000 1000 50% /srv/data\n"
            ),
        )
        root, nested = rows
        self.assertEqual(root["filesystem_type"], "ext4")
        self.assertEqual(root["mount_flags"], ("local", "read_write", "root"))
        self.assertTrue(root["writable"])
        self.assertEqual(nested["parent_mount_id"], root["mount_id"])
        self.assertEqual(
            nested["mount_flags"],
            ("local", "nested", "nodev", "nosuid", "read_only"),
        )
        self.assertNotIn("relatime", nested["mount_flags"])

    def test_mount_selection_maps_apfs_firmlink_and_distinct_managed_scope(self):
        rows = [
            {"mount_point": "/", "capacity_scope_id": "root"},
            {
                "mount_point": "/System/Volumes/Data",
                "capacity_scope_id": "data",
                "filesystem_type": "apfs",
            },
        ]
        selected = select_filesystem_mounts(
            rows,
            host_root=Path("/"),
            sandbox_home=Path("/Users/runner/sandbox"),
            managed_roots=({"path": "/Users/runner/jobs", "kind": "job"},),
            system_name="Darwin",
        )
        self.assertEqual(selected["/"], "root")
        self.assertEqual(selected["/System/Volumes/Data"], "sandbox_home")

    def test_apfs_volume_group_uses_one_capacity_scope(self):
        rows = parse_mount_output(
            "/dev/disk3s1s1 on / (apfs, sealed, local, read-only)\n"
            "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled)\n",
            capacity_rows=parse_df_output(
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/disk3s1s1 500000 20000 70000 23% /\n"
                "/dev/disk3s5 500000 400000 70000 86% /System/Volumes/Data\n"
            ),
        )
        self.assertEqual(rows[0]["capacity_scope_id"], rows[1]["capacity_scope_id"])
        self.assertNotEqual(rows[0]["mount_id"], rows[1]["mount_id"])
        self.assertFalse(rows[0]["writable"])
    def test_df_inventory_is_bounded_and_marks_writable_filesystems(self):
        rows = parse_df_output(
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/root 1000 600 400 60% /\n"
            "/dev/data 2000 1000 1000 50% /srv/data\n",
        )
        self.assertEqual([row["mount_point"] for row in rows], ["/", "/srv/data"])
        self.assertEqual(rows[0]["used_bytes"], 600 * 1024)

    def test_gdu_output_uses_raw_allocated_bytes_and_bounded_ranking(self):
        findings, total = parse_gdu_output(
            "  12288 /data\n"
            "   4096 /data/b\n"
            "   8192 /data/a\n",
            filesystem_id="fs-root",
            root="/data",
            limit=2,
        )
        self.assertEqual(total, 12288)
        self.assertEqual([item.observed_bytes for item in findings], [8192, 4096])
        self.assertTrue(all(not item.capacity_accounted for item in findings))

    def test_directory_ranking_uses_safe_labels_for_known_host_roots(self):
        findings, _total = parse_gdu_output(
            "4096 /var\n2048 /home\n6144 /\n",
            filesystem_id="fs-root",
            root="/",
        )
        self.assertEqual(
            [item.display_name for item in findings],
            ["host variable data", "user home data"],
        )

    def test_du_fallback_converts_kib_and_uses_root_total(self):
        findings, total = parse_du_output(
            "4\t/data/a\n8\t/data/b\n16\t/data\n",
            filesystem_id="fs-root",
            root="/data",
            limit=2,
        )
        self.assertEqual(total, 16 * 1024)
        self.assertEqual(
            [item.observed_bytes for item in findings],
            [8 * 1024, 4 * 1024],
        )

    def test_partial_nested_du_rows_use_non_overlapping_frontier(self):
        findings, total = parse_du_output(
            "10\t/var/lib/docker\n"
            "20\t/var/lib\n"
            "5\t/home/user\n",
            filesystem_id="fs-root",
            root="/",
        )
        self.assertEqual(total, 25 * 1024)
        self.assertEqual(len(findings), 3)

    def test_lsof_fields_deduplicate_device_inode_and_hide_paths(self):
        output = "\n".join((
            "p123",
            "cworker password=secret",
            "f4",
            "tREG",
            "D8,1",
            "i77",
            "s4096",
            "n/tmp/token=abc (deleted)",
            "f5",
            "tREG",
            "D8,1",
            "i77",
            "s4096",
            "n/tmp/token=abc (deleted)",
            "p456",
            "cother",
            "f9",
            "tREG",
            "D8,1",
            "i88",
            "s2048",
            "n/tmp/other (deleted)",
        ))
        findings, total = parse_lsof_fields(
            output,
            filesystem_id="fs-root",
        )
        self.assertEqual(total, 6144)
        self.assertEqual(len(findings), 2)
        rendered = str([item.to_dict() for item in findings])
        self.assertNotIn("token=abc", rendered)
        self.assertNotIn("secret", rendered)
        self.assertIn("process 123", rendered)

    def test_lsof_uses_allocated_blocks_and_groups_by_filesystem_process(self):
        output = "\n".join((
            "p123", "cworker", "f4", "tREG", "D8,1", "i77", "s99999", "B8",
            "f5", "tREG", "D8,1", "i88", "s99999", "B4",
            "p123", "cworker", "f6", "tREG", "D8,2", "i99", "s99999", "B2",
        ))
        findings, total = parse_lsof_fields(
            output,
            filesystem_id=None,
            filesystem_by_device={"8,1": "fs-a", "8,2": "fs-b"},
        )
        self.assertEqual(total, 14 * 512)
        self.assertEqual(
            {(item.filesystem_id, item.owner_id): item.observed_bytes for item in findings},
            {("fs-a", "123"): 12 * 512, ("fs-b", "123"): 2 * 512},
        )
        self.assertTrue(all("allocated_blocks" in item.evidence for item in findings))

    def test_lsof_maps_real_darwin_hex_device_identity(self):
        findings, total = parse_lsof_fields(
            "p42\ncworker\nf4\ntVREG\nD0x100000f\ni77\nB8\ns99999\n",
            filesystem_id=None,
            filesystem_by_device={"0x100000f": "fs-data"},
            require_deleted_marker=False,
        )
        self.assertEqual(total, 8 * 512)
        self.assertEqual(findings[0].filesystem_id, "fs-data")
        self.assertTrue(findings[0].capacity_accounted)

    def test_lsof_equal_sizes_sort_mapped_and_unmapped_filesystems_safely(self):
        findings, total = parse_lsof_fields(
            "\n".join((
                "p10", "ca", "f4", "tREG", "D8,1", "i1", "B8", "s4096",
                "p20", "cb", "f5", "tREG", "D9,1", "i2", "B8", "s4096",
            )),
            filesystem_id=None,
            filesystem_by_device={"8,1": "fs-mapped"},
        )
        self.assertEqual(total, 16 * 512)
        self.assertEqual(
            {item.filesystem_id for item in findings},
            {"fs-mapped", None},
        )

    def test_docker_structured_details_are_overlap_only(self):
        payload = {
            "Images": [{
                "ID": "sha256:abc",
                "Repository": "example/app",
                "Tag": "latest",
                "Containers": "1",
                "Size": "3GB",
                "SharedSize": "2GB",
                "UniqueSize": "1GB",
            }],
            "Containers": [{
                "ID": "container-a",
                "Names": "app",
                "State": "running",
                "Size": "20MB",
            }],
            "LocalVolumes": [{
                "Name": "data",
                "Links": "1",
                "Size": "5GB",
            }],
            "BuildCache": [{
                "ID": "cache-a",
                "InUse": "false",
                "Shared": "true",
                "Size": "500MB",
            }],
        }
        findings, logical = parse_docker_disk_usage(json.dumps(payload))
        self.assertEqual(len(findings), 4)
        self.assertGreater(logical, 0)
        self.assertTrue(all(not item.capacity_accounted for item in findings))
        image = next(item for item in findings if item.kind == "container_image")
        self.assertEqual(image.observed_bytes, 1024 ** 3)
        self.assertEqual(image.unique_bytes, 1024 ** 3)
        self.assertEqual(image.shared_bytes, 2 * 1024 ** 3)
        self.assertEqual(image.potentially_reclaimable_bytes, 0)
        self.assertEqual(image.overlap, "shared_layers")


class TestDeepAttributionCollector(unittest.TestCase):
    def test_expired_deadline_does_not_launch_runner(self):
        runner = FakeRunner([])
        collector = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
            monotonic=lambda: 10.0,
        )
        result = collector._run(("du", "-x"), 10.0, 30.0)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(runner.calls, [])

    def test_zero_budget_collection_launches_no_commands(self):
        runner = FakeRunner([])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
            monotonic=lambda: 10.0,
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            budget_seconds=0,
        )
        self.assertEqual(runner.calls, [])
        self.assertEqual(deep.filesystems[0].status, "timed_out")

    def test_df_evidence_survives_failed_mount_topology_as_partial(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("mount",), (1, "", "permission denied")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            budget_seconds=10,
        )
        mounts = next(item for item in deep.coverage if item.category == "mount_inventory")
        self.assertEqual(mounts.status, "partial")
        self.assertEqual(mounts.reason, "mount_topology_unavailable")
        self.assertEqual(len(deep.filesystems), 1)
        self.assertEqual(deep.filesystems[0].used_bytes, 80 * 1024)

    def test_du_explicitly_excludes_same_device_nested_mount(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n"
                "/dev/root 100 80 20 80% /fixture/bind\n", "")),
            (("mount",), (0,
                "/dev/root on /fixture type ext4 (rw)\n"
                "/dev/root on /fixture/bind type ext4 (rw,bind)\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
            system=lambda: "Linux",
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            budget_seconds=10,
        )
        scan = next(
            command for command, _timeout in runner.calls
            if command[:2] == ("du", "-x") and "-d" in command
        )
        self.assertIn("--exclude=/fixture/bind", scan)
        root = next(item for item in deep.filesystems if item.selection_reason == "root")
        self.assertIn("nested_mount_excluded", root.limitations)

    def test_deleted_open_parser_failure_retains_directory_and_docker_evidence(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("/usr/bin/lsof",), (0, "malformed", "")),
            (("docker", "system", "df"), (0, json.dumps({
                "Images": [{"ID": "img", "Repository": "app",
                            "UniqueSize": "10KiB", "Containers": "1"}],
            }), "")),
        ])
        collector = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
        )
        with patch(
            "sandbox.resources.attribution.parse_lsof_fields",
            side_effect=ValueError("unsafe parser detail"),
        ):
            deep = collector.collect(
                capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                          "available_bytes": 20 * 1024},
                budget_seconds=10,
            )
        self.assertEqual(deep.reconciliation.directory_allocated_bytes, 60 * 1024)
        self.assertTrue(any(item.kind == "container_image" for item in deep.findings))
        deleted = next(item for item in deep.coverage if item.category == "deleted_open")
        self.assertEqual(deleted.status, "unavailable")
        self.assertEqual(deleted.reason, "deleted_open_parse_failed")
    def test_collector_scans_apfs_volumes_but_accounts_shared_capacity_once(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/disk3s1s1 500000 20000 70000 23% /\n"
                "/dev/disk3s5 500000 400000 70000 86% /System/Volumes/Data\n", "")),
            (("mount",), (0,
                "/dev/disk3s1s1 on / (apfs, sealed, local, read-only)\n"
                "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled)\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x", "-k", "-d", "4", "/System/Volumes/Data"),
             (0, "300000\t/System/Volumes/Data\n", "")),
            (("du", "-x"), (0, "10000\t/\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        collector = DeepAttributionCollector(
            runner,
            host_root=Path("/"),
            sandbox_home=Path("/Users/runner/sandbox"),
            which=lambda _name: None,
            system=lambda: "Darwin",
        )
        with patch(
            "sandbox.resources.attribution.shutil.disk_usage",
            return_value=SimpleNamespace(used=430000 * 1024),
        ):
            deep = collector.collect(
                capacity={"total_bytes": 500000 * 1024,
                          "used_bytes": 430000 * 1024,
                          "available_bytes": 70000 * 1024},
                budget_seconds=10,
            )
        self.assertEqual(len([item for item in deep.filesystems if item.selected]), 2)
        self.assertEqual(deep.reconciliation.used_bytes, 430000 * 1024)
        self.assertEqual(deep.reconciliation.directory_allocated_bytes, 310000 * 1024)
        self.assertEqual(deep.reconciliation.capacity_drift_bytes, 0)

    def test_collector_reports_attributed_byte_drift_from_bounded_recheck(self):
        class ChangingRunner(FakeRunner):
            directory_calls = 0

            def run(self, argv, *, timeout=None, **kwargs):
                command = tuple(argv)
                if command[:2] == ("du", "-x"):
                    self.calls.append((command, timeout))
                    self.directory_calls += 1
                    kib = 100000 if self.directory_calls == 1 else 180000
                    return ProcessResult(command, 0, f"{kib}\t/fixture\n", "")
                return super().run(argv, timeout=timeout, **kwargs)

        runner = ChangingRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 1000000 800000 200000 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
        ).collect(
            capacity={"total_bytes": 1000000 * 1024,
                      "used_bytes": 800000 * 1024,
                      "available_bytes": 200000 * 1024},
            budget_seconds=10,
        )
        self.assertEqual(deep.reconciliation.attributed_drift_bytes, 80000 * 1024)
        self.assertTrue(deep.reconciliation.attributed_drift_material)

    def test_duplicate_bind_mount_preserves_explicit_exclusion_reason(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n"
                "/dev/root 100 80 20 80% /managed\n", "")),
            (("mount",), (0,
                "/dev/root on /fixture type ext4 (rw)\n"
                "/dev/root on /managed type ext4 (rw,bind)\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
            system=lambda: "Linux",
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            managed_roots=({"path": "/managed/jobs"},),
            budget_seconds=10,
        )
        duplicate = next(
            item for item in deep.coverage
            if item.reason == "duplicate_filesystem_mount"
        )
        self.assertEqual(duplicate.status, "not_selected")
    def test_collector_falls_back_when_installed_gdu_fails_with_budget_remaining(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("/usr/bin/gdu", "--version"), (0, "gdu 5.36.1\n", "")),
            (("/usr/bin/gdu", "-n"), (2, "", "incompatible option")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda name: "/usr/bin/gdu" if name == "gdu" else None,
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            budget_seconds=10,
        )
        directory = next(item for item in deep.capabilities if item.category == "directory")
        self.assertEqual(directory.name, "du")
        self.assertTrue(directory.fallback)
        self.assertIn("preferred_scanner_failed", directory.limitations)
        self.assertEqual(deep.reconciliation.directory_allocated_bytes, 60 * 1024)

    def test_collector_sums_distinct_selected_filesystem_scopes(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n"
                "/dev/data 50 40 10 80% /managed\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x", "-k", "-d", "4", "/managed"),
             (0, "30\t/managed\n", "")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            managed_roots=({"path": "/managed/jobs", "kind": "job"},),
            budget_seconds=10,
        )
        self.assertEqual(deep.reconciliation.used_bytes, 120 * 1024)
        self.assertEqual(deep.reconciliation.directory_allocated_bytes, 90 * 1024)
        self.assertEqual(len([item for item in deep.filesystems if item.selected]), 2)

    def test_unprivileged_deleted_open_visibility_is_explicitly_partial(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("/usr/bin/lsof",), (0,
                "p12\ncworker\nf4\ntREG\nD8,1\ni9\ns4096\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
        ).collect(
            capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                      "available_bytes": 20 * 1024},
            budget_seconds=10,
        )
        deleted = next(item for item in deep.coverage if item.category == "deleted_open")
        self.assertEqual(deleted.status, "partial")
        self.assertFalse(deleted.privilege_sufficient)
        self.assertEqual(deleted.reason, "elevated_visibility_unavailable")

    def test_collector_maps_darwin_lsof_hex_device_to_selected_filesystem(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/disk3s5 100 80 20 80% /fixture\n", "")),
            (("mount",), (0,
                "/dev/disk3s5 on /fixture (apfs, local, journaled)\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0, "60\t/fixture\n", "")),
            (("/usr/bin/lsof",), (0,
                "p12\ncworker\nf4\ntVREG\nD0x100000f\ni9\nB8\n"
                "s99999\nn/private/tmp/file (deleted)\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        collector = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
            system=lambda: "Darwin",
        )
        with patch(
            "sandbox.resources.attribution.os.stat",
            return_value=SimpleNamespace(st_dev=0x100000F),
        ):
            deep = collector.collect(
                capacity={"total_bytes": 100 * 1024, "used_bytes": 80 * 1024,
                          "available_bytes": 20 * 1024},
                budget_seconds=10,
            )
        finding = next(item for item in deep.findings if item.kind == "deleted_open")
        self.assertEqual(finding.observed_bytes, 8 * 512)
        self.assertIsNotNone(finding.filesystem_id)
        self.assertEqual(deep.reconciliation.deleted_open_bytes, 8 * 512)
    def test_collector_prefers_installed_gdu_and_keeps_docker_overlap_logical(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (0, '"/var/lib/docker"\n', "")),
            (("/usr/bin/gdu", "--version"), (0, "gdu 5.36.1\n", "")),
            (("/usr/bin/gdu", "-n"), (0,
                "61440 /fixture\n40960 /fixture/a\n20480 /fixture/b\n", "")),
            (("/usr/bin/lsof",), (1, "", "")),
            (("docker", "system", "df"), (0, json.dumps({
                "Images": [{
                    "ID": "img", "Repository": "app", "UniqueSize": "10KiB",
                    "SharedSize": "20KiB", "Containers": "1",
                }],
            }), "")),
        ])
        collector = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda name: {
                "gdu": "/usr/bin/gdu",
                "lsof": "/usr/bin/lsof",
            }.get(name),
        )
        deep = collector.collect(
            capacity={
                "total_bytes": 100 * 1024,
                "used_bytes": 80 * 1024,
                "available_bytes": 20 * 1024,
                "reserved_bytes": 0,
            },
            budget_seconds=10,
        )
        self.assertEqual(deep.capabilities[0].name, "gdu")
        self.assertEqual(
            deep.reconciliation.directory_allocated_bytes,
            60 * 1024,
        )
        self.assertEqual(
            deep.reconciliation.overlapping_logical_bytes,
            10 * 1024,
        )
        self.assertEqual(deep.reconciliation.accounted_bytes, 60 * 1024)
        self.assertTrue(all(
            not item.capacity_accounted
            for item in deep.findings
            if item.kind == "container_image"
        ))

    def test_collector_falls_back_to_du_and_reports_unavailable_lsof(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (0,
                "40\t/fixture/a\n60\t/fixture\n", "")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        collector = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
        )
        deep = collector.collect(
            capacity={
                "total_bytes": 100 * 1024,
                "used_bytes": 80 * 1024,
                "available_bytes": 20 * 1024,
                "reserved_bytes": 0,
            },
            budget_seconds=10,
        )
        directory = next(
            item for item in deep.capabilities if item.category == "directory"
        )
        deleted = next(
            item for item in deep.capabilities
            if item.category == "deleted_open"
        )
        self.assertEqual(directory.name, "du")
        self.assertTrue(directory.fallback)
        self.assertEqual(deleted.status, "unavailable")
        self.assertEqual(deep.status, "partial")
        self.assertNotIn(
            "nested_mount_excluded",
            next(item for item in deep.filesystems if item.selected).limitations,
        )
        commands = [" ".join(call[0]) for call in runner.calls]
        self.assertTrue(any("du -x -k -d 4" in command for command in commands))
        self.assertFalse(any(
            token in command
            for command in commands
            for token in ("rm ", "prune", "install", "kill ")
        ))

    def test_collector_preserves_partial_result_when_directory_times_out(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (124, "", "timed out")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
        ).collect(
            capacity={
                "total_bytes": 100 * 1024,
                "used_bytes": 80 * 1024,
                "available_bytes": 20 * 1024,
                "reserved_bytes": 0,
            },
            budget_seconds=10,
        )
        self.assertEqual(deep.status, "partial")
        self.assertEqual(
            deep.reconciliation.residual_unexplained_bytes,
            80 * 1024,
        )
        directory = next(
            item for item in deep.coverage
            if item.category == "directory"
        )
        self.assertEqual(directory.status, "timed_out")

    def test_collector_accounts_only_completed_du_lines_on_timeout(self):
        runner = FakeRunner([
            (("df", "-Pk"), (0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root 100 80 20 80% /fixture\n", "")),
            (("docker", "info"), (127, "", "unavailable")),
            (("du", "-x"), (124,
                "20\t/fixture/a\n30\t/fixture/b\n", "timed out")),
            (("docker", "system", "df"), (127, "", "unavailable")),
        ])
        deep = DeepAttributionCollector(
            runner,
            host_root=Path("/fixture"),
            sandbox_home=Path("/fixture/sandbox"),
            which=lambda _name: None,
        ).collect(
            capacity={
                "total_bytes": 100 * 1024,
                "used_bytes": 80 * 1024,
                "available_bytes": 20 * 1024,
                "reserved_bytes": 0,
            },
            budget_seconds=10,
        )
        self.assertEqual(
            deep.reconciliation.directory_allocated_bytes,
            50 * 1024,
        )
        self.assertEqual(
            deep.reconciliation.residual_unexplained_bytes,
            30 * 1024,
        )
        root = next(item for item in deep.filesystems if item.selected)
        self.assertEqual(root.status, "partial")
        self.assertEqual(root.hardlink_deduplication, "partial")


if __name__ == "__main__":
    unittest.main()

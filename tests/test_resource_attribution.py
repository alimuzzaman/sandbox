from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

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
    reconcile_attribution,
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
            owner_id=None,
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


class TestDeepAttributionParsers(unittest.TestCase):
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
        self.assertEqual(image.overlap, "shared_layers")


class TestDeepAttributionCollector(unittest.TestCase):
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

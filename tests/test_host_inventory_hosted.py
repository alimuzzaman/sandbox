"""Hosted targets in the host inventory: count them and attribute their containers (spec 076)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sandbox.hosting import panel_lifetime as pl
from sandbox.hosting import panel_records as pr
from sandbox.hosting.lifetime import hoststore as hs
from sandbox.remote_runtime import host_inventory
from tests.test_target_lifetime import NOW, stack_record

COMMIT = "c" * 40
SECRET = "planted-secret-value-7777777777"


def container(name, memory=100, cpu=1.5):
    return {"name": name, "cpu_percent": cpu, "memory_used_bytes": memory, "memory_percent": 1, "pids": 3}


def write_target(runtime: Path, project: str, environment: str, *, stack: str | None = None) -> None:
    target = runtime / "hosts" / project / environment
    target.mkdir(parents=True)
    summary = {
        "schema": 1, "remote": "box", "project": project, "environment": environment,
        "compose_project": f"sandbox-host-{project}-{environment}", "loopback_port": 18017,
        "healthcheck_path": "/", "routes": [{"hostname": f"{project}.example.test", "mode": "serve",
                                             "primary": True}],
        "commit": COMMIT,
        "last_apply": {"status": "succeeded", "started_at": 1, "finished_at": 2, "request_id": "r"},
        "stack": None,
    }
    if stack:
        summary["stack"] = {"slug": stack, "ref": "main", "ref_branch": "main", "commit": COMMIT,
                            "template": "sandbox.hosting.yml", "ports": {}, "created_at": 1}
    (target / "summary.json").write_text(pr.encode_record(pr.parse_target_summary(summary)))
    (target / "environment.env").write_text(f"DB_PASSWORD='{SECRET}'\n")


def write_legacy(runtime: Path, project: str, environment: str) -> None:
    target = runtime / "hosts" / project / environment
    target.mkdir(parents=True)
    (target / "compose.override.yml").write_text("services: {}\n")
    (target / "environment.env").write_text(f"DB_PASSWORD='{SECRET}'\n")


class AttributionTest(unittest.TestCase):
    def test_containers_go_to_the_longest_matching_compose_project(self):
        records = {"targets": [
            {"project": "hub", "environment": "test", "compose_project": "sandbox-host-hub-test"},
            {"project": "hub-test", "environment": "stacks",
             "compose_project": "sandbox-host-hub-test-stacks",
             "stack": {"slug": "stacks"}, "last_apply": {"status": "failed"}},
        ], "legacy": [{"project": "wp", "environment": "prod"}]}
        rows = [container("sandbox-host-hub-test-web-1"), container("sandbox-host-hub-test-stacks-web-1"),
                container("sandbox-host-hub-test-stacks_db_1", memory=50), container("sandbox-host-wp-prod-db-1"),
                container("sandbox-wp-demo-web-1"), container("sandbox-host-other-env-web-1")]
        usage, claimed = host_inventory._hosted_target_usage(records, rows)
        by_key = {row["key"]: row for row in usage}
        self.assertEqual(by_key["hub/test"]["container_count"], 1)
        stacks = by_key["hub-test/stacks"]
        self.assertEqual((stacks["container_count"], stacks["memory_used_bytes"], stacks["cpu_percent"]),
                         (2, 150, 3.0))
        self.assertEqual((stacks["stack_slug"], stacks["last_apply_status"], stacks["recorded"]),
                         ("stacks", "failed", True))
        legacy = by_key["wp/prod"]
        self.assertEqual((legacy["compose_project"], legacy["recorded"], legacy["container_count"]),
                         ("sandbox-host-wp-prod", False, 1))
        self.assertNotIn("sandbox-wp-demo-web-1", claimed)
        self.assertNotIn("sandbox-host-other-env-web-1", claimed)
        self.assertEqual(len(claimed), 4)

    def test_a_target_without_containers_is_unknown_not_running(self):
        usage, claimed = host_inventory._hosted_target_usage(
            {"targets": [], "legacy": [{"project": "a", "environment": "b"}]}, [])
        self.assertEqual((usage[0]["attribution_status"], usage[0]["running"], claimed),
                         ("unknown", False, set()))


class LifetimeRowsTest(unittest.TestCase):
    """Spec 076 follow-up: rows carry the host's own lifetime view and stack fallback."""

    def test_rows_carry_lifetime_stack_fallback_and_reason(self):
        record = stack_record("wp-test-stacks", project="xspeed-wp", expires=hs.to_rfc3339(NOW - 60))
        record["environment"] = "test-stacks"
        record["compose_project"] = "sandbox-host-xspeed-wp-test-stacks"
        listing = pl.listing({record["compose_project"]: pl.project(record, NOW)})
        records = {"targets": [
            {"project": "agent-notes", "environment": "apply-eb3345e-2",
             "compose_project": "sandbox-host-agent-notes-apply-eb3345e-2",
             "last_apply": {"status": "unverified", "reason": "required_evidence_missing"}},
        ], "legacy": [{"project": "xspeed-wp", "environment": "test-stacks"}]}
        usage, _ = host_inventory._hosted_target_usage(records, [], listing)
        by_key = {row["key"]: row for row in usage}
        legacy = by_key["xspeed-wp/test-stacks"]
        self.assertEqual(legacy["stack_slug"], "wp-test-stacks")
        self.assertEqual((legacy["lifetime"]["lifetime"], legacy["lifetime"]["reap_eligible"]), ("temporary", True))
        notes = by_key["agent-notes/apply-eb3345e-2"]
        self.assertEqual((notes["last_apply_status"], notes["last_apply_reason"]),
                         ("unverified", "required_evidence_missing"))
        self.assertEqual((notes["lifetime"]["classification"], notes["lifetime"]["reason"]),
                         ("unclassified", "no_record"))
        self.assertFalse(notes["lifetime"]["reap_eligible"])

    def test_summary_slug_wins_over_lifetime_slug(self):
        record = stack_record("other", project="hub", expires=hs.to_rfc3339(NOW + 60))
        listing = pl.listing({record["compose_project"]: pl.project(record, NOW)})
        records = {"targets": [{"project": "hub", "environment": "sandbox",
                                "compose_project": record["compose_project"], "stack": {"slug": "demo"}}],
                   "legacy": []}
        usage, _ = host_inventory._hosted_target_usage(records, [], listing)
        self.assertEqual(usage[0]["stack_slug"], "demo")

    def test_without_a_listing_lifetime_is_unknown(self):
        usage, _ = host_inventory._hosted_target_usage({"targets": [], "legacy": [
            {"project": "a", "environment": "b"}]}, [])
        self.assertEqual(usage[0]["lifetime"]["classification"], "unknown")
        self.assertFalse(usage[0]["lifetime"]["reap_eligible"])


class SnapshotTest(unittest.TestCase):
    def snapshot(self, runtime: Path, rows, instances=()):
        diagnostics = {"containers": {"status": "complete", "rows": rows}, "process_view": {}}
        storage = mock.MagicMock()
        storage.return_value.observe_reclaim.return_value = {"category_outcomes": []}
        with mock.patch("sandbox.core._paths.RUNTIME_DIR", runtime), \
                mock.patch("sandbox.core._config.load_config", return_value={}), \
                mock.patch("sandbox.core._dash.collect_instance_rows", return_value=list(instances)), \
                mock.patch("sandbox.resources.remote.LocalProbeAdapter", storage), \
                mock.patch.object(host_inventory, "_diagnostic_process_snapshot", return_value=diagnostics):
            return host_inventory._hosted_inventory_snapshot()

    def test_hosted_targets_are_counted_apart_from_dev_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            write_target(runtime, "xspeed-hub-demo", "staging", stack="demo")
            write_legacy(runtime, "xspeed-wp-demo", "production")
            rows = [container("sandbox-host-xspeed-hub-demo-staging-web-1"),
                    container("sandbox-host-xspeed-hub-demo-staging-db-1"),
                    container("sandbox-host-xspeed-wp-demo-production-wordpress-1"),
                    container("sandbox-demo-wordpress-1"), container("unrelated")]
            # An instance name that would also match a hosted container by the substring rule.
            snapshot = self.snapshot(runtime, rows, instances=[
                {"name": "sandbox-demo", "running": True}, {"name": "xspeed-hub-demo", "running": True}])
        self.assertEqual(snapshot["instances"]["total"], 2)
        hosted = snapshot["hosted_targets"]
        self.assertEqual((hosted["total"], hosted["running"]), (2, 2))
        self.assertEqual({row["key"]: row["container_count"] for row in hosted["rows"]},
                         {"xspeed-hub-demo/staging": 2, "xspeed-wp-demo/production": 1})
        per_instance = {row["name"]: row["container_count"] for row in snapshot["per_instance_usage"]}
        self.assertEqual(per_instance, {"sandbox-demo": 1, "xspeed-hub-demo": 0})
        self.assertEqual([row["name"] for row in snapshot["unattributed_containers"]], ["unrelated"])
        self.assertNotIn(SECRET, json.dumps(snapshot))

    def test_snapshot_reads_the_hosts_lifetime_records_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            write_legacy(runtime, "hub-demo", "sandbox")
            record = stack_record(expires=hs.to_rfc3339(NOW + 3600))
            store = runtime / "target-lifetimes"
            store.mkdir()
            (store / (record["compose_project"] + ".json")).write_text(json.dumps(record))
            (store / "sandbox-host-broken-x.json").write_text("{")
            snapshot = self.snapshot(runtime, [])
            self.assertFalse((store / ".lock").exists())
        row = snapshot["hosted_targets"]["rows"][0]
        self.assertEqual((row["stack_slug"], row["lifetime"]["owner"], row["lifetime"]["lifetime"]),
                         ("demo", "alice", "temporary"))
        self.assertTrue(any(reason.startswith("lifetime_record_invalid:") for reason in snapshot["partial_reasons"]))

    def test_no_hosted_targets_keeps_the_old_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self.snapshot(Path(tmp), [container("unrelated")])
        self.assertEqual(snapshot["hosted_targets"], {"total": 0, "running": 0, "rows": []})
        self.assertEqual(len(snapshot["unattributed_containers"]), 1)
        self.assertNotIn("hosted_target_inventory_unavailable", snapshot["partial_reasons"])

    def test_an_unreadable_record_store_is_partial(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("sandbox.hosting.panel_records.read_server_records", side_effect=OSError("x")):
            snapshot = self.snapshot(Path(tmp), [container("sandbox-host-a-b-web-1")])
        self.assertIn("hosted_target_inventory_unavailable", snapshot["partial_reasons"])
        self.assertEqual(snapshot["hosted_targets"]["total"], 0)
        self.assertEqual(len(snapshot["unattributed_containers"]), 1)


if __name__ == "__main__":
    unittest.main()

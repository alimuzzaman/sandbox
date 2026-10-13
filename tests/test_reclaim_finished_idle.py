"""Spec 066: finished retained jobs stop protecting idle workspaces."""
import unittest

from sandbox.resources import reclaim
from sandbox.resources.workspace_teardown import job_workspace_references

NOW = 1_800_000_000.0
HOUR = 3600
ROOT = "/h/sandbox/deploy-src/proj-workspace-0123456789abcd"


def job(lifecycle, finished=None, state="retained", policy="retain", root=ROOT):
    return {"project_root": root, "lifecycle": lifecycle, "cleanup_state": state,
            "cleanup_policy": policy, "finished_at": finished}


def refs(*jobs):
    return job_workspace_references(jobs, now=NOW, idle_seconds=6 * HOUR)


class ReferencesTest(unittest.TestCase):
    def test_old_finished_retained_job_is_idle(self):
        self.assertEqual(refs(job("succeeded", NOW - 8 * HOUR)), {ROOT: ("finished_job_idle",)})

    def test_iso_finished_at(self):
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(NOW - 8 * HOUR, timezone.utc).isoformat().replace("+00:00", "Z")
        self.assertEqual(refs(job("failed", iso))[ROOT], ("finished_job_idle",))

    def test_recent_finish_protects(self):
        self.assertEqual(refs(job("succeeded", NOW - 5 * HOUR))[ROOT], ("retained_job",))

    def test_missing_or_bad_finish_time_protects(self):
        for value in (None, "", "garbage", True):
            self.assertEqual(refs(job("succeeded", value))[ROOT], ("retained_job",), value)

    def test_running_or_queued_protects_at_any_age(self):
        for lifecycle in ("running", "queued", "accepted", "cancelling", "weird"):
            self.assertEqual(refs(job(lifecycle, NOW - 100 * HOUR))[ROOT], ("retained_job",))

    def test_any_protecting_job_wins(self):
        result = refs(job("succeeded", NOW - 8 * HOUR), job("queued"))
        self.assertEqual(result[ROOT], ("retained_job",))

    def test_completed_cleanup_contributes_nothing(self):
        self.assertEqual(refs(job("succeeded", NOW - 8 * HOUR, state="completed")), {})


def entry(**extra):
    base = {"name": "proj-workspace-0123456789abcd", "path": ROOT, "size_bytes": 10,
            "size_state": "measured", "mtime": NOW - 60, "is_workspace": True,
            "containers": [{"id": "c1", "running": True}]}
    base.update(extra)
    return reclaim.classify_entry(base, now=NOW)


class TierTest(unittest.TestCase):
    def test_finished_idle_is_safe_even_with_running_stack(self):
        self.assertEqual(reclaim._entry_tier(entry(finished_job_idle=True)),
                         ("safe", "finished_job_idle"))

    def test_active_job_still_protects(self):
        self.assertIsNone(reclaim._entry_tier(entry(finished_job_idle=True, active_job=True)))

    def test_hosted_and_registry_stay_protected(self):
        self.assertIsNone(reclaim._entry_tier(entry(finished_job_idle=True, hosted=True)))
        self.assertIsNone(reclaim._entry_tier(entry(finished_job_idle=True, registry=True)))

    def test_without_evidence_recent_workspace_is_kept(self):
        self.assertIsNone(reclaim._entry_tier(entry()))

    def test_active_lease_wins(self):
        classified = reclaim.classify_entry(
            {"name": "proj-workspace-0123456789abcd", "path": ROOT, "size_bytes": 10,
             "size_state": "measured", "mtime": NOW - 60, "is_workspace": True,
             "containers": [], "finished_job_idle": True},
            now=NOW, leases={"proj-workspace-0123456789abcd": {
                "expires_at": NOW + 10 * HOUR, "state": "active"}})
        if classified.in_use_reason == "lease_active":
            self.assertIsNone(reclaim._entry_tier(classified))

    def test_incomplete_inventory_keeps(self):
        classified = reclaim.classify_entry(
            {"name": "proj-workspace-0123456789abcd", "path": ROOT, "size_bytes": 10,
             "size_state": "measured", "mtime": NOW - 60, "is_workspace": True,
             "containers": [], "finished_job_idle": True},
            now=NOW, inventory_complete=False)
        self.assertIsNone(reclaim._entry_tier(classified))


if __name__ == "__main__":
    unittest.main()

class HostedPrefixTest(unittest.TestCase):
    def test_hosted_prefix_always_protects_worktree(self):
        self.assertIsNone(reclaim._entry_tier(entry(
            name='sandbox-host-proj-workspace-0123456789abcd', finished_job_idle=True)))

    def test_hosted_prefix_or_label_always_protects_volume(self):
        for name, project in [('sandbox-host-p-workspace-0123456789_node-modules','sandbox-host-p'),
                              ('sandbox-p-workspace-0123456789_node-modules','sandbox-host-p')]:
            classified = reclaim.classify_volume({'name':name,'project':project,'size_bytes':1},
                                                 present_workspaces=())
            self.assertFalse(classified.eligible)

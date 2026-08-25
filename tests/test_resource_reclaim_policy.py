"""Reclamation policy: every safety rule the 2026-08-16 audit paid for.

Each test here maps to a rule in
``specs/042-host-storage-reclamation/contracts/reclaim-policy.md`` and to a
near-miss from the manual cleanup that produced it.
"""

import random
import unittest

from sandbox.resources import reclaim


DAY = 86400
NOW = 1_755_000_000.0


def entry(name, **overrides):
    base = {
        "name": name,
        "path": f"/home/alim/sandbox/deploy-src/{name}",
        "size_bytes": 3 * 1024 ** 3,
        "size_state": "measured",
        "mtime": NOW - 11 * DAY,
        "is_workspace": "-workspace-" in name,
        "is_symlink": False,
        "containers": [],
        "registry": False,
        "active_job": False,
        "indexed": False,
        "hosted": False,
        "protections": [],
    }
    base.update(overrides)
    return base


def container(running=True, identity="c1"):
    return {"id": identity, "name": identity, "running": running}


class TestClassification(unittest.TestCase):
    def classify(self, record, **kwargs):
        return reclaim.classify_entry(record, now=NOW, **kwargs)

    def test_orphan_workspace_has_no_container_registry_or_index_record(self):
        decision = self.classify(entry("proof-workspace-8fd1"))
        self.assertEqual(decision.lifecycle_class, "ORPHAN")
        self.assertEqual(decision.reason, "orphan_workspace")
        self.assertIn("no_container", decision.evidence)

    def test_running_container_makes_the_entry_live(self):
        decision = self.classify(
            entry("lenzora-workspace-a655", containers=[container()]))
        self.assertEqual(decision.lifecycle_class, "LIVE")

    def test_stopped_container_makes_the_entry_stopped(self):
        decision = self.classify(
            entry("speckit-workspace-d493", containers=[container(False)]))
        self.assertEqual(decision.lifecycle_class, "STOPPED")

    def test_index_record_without_a_container_is_registry_only(self):
        decision = self.classify(entry("ui-workspace-77", indexed=True))
        self.assertEqual(decision.lifecycle_class, "REGONLY")

    def test_directory_without_a_workspace_marker_is_a_base_deployment(self):
        decision = self.classify(entry("templately-staging"))
        self.assertEqual(decision.lifecycle_class, "BASE")

    def test_instance_registry_entries_are_protected_before_any_other_rule(self):
        decision = self.classify(entry("lenzora", registry=True))
        self.assertEqual(decision.lifecycle_class, "PROTECTED")
        self.assertEqual(decision.reason, "instance_registry")

    def test_every_entry_lands_in_exactly_one_known_class(self):
        records = [
            entry("a-workspace-1"),
            entry("b-workspace-2", containers=[container()]),
            entry("c-workspace-3", containers=[container(False)]),
            entry("d-workspace-4", indexed=True),
            entry("base-target"),
            entry("hosts", hosted=True),
        ]
        classes = [self.classify(item).lifecycle_class for item in records]
        self.assertEqual(len(classes), len(records))
        for value in classes:
            self.assertIn(value, reclaim.LIFECYCLE_CLASSES)

    def test_incomplete_container_inventory_yields_unknown_not_orphan(self):
        decision = self.classify(entry("x-workspace-1"),
                                 inventory_complete=False)
        self.assertEqual(decision.lifecycle_class, "UNKNOWN")


class TestHostedSiteProtection(unittest.TestCase):
    """Rule (b): the five hosted sites must be untouchable at every tier."""

    def selection(self, tier, records, hosted=()):
        return reclaim.tier_candidates(
            {"entries": records, "volumes": [], "scratch": [],
             "hosted_sites": list(hosted), "leases": {}, "status": "complete"},
            tier, now=NOW, hosted_sites=hosted,
        )

    def test_hosts_directory_is_never_a_candidate(self):
        for tier in reclaim.TIERS:
            picked = self.selection(tier, [entry("hosts")]).candidates
            self.assertEqual(picked, ())

    def test_registered_hosted_site_is_never_a_candidate(self):
        records = [entry("amarsonar-bangla-public")]
        for tier in reclaim.TIERS:
            selection = self.selection(tier, records,
                                       hosted=("amarsonar-bangla-public",))
            self.assertEqual(selection.candidates, ())
            self.assertEqual(selection.skipped[0]["reason"], "hosted_site")

    def test_path_beneath_the_hosts_subtree_is_never_a_candidate(self):
        record = entry("site-workspace-1")
        record["path"] = "/home/alim/sandbox/deploy-src/hosts/site-workspace-1"
        for tier in reclaim.TIERS:
            self.assertEqual(self.selection(tier, [record]).candidates, ())


class TestVolumeProtection(unittest.TestCase):
    """Rule (a): a blanket prune would have destroyed live site data."""

    LIVE_DATA = (
        "lenzora-postgres-data",
        "sandbox-amarsonar-bangla-public_wordpress-db",
        "wordpress-uploads",
        "lenzora-storage",
        "lenzora_pgdata",
        "sandbox-host-lenzora-development_lenzora-dev-postgres-data",
    )

    def test_live_data_volumes_are_rejected_even_when_unused(self):
        for name in self.LIVE_DATA:
            decision = reclaim.classify_volume(
                {"name": name, "size_bytes": 10 ** 9, "mounted_running": False},
                reclaimable_workspaces=(), present_workspaces=set(),
            )
            self.assertFalse(decision.eligible, name)
            self.assertEqual(decision.reason, "volume_not_workspace_scoped", name)

    def test_workspace_scoped_volume_is_eligible_only_with_its_workspace(self):
        name = "sandbox-speckit-workspace-d493_lenzora-sandbox-node-modules"
        retained = reclaim.classify_volume(
            {"name": name, "size_bytes": 10 ** 9, "mounted_running": False},
            reclaimable_workspaces=(),
            present_workspaces={"speckit-workspace-d493"},
        )
        self.assertFalse(retained.eligible)
        self.assertEqual(retained.reason, "owning_workspace_retained")
        eligible = reclaim.classify_volume(
            {"name": name, "size_bytes": 10 ** 9, "mounted_running": False},
            reclaimable_workspaces={"speckit-workspace-d493"},
            present_workspaces={"speckit-workspace-d493"},
        )
        self.assertTrue(eligible.eligible)
        self.assertEqual(eligible.reason, "workspace_scoped_volume")

    def test_truncated_compose_project_name_does_not_orphan_a_live_workspace(self):
        """The live remote produced exactly this on the first run.

        Compose truncates the project name, so the volume segment is a prefix
        of the real directory; exact matching called a live workspace's volume
        orphaned and would have deleted it.
        """
        decision = reclaim.classify_volume(
            {"name": "sandbox-lenzora-workspace-37a8ee_lenzora-sandbox-node-modules",
             "size_bytes": 184811520, "mounted_running": False},
            reclaimable_workspaces=(),
            present_workspaces={"lenzora-workspace-37a8eec1ce1968"},
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "owning_workspace_retained")

    def test_volume_whose_workspace_is_gone_is_eligible(self):
        decision = reclaim.classify_volume(
            {"name": "sandbox-gone-workspace-11_app-node-modules",
             "size_bytes": 5, "mounted_running": False},
            reclaimable_workspaces=(), present_workspaces={"other"},
        )
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reason, "workspace_scoped_volume_orphaned")

    def test_incomplete_container_inventory_never_plans_workspace_volume(self):
        inventory = {
            "entries": [entry("gone-workspace-11")],
            "volumes": [{
                "name": "sandbox-gone-workspace-11_app-node-modules",
                "size_bytes": 5,
                "mounted_running": False,
            }],
            "scratch": [], "leases": {}, "hosted_sites": [],
            "status": "partial", "engine_complete": False,
        }
        for tier in reclaim.TIERS:
            selection = reclaim.tier_candidates(inventory, tier, now=NOW)
            self.assertEqual(selection.candidates, ())
            volume = next(
                item for item in selection.skipped
                if item["kind"] == "volume"
            )
            self.assertEqual(volume["reason"],
                             "container_inventory_unavailable")

        report = reclaim.build_report(inventory, None, now=NOW)
        self.assertEqual(report["volumes"]["eligible"], 0)
        self.assertEqual(
            report["volumes"]["records"][0]["reason"],
            "container_inventory_unavailable",
        )

    def test_mounted_workspace_volume_is_refused(self):
        decision = reclaim.classify_volume(
            {"name": "sandbox-x-workspace-11_app-node-modules",
             "size_bytes": 5, "mounted_running": True},
            reclaimable_workspaces={"x-workspace-11"},
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "volume_mounted_by_running_container")

    def test_a_volume_without_a_workspace_marker_is_refused(self):
        decision = reclaim.classify_volume(
            {"name": "sandbox-lenzora_app-node-modules", "size_bytes": 5,
             "mounted_running": False},
            reclaimable_workspaces={"lenzora"},
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "volume_not_workspace_scoped")

    def test_no_protected_volume_appears_at_any_tier(self):
        records = [entry("x-workspace-1")]
        volumes = [
            {"name": name, "size_bytes": 10 ** 9, "mounted_running": False}
            for name in self.LIVE_DATA
        ]
        for tier in reclaim.TIERS:
            selection = reclaim.tier_candidates(
                {"entries": records, "volumes": volumes, "scratch": [],
                 "leases": {}, "hosted_sites": [], "status": "complete"},
                tier, now=NOW,
            )
            picked = {item.locator for item in selection.candidates}
            self.assertEqual(picked & set(self.LIVE_DATA), set())


class TestLiveness(unittest.TestCase):
    """Rule (d): an idle keepalive container is not a reason to keep 28.8 GiB."""

    def selection(self, tier, records, leases=None):
        return reclaim.tier_candidates(
            {"entries": records, "volumes": [], "scratch": [],
             "leases": leases or {}, "hosted_sites": [], "status": "complete"},
            tier, now=NOW,
        )

    def test_idle_running_container_does_not_protect_an_expired_workspace(self):
        record = entry("speckit-workspace-d493", containers=[container()],
                       mtime=NOW - 9 * DAY)
        self.assertEqual(self.selection("safe", [record]).candidates, ())
        picked = self.selection("all", [record]).candidates
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].reason, "live_container_idle_expired")

    def test_recent_activity_protects_a_workspace_at_every_tier(self):
        record = entry("busy-workspace-1", mtime=NOW - 3600)
        for tier in reclaim.TIERS:
            self.assertEqual(self.selection(tier, [record]).candidates, ())

    def test_active_job_binding_protects_even_a_released_workspace(self):
        record = entry("job-workspace-1", active_job=True)
        leases = {"job-workspace-1": {"released": True}}
        for tier in reclaim.TIERS:
            selection = self.selection(tier, [record], leases)
            self.assertEqual(selection.candidates, ())
            self.assertEqual(selection.skipped[0]["reason"], "active_job")

    def test_release_makes_a_young_workspace_immediately_reclaimable(self):
        record = entry("done-workspace-1", mtime=NOW - 60)
        leases = {"done-workspace-1": {"released": True}}
        picked = self.selection("safe", [record], leases).candidates
        self.assertEqual([item.reason for item in picked], ["lease_released"])

    def test_release_with_a_running_container_needs_the_broad_tier(self):
        record = entry("done-workspace-2", mtime=NOW - 60,
                       containers=[container()])
        leases = {"done-workspace-2": {"released": True}}
        self.assertEqual(self.selection("safe", [record], leases).candidates, ())
        self.assertEqual(
            len(self.selection("all", [record], leases).candidates), 1)


class TestLeases(unittest.TestCase):
    def test_default_window_is_seven_days(self):
        self.assertEqual(reclaim.DEFAULT_WORKSPACE_TTL_SECONDS, 7 * DAY)
        self.assertEqual(reclaim.DEFAULT_BASE_TTL_SECONDS, 7 * DAY)
        fresh = reclaim.lease_state(None, mtime=NOW - 6 * DAY, now=NOW)
        stale = reclaim.lease_state(None, mtime=NOW - 8 * DAY, now=NOW)
        self.assertEqual(fresh.state, "active")
        self.assertEqual(stale.state, "expired")
        self.assertEqual(stale.source, "default_window")

    def test_explicit_expiry_overrides_the_default_window(self):
        state = reclaim.lease_state(
            {"expires_at": reclaim.iso(NOW + 3600)},
            mtime=NOW - 30 * DAY, now=NOW,
        )
        self.assertEqual(state.state, "active")
        self.assertEqual(state.source, "lease")

    def test_release_beats_an_unexpired_lease(self):
        state = reclaim.lease_state(
            {"released": True, "expires_at": reclaim.iso(NOW + 3600)},
            mtime=NOW, now=NOW,
        )
        self.assertEqual(state.state, "released")

    def test_unknown_mtime_never_authorises_a_deletion(self):
        state = reclaim.lease_state(None, mtime=None, now=NOW)
        self.assertEqual(state.state, "active")

    def test_durations(self):
        self.assertEqual(reclaim.parse_duration("2h"), 7200)
        self.assertEqual(reclaim.parse_duration("14d"), 14 * DAY)
        for bad in ("", "d", "-1d", "2 days", "9999999d", None, True, "0h"):
            with self.assertRaises(reclaim.ReclaimPolicyError):
                reclaim.parse_duration(bad)

    def test_lease_names_are_path_free(self):
        self.assertTrue(reclaim.valid_lease_name("lenzora-workspace-a655"))
        for bad in ("../etc", "a/b", "", "-leading", None):
            self.assertFalse(reclaim.valid_lease_name(bad))


class TestGrowthExclusion(unittest.TestCase):
    """Rule (f): our own du raced itself; mtime is the honest signal."""

    def test_advancing_mtime_excludes_the_candidate(self):
        self.assertEqual(
            reclaim.growth_excluded({"mtime": 100.0, "bytes": 10},
                                    {"mtime": 200.0, "bytes": 10}),
            "candidate_modified_since_plan",
        )

    def test_size_delta_with_equal_mtime_is_a_measurement_race_not_growth(self):
        self.assertIsNone(reclaim.growth_excluded(
            {"mtime": 100.0, "bytes": 10}, {"mtime": 100.0, "bytes": 900}))

    def test_growth_without_a_known_mtime_is_still_excluded(self):
        self.assertEqual(
            reclaim.growth_excluded({"mtime": 100.0, "bytes": 10},
                                    {"mtime": None, "bytes": 900}),
            "candidate_growing",
        )


class TestTierNesting(unittest.TestCase):
    def build(self, seed):
        rng = random.Random(seed)
        records = []
        for index in range(40):
            name = (f"e{index}-workspace-{index}" if rng.random() < 0.7
                    else f"base{index}")
            records.append(entry(
                name,
                mtime=NOW - rng.choice((1, 3, 8, 20)) * DAY,
                containers=rng.choice(([], [container()], [container(False)])),
                indexed=rng.random() < 0.3,
                registry=rng.random() < 0.1,
                hosted=rng.random() < 0.05,
            ))
        volumes = [
            {"name": f"sandbox-e{index}-workspace-{index}_app-node-modules",
             "size_bytes": 1024, "mounted_running": rng.random() < 0.2}
            for index in range(40)
        ]
        scratch = [{"name": ".drive-volume-fallbacks-1",
                    "path": "/home/alim/sandbox/runtime/.drive-volume-fallbacks-1",
                    "size_bytes": 512, "mtime": NOW - 30 * DAY}]
        return {"entries": records, "volumes": volumes, "scratch": scratch,
                "leases": {}, "hosted_sites": [], "status": "complete"}

    def test_safe_is_a_subset_of_tmp_which_is_a_subset_of_all(self):
        for seed in range(12):
            inventory = self.build(seed)
            sets = {
                tier: {
                    item.locator for item in reclaim.tier_candidates(
                        inventory, tier, now=NOW).candidates
                }
                for tier in reclaim.TIERS
            }
            self.assertTrue(sets["safe"] <= sets["tmp"], seed)
            self.assertTrue(sets["tmp"] <= sets["all"], seed)

    def test_scratch_requires_the_tmp_tier(self):
        inventory = self.build(0)
        safe = reclaim.tier_candidates(inventory, "safe", now=NOW)
        tmp = reclaim.tier_candidates(inventory, "tmp", now=NOW)
        self.assertNotIn("runtime", {item.kind for item in safe.candidates})
        self.assertIn("runtime", {item.kind for item in tmp.candidates})

    def test_unmeasured_entries_are_skipped_not_guessed(self):
        record = entry("u-workspace-1", size_state="timed_out",
                       size_bytes=None)
        selection = reclaim.tier_candidates(
            {"entries": [record], "volumes": [], "scratch": [], "leases": {},
             "hosted_sites": [], "status": "complete"}, "all", now=NOW)
        self.assertEqual(selection.candidates, ())
        self.assertEqual(selection.skipped[0]["reason"], "size_unmeasured")


class TestCapacityPressure(unittest.TestCase):
    def capacity(self, free, total=193 * 1024 ** 3):
        return {"total_bytes": total, "available_bytes": free,
                "used_bytes": total - free, "reserved_bytes": 0}

    def test_warning_threshold_is_inclusive(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(15, total=100))
        self.assertEqual(pressure["level"], "warning")
        self.assertEqual(pressure["free_ratio"], 0.15)
        self.assertEqual(pressure["threshold_crossed"], "warn_ratio")

    def test_free_space_just_above_warning_threshold_is_normal(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(16, total=100))
        self.assertEqual(pressure["level"], "normal")
        self.assertIsNone(pressure["threshold_crossed"])

    def test_critical_threshold_is_inclusive(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(5, total=100))
        self.assertEqual(pressure["level"], "critical")
        self.assertEqual(pressure["free_ratio"], 0.05)
        self.assertEqual(pressure["threshold_crossed"], "critical_ratio")

    def test_zero_free_space_remains_measured_critical_capacity(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(0, total=100))
        self.assertEqual(pressure["level"], "critical")
        self.assertEqual(pressure["free_ratio"], 0.0)
        self.assertEqual(pressure["free_bytes"], 0)
        self.assertEqual(pressure["threshold_crossed"], "critical_ratio")

    def test_custom_thresholds_drive_both_classification_boundaries(self):
        at_warning = reclaim.disk_capacity_pressure(
            self.capacity(30, total=100), warn_ratio=0.30, critical_ratio=0.10,
        )
        self.assertEqual(at_warning["level"], "warning")
        self.assertEqual(at_warning["threshold_crossed"], "warn_ratio")
        self.assertEqual(at_warning["warn_ratio"], 0.30)
        self.assertEqual(at_warning["critical_ratio"], 0.10)

        above_warning = reclaim.disk_capacity_pressure(
            self.capacity(31, total=100), warn_ratio=0.30, critical_ratio=0.10,
        )
        self.assertEqual(above_warning["level"], "normal")

        at_critical = reclaim.disk_capacity_pressure(
            self.capacity(10, total=100), warn_ratio=0.30, critical_ratio=0.10,
        )
        self.assertEqual(at_critical["level"], "critical")
        self.assertEqual(at_critical["threshold_crossed"], "critical_ratio")

    def test_scheduled_auto_gate_is_disabled_without_safe_tier(self):
        pressure = reclaim.disk_capacity_pressure(
            self.capacity(5, total=100), auto_ratio=0.10,
        )
        self.assertFalse(pressure["auto_eligible"])
        self.assertIsNone(pressure["auto_tier"])
        self.assertEqual(pressure["auto_ratio"], 0.10)

    def test_scheduled_auto_gate_is_safe_and_threshold_bounded(self):
        eligible = reclaim.disk_capacity_pressure(
            self.capacity(10, total=100), auto_tier="safe", auto_ratio=0.10,
        )
        self.assertEqual(eligible["level"], "warning")
        self.assertTrue(eligible["auto_eligible"])
        self.assertEqual(eligible["auto_tier"], "safe")

        above_auto_threshold = reclaim.disk_capacity_pressure(
            self.capacity(11, total=100), auto_tier="safe", auto_ratio=0.10,
        )
        self.assertFalse(above_auto_threshold["auto_eligible"])
        self.assertIsNone(above_auto_threshold["auto_tier"])

    def test_warning_below_fifteen_percent_free(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(
            int(193 * 1024 ** 3 * 0.11)))
        self.assertEqual(pressure["level"], "warning")
        self.assertEqual(pressure["threshold_crossed"], "warn_ratio")

    def test_critical_at_the_state_the_host_actually_reached(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(
            8 * 1024 ** 3))
        self.assertEqual(pressure["level"], "critical")

    def test_normal_after_the_cleanup(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(
            118 * 1024 ** 3))
        self.assertEqual(pressure["level"], "normal")
        self.assertIsNone(pressure["threshold_crossed"])

    def test_automatic_runs_are_off_by_default(self):
        pressure = reclaim.disk_capacity_pressure(self.capacity(1))
        self.assertIsNone(pressure["auto_tier"])
        self.assertFalse(pressure["auto_eligible"])

    def test_automatic_runs_are_limited_to_the_safe_tier(self):
        enabled = reclaim.disk_capacity_pressure(
            self.capacity(1), auto_tier="safe")
        self.assertEqual(enabled["auto_tier"], "safe")
        with self.assertRaises(reclaim.ReclaimPolicyError):
            reclaim.disk_capacity_pressure(self.capacity(1), auto_tier="all")

    def test_unmeasured_capacity_is_reported_as_unknown(self):
        self.assertEqual(
            reclaim.disk_capacity_pressure(None)["level"], "unknown")


class TestDriftAndReport(unittest.TestCase):
    def test_drift_is_reported_in_both_directions(self):
        entries = tuple(
            reclaim.classify_entry(item, now=NOW)
            for item in (entry("a-workspace-1"), entry("b-workspace-2"))
        )
        drift = reclaim.index_drift(entries, ("a-workspace-1", "gone-workspace-9"))
        self.assertEqual(drift["indexed_absent"], 1)
        self.assertEqual(drift["present_unindexed"], 2)

    def test_build_report_covers_classes_volumes_drift_and_pressure(self):
        report = reclaim.build_report(
            {"deployment_root": "/deploy", "entries": [entry("a-workspace-1")],
             "volumes": [{"name": "lenzora-storage", "size_bytes": 1,
                          "mounted_running": False}],
             "scratch": [], "leases": {}, "hosted_sites": [],
             "index_names": [], "status": "complete", "truncated": False,
             "unmeasured_count": 0},
            {"total_bytes": 100, "available_bytes": 4, "used_bytes": 96,
             "reserved_bytes": 0},
            now=NOW,
        )
        self.assertEqual(report["classes"][0]["class"], "ORPHAN")
        self.assertEqual(report["volumes"]["protected"], 1)
        self.assertEqual(report["capacity_pressure"]["level"], "critical")
        self.assertIn("safe", report["tiers"])

    def test_build_report_returns_none_without_evidence(self):
        self.assertIsNone(reclaim.build_report(None, None, now=NOW))


class TestManifestRecords(unittest.TestCase):
    def candidate(self):
        return reclaim.ReclaimCandidate(
            seq=3, kind="worktree", locator="/deploy/x-workspace-1",
            display_name="x-workspace-1", bytes=1024, mtime=NOW,
            lifecycle_class="ORPHAN", tier="safe", reason="orphan_workspace",
        )

    def test_intent_names_bytes_class_reason_and_time(self):
        record = reclaim.manifest_intent("a" * 32, self.candidate())
        self.assertEqual(record["phase"], "intent")
        for key in ("path", "bytes", "class", "reason", "at", "run_id", "seq"):
            self.assertIn(key, record)

    def test_outcome_records_verification(self):
        record = reclaim.manifest_outcome(
            "a" * 32, 3, "/deploy/x", status="failed",
            reason="partial_removal_detected", verified_absent=False)
        self.assertFalse(record["verified_absent"])
        self.assertEqual(record["phase"], "outcome")

    def test_candidate_evidence_digest_covers_class_reason_and_mtime(self):
        first = self.candidate()
        self.assertNotEqual(
            first.evidence_digest(),
            reclaim.ReclaimCandidate(
                **{**first.__dict__, "mtime": NOW + 5}).evidence_digest(),
        )


if __name__ == "__main__":
    unittest.main()

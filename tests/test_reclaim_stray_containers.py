"""Spec 080: a workspace's stray unlabeled containers are reclaimed safely."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

from sandbox.resources import reclaim, remote
from sandbox.resources.workspace_teardown import (
    MAX_STRAYS_PER_CHECKOUT, assess_stray_container, teardown_workspace_stack,
)

WS = "proj-workspace-0123456789abcd"
DEPLOY = "/h/sandbox/deploy-src"
CHECKOUT = DEPLOY + "/" + WS
PROJECT = "sandbox-" + WS
NOW = 1_800_000_000.0
ID = "a" * 64
OTHER = "b" * 64


def container(identity=ID, name="/inspiring_meninsky", mounts=None, labels=None,
              network_mode="bridge", networks=("bridge",), restart="no",
              started="2026-11-10T03:00:00.000000000Z", restart_count=0,
              running=True, created="2026-11-10T02:59:00.000000000Z",
              image="mcr.microsoft.com/playwright:v1.48.0-jammy"):
    if mounts is None:
        mounts = [{"Type": "bind", "Source": CHECKOUT + "/tests",
                   "Destination": "/work", "RW": True}]
    return {
        "Id": identity, "Name": name, "Created": created, "Image": "sha256:" + "c" * 64,
        "Config": {"Image": image, "Labels": dict(
            labels if labels is not None else
            {"org.opencontainers.image.version": "22.04"})},
        "Mounts": mounts,
        "HostConfig": {"NetworkMode": network_mode,
                       "RestartPolicy": {"Name": restart, "MaximumRetryCount": 0}},
        "NetworkSettings": {"Networks": {net: {"NetworkID": "n" * 12} for net in networks}},
        "State": {"Running": running, "StartedAt": started,
                  "FinishedAt": "0001-01-01T00:00:00Z"},
        "RestartCount": restart_count,
    }


def planned(record, checkout=CHECKOUT):
    verdict = assess_stray_container(record, checkout)
    assert verdict["owned"], verdict
    return {"id": record["Id"], "name": record["Name"].lstrip("/"),
            "fingerprint": verdict["fingerprint"], "evidence": verdict["evidence"]}


class Engine:
    """A Docker CLI stand-in holding full inspect objects; records every call."""

    MOUNTS = "{{json .Id}} {{json .Mounts}}"

    def __init__(self, *records, fail=()):
        self.containers = {item["Id"]: copy.deepcopy(item) for item in records}
        self.fail = set(fail)
        self.calls = []

    def project(self, item):
        return ((item.get("Config") or {}).get("Labels") or {}).get(
            "com.docker.compose.project", "")

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if argv == ["docker", "ps", "-aq", "--no-trunc"]:
            if "ps" in self.fail:
                return 1, "", "daemon down"
            return 0, "\n".join(self.containers), ""
        if argv[:3] == ["docker", "inspect", "--format"] and argv[3] == self.MOUNTS:
            if "mounts" in self.fail:
                return 1, "", "boom"
            out, err = [], []
            for ident in argv[4:]:
                if ident in self.containers:
                    out.append(json.dumps(ident) + " " + json.dumps(self.containers[ident]["Mounts"]))
                else:
                    err.append("Error: No such object: " + ident)
            return (1 if err else 0), "\n".join(out), "\n".join(err)
        if argv[:2] == ["docker", "inspect"] and "--format" not in argv:
            if "inspect" in self.fail:
                return 1, "", "boom"
            found = [self.containers[i] for i in argv[2:] if i in self.containers]
            missing = [i for i in argv[2:] if i not in self.containers]
            return (1 if missing else 0), json.dumps(found), "\n".join(
                "Error: No such object: " + i for i in missing)
        if argv[:4] == ["docker", "ps", "-a", "--format"]:
            return 0, "\n".join(filter(None, (self.project(c) for c in self.containers.values()))), ""
        if argv[:4] == ["docker", "volume", "ls", "--format"]:
            return 0, "", ""
        if argv[:3] == ["docker", "ps", "-aq"] and argv[3] == "--filter":
            want = argv[4].split("=", 2)[2]
            return 0, "\n".join(i for i, c in self.containers.items() if self.project(c) == want), ""
        if argv[:2] == ["docker", "stop"]:
            ident = argv[-1]
            if "stop" in self.fail:
                return 1, "", "cannot stop"
            if ident not in self.containers:
                return 1, "", "Error response from daemon: No such container: " + ident
            self.containers[ident]["State"]["Running"] = False
            return 0, ident, ""
        if argv[:2] == ["docker", "rm"]:
            ident = argv[-1]
            if "rm" in self.fail:
                return 1, "", "cannot remove"
            if ident not in self.containers:
                return 1, "", "Error response from daemon: No such container: " + ident
            del self.containers[ident]
            return 0, ident, ""
        return 0, "", ""

    def mutations(self):
        return [c for c in self.calls if c[:2] in (["docker", "stop"], ["docker", "rm"])]


class AssessTest(unittest.TestCase):
    """US2: only a container wholly inside the checkout is owned."""

    def reason(self, record, checkout=CHECKOUT):
        verdict = assess_stray_container(record, checkout)
        self.assertFalse(verdict["owned"])
        return verdict["reason"]

    def test_stray_inside_checkout_is_owned_with_evidence(self):
        verdict = assess_stray_container(container(), CHECKOUT)
        self.assertTrue(verdict["owned"])
        self.assertIsNone(verdict["reason"])
        evidence = verdict["evidence"]
        self.assertEqual(evidence["id"], ID)
        self.assertEqual(evidence["name"], "inspiring_meninsky")
        self.assertEqual(evidence["image"], "mcr.microsoft.com/playwright:v1.48.0-jammy")
        self.assertEqual(evidence["networks"], ["bridge"])
        self.assertEqual(evidence["mounts"], [{"type": "bind", "source": CHECKOUT + "/tests",
                                               "destination": "/work"}])
        self.assertEqual(len(verdict["fingerprint"]), 64)

    def test_checkout_itself_and_default_mode_are_inside(self):
        record = container(network_mode="default", mounts=[
            {"Type": "bind", "Source": CHECKOUT, "Destination": "/w"}])
        self.assertTrue(assess_stray_container(record, CHECKOUT)["owned"])

    def test_bind_outside_checkout_protects(self):
        record = container(mounts=[
            {"Type": "bind", "Source": CHECKOUT + "/tests", "Destination": "/w"},
            {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/s"}])
        self.assertEqual(self.reason(record), "bind_outside_checkout")

    def test_sibling_with_shared_prefix_is_outside(self):
        record = container(mounts=[{"Type": "bind", "Source": CHECKOUT + "-old/x",
                                    "Destination": "/w"}])
        self.assertEqual(self.reason(record), "bind_outside_checkout")

    def test_relative_or_dotted_source_is_outside(self):
        for source in ("tests", CHECKOUT + "/../other"):
            record = container(mounts=[{"Type": "bind", "Source": source, "Destination": "/w"}])
            self.assertEqual(self.reason(record), "bind_outside_checkout", source)

    def test_symlink_escaping_the_checkout_protects(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        checkout = root / WS
        (checkout / "inside").mkdir(parents=True)
        (root / "outside").mkdir()
        (checkout / "link").symlink_to(root / "outside")
        inside = container(mounts=[{"Type": "bind", "Source": str(checkout / "inside"),
                                    "Destination": "/w"}])
        self.assertTrue(assess_stray_container(inside, str(checkout))["owned"])
        escaping = container(mounts=[{"Type": "bind", "Source": str(checkout / "link"),
                                      "Destination": "/w"}])
        self.assertEqual(self.reason(escaping, str(checkout)), "bind_outside_checkout")

    def test_named_and_anonymous_volumes_protect(self):
        for name in ("pgdata", "f" * 64):
            record = container(mounts=[
                {"Type": "bind", "Source": CHECKOUT, "Destination": "/w"},
                {"Type": "volume", "Name": name, "Source": "/var/lib/docker/volumes/x",
                 "Destination": "/data"}])
            self.assertEqual(self.reason(record), "named_volume", name)

    def test_tmpfs_and_unknown_mounts_protect(self):
        for kind in ("tmpfs", "npipe", None):
            record = container(mounts=[
                {"Type": "bind", "Source": CHECKOUT, "Destination": "/w"},
                {"Type": kind, "Destination": "/t"}])
            self.assertEqual(self.reason(record), "unsupported_mount", kind)

    def test_no_mounts_is_not_owned(self):
        self.assertEqual(self.reason(container(mounts=[])), "no_mounts")

    def test_non_default_networks_protect(self):
        cases = (dict(networks=("bridge", "lenzora_default")),
                 dict(network_mode="lenzora_default", networks=("lenzora_default",)),
                 dict(network_mode="host", networks=("host",)),
                 dict(network_mode="container:" + OTHER, networks=()),
                 dict(network_mode="none", networks=("none",)))
        for case in cases:
            self.assertEqual(self.reason(container(**case)), "non_default_network", case)

    def test_restart_policy_protects(self):
        for policy in ("always", "unless-stopped", "on-failure"):
            self.assertEqual(self.reason(container(restart=policy)), "restart_policy")

    def test_compose_stack_and_sandbox_identity_protect(self):
        for key in ("com.docker.compose.project", "com.docker.stack.namespace",
                    "com.docker.swarm.service.name"):
            self.assertEqual(self.reason(container(labels={key: "x"})), "compose_project", key)
        for labels in ({"sandbox.job": "1"}, {"io.Sandbox.instance": "x"}):
            self.assertEqual(self.reason(container(labels=labels)), "sandbox_identity")
        self.assertEqual(self.reason(container(name="/sandbox-proj-wp-1")), "sandbox_identity")

    def test_malformed_records_protect(self):
        for record in (None, [], {}, {"Id": ID}, dict(container(), Mounts="x"),
                       dict(container(), Config=None)):
            verdict = assess_stray_container(record, CHECKOUT)
            self.assertFalse(verdict["owned"], record)
        self.assertFalse(assess_stray_container(container(), "relative/path")["owned"])

    def test_fingerprint_tracks_restart_mounts_labels_identity_not_running(self):
        base = assess_stray_container(container(), CHECKOUT)["fingerprint"]
        stopped = assess_stray_container(container(running=False), CHECKOUT)["fingerprint"]
        self.assertEqual(base, stopped)
        for changed in (container(started="2026-11-10T09:00:00Z"),
                        container(restart_count=1),
                        container(identity=OTHER),
                        container(created="2026-11-10T05:00:00Z"),
                        container(labels={"org.opencontainers.image.version": "24.04"}),
                        container(mounts=[{"Type": "bind", "Source": CHECKOUT + "/other",
                                           "Destination": "/work", "RW": True}])):
            self.assertNotEqual(assess_stray_container(changed, CHECKOUT)["fingerprint"], base)


def probe_record(record, checkout=CHECKOUT, project=None):
    return {"id": record["Id"], "name": record["Name"].lstrip("/"),
            "running": record["State"]["Running"], "project": project,
            "stray": assess_stray_container(record, checkout)}


def plan_entry(containers, **extra):
    base = {"name": WS, "path": CHECKOUT, "size_bytes": 10, "size_state": "measured",
            "mtime": NOW - 60, "is_workspace": True, "containers": containers,
            "finished_job_idle": True, "registry": False, "active_job": False,
            "indexed": True, "hosted": False, "protections": []}
    base.update(extra)
    return base


def select(*entries, tier="safe"):
    return reclaim.tier_candidates(
        {"entries": list(entries), "volumes": [], "scratch": [], "leases": {},
         "hosted_sites": [], "status": "complete"}, tier, now=NOW)


class PlannerTest(unittest.TestCase):
    """US1/US2: the plan names owned strays and protects everything else."""

    def test_owned_stray_is_listed_with_evidence(self):
        record = container()
        selection = select(plan_entry([probe_record(record)]))
        self.assertEqual(len(selection.candidates), 1)
        candidate = selection.candidates[0]
        self.assertEqual(candidate.reason, "finished_job_idle")
        self.assertEqual(candidate.stop_containers, (ID,))
        self.assertEqual(candidate.remove_containers, (planned(record),))
        self.assertEqual(candidate.to_dict()["remove_containers"], [planned(record)])

    def test_stray_fingerprint_changes_the_evidence_digest(self):
        one = select(plan_entry([probe_record(container())])).candidates[0]
        two = select(plan_entry([probe_record(container(restart_count=3))])).candidates[0]
        self.assertNotEqual(one.evidence_digest(), two.evidence_digest())

    def test_own_project_member_and_stray_together(self):
        member = {"id": OTHER, "name": PROJECT + "-wp-1", "running": True, "project": PROJECT}
        candidate = select(plan_entry([member, probe_record(container())])).candidates[0]
        self.assertEqual(candidate.stop_containers, (OTHER, ID))
        self.assertEqual([item["id"] for item in candidate.remove_containers], [ID])

    def test_protected_stray_keeps_the_workspace(self):
        record = container(mounts=[{"Type": "volume", "Name": "pgdata", "Destination": "/d"},
                                   {"Type": "bind", "Source": CHECKOUT, "Destination": "/w"}])
        selection = select(plan_entry([probe_record(record)]))
        self.assertEqual(selection.candidates, ())
        row = selection.skipped[0]
        self.assertEqual(row["reason"], "bound_container_protected")
        self.assertEqual(row["protected_containers"],
                         [{"id": ID, "name": "inspiring_meninsky", "reason": "named_volume"}])

    def test_foreign_compose_project_keeps_the_workspace(self):
        for project in ("sandbox-other-workspace-ffffffffffff", "sandbox-host-lenzora"):
            member = {"id": OTHER, "name": "x", "running": True, "project": project}
            selection = select(plan_entry([member]), tier="all")
            self.assertEqual(selection.candidates, (), project)
            self.assertEqual(selection.skipped[0]["protected_containers"][0]["reason"],
                             "foreign_compose_project")

    def test_too_many_strays_keep_the_workspace(self):
        records = [probe_record(container(identity=format(n, "064x"), name=f"/s{n}"))
                   for n in range(MAX_STRAYS_PER_CHECKOUT + 1)]
        selection = select(plan_entry(records))
        self.assertEqual(selection.candidates, ())
        self.assertEqual(selection.skipped[0]["reason"], "too_many_stray_containers")

    def test_base_deployment_is_unchanged(self):
        record = container(mounts=[{"Type": "bind", "Source": DEPLOY + "/proj/x",
                                    "Destination": "/w"}])
        base = plan_entry([probe_record(record, DEPLOY + "/proj")], name="proj",
                          path=DEPLOY + "/proj", is_workspace=False,
                          finished_job_idle=False, mtime=NOW - 30 * 86400, indexed=False)
        candidate = select(base, tier="all").candidates[0]
        self.assertEqual(candidate.remove_containers, ())

    def test_record_without_evidence_keeps_todays_behaviour(self):
        legacy = {"id": ID, "name": "x", "running": True, "project": None}
        candidate = select(plan_entry([legacy])).candidates[0]
        self.assertEqual(candidate.stop_containers, (ID,))
        self.assertEqual(candidate.remove_containers, ())


class StrayTeardownTest(unittest.TestCase):
    """US1/US3/US4: the cleanup acts only on planned, unchanged strays."""

    def teardown(self, engine, strays, ids=(ID,), record=None):
        return teardown_workspace_stack(engine, WS, list(ids), record=record,
                                        strays=strays, checkout=CHECKOUT)

    def test_planned_stray_is_recorded_then_stopped_then_removed(self):
        record = container()
        engine = Engine(record)
        events = []

        def note(kind, locator, **extra):
            events.append(("record", kind, locator, extra.get("fingerprint")))
        original = engine.__call__

        def spy(argv, timeout):
            if argv[:2] in (["docker", "stop"], ["docker", "rm"]):
                events.append((argv[1], argv[-1]))
            return original(argv, timeout)
        result = teardown_workspace_stack(spy, WS, [ID], record=note,
                                          strays=[planned(record)], checkout=CHECKOUT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["strays_removed"], [ID])
        self.assertEqual(events, [("record", "stray_container", ID, planned(record)["fingerprint"]),
                                  ("stop", ID), ("rm", ID)])
        self.assertEqual(engine.containers, {})
        self.assertNotIn(["docker", "rm", "-f", ID], engine.calls)
        self.assertFalse(any(c[:3] == ["docker", "container", "prune"] or
                             c[:3] == ["docker", "volume", "prune"] for c in engine.calls))

    def test_project_teardown_still_runs_after_strays(self):
        stray = container()
        member = container(identity=OTHER, name="/" + PROJECT + "-wp-1",
                           labels={"com.docker.compose.project": PROJECT})
        engine = Engine(stray, member)
        result = self.teardown(engine, [planned(stray)], ids=(ID, OTHER))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["projects"], [PROJECT])
        self.assertEqual(engine.containers, {})
        stray_rm = engine.calls.index(["docker", "rm", ID])
        member_rm = engine.calls.index(["docker", "rm", "-f", OTHER])
        self.assertLess(stray_rm, member_rm)

    def test_no_strays_and_nothing_bound_is_a_noop(self):
        engine = Engine(container(mounts=[{"Type": "bind", "Source": "/elsewhere",
                                           "Destination": "/w"}]))
        result = self.teardown(engine, [], ids=())
        self.assertTrue(result["ok"], result)
        self.assertEqual(engine.mutations(), [])

    def assert_refused(self, engine, strays, failure, ids=(ID,)):
        recorded = []
        result = self.teardown(engine, strays, ids=ids,
                               record=lambda *a, **k: recorded.append(a))
        self.assertFalse(result["ok"], result)
        self.assertIn(failure, result["failures"])
        self.assertEqual(engine.mutations(), [])
        self.assertEqual(recorded, [])
        return result

    def test_changed_since_plan_is_refused(self):
        plan = planned(container())
        for changed in (container(started="2026-11-10T09:00:00Z"),
                        container(restart_count=2),
                        container(labels={"org.opencontainers.image.version": "x",
                                          "extra": "1"}),
                        container(mounts=[{"Type": "bind", "Source": CHECKOUT + "/tests",
                                           "Destination": "/work", "RW": True},
                                          {"Type": "bind", "Source": CHECKOUT + "/more",
                                           "Destination": "/more", "RW": True}]),
                        container(networks=("bridge", "user_net"))):
            self.assert_refused(Engine(changed), [plan], "container_evidence_changed:" + ID)

    def test_changed_to_outside_the_checkout_is_refused(self):
        plan = planned(container())
        moved = container(mounts=[{"Type": "bind", "Source": CHECKOUT + "/tests",
                                   "Destination": "/work", "RW": True},
                                  {"Type": "bind", "Source": "/home/u", "Destination": "/u"}])
        self.assert_refused(Engine(moved), [plan], "container_evidence_changed:" + ID)

    def test_replaced_container_with_same_name_is_refused(self):
        plan = planned(container())
        replacement = container(identity=OTHER)
        self.assert_refused(Engine(replacement), [plan], "unplanned_bound_container:" + OTHER)

    def test_unplanned_bound_unlabeled_container_is_refused(self):
        stray = container()
        newcomer = container(identity=OTHER, name="/elastic_bartik")
        self.assert_refused(Engine(stray, newcomer), [planned(stray)],
                            "unplanned_bound_container:" + OTHER)

    def test_planned_stray_no_longer_bound_is_refused(self):
        plan = planned(container())
        moved = container(mounts=[{"Type": "bind", "Source": "/srv/other",
                                   "Destination": "/work", "RW": True}])
        self.assert_refused(Engine(moved), [plan], "container_evidence_changed:" + ID)

    def test_compose_labelled_bound_container_is_untouched(self):
        foreign = container(labels={"com.docker.compose.project": "lenzora"})
        self.assert_refused(Engine(foreign), [], "bound_container_not_exclusive")
        hosted = container(labels={"com.docker.compose.project": "sandbox-host-lenzora"})
        self.assert_refused(Engine(hosted), [], "bound_container_not_exclusive")

    def test_engine_failures_refuse(self):
        plan = planned(container())
        self.assert_refused(Engine(container(), fail={"ps"}), [plan], "bound_list_failed")
        self.assert_refused(Engine(container(), fail={"mounts"}), [plan], "bound_list_failed")
        self.assert_refused(Engine(container(), fail={"inspect"}), [plan], "bound_inspect_failed")

    def test_malformed_plan_entries_refuse(self):
        for strays in ([{"id": ID}], [{"id": "", "fingerprint": "x"}], ["x"], "x",
                       [planned(container()) for _ in range(MAX_STRAYS_PER_CHECKOUT + 1)]):
            self.assert_refused(Engine(container()), strays, "invalid_planned_strays")

    def test_change_after_validation_is_caught_before_stop(self):
        record = container()
        engine = Engine(record)
        original = engine.__call__
        served = []

        def racing(argv, timeout):
            reply = original(argv, timeout)
            if argv[:2] == ["docker", "inspect"] and "--format" not in argv:
                served.append(argv)
                if len(served) == 1:
                    engine.containers[ID]["NetworkSettings"]["Networks"]["user_net"] = {}
            return reply
        result = teardown_workspace_stack(racing, WS, [ID], strays=[planned(record)],
                                          checkout=CHECKOUT)
        self.assertFalse(result["ok"])
        self.assertIn("container_evidence_changed:" + ID, result["failures"])
        self.assertEqual(engine.mutations(), [])

    def test_restart_between_stop_and_rm_is_caught(self):
        record = container()
        engine = Engine(record)
        original = engine.__call__

        def restarting(argv, timeout):
            reply = original(argv, timeout)
            if argv[:2] == ["docker", "stop"]:
                engine.containers[ID]["State"].update(Running=True, StartedAt="2026-11-10T10:00:00Z")
            return reply
        result = teardown_workspace_stack(restarting, WS, [ID], strays=[planned(record)],
                                          checkout=CHECKOUT)
        self.assertFalse(result["ok"])
        self.assertIn("container_evidence_changed:" + ID, result["failures"])
        self.assertNotIn(["docker", "rm", ID], engine.calls)

    def test_still_running_after_stop_is_not_removed(self):
        record = container()
        engine = Engine(record)
        original = engine.__call__

        def ignoring_stop(argv, timeout):
            if argv[:2] == ["docker", "stop"]:
                return 0, argv[-1], ""
            return original(argv, timeout)
        result = teardown_workspace_stack(ignoring_stop, WS, [ID], strays=[planned(record)],
                                          checkout=CHECKOUT)
        self.assertFalse(result["ok"])
        self.assertIn("stray_still_running:" + ID, result["failures"])
        self.assertNotIn(["docker", "rm", ID], engine.calls)

    def test_inspect_that_omits_an_id_is_refused(self):
        plan = planned(container())
        engine = Engine(container())
        original = engine.__call__

        def lossy(argv, timeout):
            if argv[:3] == ["docker", "inspect", "--format"]:
                return 1, "", "Error: No such object: " + OTHER
            return original(argv, timeout)
        recorded = []
        result = teardown_workspace_stack(lossy, WS, [ID], record=lambda *a, **k: recorded.append(a),
                                          strays=[plan], checkout=CHECKOUT)
        self.assertFalse(result["ok"])
        self.assertIn("bound_list_failed", result["failures"])
        self.assertEqual(engine.mutations(), [])
        self.assertEqual(recorded, [])

    def test_container_gone_between_listing_and_inspect_counts_as_absent(self):
        engine = Engine(container())
        original = engine.__call__

        def vanishing(argv, timeout):
            if argv == ["docker", "ps", "-aq", "--no-trunc"]:
                reply = original(argv, timeout)
                engine.containers.clear()
                return reply
            return original(argv, timeout)
        result = teardown_workspace_stack(vanishing, WS, [ID], strays=[planned(container())],
                                          checkout=CHECKOUT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["strays_removed"], [ID])
        self.assertEqual(engine.mutations(), [])

    def test_planned_stray_already_absent_counts_as_removed(self):
        engine = Engine()
        result = self.teardown(engine, [planned(container())])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["strays_removed"], [ID])
        self.assertEqual(engine.mutations(), [])

    def test_stop_failure_keeps_checkout_and_names_the_container(self):
        engine = Engine(container(), fail={"stop"})
        result = self.teardown(engine, [planned(container())])
        self.assertFalse(result["ok"])
        self.assertIn("stray_stop_failed:" + ID, result["failures"])
        self.assertNotIn(["docker", "rm", ID], engine.calls)
        self.assertEqual(result["projects"], [])

    def test_rm_failure_keeps_checkout_and_names_the_container(self):
        engine = Engine(container(), fail={"rm"})
        result = self.teardown(engine, [planned(container())])
        self.assertFalse(result["ok"])
        self.assertIn("stray_rm_failed:" + ID, result["failures"])

    def test_crash_after_intent_then_rerun_removes_once(self):
        record = container()
        engine = Engine(record)

        class Crash(Exception):
            pass

        def crash(*_args, **_kwargs):
            raise Crash()
        with self.assertRaises(Crash):
            self.teardown(engine, [planned(record)], record=crash)
        self.assertEqual(engine.mutations(), [])
        intents = []
        result = self.teardown(engine, [planned(record)],
                               record=lambda kind, locator, **k: intents.append(locator))
        self.assertTrue(result["ok"], result)
        self.assertEqual(intents, [ID])
        self.assertEqual(engine.calls.count(["docker", "rm", ID]), 1)

    def test_crash_between_stop_and_rm_then_rerun_completes(self):
        record = container()
        engine = Engine(record, fail={"rm"})
        self.assertFalse(self.teardown(engine, [planned(record)])["ok"])
        self.assertFalse(engine.containers[ID]["State"]["Running"])
        engine.fail.clear()
        result = self.teardown(engine, [planned(record)])
        self.assertTrue(result["ok"], result)
        self.assertEqual(engine.containers, {})
        rerun = self.teardown(engine, [planned(record)])
        self.assertTrue(rerun["ok"], rerun)

    def test_legacy_plan_still_refuses_unlabeled_containers(self):
        engine = Engine(container())

        def labels(argv, timeout):
            if argv[:3] == ["docker", "inspect", "--format"] and "Labels" in argv[3]:
                return 0, json.dumps({}), ""
            return engine(argv, timeout)
        result = teardown_workspace_stack(labels, WS, [ID])
        self.assertFalse(result["ok"])
        self.assertIn("bound_container_not_exclusive", result["failures"])
        self.assertEqual(engine.mutations(), [])


class ProbeTest(unittest.TestCase):
    """US1: the shipped probe attaches one assessed record per container."""

    def program(self):
        source = remote._program({"action": "inventory"}).split("\nACTIONS = {", 1)[0]
        scope = {}
        exec(source, scope)
        return scope

    def test_program_embeds_the_assessment(self):
        source = remote._program({"action": "inventory"})
        self.assertIn("def assess_stray_container", source)
        compile(source, "<probe>", "exec")

    def test_reclaim_inventory_attaches_stray_evidence(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        deploy = root / "deploy-src"
        checkout = deploy / WS
        (checkout / "tests").mkdir(parents=True)
        (deploy / "proj").mkdir()
        scope = self.program()
        record = container(mounts=[
            {"Type": "bind", "Source": str(checkout / "tests"), "Destination": "/a"},
            {"Type": "bind", "Source": str(checkout), "Destination": "/b"}])
        base = container(identity=OTHER, mounts=[
            {"Type": "bind", "Source": str(deploy / "proj"), "Destination": "/a"}])
        scope.update(DEPLOY=deploy, RUNTIME=root / "runtime", DEADLINE=time.monotonic() + 30,
                     hosted_site_names=lambda: set(), read_leases=lambda: {},
                     indexed_workspace_names=lambda projection: ({}, True),
                     bounded_entry_size=lambda path: ("measured", 1))
        block = scope["reclaim_inventory"]({"containers": [record, base]}, {}, {}, None,
                                           True, True)
        entries = {item["name"]: item for item in block["entries"]}
        attached = entries[WS]["containers"]
        self.assertEqual(len(attached), 1)
        self.assertEqual(attached[0]["id"], ID)
        self.assertTrue(attached[0]["stray"]["owned"])
        self.assertEqual(attached[0]["stray"]["fingerprint"],
                         assess_stray_container(record, str(checkout))["fingerprint"])
        self.assertNotIn("stray", entries["proj"]["containers"][0])


class ReclaimRunTest(unittest.TestCase):
    """US1/US4: the host run forwards the plan and writes intents first."""

    def run_program(self, candidate_extra, engine, checkout):
        root = checkout.parent.parent
        runtime = root / "runtime"
        request = {"action": "reclaim", "run_id": "a" * 32, "budget_seconds": 30,
                   "candidates": [dict({"seq": 1, "kind": "worktree",
                                        "locator": str(checkout),
                                        "reason": "finished_job_idle"}, **candidate_extra)]}
        source = remote._program(request).split("\nACTIONS = {", 1)[0]
        scope = {}
        exec(source, scope)
        scope.update(HOME=root, DEPLOY=checkout.parent, RUNTIME=runtime,
                     DELETION_DIR=runtime / "deletions", hosted_site_names=lambda: set(),
                     reconcile_after_removal=lambda *a: {}, run=engine)
        old = {"project_root": str(checkout), "lifecycle": "succeeded",
               "cleanup_state": "retained", "finished_at": time.time() - 8 * 3600}
        with mock.patch("sandbox.jobs.registry.read_resource_index",
                        return_value={"jobs": [old]}):
            result = scope["reclaim_run"]()
        lines = [json.loads(line) for line in
                 (runtime / "deletions" / ("a" * 32 + ".jsonl")).read_text().splitlines()]
        return result, lines

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.checkout = root / "deploy-src" / WS
        (self.checkout / "tests").mkdir(parents=True)
        self.record = container(mounts=[{"Type": "bind", "Source": str(self.checkout / "tests"),
                                         "Destination": "/work", "RW": True}])

    def test_stray_intent_precedes_removal_and_checkout_goes(self):
        engine = Engine(self.record)
        plan = planned(self.record, str(self.checkout))
        result, lines = self.run_program(
            {"stop_containers": [ID], "remove_containers": [plan]}, engine, self.checkout)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["status"], "removed", outcome)
        self.assertFalse(self.checkout.exists())
        intent = next(line for line in lines if line.get("kind") == "stray_container")
        self.assertEqual(intent["path"], ID)
        self.assertEqual(intent["fingerprint"], plan["fingerprint"])
        self.assertEqual(intent["workspace"], str(self.checkout))

    def test_changed_stray_keeps_checkout(self):
        plan = planned(self.record, str(self.checkout))
        changed = copy.deepcopy(self.record)
        changed["RestartCount"] = 5
        engine = Engine(changed)
        result, lines = self.run_program(
            {"stop_containers": [ID], "remove_containers": [plan]}, engine, self.checkout)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["reason"], "stack_teardown_incomplete")
        self.assertIn("container_evidence_changed:" + ID, outcome["teardown"]["failures"])
        self.assertTrue(self.checkout.exists())
        self.assertEqual(engine.mutations(), [])
        self.assertFalse(any(line.get("kind") == "stray_container" for line in lines))

    def test_plan_without_the_field_keeps_the_legacy_refusal(self):
        engine = Engine(self.record)

        def labels(argv, timeout):
            if argv[:3] == ["docker", "inspect", "--format"] and "Labels" in argv[3]:
                return 0, json.dumps({}), ""
            return engine(argv, timeout)
        result, _lines = self.run_program({"stop_containers": [ID]}, labels, self.checkout)
        self.assertEqual(result["outcomes"][0]["reason"], "stack_teardown_incomplete")
        self.assertTrue(self.checkout.exists())
        self.assertEqual(engine.mutations(), [])


class ServiceTest(unittest.TestCase):
    """US1/US5: the controller forwards strays and checks the tier."""

    def setUp(self):
        from sandbox.resources.context import PlanStore
        from tests.test_resource_reclaim_service import FakeProvider, TARGET, entry
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = PlanStore(self.root / "plans")
        self.target = TARGET
        record = container(mounts=[{"Type": "bind", "Source": "/deploy/" + WS,
                                    "Destination": "/w"}])
        self.plan_item = planned(record, "/deploy/" + WS)
        workspace = entry(WS, containers=[probe_record(record, "/deploy/" + WS)],
                          finished_job_idle=True)
        self.provider = FakeProvider()
        self.provider.block["entries"] = [workspace]

    def service(self):
        from sandbox.resources.reclaim_service import ReclaimService
        return ReclaimService(self.provider, self.store, target=self.target)

    def test_cleanup_forwards_remove_containers(self):
        payload = self.service().cleanup(tier="safe", confirm=True)
        self.assertTrue(payload["ok"], payload)
        sent = next(call for call in self.provider.calls if call[0] == "reclaim")[3]
        self.assertEqual(sent[0]["remove_containers"], [self.plan_item])

    def test_tier_mismatch_is_refused_before_the_plan_begins(self):
        planned_tmp = self.service().plan("tmp")
        plan_id = planned_tmp["data"]["plan_id"]
        payload = self.service().cleanup(tier="safe", plan_id=plan_id, confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "plan_kind_mismatch")
        self.assertEqual(self.store.load(plan_id).state, "planned")
        self.assertFalse(any(call[0] == "reclaim" for call in self.provider.calls))
        again = self.service().cleanup(tier="tmp", plan_id=plan_id, confirm=True)
        self.assertTrue(again["ok"], again)


if __name__ == "__main__":
    unittest.main()

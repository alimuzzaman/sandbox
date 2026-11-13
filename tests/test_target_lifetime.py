"""Spec 077: target lifetime records, claims, eligibility and the host store."""
from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

from sandbox.hosting import names
from sandbox.hosting.lifetime import hoststore as hs
from tests.subprocess_support import run_test_process

NOW = hs.parse_rfc3339("2026-11-10T10:00:00Z")
CTRL_A = "a" * 32
CTRL_B = "b" * 32
OWNER_A = {"label": "alice", "controller": CTRL_A}
OWNER_B = {"label": "bob", "controller": CTRL_B}
COMMIT = "c" * 40
DEV_COMMIT = "d" * 40


def stack_record(slug="demo", *, copy_id="0000000a", owner=OWNER_A, project=None,
                 lifetime="temporary", ttl=hs.DEFAULT_TTL_SECONDS, expires=None,
                 ref_branch="stack/demo", dev_branch="dev", commit=COMMIT, ports=None,
                 hostnames=None, state="active", repo="github.com/org/repo", **extra):
    project = project or "hub-%s" % slug
    canonical = lifetime == "hosted"
    data = {
        "schema_version": 1, "compose_project": hs.compose_project_for(project, "sandbox"),
        "project": project, "environment": "sandbox", "kind": "stack",
        "lifetime": lifetime, "lifetime_source": "default" if lifetime == "temporary" else "flag",
        "ttl_seconds": ttl if lifetime == "temporary" else None,
        "expires_at": (expires or hs.to_rfc3339(NOW + ttl)) if lifetime == "temporary" else None,
        "extended_count": 0, "state": state, "owner": dict(owner),
        "created_at": hs.to_rfc3339(NOW), "updated_at": hs.to_rfc3339(NOW),
        "classified_by": None, "classified_at": None,
        "front_door": "nginx", "dns_mode": "wildcard",
        "hostnames": hostnames if hostnames is not None else ["hub-%s.example.test" % slug],
        "slug": slug, "copy_id": copy_id, "repo": repo, "ref": ref_branch,
        "ref_branch": ref_branch, "commit": commit, "dev_branch": dev_branch,
        "canonical": canonical, "template": "/checkout/sandbox.hosting.yml",
        "ports": ports if ports is not None else {},
        "merge": None, "reap_run": None, "overrides": [],
    }
    data.update(extra)
    return data


def parsed(data):
    record = hs.parse_record(data)
    assert isinstance(record, hs.TargetRecord), record
    return record


def caller(owner=OWNER_A):
    return hs.Owner(owner["label"], owner["controller"])


POLICY = hs.Policy(per_owner=5, total=40, min_free_bytes=0)


def claim(records, draft, **kwargs):
    defaults = dict(caller=caller(), now=NOW, policy=POLICY, free_bytes=None)
    defaults.update(kwargs)
    by_project = {record.compose_project: record for record in records}
    invalid = defaults.pop("invalid", set())
    deployed = defaults.pop("deployed", set())
    return hs.decide_claim(by_project, invalid, deployed, parsed(draft), **defaults)


class RecordValidationTests(unittest.TestCase):
    def test_parity_with_target_part_rule(self):
        self.assertEqual(hs.TARGET_PART_RE.pattern, names.TARGET_PART_RE.pattern)

    def test_valid_stack_record_round_trips_and_keeps_unknown_keys(self):
        data = stack_record(future_field={"x": 1})
        record = parsed(data)
        self.assertEqual(record.extra, {"future_field": {"x": 1}})
        self.assertEqual(hs.record_to_dict(record)["future_field"], {"x": 1})
        self.assertIsInstance(hs.parse_record(hs.record_to_dict(record)), hs.TargetRecord)

    def test_invalid_records_name_the_rule(self):
        cases = {
            "lifetime": dict(lifetime="forever"),
            "compose_project": dict(compose_project="sandbox-host-other-sandbox"),
            "ttl_seconds": dict(ttl_seconds=10),
            "expires_at": dict(expires_at="tomorrow"),
            "state": dict(state="gone"),
            "owner": dict(owner={"label": "", "controller": CTRL_A}),
            "email owner": dict(owner={"label": "a@example.test", "controller": CTRL_A}),
            "schema_version": dict(schema_version=2),
            "extended_count": dict(extended_count=-1),
            "stack without slug": dict(slug=None),
        }
        for label, change in cases.items():
            with self.subTest(label):
                data = stack_record()
                data.update(change)
                self.assertIsInstance(hs.parse_record(data), hs.Invalid)

    def test_file_name_must_match(self):
        self.assertIsInstance(hs.parse_record(stack_record(), "sandbox-host-x-sandbox.json"), hs.Invalid)

    def test_hosted_requires_dev_branch(self):
        data = stack_record(lifetime="hosted", ref_branch="feature/x")
        self.assertIsInstance(hs.parse_record(data), hs.Invalid)
        self.assertIsInstance(hs.parse_record(stack_record(lifetime="hosted", ref_branch="dev")), hs.TargetRecord)

    def test_reaped_requires_reap_info(self):
        self.assertIsInstance(hs.parse_record(stack_record(state="reaped")), hs.Invalid)

    def test_flat_panel_fields(self):
        data = hs.record_to_dict(parsed(stack_record()), NOW)
        for key in ("schema_version", "lifetime", "lifetime_source", "ttl_seconds", "expires_at",
                    "extended_count", "owner", "copy_id", "slug", "ref", "ref_branch", "commit",
                    "repo", "canonical", "state", "created_at", "updated_at", "reap"):
            self.assertIn(key, data)
        self.assertEqual(data["reap"], {"eligible": False, "reason": None, "merged_into": None,
                                        "merged_at": None})
        self.assertNotIn("stack", data)

    def test_classified_target_shape(self):
        data = stack_record()
        data.update({"kind": "target", "lifetime": "permanent", "lifetime_source": "classified",
                     "ttl_seconds": None, "expires_at": None, "slug": None, "copy_id": None,
                     "ref": None, "ref_branch": None, "dev_branch": None, "template": None,
                     "canonical": False, "ports": {}, "repo": None, "commit": None,
                     "classified_by": dict(OWNER_B), "classified_at": hs.to_rfc3339(NOW)})
        record = parsed(data)
        self.assertEqual(record.classified_by.label, "bob")
        missing = dict(data, classified_at=None)
        self.assertIsInstance(hs.parse_record(missing), hs.Invalid)
        with_slug = dict(data, slug="demo")
        self.assertIsInstance(hs.parse_record(with_slug), hs.Invalid)

    def test_ttl_parsing(self):
        self.assertEqual(hs.parse_ttl("14d"), 14 * 86400)
        self.assertEqual(hs.parse_ttl("36h"), 36 * 3600)
        self.assertEqual(hs.parse_ttl("2w"), 14 * 86400)
        for bad in ("0d", "91d", "1m", "", "d", "-1d", "30m"):
            with self.subTest(bad):
                with self.assertRaises(hs.StoreRefusal):
                    hs.parse_ttl(bad)


class EligibilityTests(unittest.TestCase):
    def test_table(self):
        past = hs.to_rfc3339(NOW - 1)
        merged = {"merged": True, "commit": COMMIT, "dev_branch": "dev", "dev_commit": DEV_COMMIT,
                  "checked_at": hs.to_rfc3339(NOW), "controller": CTRL_A}
        stale_merge = dict(merged, commit="e" * 40)
        reap_run = {"run_id": "r1", "started_at": hs.to_rfc3339(NOW), "reason": "expired"}
        cases = [
            ("fresh temporary", stack_record(), (False, "not_expired")),
            ("expired", stack_record(expires=past), (True, "expired")),
            ("merged", stack_record(merge=merged), (True, "merged")),
            ("merge of an older commit", stack_record(merge=stale_merge), (False, "not_expired")),
            ("hosted", stack_record(lifetime="hosted", ref_branch="dev"), (False, "hosted")),
            ("permanent", stack_record(lifetime="permanent"), (False, "permanent")),
            ("reaping resumes", stack_record(state="reaping", reap_run=reap_run), (True, "expired")),
            ("reaping, changed to permanent by hand",
             stack_record(state="reaping", reap_run=reap_run, lifetime="permanent"), (False, "permanent")),
            ("reaped", stack_record(state="reaped", reap_run=reap_run, expires=past), (False, "reaped")),
        ]
        for label, data, expected in cases:
            with self.subTest(label):
                result = hs.eligibility(parsed(data), NOW)
                self.assertEqual((result.eligible, result.reason), expected)

    def test_invalid_is_never_eligible(self):
        self.assertFalse(hs.eligibility(hs.Invalid("x", "bad"), NOW).eligible)


class ClaimDecisionTests(unittest.TestCase):
    def test_new_claim_is_stored_with_default_lifetime(self):
        decision = claim([], stack_record())
        self.assertTrue(decision.is_new)
        self.assertEqual(decision.stored.lifetime, "temporary")
        self.assertEqual(decision.stored.ttl_seconds, 14 * 86400)

    def test_other_owner_same_slug_is_refused(self):
        held = parsed(stack_record(owner=OWNER_A))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record(owner=OWNER_B, copy_id="0000000b"), caller=caller(OWNER_B))
        self.assertEqual(raised.exception.code, hs.SLUG_TAKEN)
        self.assertEqual(raised.exception.holder["owner"], "alice")

    def test_same_owner_other_copy_is_refused(self):
        held = parsed(stack_record(copy_id="0000000a"))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record(copy_id="0000000c"))
        self.assertEqual(raised.exception.code, hs.SLUG_TAKEN)

    def test_same_copy_from_its_controller_updates_in_place(self):
        held = parsed(stack_record(expires=hs.to_rfc3339(NOW + 3600), ttl=3600))
        decision = claim([held], stack_record(commit="e" * 40))
        self.assertFalse(decision.is_new)
        self.assertEqual(decision.stored.expires_at, held.expires_at)
        self.assertEqual(decision.stored.stack.commit, "e" * 40)

    def test_same_copy_from_another_controller_needs_explicit_copy(self):
        other_machine = {"label": "alice", "controller": CTRL_B}
        held = parsed(stack_record())
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record(owner=other_machine), caller=caller(other_machine))
        self.assertEqual(raised.exception.code, hs.SLUG_TAKEN)
        decision = claim([held], stack_record(owner=other_machine), caller=caller(other_machine),
                         explicit_copy=True)
        self.assertEqual(decision.stored.owner.label, "alice")
        self.assertEqual(decision.stored.owner.controller, CTRL_B)

    def test_explicit_copy_of_another_owner_is_refused(self):
        held = parsed(stack_record())
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record(owner=OWNER_B), caller=caller(OWNER_B), explicit_copy=True)
        self.assertEqual(raised.exception.code, hs.NOT_OWNER)

    def test_project_deployed_without_record_is_refused_unless_legacy(self):
        draft = stack_record()
        compose = draft["compose_project"]
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([], draft, deployed={compose})
        self.assertEqual(raised.exception.code, hs.PROJECT_TAKEN)
        self.assertTrue(claim([], draft, deployed={compose}, legacy=True).is_new)

    def test_classified_target_project_is_refused(self):
        target = stack_record()
        target.update({"kind": "target", "lifetime": "permanent", "lifetime_source": "classified",
                       "ttl_seconds": None, "expires_at": None, "slug": None, "copy_id": None,
                       "ref": None, "ref_branch": None, "dev_branch": None, "template": None,
                       "canonical": False, "ports": {}, "classified_by": dict(OWNER_A),
                       "classified_at": hs.to_rfc3339(NOW)})
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([parsed(target)], stack_record())
        self.assertEqual(raised.exception.code, hs.PROJECT_TAKEN)

    def test_invalid_record_for_the_project_is_refused(self):
        draft = stack_record()
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([], draft, invalid={draft["compose_project"]})
        self.assertEqual(raised.exception.code, hs.RECORD_INVALID)

    def test_expired_rerun_needs_new_ttl(self):
        held = parsed(stack_record(expires=hs.to_rfc3339(NOW - 5)))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record())
        self.assertEqual(raised.exception.code, hs.EXPIRED)
        decision = claim([held], stack_record(ttl=7 * 86400), new_ttl=True)
        self.assertEqual(decision.stored.expires_at, hs.to_rfc3339(NOW + 7 * 86400))

    def test_reaping_is_refused(self):
        reap_run = {"run_id": "r1", "started_at": hs.to_rfc3339(NOW), "reason": "expired"}
        held = parsed(stack_record(state="reaping", reap_run=reap_run))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record())
        self.assertEqual(raised.exception.code, hs.REAPING)

    def test_reaped_record_of_same_copy_is_claimed_fresh(self):
        reap_run = {"run_id": "r1", "started_at": hs.to_rfc3339(NOW), "reason": "expired",
                    "finished_at": hs.to_rfc3339(NOW)}
        held = parsed(stack_record(state="reaped", reap_run=reap_run, expires=hs.to_rfc3339(NOW - 5)))
        decision = claim([held], stack_record())
        self.assertTrue(decision.is_new)
        self.assertEqual(decision.stored.state, "active")
        self.assertIsNone(decision.stored.reap_run)

    def test_rerun_keeps_lifetime_unless_flagged(self):
        held = parsed(stack_record(lifetime="permanent"))
        self.assertEqual(claim([held], stack_record()).stored.lifetime, "permanent")
        flagged = claim([held], stack_record(), lifetime_explicit=True)
        self.assertEqual(flagged.stored.lifetime, "temporary")

    def test_one_canonical_hosted_stack_per_repo(self):
        dev = parsed(stack_record("dev", lifetime="hosted", ref_branch="dev", copy_id="0000000d"))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([dev], stack_record("dev2", lifetime="hosted", ref_branch="dev", copy_id="0000000e"))
        self.assertEqual(raised.exception.code, hs.HOSTED_TAKEN)
        other_repo = stack_record("dev2", lifetime="hosted", ref_branch="dev", copy_id="0000000e",
                                  repo="github.com/org/other")
        self.assertTrue(claim([dev], other_repo).is_new)
        other_dev = stack_record("main", lifetime="hosted", ref_branch="main", dev_branch="main",
                                 copy_id="0000000f")
        self.assertTrue(claim([dev], other_dev).is_new)

    def test_hosted_stack_rerun_at_a_feature_ref_is_refused(self):
        dev = parsed(stack_record("dev", lifetime="hosted", ref_branch="dev"))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([dev], stack_record("dev", ref_branch="feature/x"))
        self.assertEqual(raised.exception.code, hs.LIFETIME_INVALID)

    def test_every_claim_drops_merge_evidence(self):
        merged = {"merged": True, "commit": COMMIT, "dev_branch": "dev", "dev_commit": DEV_COMMIT,
                  "checked_at": hs.to_rfc3339(NOW), "controller": CTRL_A}
        held = parsed(stack_record(merge=merged))
        self.assertIsNone(claim([held], stack_record()).stored.merge)
        self.assertIsNone(claim([held], stack_record(commit="e" * 40)).stored.merge)

    def test_ports_and_hostnames_of_other_stacks_are_refused(self):
        held = parsed(stack_record("one", ports={"mailpit": 18142}, hostnames=["hub-one.example.test"]))
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record("two", copy_id="0000000b", ports={"mailpit": 18142}))
        self.assertEqual(raised.exception.code, hs.PORT_UNAVAILABLE)
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([held], stack_record("two", copy_id="0000000b", hostnames=["hub-one.example.test"]))
        self.assertEqual(raised.exception.code, hs.HOSTNAME_OWNED)

    def test_limits_apply_to_new_copies_only(self):
        mine = [parsed(stack_record("s%d" % index, copy_id="%08x" % index)) for index in range(2)]
        tight = hs.Policy(per_owner=2, total=40, min_free_bytes=0)
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim(mine, stack_record("s9", copy_id="00000009"), policy=tight)
        self.assertEqual(raised.exception.code, hs.LIMIT_REACHED)
        self.assertFalse(claim(mine, stack_record("s0", copy_id="00000000"), policy=tight).is_new)
        self.assertTrue(claim(mine, stack_record("s9", copy_id="00000009", owner=OWNER_B),
                              caller=caller(OWNER_B), policy=tight).is_new)
        total = hs.Policy(per_owner=9, total=2, min_free_bytes=0)
        with self.assertRaises(hs.StoreRefusal):
            claim(mine, stack_record("s9", copy_id="00000009", owner=OWNER_B), caller=caller(OWNER_B),
                  policy=total)
        disk = hs.Policy(per_owner=9, total=40, min_free_bytes=100)
        with self.assertRaises(hs.StoreRefusal) as raised:
            claim([], stack_record(), policy=disk, free_bytes=99)
        self.assertIn("GiB free", raised.exception.message)


def _request(op, **fields):
    body = {"op": op, "now": hs.to_rfc3339(NOW), "caller": OWNER_A}
    body.update(fields)
    return body


def _race_claim(args):
    home, copy_id, label = args
    owner = {"label": label, "controller": ("%x" % (int(copy_id, 16) % 16)) * 32}
    request = {"op": "claim", "now": hs.to_rfc3339(NOW), "caller": owner,
               "record": stack_record(copy_id=copy_id, owner=owner)}
    return hs.handle(request, home, free_paths=())


class HostStoreTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sb-lifetime-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))

    def run_op(self, op, **fields):
        return hs.handle(_request(op, **fields), str(self.home), free_paths=())

    def test_claim_list_update_release(self):
        claimed = self.run_op("claim", record=stack_record())
        self.assertTrue(claimed["ok"], claimed)
        root = self.home / "runtime" / "target-lifetimes"
        record_file = root / "sandbox-host-hub-demo-sandbox.json"
        self.assertEqual(record_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        listed = self.run_op("list")["result"]
        self.assertEqual([item["slug"] for item in listed["records"]], ["demo"])
        self.assertFalse(listed["records"][0]["reap"]["eligible"])
        self.assertEqual(listed["records"][0]["schema_version"], 1)
        updated = self.run_op("update", compose_project="sandbox-host-hub-demo-sandbox", ttl_seconds=3600)
        self.assertEqual(updated["result"]["record"]["expires_at"], hs.to_rfc3339(NOW + 3600))
        self.assertEqual(updated["result"]["record"]["extended_count"], 1)
        refused = hs.handle(dict(_request("update", compose_project="sandbox-host-hub-demo-sandbox",
                                          ttl_seconds=3600), caller=OWNER_B), str(self.home))
        self.assertEqual(refused["code"], hs.NOT_OWNER)
        forced = hs.handle(dict(_request("update", compose_project="sandbox-host-hub-demo-sandbox",
                                         ttl_seconds=7200, force_owner=True), caller=OWNER_B),
                           str(self.home))
        self.assertEqual(forced["result"]["record"]["overrides"][-1]["by"]["label"], "bob")
        self.assertEqual(forced["result"]["record"]["extended_count"], 2)
        self.assertTrue(self.run_op("release", compose_project="sandbox-host-hub-demo-sandbox")["ok"])
        self.assertFalse(record_file.exists())
        audit = [json.loads(line) for line in (root / "audit.jsonl").read_text().splitlines()]
        self.assertEqual([line["op"] for line in audit], ["claim", "update", "update", "release"])

    def test_refused_claim_changes_nothing(self):
        self.run_op("claim", record=stack_record())
        root = self.home / "runtime" / "target-lifetimes"
        before = {child.name: child.read_bytes() for child in root.iterdir()}
        refused = hs.handle(dict(_request("claim", record=stack_record(owner=OWNER_B, copy_id="0000000b")),
                                 caller=OWNER_B), str(self.home))
        self.assertEqual(refused["code"], hs.SLUG_TAKEN)
        self.assertEqual({child.name: child.read_bytes() for child in root.iterdir()}, before)

    def test_check_does_not_write(self):
        self.assertTrue(self.run_op("check", record=stack_record())["ok"])
        self.assertEqual(self.run_op("list")["result"]["records"], [])

    def test_invalid_file_is_reported_and_never_rewritten_by_claim(self):
        root = self.home / "runtime" / "target-lifetimes"
        root.mkdir(parents=True)
        bad = root / "sandbox-host-hub-demo-sandbox.json"
        bad.write_text("{not json")
        listed = self.run_op("list")["result"]
        self.assertEqual(listed["invalid"][0]["compose_project"], "sandbox-host-hub-demo-sandbox")
        self.assertEqual(self.run_op("claim", record=stack_record())["code"], hs.RECORD_INVALID)
        self.assertEqual(bad.read_text(), "{not json")

    def test_forced_repair_sets_an_invalid_file_aside(self):
        root = self.home / "runtime" / "target-lifetimes"
        root.mkdir(parents=True)
        (self.home / "runtime" / "hosts" / "mockups" / "sandbox").mkdir(parents=True)
        bad = root / "sandbox-host-mockups-sandbox.json"
        bad.write_text("{not json")
        refused = self.run_op("classify", project="mockups", environment="sandbox", lifetime="permanent")
        self.assertEqual(refused["code"], hs.RECORD_INVALID)
        self.assertEqual(bad.read_text(), "{not json")
        rejected = self.run_op("classify", project="mockups", environment="sandbox", lifetime="nope",
                               force_owner=True)
        self.assertFalse(rejected["ok"])
        self.assertEqual(bad.read_text(), "{not json")
        done = self.run_op("classify", project="mockups", environment="sandbox", lifetime="permanent",
                           force_owner=True)
        kept = root / done["result"]["kept"]
        self.assertEqual(kept.read_text(), "{not json")
        self.assertEqual(self.run_op("list")["result"]["invalid"], [])
        bad.write_text("{again")
        released = self.run_op("release", compose_project="sandbox-host-mockups-sandbox", force_owner=True)
        self.assertTrue(released["result"]["released"])
        self.assertNotEqual(released["result"]["kept"], done["result"]["kept"])  # same second, both kept
        self.assertEqual((root / released["result"]["kept"]).read_text(), "{again")
        self.assertEqual(kept.read_text(), "{not json")
        self.assertFalse(bad.exists())

    def test_host_store_parses_as_python_3_8(self):
        import ast
        ast.parse(Path(hs.__file__).read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_unclassified_deployed_targets_are_listed(self):
        (self.home / "runtime" / "hosts" / "agent-notes" / "production").mkdir(parents=True)
        self.assertEqual(self.run_op("list")["result"]["unclassified"],
                         ["sandbox-host-agent-notes-production"])

    def test_classify_and_unclassify(self):
        missing = self.run_op("classify", project="mockups", environment="sandbox", lifetime="permanent")
        self.assertEqual(missing["code"], hs.TARGET_NOT_FOUND)
        (self.home / "runtime" / "hosts" / "mockups" / "sandbox").mkdir(parents=True)
        done = self.run_op("classify", project="mockups", environment="sandbox", lifetime="permanent",
                           commit=COMMIT)
        record = done["result"]["record"]
        self.assertEqual(record["lifetime_source"], "classified")
        self.assertEqual(record["classified_by"], OWNER_A)
        self.assertEqual((record["slug"], record["copy_id"], record["ttl_seconds"]), (None, None, None))
        self.assertEqual((record["commit"], record["repo"]), (COMMIT, None))
        self.assertEqual(self.run_op("update", compose_project="sandbox-host-mockups-sandbox",
                                     ttl_seconds=3600)["code"], hs.LIFETIME_INVALID)
        self.assertEqual(self.run_op("list")["result"]["unclassified"], [])
        cleared = self.run_op("classify", project="mockups", environment="sandbox", clear=True)
        self.assertTrue(cleared["result"]["removed"])

    def write_summary(self, commit, status="succeeded", project="mockups", environment="sandbox",
                      routes=None):
        path = self.home / "runtime" / "hosts" / project / environment / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = {"schema": 1, "project": project, "environment": environment,
            "compose_project": hs.compose_project_for(project, environment), "commit": commit,
            "last_apply": {"status": status}}
        if routes is not None:
            summary["routes"] = [{"hostname": name, "mode": "serve", "primary": index == 0}
                                 for index, name in enumerate(routes)]
        path.write_text(json.dumps(summary))
        return path

    def test_record_apply_never_creates_a_record(self):
        self.write_summary(DEV_COMMIT)
        root = self.home / "runtime" / "target-lifetimes"
        result = self.run_op("record_apply", project="mockups", environment="sandbox", commit=DEV_COMMIT)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["result"]["recorded"])
        self.assertEqual(self.run_op("list")["result"]["records"], [])
        self.assertFalse((root / "sandbox-host-mockups-sandbox.json").exists())

    def test_record_apply_moves_only_the_commit_of_a_classified_target(self):
        self.write_summary(COMMIT)
        before = self.run_op("classify", project="mockups", environment="sandbox", lifetime="temporary",
                             ttl_seconds=7200)["result"]["record"]
        self.assertEqual(before["commit"], COMMIT)
        self.write_summary(DEV_COMMIT)
        later = hs.to_rfc3339(NOW + 60)
        done = hs.handle(dict(_request("record_apply", project="mockups", environment="sandbox",
                                       commit=DEV_COMMIT), now=later, caller=None), str(self.home))
        self.assertTrue(done["result"]["recorded"], done)
        after = self.run_op("list")["result"]["records"][0]
        self.assertEqual(after["commit"], DEV_COMMIT)
        self.assertEqual(after["updated_at"], later)
        for key in ("kind", "lifetime", "lifetime_source", "ttl_seconds", "expires_at", "owner",
                    "classified_by", "classified_at", "created_at", "hostnames", "extended_count"):
            self.assertEqual(after[key], before[key], key)
        audit = [json.loads(line) for line in
                 (self.home / "runtime" / "target-lifetimes" / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(audit[-1]["op"], "record_apply")
        self.assertEqual((audit[-1]["commit"], audit[-1]["previous"]), (DEV_COMMIT, COMMIT))

    def test_record_apply_needs_the_server_summary_of_that_succeeded_apply(self):
        self.write_summary(COMMIT)
        self.run_op("classify", project="mockups", environment="sandbox", lifetime="hosted")
        for commit, status in ((DEV_COMMIT, "applying"), (DEV_COMMIT, "unverified"),
                               ("e" * 40, "succeeded")):
            with self.subTest(commit=commit, status=status):
                self.write_summary(DEV_COMMIT, status)
                result = self.run_op("record_apply", project="mockups", environment="sandbox", commit=commit)
                self.assertFalse(result["result"]["recorded"], result)
                self.assertEqual(self.run_op("list")["result"]["records"][0]["commit"], COMMIT)
        self.assertEqual(self.run_op("record_apply", project="mockups", environment="sandbox",
                                     commit="short")["code"], hs.REQUEST_INVALID)

    def test_record_apply_leaves_stack_records_alone(self):
        self.run_op("claim", record=stack_record())
        self.write_summary(DEV_COMMIT, project="hub-demo")
        result = self.run_op("record_apply", project="hub-demo", environment="sandbox", commit=DEV_COMMIT)
        self.assertFalse(result["result"]["recorded"])
        self.assertEqual(self.run_op("list")["result"]["records"][0]["commit"], COMMIT)

    def test_classify_prefers_the_server_summary_commit_over_controller_history(self):
        self.write_summary(DEV_COMMIT)
        done = self.run_op("classify", project="mockups", environment="sandbox", lifetime="hosted",
                           commit=COMMIT)
        self.assertEqual(done["result"]["record"]["commit"], DEV_COMMIT)

    def test_classify_prefers_the_server_summary_hostnames_over_controller_history(self):
        current = ["wp-setup-plan.example.test", "hub-setup-plan.example.test"]
        old = ["old-a.example.test", "old-b.example.test", "old-c.example.test"]
        self.write_summary(DEV_COMMIT, routes=current)
        done = self.run_op("classify", project="mockups", environment="sandbox", lifetime="hosted",
                           commit=COMMIT, hostnames=old)
        self.assertEqual(done["result"]["record"]["hostnames"], sorted(current))
        # Without a succeeded summary listing routes, the controller's names are kept.
        for status, routes in (("failed", current), ("succeeded", None)):
            with self.subTest(status=status, routes=routes):
                self.write_summary(DEV_COMMIT, status=status, routes=routes)
                done = self.run_op("classify", project="mockups", environment="sandbox",
                                   lifetime="hosted", commit=COMMIT, hostnames=old)
                self.assertEqual(done["result"]["record"]["hostnames"], sorted(old))

    def test_classify_keeps_controller_commit_without_a_succeeded_server_summary(self):
        self.write_summary(DEV_COMMIT, status="failed")
        done = self.run_op("classify", project="mockups", environment="sandbox", lifetime="hosted",
                           commit=COMMIT)
        self.assertEqual(done["result"]["record"]["commit"], COMMIT)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_summary_reader_refuses_a_fifo_without_blocking(self):
        import threading
        path = self.home / "runtime" / "hosts" / "mockups" / "sandbox" / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        result = []
        reader = threading.Thread(target=lambda: result.append(
            hs._server_summary_commit(self.home, "mockups", "sandbox")), daemon=True)
        reader.start()
        reader.join(5)
        self.assertFalse(reader.is_alive(), "summary reader blocked on a FIFO under the store lock")
        self.assertEqual(result, [None])

    def test_summary_reader_refuses_a_symlink_and_another_target(self):
        real = self.write_summary(DEV_COMMIT)
        self.assertEqual(hs._server_summary_commit(self.home, "mockups", "sandbox"), DEV_COMMIT)
        moved = real.with_name("real.json")
        real.rename(moved)
        real.symlink_to(moved)
        self.assertIsNone(hs._server_summary_commit(self.home, "mockups", "sandbox"))
        real.unlink()
        for project, environment, compose in (("other", "sandbox", None), ("mockups", "dev", None),
                                              ("mockups", "sandbox", "sandbox-host-other-sandbox")):
            with self.subTest(project=project, environment=environment, compose=compose):
                real.write_text(json.dumps({"schema": 1, "project": project, "environment": environment,
                    "compose_project": compose or hs.compose_project_for(project, environment),
                    "commit": DEV_COMMIT, "last_apply": {"status": "succeeded"}}))
                self.assertIsNone(hs._server_summary_commit(self.home, "mockups", "sandbox"))

    def test_record_apply_skips_a_target_being_reaped(self):
        self.write_summary(COMMIT)
        self.run_op("classify", project="mockups", environment="sandbox", lifetime="hosted")
        path = self.home / "runtime" / "target-lifetimes" / "sandbox-host-mockups-sandbox.json"
        record = json.loads(path.read_text())
        record["state"] = "reaping"
        record["reap_run"] = {"run_id": "r1", "started_at": hs.to_rfc3339(NOW), "reason": "expired"}
        path.write_text(json.dumps(record))
        self.write_summary(DEV_COMMIT)
        result = self.run_op("record_apply", project="mockups", environment="sandbox", commit=DEV_COMMIT)
        self.assertFalse(result["result"]["recorded"], result)
        self.assertEqual(json.loads(path.read_text())["commit"], COMMIT)

    def test_record_merge_only_for_the_checked_commit(self):
        self.run_op("claim", record=stack_record())
        evidence = {"merged": True, "dev_branch": "dev", "dev_commit": DEV_COMMIT}
        stale = self.run_op("record_merge", compose_project="sandbox-host-hub-demo-sandbox",
                            commit="e" * 40, evidence=evidence)
        self.assertFalse(stale["result"]["recorded"])
        done = self.run_op("record_merge", compose_project="sandbox-host-hub-demo-sandbox",
                           commit=COMMIT, evidence=evidence)
        self.assertTrue(done["result"]["recorded"])
        listed = self.run_op("list")["result"]["records"][0]
        self.assertEqual(listed["reap"]["reason"], "merged")
        self.assertEqual(listed["reap"]["merged_into"], "dev")

    def test_policy_round_trip_and_validation(self):
        self.assertEqual(self.run_op("policy")["result"]["policy"], hs.DEFAULT_POLICY)
        self.assertEqual(self.run_op("policy", set={"per_owner": 2})["result"]["policy"]["per_owner"], 2)
        self.assertEqual(self.run_op("policy", set={"total": 0})["code"], hs.LIMITS_INVALID)

    def test_concurrent_claims_have_one_winner(self):
        context = multiprocessing.get_context("spawn")
        with context.Pool(4) as pool:
            results = pool.map(_race_claim, [(str(self.home), "%08x" % (index + 1), "user%d" % index)
                                             for index in range(4)])
        winners = [item for item in results if item.get("ok")]
        self.assertEqual(len(winners), 1, results)
        self.assertTrue(all(item["code"] == hs.SLUG_TAKEN for item in results if not item.get("ok")))

    def test_shipped_program_runs_as_python_c(self):
        source = Path(hs.__file__).read_text(encoding="utf-8")
        result = run_test_process([sys.executable, "-I", "-c", source], input=json.dumps(_request("list")),
                                  capture_output=True, text=True, env={"SANDBOX_HOME": str(self.home)},
                                  cwd=str(self.home), timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_unknown_op_and_bad_now(self):
        self.assertEqual(self.run_op("drop")["code"], hs.REQUEST_INVALID)
        self.assertEqual(hs.handle({"op": "list", "now": "x"}, str(self.home))["code"], hs.REQUEST_INVALID)



class FakeShell:
    """Records reaper commands; answers the inventory like a host with one stack."""

    def __init__(self, *, fail=None, inventory=None):
        self.commands = []
        self.fail = fail or ()
        self.inventory = inventory

    def __call__(self, command, timeout):
        self.commands.append(command)
        if any(token in command for token in self.fail):
            return subprocess.CompletedProcess(command, 7, "", "boom")
        if "echo done=1" in command:
            lines = self.inventory if self.inventory is not None else [
                "container=c1", "volume=sandbox-host-hub-demo-sandbox_db", "network=n1",
                "image=sandbox-host-hub-demo-sandbox-web:latest",
                "route=/etc/nginx/conf.d/sandbox-host-hub-demo-sandbox.conf",
                "runtime=/home/runtime/hosts/hub-demo/sandbox", "done=1"]
            return subprocess.CompletedProcess(command, 0, "\n".join(lines) + "\n", "")
        return subprocess.CompletedProcess(command, 0, "removed\n", "")

    def kinds(self):
        out = []
        for command in self.commands:
            if "echo done=1" in command:
                out.append("inventory")
            elif "sandbox-nginx" in command:
                out.append("route")
            elif "certbot" in command or "/certs/" in command and "docker" not in command:
                out.append("certs")
            elif "docker volume ls" in command and "xargs" in command:
                out.append("teardown")
            elif "deploy-src" in command:
                out.append("source")
            else:
                out.append("other")
        return out


def _write_record(home, data):
    store = hs.Store(home)
    record = parsed(data)
    with store.lock():
        store.write(record)
    return record


class ReaperTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sb-reaper-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.old = hs.to_rfc3339(NOW - 3 * 86400)

    def expired(self, slug="demo", **extra):
        data = stack_record(slug, expires=hs.to_rfc3339(NOW - 60), **extra)
        data["updated_at"] = self.old
        return data

    def reap(self, shell, **kwargs):
        from sandbox.hosting.lifetime import reaper
        return reaper.reap(self.home, now=NOW, run_id="run1", confirm=True, runner=shell, **kwargs)

    def records(self):
        records, invalid = hs.Store(self.home).load()
        return records, invalid

    def manifest(self):
        path = self.home / "runtime" / "resources" / "deletions" / "hosted-run1.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_expired_target_is_torn_down_with_intents_first(self):
        (self.home / "runtime" / "hosts" / "hub-demo" / "sandbox").mkdir(parents=True)
        _write_record(self.home, self.expired())
        shell = FakeShell()
        result = self.reap(shell)
        self.assertTrue(result["ok"], result)
        self.assertEqual([item["status"] for item in result["outcomes"]], ["reaped"])
        self.assertEqual(shell.kinds()[:4], ["inventory", "route", "certs", "teardown"])
        lines = self.manifest()
        self.assertEqual(lines[0]["phase"], "run_start")
        intents = [line for line in lines if line["phase"] == "intent"]
        self.assertEqual({line["kind"] for line in intents},
                         {"route", "container", "volume", "network", "image", "runtime"})
        last_intent = max(line["seq"] for line in intents)
        outcome = next(line for line in lines if line["phase"] == "outcome")
        self.assertGreater(outcome["seq"], last_intent)
        record = self.records()[0]["sandbox-host-hub-demo-sandbox"]
        self.assertEqual(record.state, "reaped")
        self.assertEqual(record.reap_run.reason, "expired")
        self.assertFalse(record.reap_run.dns_left)

    def test_source_is_removed_only_when_no_environment_remains(self):
        source = self.home / "deploy-src" / "hosts" / "hub-demo"
        source.mkdir(parents=True)
        _write_record(self.home, self.expired())
        shell = FakeShell()
        self.reap(shell)
        self.assertIn("source", shell.kinds())
        self.assertTrue(any(line.get("kind") == "source" for line in self.manifest()))

    def test_protected_records_are_never_touched(self):
        _write_record(self.home, self.expired("hosted", lifetime="hosted", ref_branch="dev", copy_id="00000001"))
        _write_record(self.home, self.expired("perm", lifetime="permanent", copy_id="00000002"))
        fresh = stack_record("fresh", copy_id="00000003")
        _write_record(self.home, fresh)
        root = self.home / "runtime" / "target-lifetimes"
        (root / "sandbox-host-broken-sandbox.json").write_text("{}")
        (self.home / "runtime" / "hosts" / "agent-notes" / "production").mkdir(parents=True)
        shell = FakeShell()
        result = self.reap(shell)
        self.assertEqual(shell.commands, [])
        self.assertEqual(result["candidates"], [])
        self.assertIn("record_invalid", [item["reason"] for item in result["skipped"]])
        records, invalid = self.records()
        self.assertTrue(all(record.state == "active" for record in records.values()))
        self.assertIn("sandbox-host-broken-sandbox", invalid)

    def test_merged_target_is_reaped_before_expiry(self):
        merged = {"merged": True, "commit": COMMIT, "dev_branch": "dev", "dev_commit": DEV_COMMIT,
                  "checked_at": self.old, "controller": CTRL_A}
        data = stack_record(merge=merged)
        data["updated_at"] = self.old
        _write_record(self.home, data)
        result = self.reap(FakeShell())
        self.assertEqual(result["outcomes"][0]["reason"], "merged")

    def test_recently_claimed_target_waits_for_the_apply_grace(self):
        data = stack_record(expires=hs.to_rfc3339(NOW - 60))
        data["updated_at"] = hs.to_rfc3339(NOW - 60)
        _write_record(self.home, data)
        shell = FakeShell()
        result = self.reap(shell)
        self.assertEqual(shell.commands, [])
        self.assertEqual(result["skipped"][0]["reason"], "recently_applied")

    def test_caddy_front_door_is_skipped(self):
        _write_record(self.home, self.expired(front_door="caddy"))
        shell = FakeShell()
        result = self.reap(shell)
        self.assertEqual(shell.commands, [])
        self.assertEqual(result["skipped"][0]["reason"], "front_door_capability_unavailable")

    def test_failed_teardown_stays_reaping_and_the_next_run_resumes(self):
        _write_record(self.home, self.expired())
        failed = self.reap(FakeShell(fail=("xargs -r docker volume rm",)))
        self.assertFalse(failed["ok"])
        self.assertEqual(self.records()[0]["sandbox-host-hub-demo-sandbox"].state, "reaping")
        from sandbox.hosting.lifetime import reaper
        later = reaper.reap(self.home, now=NOW + 60, run_id="run2", confirm=True, runner=FakeShell())
        self.assertEqual([item["status"] for item in later["outcomes"]], ["reaped"])
        self.assertEqual(self.records()[0]["sandbox-host-hub-demo-sandbox"].state, "reaped")

    def test_reaping_record_made_permanent_by_hand_is_not_resumed(self):
        reap_run = {"run_id": "r1", "started_at": self.old, "reason": "expired"}
        data = stack_record(state="reaping", reap_run=reap_run, lifetime="permanent")
        data["updated_at"] = self.old
        _write_record(self.home, data)
        shell = FakeShell()
        result = self.reap(shell)
        self.assertEqual(shell.commands, [])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(self.records()[0]["sandbox-host-hub-demo-sandbox"].state, "reaping")

    def test_failed_inventory_removes_nothing(self):
        _write_record(self.home, self.expired())
        shell = FakeShell(fail=("echo done=1",))
        result = self.reap(shell)
        self.assertFalse(result["ok"])
        self.assertEqual(shell.kinds(), ["inventory"])

    def test_record_changed_since_plan_is_skipped(self):
        from sandbox.hosting.lifetime import reaper
        _write_record(self.home, stack_record())
        row = {"compose_project": "sandbox-host-hub-demo-sandbox", "reason": "expired"}
        shell = FakeShell()
        with unittest.mock.patch.object(reaper, "_candidates", return_value=([row], [])):
            result = self.reap(shell)
        self.assertEqual(result["outcomes"][0]["reason"], "changed_since_plan")
        self.assertEqual(shell.commands, [])

    def test_plan_reads_only(self):
        _write_record(self.home, self.expired())
        from sandbox.hosting.lifetime import reaper
        shell = FakeShell()
        result = reaper.reap(self.home, now=NOW, run_id="run1", confirm=False, runner=shell)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(shell.commands, [])
        self.assertFalse((self.home / "runtime" / "resources" / "deletions").exists())

    def test_claim_of_a_reaping_record_is_refused(self):
        _write_record(self.home, self.expired())
        self.reap(FakeShell(fail=("xargs -r docker volume rm",)))
        response = hs.handle(_request("claim", record=stack_record(ttl=86400), new_ttl=True), str(self.home),
                             free_paths=())
        self.assertEqual(response["code"], hs.REAPING)


class RoutinePassTests(unittest.TestCase):
    def test_routine_reaps_hosted_targets_and_counts_them(self):
        from tests.test_cleanup_routine_run import RunCase
        from sandbox.hosting.lifetime import reaper

        class Case(RunCase):
            def runTest(self):
                pass
        case = Case()
        case.setUp()
        try:
            data = stack_record(expires=hs.to_rfc3339(int(__import__("time").time()) - 60))
            data["updated_at"] = hs.to_rfc3339(int(__import__("time").time()) - 3 * 86400)
            _write_record(case.home, data)
            with unittest.mock.patch.object(reaper, "local_runner", FakeShell()):
                payload = case.run_once()
            record = payload["run"]
            self.assertGreaterEqual(record["removed"], 1)
            self.assertEqual(record["hosted_targets"]["outcomes"][0]["status"], "reaped")
            records, _invalid = hs.Store(case.home).load()
            self.assertEqual(records["sandbox-host-hub-demo-sandbox"].state, "reaped")
        finally:
            case.doCleanups()

    def test_hosted_pass_skips_when_the_guard_is_held(self):
        from sandbox.resources.cleanup_routine import run as routine_run
        from sandbox.resources.host_guard import try_reclaim_guard
        home = Path(tempfile.mkdtemp(prefix="sb-routine-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        (home / "runtime").mkdir()
        with try_reclaim_guard(runtime=home / "runtime") as acquired:
            self.assertTrue(acquired)
            result = routine_run._hosted_pass(home / "runtime", "run1", 600)
        self.assertEqual(result["status"], "skipped_busy")


class NamingTests(unittest.TestCase):
    def test_normalize_repo(self):
        from sandbox.hosting.lifetime import naming
        for url in ("git@github.com:Org/Repo.git", "https://user:secret@github.com/org/repo.git",
                    "ssh://git@github.com:22/org/repo", "https://github.com/org/repo/"):
            with self.subTest(url=url):
                self.assertEqual(naming.normalize_repo(url), "github.com/org/repo")

    def test_generate_slug_counts_and_fits_the_pattern(self):
        from sandbox.hosting.lifetime import naming
        import re as _re
        pattern = _re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
        accepts = lambda value: bool(pattern.fullmatch(value))
        self.assertEqual(naming.generate_slug("stack/demo", COMMIT, "alice", set(), accepts), "demo-alice-1")
        self.assertEqual(naming.generate_slug("stack/demo", COMMIT, "alice", {"demo-alice-1"}, accepts),
                         "demo-alice-2")
        self.assertEqual(naming.generate_slug("", COMMIT, "Alice Example", set(), accepts), "cccccccc-alice-ex-1")
        long_branch = "feature/" + "x" * 40
        slug = naming.generate_slug(long_branch, COMMIT, "Someone With A Long Name", set(), accepts)
        self.assertTrue(accepts(slug), slug)
        short = _re.compile(r"^[a-z]{1,4}-[0-9a-f]{4}$")
        slug = naming.generate_slug("stack/demo", COMMIT, "alice", set(),
                                    lambda value: bool(short.fullmatch(value)), token=lambda: "beef")
        self.assertEqual(slug, "demo-beef")

    def test_owner_label_requires_a_value(self):
        from sandbox.core._hosting import HostingRefusal
        from sandbox.hosting.lifetime import naming
        with unittest.mock.patch.object(naming, "_git", return_value=""):
            with self.assertRaises(HostingRefusal):
                naming.owner_label(Path("."), None)
            self.assertEqual(naming.owner_label(Path("."), "  Alice   Example "), "Alice Example")
            with self.assertRaises(HostingRefusal) as raised:
                naming.owner_label(Path("."), "alice@example.test")
            self.assertEqual(raised.exception.code, "stack_owner_invalid")
        with unittest.mock.patch.object(naming, "_git", return_value="Alice Example"):
            self.assertEqual(naming.owner_label(Path("."), None), "Alice Example")


class MergeSweepTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sb-merge-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(self.root)}
        origin = self.root / "origin"
        self.git(self.root, "init", "--quiet", "--bare", "--initial-branch=dev", str(origin))
        self.work = self.root / "work"
        self.git(self.root, "clone", "--quiet", str(origin), str(self.work))
        (self.work / "a").write_text("a")
        self.git(self.work, "add", "a")
        self.git(self.work, "commit", "--quiet", "-m", "a")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:dev")
        self.git(self.work, "checkout", "--quiet", "-b", "feature")
        (self.work / "b").write_text("b")
        self.git(self.work, "add", "b")
        self.git(self.work, "commit", "--quiet", "-m", "b")
        self.feature = self.git(self.work, "rev-parse", "HEAD")
        self.calls = []

    def git(self, cwd, *args):
        result = run_test_process(["git", "-C", str(cwd), *args], env=self.env, capture_output=True,
                                  text=True, check=True, timeout=60)
        return result.stdout.strip()

    def runner(self, checkout, *args, timeout=120):
        return run_test_process(["git", "-C", str(checkout), *args], env=self.env, capture_output=True,
                                text=True, check=False, timeout=timeout)

    def record(self, commit):
        return {"kind": "stack", "lifetime": "temporary", "state": "active",
                "compose_project": "sandbox-host-hub-demo-sandbox",
                "slug": "demo", "repo": "github.com/org/repo", "dev_branch": "dev", "commit": commit}

    def sweep(self, commit, confirm=True):
        from sandbox.hosting.lifetime import merge
        store = unittest.mock.Mock()
        store.call.return_value = {"recorded": True}
        result = merge.sweep(store, remote_records=[self.record(commit)],
                             checkouts={"github.com/org/repo": self.work}, confirm=confirm, git=self.runner)
        return result, store

    def test_unmerged_branch_records_nothing(self):
        result, store = self.sweep(self.feature)
        self.assertIs(result["checked"][0]["merged"], False)
        store.call.assert_not_called()

    def test_merged_branch_records_evidence(self):
        self.git(self.work, "push", "--quiet", "origin", "feature:dev")
        result, store = self.sweep(self.feature)
        self.assertIs(result["checked"][0]["merged"], True)
        self.assertEqual(result["recorded"], 1)
        self.assertEqual(store.call.call_args.kwargs["commit"], self.feature)

    def test_plan_mode_does_not_record(self):
        self.git(self.work, "push", "--quiet", "origin", "feature:dev")
        result, store = self.sweep(self.feature, confirm=False)
        self.assertIs(result["checked"][0]["merged"], True)
        store.call.assert_not_called()

    def test_unknown_commit_is_not_merged(self):
        result, _store = self.sweep("f" * 40)
        self.assertIs(result["checked"][0]["merged"], False)

    def test_no_checkout_and_fetch_failure(self):
        from sandbox.hosting.lifetime import merge
        store = unittest.mock.Mock()
        result = merge.sweep(store, remote_records=[self.record(self.feature)], checkouts={}, confirm=True)
        self.assertEqual(result["checked"][0]["reason"], "no_checkout")
        self.git(self.work, "remote", "set-url", "origin", str(self.root / "missing"))
        result = merge.sweep(store, remote_records=[self.record(self.feature)],
                             checkouts={"github.com/org/repo": self.work}, confirm=True, git=self.runner)
        self.assertEqual(result["checked"][0]["reason"], "dev_unavailable")
        store.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()

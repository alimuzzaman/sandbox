"""Spec 077 lifetimes in the `sb web` panel (spec 076 follow-up): read-only and protected."""
from __future__ import annotations

import json
import base64
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sandbox.hosting import panel_lifetime as pl
from sandbox.hosting.lifetime import hoststore as hs
from tests.test_target_lifetime import NOW, OWNER_A, OWNER_B, stack_record

HOUR = 3600


def target_record(project="marketing", environment="production", *, lifetime="permanent"):
    return {
        "schema_version": 1, "compose_project": hs.compose_project_for(project, environment),
        "project": project, "environment": environment, "kind": "target",
        "lifetime": lifetime, "lifetime_source": "classified", "ttl_seconds": None, "expires_at": None,
        "extended_count": 0, "state": "active", "owner": dict(OWNER_A),
        "created_at": hs.to_rfc3339(NOW), "updated_at": hs.to_rfc3339(NOW),
        "classified_by": dict(OWNER_A), "classified_at": hs.to_rfc3339(NOW),
        "front_door": "nginx", "dns_mode": "exact", "hostnames": ["www.example.test"],
        "slug": None, "copy_id": None, "repo": None, "ref": None, "ref_branch": None,
        "commit": None, "dev_branch": None, "canonical": False, "template": None, "ports": {},
        "merge": None, "reap_run": None, "overrides": [],
    }


def write(root: Path, record: dict, name: str | None = None) -> Path:
    path = root / "runtime" / "target-lifetimes" / (name or record["compose_project"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def snapshot_tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        out[str(path.relative_to(root))] = (stat.st_mode, stat.st_size, stat.st_mtime_ns)
    return out


class ProjectionTest(unittest.TestCase):
    def test_temporary_copy_fields(self):
        view = pl.project(stack_record(expires=hs.to_rfc3339(NOW + 2 * HOUR)), NOW)
        self.assertEqual(view["classification"], "recorded")
        self.assertEqual(view["lifetime"], "temporary")
        self.assertEqual(view["owner"], "alice")
        self.assertEqual(view["expires_at"], NOW + 2 * HOUR)
        self.assertEqual(view["seconds_left"], 2 * HOUR)
        self.assertFalse(view["reap_eligible"])
        self.assertIsNone(view["reap_reason"])
        self.assertEqual(view["state"], "active")
        self.assertEqual(view["extended_count"], 0)
        self.assertEqual((view["kind"], view["slug"], view["copy_id"], view["ref_branch"]),
                         ("stack", "demo", "0000000a", "stack/demo"))
        self.assertFalse(view["canonical"])

    def test_expired_copy_is_reapable_now(self):
        view = pl.project(stack_record(expires=hs.to_rfc3339(NOW - HOUR)), NOW)
        self.assertTrue(view["reap_eligible"])
        self.assertEqual(view["reap_reason"], "expired")
        self.assertEqual(view["seconds_left"], -HOUR)

    def test_eligibility_is_recomputed_at_read_time(self):
        # A row computed at an earlier clock says "not eligible"; the panel's now decides.
        row = stack_record(expires=hs.to_rfc3339(NOW + HOUR))
        row["reap"] = {"eligible": False, "reason": None, "merged_into": None, "merged_at": None}
        self.assertTrue(pl.project(row, NOW + 2 * HOUR)["reap_eligible"])
        row["reap"] = {"eligible": True, "reason": "expired", "merged_into": None, "merged_at": None}
        self.assertFalse(pl.project(row, NOW)["reap_eligible"])

    def test_hosted_canonical_and_permanent_are_never_reapable(self):
        hosted = pl.project(stack_record(lifetime="hosted", ref_branch="dev"), NOW + 400 * 86400)
        self.assertEqual((hosted["lifetime"], hosted["canonical"], hosted["reap_eligible"]), ("hosted", True, False))
        self.assertIsNone(hosted["expires_at"])
        permanent = pl.project(target_record(), NOW)
        self.assertEqual((permanent["lifetime"], permanent["kind"], permanent["reap_eligible"]),
                         ("permanent", "target", False))

    def test_reaped_state_is_not_reapable(self):
        row = stack_record(expires=hs.to_rfc3339(NOW - HOUR), state="reaped", reap_run={
            "run_id": "r1", "started_at": hs.to_rfc3339(NOW), "reason": "expired",
            "finished_at": hs.to_rfc3339(NOW), "dns_left": False})
        view = pl.project(row, NOW)
        self.assertEqual((view["state"], view["reap_eligible"]), ("reaped", False))

    def test_invalid_rows_are_unclassified_and_protected(self):
        expired = hs.to_rfc3339(NOW - HOUR)
        broken = [
            None, [], "x", {}, {"schema_version": 2},
            stack_record(expires=expired, lifetime="forever"),
            stack_record(expires=expired, state="gone"),
            stack_record(expires="yesterday"),
            {**stack_record(expires=expired), "compose_project": "sandbox-host-other-x"},
            {**stack_record(expires=expired), "owner": {"label": "", "controller": "a" * 32}},
        ]
        for row in broken:
            with self.subTest(row=str(row)[:60]):
                view = pl.project(row, NOW)
                self.assertEqual((view["classification"], view["reason"]), ("unclassified", "invalid"))
                self.assertFalse(view["reap_eligible"])
                self.assertIsNone(view["lifetime"])

    def test_projection_is_an_allow_list(self):
        row = stack_record(expires=hs.to_rfc3339(NOW + HOUR), secret_token="planted-secret-1234")
        view = pl.project(row, NOW)
        self.assertNotIn("planted-secret-1234", json.dumps(view))
        self.assertEqual(set(view), set(pl.LifetimeView.__annotations__))

    def test_lookup_states(self):
        listing = pl.listing({"sandbox-host-a-b": pl.project(target_record("a", "b"), NOW)})
        self.assertEqual(pl.lookup(listing, "sandbox-host-a-b")["lifetime"], "permanent")
        self.assertEqual(pl.lookup(listing, "sandbox-host-x-y")["reason"], "no_record")
        self.assertEqual(pl.lookup(None, "sandbox-host-a-b")["classification"], "unknown")
        self.assertEqual(pl.lookup(None, "sandbox-host-a-b")["reason"], "loading")
        down = pl.unavailable("lifetime_unavailable:r")
        self.assertEqual((pl.lookup(down, "sandbox-host-a-b")["classification"],
                          pl.lookup(down, "sandbox-host-a-b")["reason"]), ("unknown", "unavailable"))
        for view in (pl.lookup(None, "k"), pl.lookup(down, "k"), pl.lookup(listing, "k")):
            self.assertFalse(view["reap_eligible"])


class OrderTest(unittest.TestCase):
    def row(self, view, name, owner_project="p"):
        return {**view, "remote": "r", "source": "this-server", "project": owner_project,
                "environment": "e", "compose_project": f"sandbox-host-{owner_project}-e",
                "name": name, "card": True}

    def test_temporary_near_expiry_first_then_hosted_permanent_unknown_unclassified(self):
        soon = pl.project(stack_record("soon", expires=hs.to_rfc3339(NOW + HOUR)), NOW)
        later = pl.project(stack_record("later", expires=hs.to_rfc3339(NOW + 5 * HOUR)), NOW)
        expired = pl.project(stack_record("old", expires=hs.to_rfc3339(NOW - HOUR)), NOW)
        merged = pl.project(stack_record("merged", expires=hs.to_rfc3339(NOW + 9 * HOUR), merge={
            "merged": True, "commit": "c" * 40, "dev_branch": "dev", "dev_commit": "d" * 40,
            "checked_at": hs.to_rfc3339(NOW), "controller": "a" * 32}), NOW)
        hosted = pl.project(stack_record("main", lifetime="hosted", ref_branch="dev"), NOW)
        permanent = pl.project(target_record(), NOW)
        rows = [self.row(pl.unclassified("no_record"), "none"), self.row(permanent, "perm"),
                self.row(pl.unknown("loading"), "wait"), self.row(later, "later"),
                self.row(hosted, "main"), self.row(soon, "soon"), self.row(merged, "merged"),
                self.row(expired, "old")]
        self.assertEqual([row["name"] for row in pl.order(rows)],
                         ["old", "merged", "soon", "later", "main", "perm", "wait", "none"])

    def test_two_copies_of_one_branch_stay_separate_rows_by_owner(self):
        expires = hs.to_rfc3339(NOW + HOUR)
        alice = pl.project(stack_record("feat-alice-1", copy_id="0000000a", owner=OWNER_A,
                                        ref_branch="feature/x", project="hub-a", expires=expires), NOW)
        bob = pl.project(stack_record("feat-bob-1", copy_id="0000000b", owner=OWNER_B,
                                      ref_branch="feature/x", project="hub-b", expires=expires), NOW)
        rows = pl.order([self.row(bob, "feat-bob-1", "hub-b"), self.row(alice, "feat-alice-1", "hub-a")])
        self.assertEqual([(row["owner"], row["ref_branch"]) for row in rows],
                         [("alice", "feature/x"), ("bob", "feature/x")])


class ServerReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_reads_records_and_marks_bad_files_protected(self):
        good = stack_record(expires=hs.to_rfc3339(NOW - HOUR))
        write(self.home, good)
        write(self.home, target_record())
        bad = self.home / "runtime" / "target-lifetimes" / "sandbox-host-broken-x.json"
        bad.write_text("{\"schema_version\": 1, \"compose_pro", encoding="utf-8")  # partly written
        write(self.home, {**target_record("wrong", "name")}, name="sandbox-host-other-name.json")
        big = self.home / "runtime" / "target-lifetimes" / "sandbox-host-big-x.json"
        big.write_text(json.dumps({**target_record("big", "x"), "pad": "x" * (hs.PEEK_MAX_BYTES + 10)}))
        os.symlink(str(bad), str(self.home / "runtime" / "target-lifetimes" / "sandbox-host-link-x.json"))
        listing = pl.read_server(self.home / "runtime", NOW)
        self.assertTrue(listing["available"])
        records = listing["records"]
        self.assertEqual(records[good["compose_project"]]["reap_reason"], "expired")
        self.assertEqual(records["sandbox-host-marketing-production"]["lifetime"], "permanent")
        for key in ("sandbox-host-broken-x", "sandbox-host-other-name", "sandbox-host-big-x",
                    "sandbox-host-link-x"):
            with self.subTest(key=key):
                self.assertEqual((records[key]["classification"], records[key]["reason"]),
                                 ("unclassified", "invalid"))
                self.assertFalse(records[key]["reap_eligible"])
        self.assertTrue(any(reason.startswith("lifetime_record_invalid:") for reason in listing["partial_reasons"]))

    def test_read_is_lock_free_and_writes_nothing(self):
        write(self.home, stack_record(expires=hs.to_rfc3339(NOW + HOUR)))
        before = snapshot_tree(self.home)
        pl.read_server(self.home / "runtime", NOW)
        self.assertEqual(snapshot_tree(self.home), before)
        self.assertFalse((self.home / "runtime" / "target-lifetimes" / ".lock").exists())

    def test_missing_directory_is_empty_and_creates_nothing(self):
        listing = pl.read_server(self.home / "runtime", NOW)
        self.assertEqual((listing["available"], listing["records"]), (True, {}))
        self.assertFalse((self.home / "runtime").exists())

    def test_peek_is_bounded(self):
        for index in range(5):
            write(self.home, target_record(f"p{index}", "e"))
        result = hs.Store(self.home).peek(NOW, max_files=3)
        self.assertEqual(len(result["records"]) + len(result["invalid"]), 3)
        self.assertTrue(result["truncated"])
        self.assertIn("lifetime_records_truncated", pl.read_server(self.home / "runtime", NOW, max_files=3)["partial_reasons"])


class ControllerListTest(unittest.TestCase):
    def payload(self, **extra):
        stack = {**stack_record(expires=hs.to_rfc3339(NOW + HOUR)), "mine": True, "remote_record": "present",
                 "manifest": "/abs/path", "last_apply": None}
        stack.pop("overrides")
        local_only = {key: None for key in stack}
        local_only.update({"project": "hub-local", "environment": "sandbox", "kind": "stack",
                           "compose_project": "sandbox-host-hub-local-sandbox", "remote_record": "missing"})
        target = target_record()
        target.pop("overrides")
        data = {"ok": True, "action": "stack-list", "remote": "xspeed-dev", "stacks": [stack, local_only],
                "targets": [target], "unclassified": ["sandbox-host-old-production"],
                "invalid": [{"compose_project": "sandbox-host-bad-x", "reason": "kind is unknown"}],
                "policy": {"per_owner": 5, "total": 40, "min_free_bytes": 0}}
        data.update(extra)
        return data

    def test_stack_list_rows_are_parsed_by_the_store_rules(self):
        listing = pl.listing_from_stack_list(self.payload(), NOW)
        self.assertTrue(listing["available"])
        records = listing["records"]
        self.assertEqual(records["sandbox-host-hub-demo-sandbox"]["lifetime"], "temporary")
        self.assertEqual(records["sandbox-host-marketing-production"]["lifetime"], "permanent")
        self.assertEqual(records["sandbox-host-hub-local-sandbox"]["reason"], "no_record")
        self.assertEqual(records["sandbox-host-old-production"]["reason"], "no_record")
        self.assertEqual(records["sandbox-host-bad-x"]["reason"], "invalid")
        self.assertNotIn("/abs/path", json.dumps(listing))

    def test_remote_error_or_bad_payload_is_unavailable(self):
        for payload in (self.payload(remote_error={"code": "stack_record_unavailable", "error": "ssh: x"}),
                        {"ok": False, "code": "stack_remote_unknown"}, None, [], {"ok": True, "stacks": "x"}):
            with self.subTest(payload=str(payload)[:40]):
                listing = pl.listing_from_stack_list(payload, NOW)
                self.assertFalse(listing["available"])
                self.assertEqual(listing["records"], {})


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.jobs = []
        self.calls = []
        self.clock = [100.0]

    def fetch(self, remote):
        self.calls.append(remote)
        return {"ok": True, "stacks": [], "targets": [{**target_record(), "remote": remote}],
                "unclassified": [], "invalid": []}

    def cache(self, **kwargs):
        return pl.LifetimeCache(self.fetch, clock=lambda: self.clock[0],
                                submit=self.jobs.append, **kwargs)

    def test_first_read_is_loading_then_cached_and_refreshed_after_ttl(self):
        cache = self.cache(ttl=60)
        first = cache.snapshot(["r1"], NOW)
        self.assertIsNone(first["r1"])
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(cache.snapshot(["r1"], NOW), {"r1": None})
        self.assertEqual(len(self.jobs), 1)  # one fetch per remote in flight
        self.jobs.pop()()
        listing = cache.snapshot(["r1"], NOW)["r1"]
        self.assertEqual(listing["records"]["sandbox-host-marketing-production"]["lifetime"], "permanent")
        self.assertEqual(self.jobs, [])
        self.clock[0] += 61
        self.assertIsNotNone(cache.snapshot(["r1"], NOW)["r1"])  # stale value served while refreshing
        self.assertEqual(len(self.jobs), 1)

    def test_fetch_failure_is_unavailable(self):
        cache = pl.LifetimeCache(lambda remote: (_ for _ in ()).throw(OSError("down")),
                                 clock=lambda: self.clock[0], submit=self.jobs.append)
        cache.snapshot(["r1"], NOW)
        self.jobs.pop()()
        listing = cache.snapshot(["r1"], NOW)["r1"]
        self.assertFalse(listing["available"])
        self.assertEqual(listing["partial_reasons"], ["lifetime_unavailable:r1"])

    def test_remotes_are_bounded(self):
        cache = self.cache(max_remotes=2)
        result = cache.snapshot([f"r{index}" for index in range(5)], NOW)
        self.assertEqual(sorted(result), ["r0", "r1"])
        self.assertEqual(len(self.jobs), 2)

    def test_results_for_remotes_no_longer_asked_for_are_dropped(self):
        cache = self.cache(max_remotes=2)
        for batch in (["r0", "r1"], ["r2", "r3"], ["r4", "r5"]):
            cache.snapshot(batch, NOW)
            while self.jobs:
                self.jobs.pop()()
        cache.snapshot(["r4", "r5"], NOW)
        self.assertLessEqual(len(cache._results), 4)
        self.assertEqual(sorted(cache.snapshot(["r4", "r5"], NOW)), ["r4", "r5"])

    def test_fetches_in_flight_are_capped_across_snapshots(self):
        cache = self.cache(max_remotes=2)
        cache.snapshot(["r0", "r1"], NOW)
        cache.snapshot(["r2", "r3"], NOW)  # earlier fetches have not finished
        self.assertEqual(len(self.jobs), 2)
        while self.jobs:
            self.jobs.pop()()
        cache.snapshot(["r2", "r3"], NOW)
        self.assertEqual(len(self.jobs), 2)

    def test_remote_names_are_validated(self):
        result = self.cache().snapshot(["ok-remote", "bad name", "-x", "../y"], NOW)
        self.assertEqual(list(result), ["ok-remote"])

    def test_fetcher_runs_the_stack_list_command(self):
        seen = {}

        def run(argv, **kwargs):
            seen["argv"], seen["kwargs"] = argv, kwargs

            class Done:
                returncode = 0
                stdout = "noise\n" + json.dumps({"ok": True, "stacks": [], "targets": []}) + "\n"
            return Done()
        fetch = pl.stack_list_fetcher(Path("/checkout/sb"), run=run)
        self.assertEqual(fetch("xspeed-dev")["ok"], True)
        self.assertEqual(seen["argv"][:5], ["/checkout/sb", "host", "stack", "list", "--request-id"])
        self.assertRegex(seen["argv"][5], r"^panel-lifetime-[0-9a-f]{32}$")
        self.assertEqual(seen["argv"][6:], ["--remote", "xspeed-dev", "--json"])
        self.assertEqual(seen["kwargs"]["env"]["SANDBOX_AUTO_UPDATE"], "0")
        self.assertGreater(seen["kwargs"]["timeout"], 0)
        self.assertLessEqual(seen["kwargs"]["timeout"], pl.FETCH_TIMEOUT_SECONDS)


class AcceptedJobFetcherTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.job = "a" * 32
        self.request = None
        self.running = True
        self.bad_digest = False
        self.wrong_job = False
        self.payload = {"ok": True, "stacks": [], "targets": [], "note": "বাংলা"}
        self.body = json.dumps(self.payload, ensure_ascii=False).encode()
        # Deliberately split a multibyte character across retained pages.
        split = self.body.index("ব".encode()) + 1
        self.parts = (self.body[:split], self.body[split:])

    def run_command(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertGreater(kwargs["timeout"], 0)
        if argv[1] == "host":
            self.request = argv[argv.index("--request-id") + 1]
            value = {"ok": True, "status": "accepted", "job_id": self.job, "request_id": self.request}
        elif argv[1] == "job-status":
            self.assertEqual(argv[2], self.job)
            value = {"ok": True, "job_id": "b" * 32 if self.wrong_job else self.job,
                     "request_id": self.request, "lifecycle": "running" if self.running else "succeeded",
                     "exit_code": 0, "output_completeness": "complete", "output": [{
                         "stream": "stdout", "available": True, "complete": 1,
                         "bytes_stored": len(self.body), "sha256": "0" * 64 if self.bad_digest
                         else hashlib.sha256(self.body).hexdigest()}]}
        else:
            self.assertEqual(argv[1:3], ["job-output", self.job])
            self.assertEqual(argv[argv.index("--encoding") + 1], "base64")
            second = "--cursor" in argv
            if second:
                self.assertEqual(argv[argv.index("--cursor") + 1], "next-page")
            value = {"ok": True, "job_id": self.job, "stream": "stdout", "encoding": "base64",
                     "data": base64.b64encode(self.parts[int(second)]).decode(),
                     "has_more": not second, "cursor": "next-page"}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(value), stderr="")

    def test_refresh_observes_original_job_and_reassembles_exact_inventory(self):
        fetch = pl.stack_list_fetcher(Path("/checkout/sb"), run=self.run_command)
        self.assertIsNone(fetch("joined"))
        self.running = False
        self.assertEqual(fetch("joined"), self.payload)
        self.assertEqual([c[1] for c in self.calls],
                         ["host", "job-status", "job-status", "job-output", "job-output"])

    def test_wrong_job_and_bad_output_never_resubmit(self):
        fetch = pl.stack_list_fetcher(Path("/checkout/sb"), run=self.run_command)
        self.assertIsNone(fetch("joined"))
        self.running = False
        self.wrong_job = True
        self.assertIsNone(fetch("joined"))
        self.wrong_job = False
        self.bad_digest = True
        self.assertIsNone(fetch("joined"))
        self.bad_digest = False
        self.assertEqual(fetch("joined"), self.payload)
        self.assertEqual(sum(c[1] == "host" for c in self.calls), 1)

    def test_unknown_submission_is_not_repeated_on_refresh(self):
        calls = []
        def timeout(argv, **kwargs):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 1)
        fetch = pl.stack_list_fetcher(Path("/checkout/sb"), run=timeout)
        with self.assertRaises(subprocess.TimeoutExpired):
            fetch("joined")
        self.assertIsNone(fetch("joined"))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

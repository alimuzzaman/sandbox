"""Tiered reclamation: planning, execution, manifest, resume, idempotency.

The probe tests run the real shipped program in a subprocess against a
temporary SANDBOX_HOME, so the host-side protections are exercised as shipped
rather than re-implemented in a mock.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

from sandbox.resources import reclaim
from sandbox.resources.context import PlanStore
from sandbox.resources.models import CleanupPlan, StorageTarget
from sandbox.resources.reclaim_service import ReclaimService, _RemoteReclaimProvider
from sandbox.resources.remote import LocalProbeAdapter, RemoteResourceAdapter, _program
from sandbox.services.process import ProcessResult
from tests.subprocess_support import run_test_process, synthetic_environment


DAY = 86400
TARGET = StorageTarget("remote", "fixture", "a" * 24)


def entry(name, **overrides):
    base = {
        "name": name,
        "path": f"/deploy/{name}",
        "size_bytes": 1024,
        "size_state": "measured",
        "mtime": time.time() - 11 * DAY,
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


class FakeProvider:
    """Records what the service asked the host to do; changes nothing."""

    def __init__(self, block=None, capacity=None, outcomes=None):
        self.block = block if block is not None else {
            "deployment_root": "/deploy",
            "runtime_root": "/runtime",
            "entries": [entry("a-workspace-1"), entry("live-workspace-2",
                                                      mtime=time.time())],
            "volumes": [{"name": "lenzora-postgres-data", "size_bytes": 99,
                         "mounted_running": False}],
            "scratch": [],
            "leases": {},
            "hosted_sites": [],
            "index_names": [],
            "workspace_ids": {},
            "status": "complete",
            "truncated": False,
            "unmeasured_count": 0,
        }
        self.capacity = capacity or {
            "total_bytes": 100, "used_bytes": 90, "available_bytes": 10,
            "reserved_bytes": 0,
        }
        self.outcomes = outcomes
        self.calls = []
        self.leases = {}

    def target(self):
        return TARGET

    def inventory(self, *, budget_seconds, directory_cache):
        self.calls.append(("inventory", budget_seconds, directory_cache))
        return {"capacity": self.capacity, "reclaim": self.block}

    def reclaim(self, candidates, *, run_id, trigger="manual",
                workspace_ids=None, budget_seconds=900):
        self.calls.append(("reclaim", run_id, trigger, list(candidates)))
        outcomes = self.outcomes if self.outcomes is not None else [
            {"seq": item["seq"], "locator": item["locator"],
             "status": "removed", "reason": "removed", "bytes": item["bytes"],
             "elevated": False, "verified_absent": True}
            for item in candidates
        ]
        return {
            "ok": True, "run_id": run_id,
            "manifest_path": f"/runtime/resources/deletions/{run_id}.jsonl",
            "outcomes": outcomes,
            "reconciled": {"registry_removed": 0, "index_removed": 0,
                           "index_pending": 0, "leases_removed": 0,
                           "status": "complete"},
            "capacity_before": self.capacity,
            "capacity_after": self.capacity,
            "budget_exhausted": False,
        }

    def lease(self, op, *, name=None, expires_at=None):
        self.calls.append(("lease", op, name, expires_at))
        if op == "release":
            self.leases[name] = {"name": name, "released": True}
        elif op == "set":
            self.leases[name] = {"name": name, "expires_at": expires_at,
                                 "released": False}
        return {"ok": True, "op": op, "leases": dict(self.leases)}


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = PlanStore(self.root / "plans")

    def service(self, provider):
        return ReclaimService(provider, self.store, target=TARGET)


class TestPlanning(ServiceCase):
    def test_retained_terminal_guidance_survives_plan_and_reap(self):
        job_id = "a" * 16
        item = entry("p-workspace-12345678", retained_terminal=True,
                     retained_terminal_jobs={"remote": "fixture", "job_ids": [job_id]})
        provider = FakeProvider()
        provider.block["entries"] = [item]
        service = self.service(provider)
        for payload in (service.plan("all"), service.reap(dry_run=True)):
            self.assertTrue(payload["ok"])
            skipped = payload["data"]["skipped"]
            row = next(row for row in skipped if row["kind"] == "worktree")
            self.assertEqual(row["reason"], "retained_terminal")
            self.assertEqual(row["remedy"]["commands"], [
                f"./sb job-cleanup {job_id} --remote fixture --confirm"])
        self.assertFalse(any(call[0] == "reclaim" for call in provider.calls))

    def test_remote_reclaim_inventory_does_not_request_deep_attribution(self):
        class RecordingAdapter:
            def __init__(self):
                self.calls = []

            def observe(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(capacity={"total_bytes": 1}, reclaim={"entries": []})

        adapter = RecordingAdapter()
        payload = _RemoteReclaimProvider(adapter).inventory(
            budget_seconds=12, directory_cache="cache_only",
        )

        self.assertEqual(payload["reclaim"], {"entries": []})
        self.assertEqual(len(adapter.calls), 1)
        self.assertFalse(adapter.calls[0]["deep"])
        self.assertFalse(adapter.calls[0]["thorough"])
        self.assertEqual(adapter.calls[0]["directory_cache"], "cache_only")

    def test_plan_has_no_side_effects_on_the_host(self):
        provider = FakeProvider()
        payload = self.service(provider).plan("safe")
        self.assertTrue(payload["ok"])
        self.assertEqual(
            [call[0] for call in provider.calls], ["inventory"])

    def test_plan_names_path_size_mtime_class_and_reason(self):
        payload = self.service(FakeProvider()).plan("safe")
        candidate = payload["data"]["candidates"][0]
        for key in ("locator", "bytes", "modified_at", "class", "tier",
                    "reason"):
            self.assertIn(key, candidate)
        self.assertEqual(candidate["reason"], "orphan_workspace")

    def test_plan_lists_what_it_skipped_and_why(self):
        payload = self.service(FakeProvider()).plan("safe")
        reasons = {item["reason"] for item in payload["data"]["skipped"]}
        self.assertIn("volume_not_workspace_scoped", reasons)
        self.assertIn("recent_activity", reasons)

    def test_plan_reports_every_tier_total(self):
        payload = self.service(FakeProvider()).plan("safe")
        self.assertEqual(
            set(payload["data"]["tier_totals"]), {"safe", "tmp", "all"})

    def test_plan_is_stored_and_reloadable_by_id(self):
        service = self.service(FakeProvider())
        plan_id = service.plan("safe")["data"]["plan_id"]
        stored = self.store.load(plan_id)
        self.assertEqual(stored.scope, "safe")
        self.assertTrue(stored.metadata["candidates"])

    def test_unknown_tier_is_refused(self):
        payload = self.service(FakeProvider()).plan("everything")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_tier")

    def test_missing_host_evidence_is_reported_not_guessed(self):
        provider = FakeProvider(block=None)
        provider.block = None
        payload = self.service(provider).plan("safe")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"],
                         "reclaim_inventory_unavailable")

    def test_partial_inventory_is_surfaced_in_the_plan(self):
        provider = FakeProvider()
        provider.block["truncated"] = True
        provider.block["unmeasured_count"] = 7
        payload = self.service(provider).plan("safe")
        self.assertTrue(payload["data"]["truncated"])
        self.assertEqual(payload["data"]["unmeasured_count"], 7)


class TestExecution(ServiceCase):
    def test_cleanup_refuses_without_confirmation(self):
        payload = self.service(FakeProvider()).cleanup(tier="safe")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "confirmation_required")

    def test_cleanup_requires_a_tier_or_a_plan(self):
        payload = self.service(FakeProvider()).cleanup(confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_tier")

    def test_cleanup_rejects_a_resource_scope_plan_with_typed_kind_error(self):
        plan = CleanupPlan.create(
            TARGET, "cache", (), (), now=datetime.now(timezone.utc),
        )
        self.store.save(plan)

        payload = self.service(FakeProvider()).cleanup(
            plan_id=plan.plan_id, confirm=True,
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "plan_kind_mismatch")

    def test_cleanup_executes_the_reviewed_candidate_set(self):
        provider = FakeProvider()
        service = self.service(provider)
        plan_id = service.plan("safe")["data"]["plan_id"]
        payload = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")
        sent = next(call for call in provider.calls if call[0] == "reclaim")[3]
        self.assertEqual([item["locator"] for item in sent],
                         ["/deploy/a-workspace-1"])
        self.assertIn("expected_mtime", sent[0])

    def test_remote_plan_cleanup_resolves_authoritative_target_before_begin(self):
        fallback = StorageTarget("remote", "fixture", "b" * 24)

        class RemoteLike(FakeProvider):
            def target(self):
                return fallback

            def authoritative_target(self):
                return TARGET

        provider = RemoteLike()
        service = ReclaimService(provider, self.store)
        plan_id = service.plan("safe")["data"]["plan_id"]
        payload = ReclaimService(provider, self.store).cleanup(
            plan_id=plan_id, confirm=True,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")

    def test_partial_removal_is_never_counted_as_reclaimed(self):
        provider = FakeProvider(outcomes=[{
            "seq": 1, "locator": "/deploy/a-workspace-1", "status": "failed",
            "reason": "partial_removal_detected", "bytes": 0,
            "elevated": True, "verified_absent": False,
        }])
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["observed_reclaimed_bytes"], 0)
        self.assertEqual(payload["data"]["outcomes"][0]["status"], "failed")

    def test_a_second_run_of_the_same_tier_is_a_no_op(self):
        provider = FakeProvider(outcomes=[{
            "seq": 1, "locator": "/deploy/a-workspace-1",
            "status": "already_absent", "reason": "already_absent",
            "bytes": 0, "elevated": False, "verified_absent": True,
        }])
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["observed_reclaimed_bytes"], 0)

    def test_an_interrupted_plan_is_resumable(self):
        provider = FakeProvider()
        service = self.service(provider)
        plan_id = service.plan("safe")["data"]["plan_id"]
        self.store.begin(plan_id, TARGET)          # simulate a killed run
        payload = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["resumed"])

    def test_a_completed_plan_cannot_be_replayed(self):
        provider = FakeProvider()
        service = self.service(provider)
        plan_id = service.plan("safe")["data"]["plan_id"]
        service.cleanup(plan_id=plan_id, confirm=True)
        again = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"]["code"], "plan_already_used")

    def test_a_refused_host_run_is_indeterminate_not_success(self):
        class Refusing(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                return {"ok": False, "reason": "manifest_unavailable"}

        payload = self.service(Refusing()).cleanup(tier="safe", confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "reclaim_refused")

    def test_an_exhausted_budget_is_reported_as_indeterminate(self):
        class Bounded(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                payload = FakeProvider.reclaim(self, candidates, **kwargs)
                payload["budget_exhausted"] = True
                return payload

        payload = self.service(Bounded()).cleanup(tier="safe", confirm=True)
        self.assertEqual(payload["status"], "indeterminate")
        self.assertTrue(payload["data"]["budget_exhausted"])

    def test_run_receipt_is_recorded(self):
        provider = FakeProvider()
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        receipts = list((self.root / "plans").glob("*.run.json"))
        self.assertEqual(len(receipts), 1)
        record = json.loads(receipts[0].read_text())
        self.assertEqual(record["run_id"], payload["data"]["run_id"])


class TestRetention(ServiceCase):
    def test_release_marks_the_workspace(self):
        provider = FakeProvider()
        payload = self.service(provider).release("a-workspace-1")
        self.assertTrue(payload["ok"])
        self.assertTrue(provider.leases["a-workspace-1"]["released"])

    def test_release_refuses_a_path_bearing_name(self):
        provider = FakeProvider()
        payload = self.service(provider).release("../etc")
        self.assertFalse(payload["ok"])
        self.assertEqual(provider.calls, [])

    def test_ttl_sets_an_expiry(self):
        provider = FakeProvider()
        payload = self.service(provider).set_ttl("a-workspace-1", "14d")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["ttl_seconds"], 14 * DAY)
        self.assertIsNotNone(provider.leases["a-workspace-1"]["expires_at"])

    def test_ttl_refuses_an_invalid_duration(self):
        provider = FakeProvider()
        payload = self.service(provider).set_ttl("a-workspace-1", "soon")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_duration")

    def test_reap_dry_run_changes_nothing(self):
        provider = FakeProvider()
        payload = self.service(provider).reap(dry_run=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["dry_run"])
        self.assertEqual([call[0] for call in provider.calls], ["inventory"])

    def test_reap_without_confirmation_is_refused(self):
        payload = self.service(FakeProvider()).reap(dry_run=False)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "confirmation_required")

    def test_reap_name_globs_drop_candidates_and_report_them(self):
        provider = FakeProvider()
        everything = self.service(provider).reap(dry_run=True)["data"]["candidates"]
        self.assertTrue(everything)
        name = everything[0]["display_name"]
        payload = self.service(FakeProvider()).reap(dry_run=True, exclude_names=(name[:3] + "*",))
        kept = {item["display_name"] for item in payload["data"]["candidates"]}
        self.assertFalse(any(item.startswith(name[:3]) for item in kept))
        skipped = [item for item in payload["data"].get("skipped", ())
                   if item.get("reason") == "excluded_by_request"]
        self.assertTrue(any(item["display_name"] == name for item in skipped))

    def test_reap_name_glob_also_keeps_the_workspace_package_volume(self):
        provider = FakeProvider()
        provider.block["volumes"] = [{
            "name": "sandbox-a-workspace-1_node-modules", "size_bytes": 64,
            "mounted_running": False,
        }]
        everything = self.service(provider).reap(dry_run=True)["data"]["candidates"]
        self.assertIn("sandbox-a-workspace-1_node-modules",
                      {item["locator"] for item in everything})
        payload = self.service(provider).reap(dry_run=True, exclude_names=("a-work*",))
        self.assertEqual(payload["data"]["candidates"], [])
        skipped = {item["locator"] for item in payload["data"]["skipped"]
                   if item.get("reason") == "excluded_by_request"}
        self.assertIn("sandbox-a-workspace-1_node-modules", skipped)

    def test_reap_excludes_disposable_scratch(self):
        provider = FakeProvider()
        provider.block["scratch"] = [{
            "name": ".drive-volume-fallbacks-1",
            "path": "/runtime/.drive-volume-fallbacks-1",
            "size_bytes": 512, "mtime": time.time() - 30 * DAY,
        }]
        payload = self.service(provider).reap(dry_run=True)
        kinds = {item["kind"] for item in payload["data"]["candidates"]}
        self.assertNotIn("runtime", kinds)


# Execute the shipped program, but replace only its external boundaries. The
# child has no project import path and cannot spawn any command, including an
# absolute executable. Job rows are typed API responses, not a real registry.
_PROBE_CHILD = r"""
import enum
import json
import os
import socket
import subprocess
import sys
import types

fixture = json.loads(sys.stdin.read())
for name in ("sandbox", "sandbox.jobs", "sandbox.jobs.registry", "sandbox.jobs.models"):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
registry = sys.modules["sandbox.jobs.registry"]
def read_index(_path):
    if fixture["index_error"]:
        raise OSError("synthetic unavailable index")
    return fixture["index"]
registry.read_resource_index = read_index
class Lifecycle(enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
sys.modules["sandbox.jobs.models"].TERMINAL_LIFECYCLES = set(Lifecycle)
def deny_spawn(*args, **kwargs):
    raise AssertionError("external process denied")
subprocess.Popen = deny_spawn
for name in ("system", "popen", "execl", "execle", "execlp", "execlpe",
             "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle",
             "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe"):
    if hasattr(os, name):
        setattr(os, name, deny_spawn)
socket.socket.connect = deny_spawn
socket.socket.connect_ex = deny_spawn
socket.create_connection = deny_spawn
source, tail = fixture["program"].split("\nACTIONS = {", 1)
namespace = {}
exec(source, namespace)
def fake_run(argv, timeout):
    if argv == [str(namespace["SB"]), "job-list", "--limit", "50", "--json"]:
        return 0, json.dumps(fixture["legacy"]), ""
    if argv == ["docker", "volume", "ls", "-q"] and fixture["volumes"]:
        return 0, "\n".join(fixture["volumes"]), ""
    if argv[:3] == ["docker", "volume", "inspect"]:
        return 0, json.dumps([{"Name": name} for name in argv[3:]]), ""
    # Synthetic empty engine listings let the real teardown code continue
    # without contacting an engine. All other commands are denied.
    if argv in (["docker", "ps", "-aq"], ["docker", "volume", "ls", "-q"],
                ["docker", "network", "ls", "-q"], ["docker", "image", "ls", "-q"],
                ["docker", "buildx", "du", "--format=json"],
                ["docker", "ps", "-a", "--format", '{{.Label "com.docker.compose.project"}}'],
                ["docker", "volume", "ls", "--format", '{{.Label "com.docker.compose.project"}}']):
        return 0, "", ""
    if argv[:3] == ["du", "-sx", "-sk"] and len(argv) == 4:
        path = namespace["Path"](argv[3]).resolve()
        if path.is_relative_to(namespace["HOME"]):
            return 0, "1\t" + str(path), ""
    return 127, "", "synthetic command denied"
namespace["run"] = fake_run
namespace["load_workspace_projection"] = lambda: {"records": [], "counts": {}}
namespace["APPLY_TRANSACTION_LOCKS"] = ()
namespace["docker_storage_resources"] = lambda *_args: ([], [])
namespace["host_capacity_resources"] = lambda *_args: ([], [])
namespace["platform"].node = lambda: "synthetic-host"
original_stat = namespace["os"].stat
def isolated_stat(path, *args, **kwargs):
    return original_stat(str(namespace["HOME"]) if str(path) == "/" else path, *args, **kwargs)
namespace["os"].stat = isolated_stat
original_disk_usage = namespace["shutil"].disk_usage
namespace["shutil"].disk_usage = lambda _path: original_disk_usage(str(namespace["HOME"]))
exec("\nACTIONS = {" + tail, namespace)
"""


class IsolatedProbeRunner:
    def __init__(self, home):
        self.home = home
        self.index = {"jobs": [], "artifacts": []}
        self.index_error = False
        self.legacy = {"ok": True, "jobs": []}
        self.volumes = []

    def __call__(self, request, timeout):
        fixture = {"program": _program(request), "index": self.index,
                   "index_error": self.index_error, "legacy": self.legacy,
                   "volumes": self.volumes}
        # Override every compatibility key so no host paths, locale, or native
        # environment values reach the child. No shell or project imports.
        overrides = {key: "" for key in ("USERPROFILE", "LANG", "LC_ALL", "LC_CTYPE",
                     "TZ", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")}
        overrides.update({"SANDBOX_HOME": str(self.home), "HOME": str(self.home),
                          "TMPDIR": str(self.home), "PATH": str(self.home / "empty-bin")})
        env = synthetic_environment(overrides)
        completed = run_test_process([sys.executable, "-I", "-c", _PROBE_CHILD],
            input=json.dumps(fixture), text=True, capture_output=True,
            env=env, timeout=timeout)
        return ProcessResult(("isolated-probe",), completed.returncode,
                             completed.stdout, completed.stderr)


class ProbeCase(unittest.TestCase):
    """Exercise the shipped probe program itself, in a subprocess."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        (self.home / "runtime").mkdir(parents=True)
        (self.home / "deploy-src").mkdir(parents=True)
        self.runner = IsolatedProbeRunner(self.home)
        self.probe = LocalProbeAdapter(home=str(self.home), runner=self.runner)

    def workspace(self, name):
        path = self.home / "deploy-src" / name
        (path / "node_modules").mkdir(parents=True)
        (path / "node_modules" / "a.txt").write_text("x" * 32)
        return path

    def manifest_records(self, run_id):
        path = (self.home / "runtime" / "resources" / "deletions"
                / f"{run_id}.jsonl")
        return [json.loads(line) for line in
                path.read_text().splitlines() if line.strip()]

    def test_lease_round_trip(self):
        listing = self.probe.lease("list")
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["leases"], {})
        self.probe.lease("set", name="x-workspace-1",
                         expires_at="2030-01-01T00:00:00Z")
        self.probe.lease("release", name="x-workspace-1")
        listing = self.probe.lease("list")
        self.assertTrue(listing["leases"]["x-workspace-1"]["released"])
        stored = (self.home / "runtime" / "resources" / "leases"
                  / "x-workspace-1.json")
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)

    def test_lease_refuses_a_path_bearing_name(self):
        response = self.probe.lease("release", name="../../etc/passwd")
        self.assertFalse(response["ok"])
        self.assertEqual(response["reason"], "invalid_lease_name")

    def test_reclaim_writes_the_intent_before_the_outcome(self):
        path = self.workspace("gone-workspace-1")
        run_id = "b" * 32
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }], run_id=run_id, budget_seconds=60)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["outcomes"][0]["status"], "removed")
        self.assertFalse(path.exists())
        records = self.manifest_records(run_id)
        phases = [item["phase"] for item in records]
        self.assertEqual(phases, ["run_start", "intent", "outcome"])
        self.assertEqual(records[1]["bytes"], 32)
        self.assertEqual(records[1]["class"], "ORPHAN")
        self.assertTrue(records[2]["verified_absent"])

    def test_reclaim_is_idempotent_and_resumable(self):
        path = self.workspace("gone-workspace-2")
        candidate = {
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }
        first = self.probe.reclaim([candidate], run_id="c" * 32,
                                   budget_seconds=60)
        second = self.probe.reclaim([candidate], run_id="c" * 32,
                                    budget_seconds=60)
        self.assertEqual(first["outcomes"][0]["status"], "removed")
        self.assertEqual(second["outcomes"][0]["status"], "already_absent")
        # The manifest is append-only: the resumed run adds to it.
        self.assertGreater(len(self.manifest_records("c" * 32)), 3)

    def test_reclaim_refuses_a_path_outside_the_managed_roots(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(outside), "bytes": 1,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }], run_id="d" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"], "path_escape")
        self.assertTrue(outside.exists())

    def test_reclaim_refuses_the_hosts_subtree(self):
        hosts = self.home / "deploy-src" / "hosts" / "site"
        hosts.mkdir(parents=True)
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(hosts), "bytes": 1,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }], run_id="e" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"], "hosted_site")
        self.assertTrue(hosts.exists())

    def test_reclaim_refuses_the_managed_root_itself(self):
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree",
            "locator": str(self.home / "deploy-src"), "bytes": 1,
            "class": "BASE", "tier": "all", "reason": "one_shot_base_expired",
        }], run_id="f" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"], "managed_root")
        self.assertTrue((self.home / "deploy-src").exists())

    def test_reclaim_refuses_a_volume_that_is_not_workspace_scoped(self):
        for name in ("lenzora-postgres-data", "wordpress-uploads",
                     "lenzora-storage",
                     "sandbox-amarsonar-bangla-public_wordpress-db",
                     "sandbox-lenzora_app-node-modules"):
            response = self.probe.reclaim([{
                "seq": 1, "kind": "volume", "locator": name, "bytes": 1,
                "class": "VOLUME", "tier": "safe",
                "reason": "workspace_scoped_volume",
            }], run_id="0" * 32, budget_seconds=60)
            self.assertEqual(response["outcomes"][0]["reason"],
                             "volume_not_workspace_scoped", name)

    def test_reclaim_skips_a_candidate_that_changed_since_planning(self):
        path = self.workspace("moving-workspace-1")
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
            "expected_mtime": 1.0,
        }], run_id="9" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"],
                         "candidate_modified_since_plan")
        self.assertTrue(path.exists())

    def test_reclaim_refuses_a_run_without_a_valid_identifier(self):
        response = self.probe.reclaim([], run_id="nope", budget_seconds=30)
        self.assertFalse(response["ok"])
        self.assertEqual(response["reason"], "invalid_run_id")

    def test_observe_emits_the_reclaim_inventory(self):
        self.workspace("seen-workspace-1")
        payload = self.probe.observe_reclaim(budget_seconds=25)
        block = payload.get("reclaim")
        self.assertIsInstance(block, dict)
        names = {item["name"] for item in block["entries"]}
        self.assertIn("seen-workspace-1", names)
        record = next(item for item in block["entries"]
                      if item["name"] == "seen-workspace-1")
        self.assertTrue(record["is_workspace"])
        self.assertIsNotNone(record["mtime"])

    def test_local_reclaim_inventory_does_not_request_deep_attribution(self):
        calls = []

        def runner(request, timeout):
            calls.append((request, timeout))
            return ProcessResult(
                ("probe",), 0,
                json.dumps({
                    "identity": "a" * 24,
                    "capacity": {"total_bytes": 1},
                    "reclaim": {"entries": []},
                    "stage": "final",
                }) + "\n",
                "",
            )

        payload = LocalProbeAdapter(runner=runner).observe_reclaim(
            budget_seconds=12, directory_cache="cache_only",
        )

        self.assertEqual(payload["reclaim"], {"entries": []})
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request["action"], "observe")
        self.assertFalse(request["deep"])
        self.assertFalse(request["thorough"])
        self.assertEqual(request["directory_cache"], "cache_only")
        self.assertEqual(timeout, 17)


class TestGeneratedJobEvidence(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        (self.home / "runtime").mkdir()
        (self.home / "deploy-src").mkdir()
        self.path = self.home / "deploy-src" / "project-workspace-12345678"
        self.path.mkdir()
        self.runner = IsolatedProbeRunner(self.home)
        self.probe = LocalProbeAdapter(home=str(self.home), runner=self.runner)
        self.candidate = {"seq": 1, "kind": "worktree", "locator": str(self.path),
                          "bytes": 1, "class": "ORPHAN", "tier": "safe",
                          "reason": "orphan_workspace"}

    def job(self, lifecycle="running", finished_at=None, **extra):
        return {"project_root": str(self.path), "job_id": "a" * 16,
                "lifecycle": lifecycle, "cleanup_state": "retained",
                "target_kind": "local", "remote_name": None,
                "finished_at": finished_at, **extra}

    def reclaim(self):
        return self.probe.reclaim([self.candidate], run_id="b" * 32,
                                  budget_seconds=30)["outcomes"][0]

    def observe_block(self):
        def service(_entry, _command, *, input_data, timeout):
            return self.runner(json.loads(input_data), timeout)
        adapter = RemoteResourceAdapter("fixture", remote_lookup=lambda _name:
            {"ssh": "synthetic", "provisioned": True}, service_request=service)
        return adapter.observe(thorough=False, budget_seconds=25).reclaim

    def prepare_observe(self):
        other = self.home / "deploy-src" / "other-workspace-12345678"
        other.mkdir()
        old = time.time() - 30 * DAY
        os.utime(self.path, (old, old))
        os.utime(other, (old, old))
        volume = "sandbox-other-workspace-12345678_node-modules"
        self.runner.volumes = [volume]
        return other, volume

    def assert_selection(self, block, other, volume, *, available):
        for tier in reclaim.TIERS:
            result = reclaim.tier_candidates(block, tier, now=time.time())
            candidates = {(item.kind, item.locator) for item in result.candidates}
            if available:
                self.assertIn(("worktree", str(other)), candidates)
                self.assertIn(("volume", volume), candidates)
            else:
                self.assertNotIn(("worktree", str(other)), candidates)
                self.assertNotIn(("volume", volume), candidates)
                self.assertTrue(any(row["kind"] == "volume" and row["locator"] == volume
                                    and row["reason"] == "container_inventory_unavailable"
                                    for row in result.skipped))

    def test_observe_malformed_job_rows_preserves_known_protection_and_marks_unknown(self):
        other, volume = self.prepare_observe()
        self.runner.index = {"jobs": [self.job()], "artifacts": []}
        clean = self.observe_block()
        self.assertTrue(clean["engine_complete"])
        self.assert_selection(clean, other, volume, available=True)
        for bad in (None, {}, {"project_root": "relative", "lifecycle": "running"},
                    self.job(lifecycle=[])):
            with self.subTest(bad=bad):
                rows = [bad] if isinstance(bad, dict) and bad.get("project_root") == str(self.path) else [self.job(), bad]
                self.runner.index = {"jobs": rows, "artifacts": []}
                block = self.observe_block()
                known = next(row for row in block["entries"] if row["path"] == str(self.path))
                self.assertTrue(known["active_job"])
                self.assertFalse(block["engine_complete"])
                self.assertEqual(block["reason"], "lifecycle_evidence_unavailable")
                for tier in reclaim.TIERS:
                    selected = reclaim.tier_candidates(block, tier, now=time.time())
                    row = next(row for row in selected.skipped if row["locator"] == str(other))
                    self.assertEqual(row["class"], "UNKNOWN")
                self.assert_selection(block, other, volume, available=False)

    def test_observe_job_index_cap_is_conservative_at_10000(self):
        other, volume = self.prepare_observe()
        terminal = self.job("succeeded", datetime.now(timezone.utc).isoformat())
        # Completed jobs do not bind another workspace.
        filler = {"project_root": str(other), "lifecycle": "succeeded",
                  "cleanup_state": "completed"}
        for count in (9999, 10000):
            with self.subTest(count=count):
                self.runner.index = {"jobs": [terminal] + [filler] * (count - 1), "artifacts": []}
                block = self.observe_block()
                known = next(row for row in block["entries"] if row["path"] == str(self.path))
                self.assertEqual(block["engine_complete"], count < 10000)
                self.assertEqual(known["retained_terminal"], count < 10000)
                self.assertEqual(known["active_job"], count == 10000)
                self.assertEqual(known["retained_terminal_jobs"] is not None, count < 10000)
                for tier in reclaim.TIERS:
                    selected = reclaim.tier_candidates(block, tier, now=time.time())
                    row = next(row for row in selected.skipped if row["locator"] == str(self.path))
                    if count < 10000:
                        self.assertEqual(row["remedy"]["commands"],
                                         ["./sb job-cleanup " + "a" * 16 + " --remote fixture --confirm"])
                    else:
                        self.assertNotIn("remedy", row)
                self.assert_selection(block, other, volume, available=count < 10000)

    def test_observe_legacy_job_list_fallback_below_and_at_limit(self):
        other, volume = self.prepare_observe()
        self.runner.index_error = True
        active = self.job()
        filler = {"project_root": str(other), "lifecycle": "succeeded",
                  "cleanup_state": "completed"}
        for count in (49, 50):
            with self.subTest(count=count):
                self.runner.legacy = {"ok": True, "jobs": [active] + [filler] * (count - 1)}
                block = self.observe_block()
                known = next(row for row in block["entries"] if row["path"] == str(self.path))
                self.assertTrue(known["active_job"])
                self.assertFalse(known["retained_terminal"])
                self.assertIsNone(known["retained_terminal_jobs"])
                self.assertEqual(block["engine_complete"], count < 50)
                self.assert_selection(block, other, volume, available=count < 50)

    def test_post_plan_active_and_recent_retained_terminal_refuse_removal(self):
        for row in (self.job(), self.job("succeeded", datetime.now(timezone.utc).isoformat())):
            with self.subTest(lifecycle=row["lifecycle"]):
                self.runner.index = {"jobs": [row], "artifacts": []}
                self.assertEqual(self.reclaim()["reason"], "job_active_since_plan")
                self.assertTrue(self.path.exists())

    def test_post_plan_reservation_refuses_removal(self):
        ledger = self.home / "runtime" / "storage" / "reservations.json"
        ledger.parent.mkdir()
        ledger.write_text(json.dumps([{"target": str(self.home / "deploy-src" / "project"),
                                       "expires_at": time.time() + 3600}]))
        self.assertEqual(self.reclaim()["reason"], "job_active_since_plan")
        self.assertTrue(self.path.exists())

    def test_unreadable_malformed_and_truncated_index_refuse_removal(self):
        cases = ((True, {}), (False, {"jobs": "bad"}),
                 (False, {"jobs": [{}]}), (False, {"jobs": [self.job()] * 10000}))
        for error, index in cases:
            with self.subTest(error=error, index_type=type(index.get("jobs")).__name__,
                              count=len(index.get("jobs", ()))):
                self.runner.index_error, self.runner.index = error, index
                self.assertEqual(self.reclaim()["reason"], "job_active_since_plan")
                self.assertTrue(self.path.exists())

    def test_complete_idle_evidence_allows_prechange_removal(self):
        self.runner.index = {"jobs": [self.job("succeeded", "2000-01-01T00:00:00Z")],
                             "artifacts": []}
        self.candidate["reason"] = "finished_job_idle"
        self.assertEqual(self.reclaim()["status"], "removed")
        self.assertFalse(self.path.exists())

    def test_selected_remote_reaches_generated_reporting_and_legacy_is_conservative(self):
        def service(_entry, _command, *, input_data, timeout):
            return self.runner(json.loads(input_data), timeout)
        adapter = RemoteResourceAdapter("fixture", remote_lookup=lambda _name:
            {"ssh": "synthetic", "provisioned": True}, service_request=service)
        self.runner.index = {"jobs": [self.job("succeeded", datetime.now(timezone.utc).isoformat())],
                             "artifacts": []}
        snapshot = adapter.observe(thorough=False, budget_seconds=25)
        row = next(item for item in snapshot.reclaim["entries"] if item["path"] == str(self.path))
        self.assertTrue(row["retained_terminal"])
        self.assertEqual(row["retained_terminal_jobs"], {"remote": "fixture", "job_ids": ["a" * 16]})
        self.runner.index["jobs"][0].pop("target_kind")
        snapshot = adapter.observe(thorough=False, budget_seconds=25)
        row = next(item for item in snapshot.reclaim["entries"] if item["path"] == str(self.path))
        self.assertFalse(row["retained_terminal"])
        self.assertTrue(row["active_job"])
        self.assertIsNone(row["retained_terminal_jobs"])


class TestHostReclaimGuard(ProbeCase):
    """Spec 057: one host guard serializes every reclaiming path."""

    def candidate(self, path):
        return {
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }

    def test_probe_reclaim_while_the_guard_is_held_is_busy_and_writes_nothing(self):
        from sandbox.resources.host_guard import try_reclaim_guard

        path = self.workspace("held-workspace-1")
        run_id = "a1" * 16
        with try_reclaim_guard(runtime=self.home / "runtime") as acquired:
            self.assertTrue(acquired)
            response = self.probe.reclaim(
                [self.candidate(path)], run_id=run_id, budget_seconds=30,
            )
        self.assertFalse(response["ok"])
        self.assertEqual(response["reason"], "host_reclaim_busy")
        self.assertEqual(response["detail"], "guard")
        self.assertTrue(path.exists())
        manifest = (self.home / "runtime" / "resources" / "deletions"
                    / f"{run_id}.jsonl")
        self.assertFalse(manifest.exists())

    def test_guard_is_released_after_a_probe_reclaim(self):
        from sandbox.resources.host_guard import try_reclaim_guard

        path = self.workspace("free-workspace-1")
        response = self.probe.reclaim(
            [self.candidate(path)], run_id="a2" * 16, budget_seconds=30,
        )
        self.assertTrue(response["ok"], response)
        with try_reclaim_guard(runtime=self.home / "runtime") as acquired:
            self.assertTrue(acquired)

    def test_reap_trigger_reaches_manifest_run_start_and_intents(self):
        path = self.workspace("orphan-workspace-9")
        old = time.time() - 11 * DAY
        for item in (path / "node_modules" / "a.txt", path / "node_modules", path):
            os.utime(item, (old, old))
        probe = self.probe

        class Hybrid(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                return probe.reclaim(candidates, **kwargs)

        provider = Hybrid()
        provider.block["entries"] = [entry(
            "orphan-workspace-9", path=str(path), size_bytes=32,
            mtime=path.stat().st_mtime,
        )]
        provider.block["volumes"] = []
        store = PlanStore(self.home / "plans")
        payload = ReclaimService(provider, store, target=TARGET).reap(
            dry_run=False, confirm=True, tier="safe",
            trigger="scheduled_routine",
        )
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["data"]["trigger"], "scheduled_routine")
        records = self.manifest_records(payload["data"]["run_id"])
        self.assertEqual(records[0]["phase"], "run_start")
        self.assertEqual(records[0]["trigger"], "scheduled_routine")
        intents = [item for item in records if item["phase"] == "intent"]
        self.assertTrue(intents)
        self.assertTrue(all(item["trigger"] == "scheduled_routine"
                            for item in intents))


class TestBusyMapping(ServiceCase):
    def test_host_reclaim_busy_is_a_skipped_cleanup(self):
        class Busy(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                self.calls.append(("reclaim",))
                return {"stage": "final", "ok": False,
                        "reason": "host_reclaim_busy", "detail": "guard"}

        provider = Busy()
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["error"]["code"], "host_reclaim_busy")
        self.assertEqual(payload["data"]["detail"], "guard")

    def test_busy_plan_is_left_retryable(self):
        state = {"busy": True}

        class SometimesBusy(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                if state["busy"]:
                    return {"ok": False, "reason": "host_reclaim_busy",
                            "detail": "apply_transaction"}
                return FakeProvider.reclaim(self, candidates, **kwargs)

        service = self.service(SometimesBusy())
        plan_id = service.plan("safe")["data"]["plan_id"]
        busy = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertEqual(busy["status"], "skipped")
        state["busy"] = False
        again = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertTrue(again["ok"], again)

    def test_reap_default_trigger_is_unchanged(self):
        provider = FakeProvider()
        self.service(provider).reap(dry_run=False, confirm=True, tier="safe")
        call = next(item for item in provider.calls if item[0] == "reclaim")
        self.assertEqual(call[2], "reap")

    def test_cleanup_accepts_a_caller_run_id(self):
        provider = FakeProvider()
        payload = self.service(provider).cleanup(
            tier="safe", confirm=True, run_id="ab" * 16,
        )
        self.assertEqual(payload["data"]["run_id"], "ab" * 16)
        bad = self.service(FakeProvider()).cleanup(
            tier="safe", confirm=True, run_id="../x",
        )
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "invalid_run_id")


if __name__ == "__main__":
    unittest.main()

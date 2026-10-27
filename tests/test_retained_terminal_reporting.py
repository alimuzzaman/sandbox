"""Retained-job reporting never changes reclaim authority."""
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.commands import resources, workspaces
from sandbox.jobs.models import TERMINAL_LIFECYCLES
from sandbox.resources import reclaim
from sandbox.resources.remote import retained_terminal_bindings

ROOT = "/deploy/project-workspace-12345678"
ID = "a" * 16
NOW = 1_800_000_000
TERMINAL = {item.value for item in TERMINAL_LIFECYCLES}
COMMAND = f"./sb job-cleanup {ID} --remote fixture --confirm"


def job(**overrides):
    return {"project_root": ROOT, "job_id": ID, "lifecycle": "succeeded",
            "target_kind": "local", "remote_name": None,
            "cleanup_state": "retained", "finished_at": "2026-10-27T00:00:00Z",
            **overrides}


def bindings(rows, *, remote="fixture", complete=True, reserved=()):
    return retained_terminal_bindings(rows, {ROOT: ("retained_job",)}, set(reserved),
                                      remote, complete, TERMINAL)


def selection(**fields):
    item = {"name": "project-workspace-12345678", "path": ROOT,
            "is_workspace": True, "size_state": "measured", "size_bytes": 10,
            "mtime": NOW - 900000, "containers": [], **fields}
    inventory = {"entries": [item], "volumes": [], "scratch": [],
                 "leases": {item["name"]: {"released": True}}, "status": "complete"}
    return [reclaim.tier_candidates(inventory, tier, now=NOW) for tier in reclaim.TIERS]


class TerminalEvidenceTest(unittest.TestCase):
    def test_proven_terminal_and_selected_remote(self):
        self.assertEqual(bindings([job()]), {ROOT: {"remote": "fixture", "job_ids": [ID]}})

    def test_ambiguous_metadata_never_produces_binding(self):
        bad = [job(lifecycle="running"), job(lifecycle=[]), job(cleanup_state="completed"),
               job(target_kind="remote"), job(remote_name="other"), job(finished_at=None),
               job(job_id=[]), job(project_root="relative"), {}, None]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual(bindings([job(), value]), {})
        self.assertEqual(bindings([job()], complete=False), {})
        self.assertEqual(bindings([job()], reserved=[ROOT]), {})
        self.assertEqual(bindings([job()], remote="other;echo"), {})
        self.assertEqual(bindings([job(), job()]), {})
        self.assertEqual(bindings([job(job_id=f"{i:016x}") for i in range(9)]), {})

    def test_idle_reference_is_not_relabelled(self):
        self.assertEqual(retained_terminal_bindings([job()], {ROOT: ("finished_job_idle",)},
                                                   set(), "fixture", True, TERMINAL), {})


class PolicyTest(unittest.TestCase):
    def test_both_protections_win_at_every_tier_and_released_lease(self):
        for field in ("active_job", "retained_terminal"):
            for result in selection(**{field: True, "finished_job_idle": True}):
                self.assertFalse(result.candidates)
                self.assertEqual(result.skipped[0]["reason"], field)

    def test_valid_guidance_and_malformed_ids(self):
        for result in selection(retained_terminal=True,
                                retained_terminal_jobs={"remote": "fixture", "job_ids": [ID]}):
            self.assertEqual(result.skipped[0]["remedy"]["commands"], [COMMAND])
        for ids in ([[]], [{"id": ID}], [ID, ID], ["bad"]):
            result = selection(retained_terminal=True,
                               retained_terminal_jobs={"remote": "fixture", "job_ids": ids})[0]
            self.assertNotIn("remedy", result.skipped[0])
            self.assertFalse(result.candidates)

    def test_guidance_validates_policy_boundary_limits(self):
        ids = [f"{i:032x}" for i in range(8)]
        for result in selection(retained_terminal=True,
                                retained_terminal_jobs={"remote": "fixture", "job_ids": ids}):
            self.assertEqual(result.skipped[0]["remedy"]["commands"], [
                f"./sb job-cleanup {job_id} --remote fixture --confirm" for job_id in ids])
            self.assertFalse(result.candidates)
        for remote, job_ids in (("fixture", ids + ["f" * 32]), ("", ids),
                                ("-invalid", ids), ("fixture;echo", ids),
                                ("a" * 65, ids), (None, ids)):
            with self.subTest(remote=remote, count=len(job_ids)):
                for result in selection(retained_terminal=True,
                                        retained_terminal_jobs={"remote": remote, "job_ids": job_ids}):
                    self.assertNotIn("remedy", result.skipped[0])
                    self.assertFalse(result.candidates)

    def test_workspace_volume_is_kept_with_terminal_job(self):
        item = {"name": "project-workspace-12345678", "path": ROOT,
                "is_workspace": True, "size_state": "measured", "size_bytes": 10,
                "mtime": NOW - 900000, "containers": [], "retained_terminal": True}
        volume = {"name": "sandbox-project-workspace-12345678_node-modules",
                  "size_bytes": 20, "mounted_running": False}
        for tier in reclaim.TIERS:
            result = reclaim.tier_candidates({"entries": [item], "volumes": [volume],
                "scratch": [], "leases": {}, "status": "complete"}, tier, now=NOW)
            self.assertFalse(result.candidates)


class RendererTest(unittest.TestCase):
    def test_workspace_reap_text_and_json(self):
        row = {"kind": "worktree", "display_name": "project", "reason": "retained_terminal",
               "remedy": {"commands": [COMMAND]}}
        payload = {"ok": True, "status": "planned", "data": {"dry_run": True,
                   "candidates": [], "skipped": [row]}}
        service = mock.Mock()
        service.reap.return_value = payload
        args = SimpleNamespace(action="reap", remote="fixture", dry_run=True, confirm=False,
                               ttl=None, budget=None, tier="all", json=False)
        for json_mode in (False, True):
            args.json = json_mode
            out = io.StringIO()
            with mock.patch("sandbox.resources.context.reclaim_service", return_value=service), \
                 contextlib.redirect_stdout(out):
                workspaces.cmd_retention(args)
            if json_mode:
                self.assertEqual(json.loads(out.getvalue())["data"]["skipped"], [row])
            else:
                self.assertIn("[retained_terminal]", out.getvalue())
                self.assertIn(COMMAND, out.getvalue())
        self.assertTrue(all(call.kwargs["dry_run"] for call in service.reap.call_args_list))

    def test_resources_plan_text(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            resources._emit_reclaim_plan({"skipped": [{"kind": "worktree", "display_name": "project",
                "reason": "retained_terminal", "remedy": {"commands": [COMMAND]}}]})
        self.assertIn("[retained_terminal]", out.getvalue())
        self.assertIn(COMMAND, out.getvalue())

    def test_resources_plan_json(self):
        row = {"kind": "worktree", "reason": "retained_terminal",
               "remedy": {"commands": [COMMAND]}}
        payload = {"action": "plan", "status": "planned", "data": {"skipped": [row]}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            resources._emit(payload, True)
        self.assertEqual(json.loads(out.getvalue())["data"]["skipped"], [row])

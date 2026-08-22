"""Focused unit coverage for the remote Hermes control plane (spec 016).

These tests intentionally mock SSH: they validate the command/state contract
without installing Hermes, authenticating a Git provider, or changing a VPS.
"""
from __future__ import annotations

import json
import base64
import hashlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._hermes as hermes  # noqa: E402
from sandbox.commands.hermes import _job_payload, _repo_action  # noqa: E402
from sandbox.hermes.scheduler import (  # noqa: E402
    SCHEDULE_GUARD_START, audit_jobs, catalog_fingerprint, classify_job,
    effective_job_status, guarded_prompt, invalid_model_reason, load_catalog,
    reconciliation_plan, render_entry, scheduled_route,
)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _exact_catalog_snapshot(paths: dict[str, str]) -> dict:
    catalog = _catalog_with_worker_enabled()
    jobs = []
    for index, entry in enumerate(entry for entry in catalog["jobs"] if entry.enabled):
        rendered = render_entry(entry, paths)
        job = {
            "id": f"deadbeef{index:04d}", "name": entry.name, "schedule": entry.schedule,
            "enabled": True, "deliver": entry.deliver, "workdir": rendered["workdir"],
            "no_agent": entry.kind == "script", "script": entry.script,
            "provider_snapshot": scheduled_route(entry.profile).provider if entry.profile else None,
            "model_snapshot": scheduled_route(entry.profile).model if entry.profile else None,
        }
        if entry.script:
            job["script_sha256"] = hashlib.sha256(
                (ROOT / "sandbox/hermes/cron_scripts" / entry.script).read_bytes()).hexdigest()
        else:
            job["reasoning_effort_snapshot"] = scheduled_route(entry.profile).effort
            job["prompt_sha256"] = hashlib.sha256(rendered["prompt"].encode()).hexdigest()
        jobs.append(job)
    return {"jobs": jobs}


def _catalog_with_worker_enabled() -> dict:
    catalog = load_catalog()
    return {"jobs": [replace(entry, enabled=entry.name == "lenzora-todo-task")
                      for entry in catalog["jobs"]], "schema_version": catalog["schema_version"]}


class TestValidation(unittest.TestCase):
    @patch("sandbox.core._hermes.cron_output")
    @patch("sandbox.core._hermes._cron_snapshot")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_sync_creates_one_review_draft_per_blocker(
            self, require_remote, paths, read_state, write_state, snapshot, output):
        paths.return_value = {"repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
                              "worktrees": "/home/u/worktrees"}
        state = hermes._new_state()
        read_state.return_value = state
        snapshot.return_value = {"jobs": [{"id": "deadbeef1234", "name": "lenzora-todo-task", "enabled": True}]}
        output.return_value = {"status": "available", "data": {"output": "REVIEW_REQUIRED — exact origin required"}}
        with patch.object(hermes, "load_catalog", return_value=_catalog_with_worker_enabled()):
            first = hermes.authorization_sync("test")
            second = hermes.authorization_sync("test")
        self.assertEqual(first["data"]["created_count"], 1)
        self.assertEqual(second["data"]["created_count"], 0)
        request = next(iter(state["authorizations"]["requests"].values()))
        self.assertEqual(request["status"], "review_required")
        self.assertEqual(request["blocker"], "exact origin required")
        self.assertEqual(write_state.call_count, 1)

    def test_authorization_validates_scope_origin_reason_and_identifier(self):
        self.assertEqual(hermes._valid_authorization_scope("preview-overlay"), "preview-overlay")
        self.assertEqual(hermes._valid_replay_origin("https://Replay.Example.test/"), "https://replay.example.test")
        self.assertEqual(hermes._valid_authorization_reason("bounded review"), "bounded review")
        for value in ("https://example.test/path", "http://example.test", "https://u:p@example.test",
                      "https://example.test\r\nX-Injected: value"):
            with self.subTest(origin=value):
                with self.assertRaises(hermes.HermesError):
                    hermes._valid_replay_origin(value)
        with self.assertRaises(hermes.HermesError):
            hermes._valid_authorization_reason("token=secret-value-that-must-not-be-stored")
        with self.assertRaises(hermes.HermesError):
            hermes._valid_authorization_reason("review\r\nforbidden")
        with self.assertRaises(hermes.HermesError):
            hermes._valid_authorization_id("not-an-id")

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_request_supersedes_pending_request(self, require_remote, paths, read_state, write_state):
        paths.return_value = {"repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
                              "worktrees": "/home/u/worktrees"}
        state = hermes._new_state()
        read_state.return_value = state
        with patch.object(hermes, "load_catalog", return_value=_catalog_with_worker_enabled()):
            first = hermes.authorization_request("test", "lenzora-todo-task", "preview-overlay",
                                                 "https://replay.example.test", "first review", 60)
            second = hermes.authorization_request("test", "lenzora-todo-task", "preview-overlay",
                                                  "https://replay.example.test", "second review", 60)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(second["status"], "pending")
        requests = state["authorizations"]["requests"]
        self.assertEqual(sum(item["status"] == "pending" for item in requests.values()), 1)
        self.assertIn("superseded", [item["event"] for item in state["authorizations"]["audit"]])
        self.assertEqual(write_state.call_count, 2)

    @patch("sandbox.core._hermes._set_cron_prompt")
    @patch("sandbox.core._hermes._cron_snapshot")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_approval_updates_only_matching_prompt(self, require_remote, paths, read_state,
                                                                  write_state, snapshot, set_prompt):
        paths.return_value = {"repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
                              "worktrees": "/home/u/worktrees"}
        state = hermes._new_state()
        request = {"id": "a" * 16, "job_name": "lenzora-todo-task", "scope": "preview-overlay",
                   "replay_origin": "https://replay.example.test", "rationale": "bounded review",
                   "fingerprint": hermes._authorization_fingerprint("lenzora-todo-task", "preview-overlay",
                                                                      "https://replay.example.test", "bounded review"),
                   "status": "pending", "created_at": "2026-07-15T00:00:00+00:00",
                   "expires_at": "2099-07-15T00:00:00+00:00"}
        prior = {**request, "id": "c" * 16, "status": "approved",
                 "approved_at": "2026-07-14T00:00:00+00:00"}
        state["authorizations"]["requests"][request["id"]] = request
        state["authorizations"]["requests"][prior["id"]] = prior
        read_state.return_value = state
        snapshot.return_value = {"jobs": [{"id": "deadbeef1234", "name": "lenzora-todo-task", "enabled": True}]}
        with patch.object(hermes, "load_catalog", return_value=_catalog_with_worker_enabled()):
            out = hermes.authorization_approve("test", request["id"], True)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(request["status"], "approved")
        self.assertEqual(prior["status"], "superseded")
        self.assertIn("superseded", [event["event"] for event in state["authorizations"]["audit"]])
        self.assertEqual(set_prompt.call_count, 1)
        self.assertIn("preview-overlay", set_prompt.call_args.args[2])
        self.assertIn("https://replay.example.test", set_prompt.call_args.args[2])
        self.assertIn("Expires at 2099-07-15T00:00:00+00:00", set_prompt.call_args.args[2])
        self.assertIn("at or after expiry", set_prompt.call_args.args[2])
        write_state.assert_called_once()

    @patch("sandbox.core._hermes._set_cron_prompt")
    @patch("sandbox.core._hermes._cron_snapshot")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_approval_rolls_back_prompt_on_state_conflict(
            self, require_remote, paths, read_state, write_state, snapshot, set_prompt):
        paths.return_value = {"repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
                              "worktrees": "/home/u/worktrees"}
        state = hermes._new_state()
        request = {"id": "a" * 16, "job_name": "lenzora-todo-task", "scope": "preview-overlay",
                   "replay_origin": "https://replay.example.test", "rationale": "bounded review",
                   "fingerprint": hermes._authorization_fingerprint("lenzora-todo-task", "preview-overlay",
                                                                      "https://replay.example.test", "bounded review"),
                   "status": "pending", "created_at": "2026-07-15T00:00:00+00:00",
                   "expires_at": "2099-07-15T00:00:00+00:00"}
        state["authorizations"]["requests"][request["id"]] = request
        read_state.return_value = state
        snapshot.return_value = {"jobs": [{"id": "deadbeef1234", "name": "lenzora-todo-task", "enabled": True}]}
        write_state.side_effect = hermes.HermesError("state changed", "state_conflict", True)
        with patch.object(hermes, "load_catalog", return_value=_catalog_with_worker_enabled()):
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.authorization_approve("test", request["id"], True)
        self.assertEqual(caught.exception.code, "state_conflict")
        self.assertEqual(set_prompt.call_count, 0)

    @patch("sandbox.core._hermes._set_cron_prompt")
    @patch("sandbox.core._hermes._cron_snapshot")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_prompt_failure_rolls_back_state_with_cas(
            self, require_remote, paths, read_state, write_state, snapshot, set_prompt):
        paths.return_value = {"repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
                              "worktrees": "/home/u/worktrees"}
        state = hermes._new_state()
        request = {"id": "a" * 16, "job_name": "lenzora-todo-task", "scope": "preview-overlay",
                   "replay_origin": "https://replay.example.test", "rationale": "bounded review",
                   "fingerprint": hermes._authorization_fingerprint("lenzora-todo-task", "preview-overlay",
                                                                      "https://replay.example.test", "bounded review"),
                   "status": "pending", "created_at": "2026-07-15T00:00:00+00:00",
                   "expires_at": "2099-07-15T00:00:00+00:00"}
        state["authorizations"]["requests"][request["id"]] = request
        read_state.return_value = state
        snapshot.return_value = {"jobs": [{"id": "deadbeef1234", "name": "lenzora-todo-task", "enabled": True}]}
        set_prompt.side_effect = hermes.HermesError("prompt update failed", "authorization_prompt_update_failed", True)
        with patch.object(hermes, "load_catalog", return_value=_catalog_with_worker_enabled()):
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.authorization_approve("test", request["id"], True)
        self.assertEqual(caught.exception.code, "authorization_prompt_update_failed")
        self.assertEqual(write_state.call_count, 2)
        self.assertEqual(write_state.call_args_list[1].args[2]["authorizations"]["requests"][request["id"]]["status"],
                         "pending")
        self.assertEqual(write_state.call_args_list[1].kwargs["expected_digest"],
                         hermes._state_digest(write_state.call_args_list[0].args[2]))

    @patch("sandbox.core._hermes._set_cron_prompt")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths", return_value={})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_approval_rejects_tampered_fingerprint_before_mutation(
            self, require_remote, paths, read_state, write_state, set_prompt):
        state = hermes._new_state()
        request = {"id": "a" * 16, "job_name": "lenzora-todo-task", "scope": "preview-overlay",
                   "replay_origin": "https://replay.example.test", "rationale": "changed scope",
                   "fingerprint": "b" * 64, "status": "pending", "created_at": "2026-07-15T00:00:00+00:00",
                   "expires_at": "2099-07-15T00:00:00+00:00"}
        state["authorizations"]["requests"][request["id"]] = request
        read_state.return_value = state
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.authorization_approve("test", request["id"], True)
        self.assertEqual(caught.exception.code, "authorization_fingerprint_mismatch")
        write_state.assert_not_called()
        set_prompt.assert_not_called()

    def test_authorization_approval_requires_confirmation(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.authorization_approve("test", "a" * 16, False)
        self.assertEqual(caught.exception.code, "confirmation_required")

    def test_authorization_validators_reject_non_string_values_and_boolean_expiry(self):
        for validator in (hermes._valid_authorization_id, hermes._valid_authorization_scope,
                          hermes._valid_replay_origin, hermes._valid_authorization_reason):
            with self.subTest(validator=validator.__name__), self.assertRaises(hermes.HermesError):
                validator(123)
        with patch.object(hermes, "_require_remote") as require_remote:
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.authorization_request("test", "job", "scope", "https://replay.example", "reason", True)
        self.assertEqual(caught.exception.code, "invalid_authorization_expiry")
        require_remote.assert_not_called()

    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths", return_value={})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_authorization_rejects_malformed_expiry_as_invalid_state(self, require_remote, paths, read_state):
        state = hermes._new_state()
        state["authorizations"]["requests"]["a" * 16] = {
            "id": "a" * 16, "status": "pending", "expires_at": "not-a-timestamp",
            "created_at": "2026-07-15T00:00:00+00:00",
        }
        read_state.return_value = state
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.authorization_list("test")
        self.assertEqual(caught.exception.code, "invalid_state")

    def test_authorization_rejects_malformed_record_shape(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._normalize_state({"authorizations": {"requests": {"a" * 16: []}, "audit": []}})
        self.assertEqual(caught.exception.code, "invalid_state")
        with self.assertRaises(hermes.HermesError):
            hermes._normalize_state({"authorizations": {"requests": {"a" * 16: {
                "id": "a" * 16, "status": "pending", "created_at": "now",
                "expires_at": "later"}}, "audit": []}})

    def test_authorization_state_rejects_mismatched_ids_statuses_and_digests(self):
        request = {"id": "b" * 16, "status": "unknown", "created_at": "now",
                   "expires_at": "later", "fingerprint": "c" * 64}
        with self.assertRaisesRegex(hermes.HermesError, "authorization record"):
            hermes._normalize_state({"authorizations": {"requests": {"a" * 16: request}, "audit": []}})

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths", return_value={})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._authorization_now")
    def test_authorization_review_reports_expiry_without_mutating_state(
            self, now, require_remote, paths, read_state, write_state):
        state = hermes._new_state()
        request = {"id": "a" * 16, "job_name": "job", "status": "pending",
                   "created_at": "2026-07-15T00:00:00+00:00",
                   "expires_at": "2026-07-15T01:00:00+00:00", "fingerprint": "b" * 64}
        state["authorizations"]["requests"][request["id"]] = request
        read_state.return_value = state
        now.return_value = hermes.datetime(2026, 7, 15, 2, tzinfo=hermes.timezone.utc)
        listed = hermes.authorization_list("test")
        self.assertEqual(listed["data"]["requests"][0]["status"], "expired")
        write_state.assert_not_called()

    def test_committed_cron_catalog_is_strict_and_fingerprinted(self):
        catalog = load_catalog()
        self.assertEqual([job.name for job in catalog["jobs"]], [
            "codex-quota-requeue", "authorization-expiry", "lenzora-kanban-dispatch",
            "sandbox-spec-backlog", "lenzora-todo-task",
        ])
        self.assertEqual([job.name for job in catalog["jobs"] if job.enabled], [
            "codex-quota-requeue", "authorization-expiry", "sandbox-spec-backlog",
        ])
        self.assertEqual(len(catalog_fingerprint(catalog)), 64)
        worker = next(job for job in catalog["jobs"] if job.name == "sandbox-spec-backlog")
        self.assertEqual(worker.profile, "terra")
        self.assertEqual(scheduled_route(worker.profile).effort, "medium")
        self.assertEqual(worker.schedule, "17 */4 * * *")
        self.assertIn("hermes/autonomous-backlog", worker.prompt)
        self.assertIn("permits routine local implementation", worker.prompt)
        self.assertIn("Do not inventory every spec", worker.prompt)
        self.assertIn("Continue through dependency-ready tasks", worker.prompt)
        self.assertIn("do not stop after one task", worker.prompt)
        self.assertIn("NO_BACKLOG_WORK", worker.prompt)
        self.assertIn("Finish with exactly one non-empty terminal result", worker.prompt)
        rendered = render_entry(worker, {
            "repo_root": "/home/u/sandbox/hermes-repos", "sandbox_home": "/home/u/sandbox",
            "worktrees": "/home/u/sandbox/runtime/hermes-worktrees",
        })
        self.assertEqual(rendered["workdir"], "/home/u/sandbox/runtime/hermes-worktrees/sandbox-autonomous-backlog")

    def test_remote_install_validates_catalog_scripts_without_retired_names(self):
        script = (ROOT / "scripts/install-remote.sh").read_text()
        self.assertIn('jobs = catalog.get("jobs")', script)
        self.assertIn('root / "cron_scripts" / script', script)
        self.assertNotIn("todo_md_monitor.py", script)

    def test_remote_install_upgrades_cloudflared_for_token_file_connectors(self):
        script = (ROOT / "scripts/install-remote.sh").read_text()
        self.assertIn('cloudflared tunnel run --help 2>&1 | grep -q -- "--token-file"', script)
        self.assertIn("https://pkg.cloudflare.com/cloudflare-main.gpg", script)
        self.assertIn("https://pkg.cloudflare.com/cloudflared any main", script)
        self.assertIn("apt-get install -y cloudflared", script)

    def test_cron_status_prefers_provider_failure_over_false_green(self):
        job = {"last_status": "ok", "last_run_at": "now", "model_snapshot": "gpt-5.6-luna"}
        status = effective_job_status(job, "provider_bad_request")
        self.assertEqual(status["effective_status"], "failed")
        self.assertTrue(status["false_success"])
        self.assertEqual(classify_job({"no_agent": True, "script": "watch.py"}), "script")

    def test_documented_terminal_marker_overrides_only_wrapper_error(self):
        status = effective_job_status({
            "last_status": "error", "last_run_at": "now",
            "last_error": "RuntimeError: COMPLETED_SPEC_TASK",
        })
        self.assertEqual(status["effective_status"], "ok")
        self.assertEqual(status["terminal_result"], "COMPLETED_SPEC_TASK")
        self.assertTrue(status["result_protocol_error"])
        provider = effective_job_status({
            "last_status": "error", "last_run_at": "now",
            "last_error": "RuntimeError: COMPLETED_SPEC_TASK\nHTTP 429 quota exceeded",
        })
        self.assertEqual(provider["effective_status"], "failed")
        self.assertEqual(provider["terminal_classification"], "provider_failure")

    def test_documented_terminal_marker_without_transition_remains_failed(self):
        status = effective_job_status({
            "last_status": "error", "last_run_at": None,
            "last_error": "RuntimeError: COMPLETED_SPEC_TASK",
        })
        self.assertEqual(status["effective_status"], "failed")
        self.assertEqual(status["terminal_classification"], "protocol_error")
        self.assertEqual(status["reason"], "documented terminal result lacks an observed transition")

    def test_reconciliation_is_exact_and_idempotent(self):
        paths = {"repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
                 "worktrees": "/home/u/sandbox/runtime/hermes-worktrees"}
        catalog = _catalog_with_worker_enabled()
        observed = _exact_catalog_snapshot(paths)["jobs"]
        converged = reconciliation_plan(catalog, observed, paths=paths)
        self.assertFalse(converged["changes"])
        self.assertEqual(converged["create"], [])
        forced = reconciliation_plan(catalog, observed, force_replace=True, paths=paths)
        self.assertTrue(forced["changes"])
        self.assertEqual(len(forced["remove"]), 1)
        self.assertEqual(len(forced["create"]), 1)

    def test_scheduled_routes_keep_model_and_effort_separate(self):
        route = scheduled_route("terra")
        self.assertEqual(route.model, "gpt-5.6-terra")
        self.assertEqual(route.effort, "medium")
        self.assertNotIn(route.effort, route.model)
        with self.assertRaises(ValueError):
            scheduled_route("terra/high")

    @patch("sandbox.core._hermes._cron_snapshot")
    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_manual_cron_create_uses_pinned_cli_positional_contract(
            self, require_remote, paths, ssh, snapshot):
        require_remote.return_value = {}
        paths.return_value = {"launcher": "$HOME/.local/bin/hermes"}
        snapshot.side_effect = [{"jobs": []}, {"jobs": [{"id": "deadbeef1234"}]}]
        ssh.return_value = _completed()
        with patch("sandbox.core._hermes._set_cron_route", return_value={"profile": "terra"}):
            out = hermes.cron_create("test", "every 1h", "bounded", name="demo",
                                     workdir=None, profile="terra", confirm=True)
        self.assertTrue(out["ok"])
        command = ssh.call_args.args[1]
        self.assertTrue(command.startswith("$HOME/.local/bin/hermes cron create "))
        self.assertNotIn("'$HOME/.local/bin/hermes'", command)
        self.assertIn("cron create 'every 1h' bounded", command)
        self.assertNotIn("--schedule", command)

    def test_cron_create_rejects_malformed_text_before_remote_lookup(self):
        for kwargs, code in (
            ({"schedule": 3600, "prompt": "safe"}, "invalid_cron_schedule"),
            ({"schedule": "daily", "prompt": "unsafe\0prompt"}, "invalid_prompt"),
            ({"schedule": "daily", "prompt": "safe", "workdir": 7}, "invalid_cron_workdir"),
        ):
            with self.subTest(code=code), self.assertRaises(hermes.HermesError) as caught:
                hermes.cron_create("test", kwargs.pop("schedule"), kwargs.pop("prompt"),
                                   name=None, workdir=kwargs.pop("workdir", None),
                                   profile="terra", confirm=True, **kwargs)
            self.assertEqual(caught.exception.code, code)


class TestSchedulerReliability(unittest.TestCase):
    def setUp(self):
        self.catalog_patch = patch.object(hermes, "load_catalog", return_value=_catalog_with_worker_enabled())
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._paths", return_value={"worktrees": "/home/u/worktrees"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_worktree_inspect_returns_bounded_review_evidence(self, require_remote, paths, checked):
        checked.return_value = _completed(stdout=json.dumps({
            "path": "/home/u/worktrees/demo", "branch": "hermes/demo",
            "status": [" M docs/file.md"], "diff_check_ok": True,
            "stat": "docs/file.md | 1 +", "diff": "+safe", "secret_like": False,
            "truncated": False,
        }))
        out = hermes.worktree_inspect("test", "demo")
        self.assertEqual(out["status"], "reviewable")
        self.assertEqual(out["data"]["diff"], "+safe")
        with self.assertRaises(hermes.HermesError):
            hermes.worktree_inspect("test", "../escape")

    @patch("sandbox.core._hermes._checked", return_value=_completed(stdout="a" * 40 + "\n"))
    @patch("sandbox.core._hermes._paths", return_value={"worktrees": "/home/u/worktrees"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes.worktree_inspect")
    def test_worktree_preserve_requires_preview_then_pushes_expected_branch(
            self, inspect, require_remote, paths, checked):
        inspect.return_value = {"data": {
            "path": "/home/u/worktrees/demo", "branch": "hermes/demo",
            "status": [" M docs/file.md"], "diff_check_ok": True,
            "secret_like": False, "head": "b" * 40, "review_id": "c" * 64,
        }}
        preview = hermes.worktree_preserve("test", "demo", False)
        self.assertEqual(preview["status"], "planned")
        checked.assert_not_called()
        applied = hermes.worktree_preserve("test", "demo", True)
        self.assertEqual(applied["status"], "pushed")
        self.assertEqual(applied["commit"], "a" * 40)
        command = checked.call_args.args[1]
        self.assertIn("HEAD:hermes/demo", command)
        self.assertIn("flock -n 9", command)
        self.assertIn(".hermes/cron/.tick.lock", command)
        self.assertLess(command.index(".tick.lock"), command.index("worktree-demo.lock"))
        self.assertIn("git -C /home/u/worktrees/demo diff --cached --check", command)
        self.assertIn("symbolic-ref --short HEAD", command)
        self.assertIn("sha256sum", command)
        self.assertIn("re.search(sys.argv[1]", command)

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._paths", return_value={"worktrees": "/home/u/worktrees"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_worktree_inspect_withholds_generic_bearer_jwt(self, require_remote, paths, checked):
        token = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
        checked.return_value = _completed(stdout=json.dumps({
            "path": "/home/u/worktrees/demo", "branch": "hermes/demo", "status": [],
            "diff_check_ok": True, "stat": "", "diff": token, "secret_like": False,
            "head": "a" * 40, "review_id": "b" * 64, "truncated": False,
        }))
        out = hermes.worktree_inspect("test", "demo")
        self.assertTrue(out["data"]["secret_like"])
        self.assertEqual(out["data"]["diff"], "")
        self.assertNotIn(token, json.dumps(out))
        self.assertIn("credential_pattern = sys.argv[2]", checked.call_args.args[1])

    @patch("sandbox.core._hermes._ssh")
    def test_managed_cron_worktree_fast_forwards_only_when_clean(self, ssh):
        ssh.side_effect = [_completed(), _completed()]
        desired = {
            "kind": "agent", "name": "lenzora-todo-task",
            "workdir": "/home/u/worktrees/lenzora-todo-task",
        }
        out = hermes._prepare_catalog_workdir(
            {}, {"repo_root": "/home/u/repos"}, desired,
        )
        self.assertEqual(out, desired["workdir"])
        command = ssh.call_args_list[1].args[1]
        self.assertIn("diff --quiet", command)
        self.assertIn("diff --cached --quiet", command)
        self.assertIn('merge --ff-only "$target"', command)
        self.assertIn("worktree-lenzora-todo-task.lock", command)
        self.assertIn(".hermes/cron/.tick.lock", command)
        self.assertLess(command.index(".tick.lock"), command.index("worktree-lenzora-todo-task.lock"))
        self.assertIn("/home/u/repos/lenzora", command)

    @patch("sandbox.core._hermes._ssh")
    def test_managed_cron_worktree_refuses_dirty_or_divergent_state(self, ssh):
        ssh.side_effect = [_completed(), _completed(returncode=1)]
        desired = {
            "kind": "agent", "name": "lenzora-todo-task",
            "workdir": "/home/u/worktrees/lenzora-todo-task",
        }
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._prepare_catalog_workdir({}, {"repo_root": "/home/u/repos"}, desired)
        self.assertEqual(caught.exception.code, "cron_workdir_not_clean")

    @patch("sandbox.core._hermes._checked", return_value=_completed())
    def test_lenzora_todo_bootstrap_uses_committed_skills_and_ignores_them(self, checked):
        hermes._bootstrap_lenzora_speckit({}, {
            "sandbox_home": "/home/u/sandbox", "worktrees": "/home/u/worktrees",
        }, {"name": "lenzora-todo-task", "workdir": "/home/u/worktrees/lenzora-todo-task"})
        command = checked.call_args.args[1]
        self.assertIn("runtime=/home/u/sandbox/sb-src", command)
        self.assertIn('"$runtime/skills/$skill/SKILL.md"', command)
        self.assertIn("speckit-refine", command)
        self.assertIn('"$runtime/.specify/templates"', command)
        self.assertIn("rev-parse --git-path info/exclude", command)
        self.assertIn("/.agents/", command)
        self.assertIn("/.specify/", command)

    @patch("sandbox.core._hermes._checked")
    def test_cron_snapshot_returns_only_safe_catalog_hashes(self, checked):
        raw_prompt = "Private task intent"
        script = b"#!/usr/bin/env python3\nprint('safe')\n"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cron = home / ".hermes" / "cron"
            scripts = home / ".hermes" / "scripts"
            cron.mkdir(parents=True)
            scripts.mkdir()
            (home / ".hermes" / "config.yaml").write_text("agent:\n  reasoning_effort: medium\n")
            (scripts / "safe.py").write_bytes(script)
            (cron / "jobs.json").write_text(json.dumps({"jobs": [{
                "id": "agent", "name": "worker", "enabled": True, "schedule": "every 1h",
                "deliver": "local", "workdir": "/srv/work", "provider": "openai-codex",
                "model": "gpt-5.6-terra", "prompt": raw_prompt, "no_agent": False,
            }, {
                "id": "script", "name": "monitor", "enabled": True, "schedule": "every 1h",
                "deliver": "local", "workdir": "/srv/work", "script": "safe.py", "no_agent": True,
            }]}))

            def execute_snapshot(_, command, **__):
                run = subprocess.run(shlex.split(command), text=True, capture_output=True, check=False,
                                     env={**os.environ, "HOME": str(home)})
                return _completed(run.returncode, run.stdout, run.stderr)

            checked.side_effect = execute_snapshot
            observed = hermes._cron_snapshot({})

        agent, script_job = observed["jobs"]
        self.assertEqual(agent["prompt_sha256"], hashlib.sha256(guarded_prompt(raw_prompt).encode()).hexdigest())
        self.assertEqual(agent["reasoning_effort_snapshot"], "medium")
        self.assertEqual(script_job["script_sha256"], hashlib.sha256(script).hexdigest())
        self.assertNotIn(raw_prompt, json.dumps(observed))
        self.assertNotIn("prompt", agent)

    @patch("sandbox.core._hermes._checked")
    def test_cron_snapshot_rejects_malformed_jobs_collection(self, checked):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cron = home / ".hermes" / "cron"
            cron.mkdir(parents=True)
            (cron / "jobs.json").write_text(json.dumps({"jobs": ["malformed"]}))

            def execute_snapshot(_, command, **__):
                run = subprocess.run(shlex.split(command), text=True, capture_output=True, check=False,
                                     env={**os.environ, "HOME": str(home)})
                return _completed(run.returncode, run.stdout, run.stderr)

            checked.side_effect = execute_snapshot
            with self.assertRaises(hermes.HermesError) as caught:
                hermes._cron_snapshot({})
        self.assertEqual(caught.exception.code, "invalid_cron_state")

    @patch("sandbox.core._hermes._checked")
    def test_cron_snapshot_rejects_missing_jobs_field(self, checked):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cron = home / ".hermes" / "cron"
            cron.mkdir(parents=True)
            (cron / "jobs.json").write_text("{}")

            def execute_snapshot(_, command, **__):
                run = subprocess.run(shlex.split(command), text=True, capture_output=True, check=False,
                                     env={**os.environ, "HOME": str(home)})
                return _completed(run.returncode, run.stdout, run.stderr)

            checked.side_effect = execute_snapshot
            with self.assertRaises(hermes.HermesError) as caught:
                hermes._cron_snapshot({})
        self.assertEqual(caught.exception.code, "invalid_cron_state")

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
        "worktrees": "/home/u/sandbox/runtime/hermes-worktrees", "launcher": "$HOME/.local/bin/hermes",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    def test_reconcile_blocked_snapshot_makes_zero_remote_mutations(
            self, snapshot, require_remote, paths, ssh, checked):
        observed = _exact_catalog_snapshot(paths.return_value)
        del next(job for job in observed["jobs"] if not job["no_agent"])["prompt_sha256"]
        snapshot.return_value = observed

        out = hermes.cron_reconcile("test", confirm=True)

        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["error"]["code"], "cron_reconcile_blocked")
        ssh.assert_not_called()
        checked.assert_not_called()

    @patch("sandbox.core._hermes._create_catalog_job", return_value="created-1")
    @patch("sandbox.core._hermes._install_cron_scripts")
    @patch("sandbox.core._hermes._prepare_catalog_workdir", return_value=None)
    @patch("sandbox.core._hermes._checked", return_value=_completed())
    @patch("sandbox.core._hermes._ssh", return_value=_completed())
    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
        "worktrees": "/home/u/sandbox/runtime/hermes-worktrees", "launcher": "$HOME/.local/bin/hermes",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    def test_force_reconcile_bypasses_missing_hashes_then_requires_exact_final_snapshot(
            self, snapshot, require_remote, paths, ssh, checked, prepare, install_scripts, create):
        initial = _exact_catalog_snapshot(paths.return_value)
        del next(job for job in initial["jobs"] if not job["no_agent"])["prompt_sha256"]
        final = _exact_catalog_snapshot(paths.return_value)
        snapshot.side_effect = [initial, final]

        out = hermes.cron_reconcile("test", confirm=True, force_replace=True)

        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "converged")
        self.assertEqual(out["data"]["blocked_by"], [])
        self.assertEqual(ssh.call_count, 1)
        self.assertEqual(create.call_count, 1)
        install_scripts.assert_called_once()
        backup_command = checked.call_args_list[0].args[1]
        self.assertIn("prechange_scheduler_evidence", backup_command)
        self.assertIn("inventory_digest", backup_command)

    @patch("sandbox.core._hermes._create_catalog_job", side_effect=hermes.HermesError("create failed", "cron_create_failed"))
    @patch("sandbox.core._hermes._install_cron_scripts")
    @patch("sandbox.core._hermes._prepare_catalog_workdir", return_value=None)
    @patch("sandbox.core._hermes._checked", return_value=_completed())
    @patch("sandbox.core._hermes._ssh", return_value=_completed())
    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/repos", "sandbox_home": "/home/u/sandbox",
        "worktrees": "/home/u/sandbox/runtime/hermes-worktrees", "launcher": "$HOME/.local/bin/hermes",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    def test_reconcile_restores_prior_inventory_after_post_removal_failure(
            self, snapshot, require_remote, paths, ssh, checked, prepare, install_scripts, create):
        initial = _exact_catalog_snapshot(paths.return_value)
        del next(job for job in initial["jobs"] if not job["no_agent"])["prompt_sha256"]
        snapshot.side_effect = [initial, initial]
        out = hermes.cron_reconcile("test", confirm=True, force_replace=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "rolled_back")
        self.assertTrue(out["data"]["rollback"]["verified"])
        self.assertEqual(ssh.call_count, 2)  # controlled removal, then inventory restore

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_cron_output_reads_only_latest_bounded_valid_job_output(self, require_remote, checked):
        checked.return_value = _completed(stdout=json.dumps({
            "found": True, "file": "2026-07-13.md", "output": "completed one task", "truncated": False,
            "format_supported": True, "secret_like": False, "source": "saved-output",
        }))
        out = hermes.cron_output("test", "deadbeef1234", 50)
        self.assertEqual(out["status"], "available")
        self.assertEqual(out["data"]["output"], "completed one task")
        command = checked.call_args.args[1]
        self.assertIn("deadbeef1234", command)
        self.assertTrue(command.endswith(" 50"))
        self.assertIn("## Response", command)
        self.assertIn("rsplit(marker, 1)", command)
        self.assertIn("secret_like", command)
        self.assertIn("sandbox-trigger-{job_id}.log", command)
        self.assertIn("trigger-log", command)
        with self.assertRaises(hermes.HermesError):
            hermes.cron_output("test", "../escape", 50)

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_cron_output_rejects_malformed_response_shapes(self, require_remote, checked):
        for payload in ([], {"found": "yes", "file": None, "output": "", "truncated": False}):
            with self.subTest(payload=payload):
                checked.return_value = _completed(stdout=json.dumps(payload))
                with self.assertRaisesRegex(hermes.HermesError, "invalid") as caught:
                    hermes.cron_output("test", "deadbeef1234", 50)
                self.assertEqual(caught.exception.code, "invalid_cron_output")

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_cron_output_reports_secret_like_saved_response_as_withheld(self, require_remote, checked):
        checked.return_value = _completed(stdout=json.dumps({
            "found": True, "file": "run.md", "output": "", "format_supported": True,
            "secret_like": True, "source": "saved-output", "truncated": False,
        }))
        out = hermes.cron_output("test", "deadbeef1234", 50)
        self.assertEqual(out["status"], "withheld")
        self.assertEqual(out["data"]["output"], "")

    @patch("sandbox.core._hermes._set_cron_route", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    @patch("sandbox.core._hermes._ssh", return_value=_completed())
    def test_catalog_create_expands_launcher_and_quotes_catalog_arguments(self, ssh, snapshot, route):
        snapshot.side_effect = [{"jobs": []}, {"jobs": [{"id": "deadbeef1234"}]}]
        desired = {
            "kind": "agent", "schedule": "17 */4 * * *", "prompt": "one bounded task",
            "name": "sandbox-approved-spec-task", "deliver": "local",
            "workdir": "/home/u/work tree", "profile": "terra",
        }
        out = hermes._create_catalog_job({}, {"launcher": "$HOME/.local/bin/hermes"}, desired)
        self.assertEqual(out, "deadbeef1234")
        command = ssh.call_args.args[1]
        self.assertTrue(command.startswith("$HOME/.local/bin/hermes cron create "))
        self.assertNotIn("'$HOME/.local/bin/hermes'", command)
        self.assertIn("'/home/u/work tree'", command)

    def test_repo_sync_requires_confirmation_before_remote_access(self):
        with patch("sandbox.core._hermes._require_remote") as require_remote:
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.repo_sync("test", "sandbox", False)
        self.assertEqual(caught.exception.code, "confirmation_required")
        require_remote.assert_not_called()

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/sandbox/hermes-repos", "sandbox_home": "/home/u/sandbox",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_repo_sync_refreshes_runtime_only_for_sandbox(self, require_remote, paths, checked):
        checked.side_effect = [
            _completed(stdout=json.dumps({"branch": "feature", "head": "a" * 40}) + "\n"),
            _completed(),
        ]
        out = hermes.repo_sync("test", "sandbox", True)
        self.assertTrue(out["data"]["runtime_refreshed"])
        self.assertEqual(out["commit"], "a" * 40)
        self.assertIn("git -C \"$repo\" archive HEAD", checked.call_args_list[1].args[1])

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/sandbox/hermes-repos", "sandbox_home": "/home/u/sandbox",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_repo_sync_restores_old_runtime_and_venvs_after_smoke_failure(self, require_remote, paths, checked):
        checked.side_effect = [
            _completed(stdout=json.dumps({"branch": "feature", "head": "a" * 40}) + "\n"),
            hermes.HermesError("smoke failed", "repo_sync_failed"),
        ]
        with self.assertRaises(hermes.HermesError):
            hermes.repo_sync("test", "sandbox", True)
        command = checked.call_args_list[1].args[1]
        self.assertIn('cp -a "$runtime/$rel" "$stage/$rel"', command)
        self.assertIn('if test "$had_runtime" = 1 && test -e "$old"; then mv "$old" "$runtime"; fi', command)
        self.assertIn('if ! mv "$stage" "$runtime"; then rollback; exit 1; fi', command)
        self.assertIn(".cli-venv mcp/wp-server/.venv", command)

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/sandbox/hermes-repos", "sandbox_home": "/home/u/sandbox",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_repo_sync_supports_a_fresh_server_without_live_runtime(self, require_remote, paths, checked):
        checked.side_effect = [
            _completed(stdout=json.dumps({"branch": "feature", "head": "a" * 40}) + "\n"),
            _completed(),
        ]
        out = hermes.repo_sync("test", "sandbox", True)
        command = checked.call_args_list[1].args[1]
        self.assertTrue(out["data"]["runtime_refreshed"])
        self.assertIn('had_runtime=0', command)
        self.assertIn('if test -e "$runtime"; then mv "$runtime" "$old"; had_runtime=1; fi', command)

    @patch("sandbox.core._hermes._paths", return_value={
        "repo_root": "/home/u/sandbox/hermes-repos", "sandbox_home": "/home/u/sandbox",
        "worktrees": "/home/u/sandbox/runtime/hermes-worktrees",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot", return_value={"jobs": []})
    @patch("sandbox.core._hermes._ssh")
    def test_reconcile_preview_is_side_effect_free(self, ssh, snapshot, require_remote, paths):
        out = hermes.cron_reconcile("test", confirm=False, force_replace=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "planned")
        self.assertEqual(len(out["data"]["create"]), 1)
        ssh.assert_not_called()

    @patch("sandbox.core._hermes._cron_request_evidence", return_value={
        "files_checked": 1, "failure": True, "reason": "unsupported_model",
    })
    @patch("sandbox.core._hermes._ssh", return_value=_completed())
    @patch("sandbox.core._hermes._paths", return_value={"launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    def test_verified_run_rejects_false_green_provider_failure(
            self, snapshot, require_remote, paths, ssh, evidence):
        snapshot.side_effect = [
            {"jobs": [{"id": "deadbeef1234", "name": "worker", "last_run_at": "before",
                       "last_status": "ok", "model_snapshot": "gpt-5.6-terra"}]},
            {"jobs": [{"id": "deadbeef1234", "name": "worker", "last_run_at": "after",
                       "last_status": "ok", "model_snapshot": "gpt-5.6-terra"}]},
        ]
        out = hermes.cron_verify("test", "deadbeef1234", 60, True)
        self.assertFalse(out["ok"])
        self.assertTrue(out["data"]["transitioned"])
        self.assertTrue(out["data"]["false_success"])
        self.assertEqual(out["status"], "failed")

    @patch("sandbox.core._hermes.time.sleep")
    @patch("sandbox.core._hermes.time.time", side_effect=[0, 60, 60])
    @patch("sandbox.core._hermes._cron_request_evidence", return_value={"files_checked": 0, "failure": False, "reason": ""})
    @patch("sandbox.core._hermes._ssh", return_value=_completed())
    @patch("sandbox.core._hermes._paths", return_value={"launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    def test_verified_run_marker_without_transition_remains_failed(
            self, snapshot, require_remote, paths, ssh, evidence, now, sleep):
        job = {"id": "deadbeef1234", "name": "worker", "last_run_at": None,
               "last_status": "error", "last_error": "RuntimeError: COMPLETED_SPEC_TASK",
               "model_snapshot": "gpt-5.6-terra"}
        snapshot.side_effect = [{"jobs": [job]}, {"jobs": [job]}]
        out = hermes.cron_verify("test", "deadbeef1234", 60, True)
        self.assertFalse(out["ok"])
        self.assertFalse(out["data"]["transitioned"])
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["data"]["terminal_result"]["classification"], "protocol_error")

    @patch("sandbox.core._hermes.time.sleep")
    @patch("sandbox.core._hermes.time.time", side_effect=[0, 0, 0, 1])
    @patch("sandbox.core._hermes._cron_request_evidence", return_value={"files_checked": 0, "failure": False, "reason": ""})
    @patch("sandbox.core._hermes._ssh", return_value=_completed())
    @patch("sandbox.core._hermes._paths", return_value={"launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    @patch("sandbox.core._hermes._cron_snapshot")
    def test_verified_run_waits_for_queued_scheduler_tick(
            self, snapshot, require_remote, paths, ssh, evidence, now, sleep):
        before = {"id": "deadbeef1234", "name": "worker", "last_run_at": None,
                  "last_status": None, "model_snapshot": "gpt-5.6-terra"}
        after = {**before, "last_run_at": "after", "last_status": "ok"}
        snapshot.side_effect = [{"jobs": [before]}, {"jobs": [before]}, {"jobs": [after]}]
        out = hermes.cron_verify("test", "deadbeef1234", 60, True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["data"]["transitioned"])
        sleep.assert_called_once()

    @patch("sandbox.core._hermes._gateway_ownership", return_value={
        "healthy": False, "gateway_process_count": 2,
        "units": {"hermes-gateway.service": {"active_state": "activating", "unit_file_state": "disabled"}},
    })
    @patch("sandbox.core._hermes._paths", return_value={"repo_root": "/home/u/repos"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_gateway_convergence_preview_reports_conflicting_owner(self, require_remote, paths, ownership):
        out = hermes.gateway_converge("test", confirm=False)
        self.assertEqual(out["status"], "planned")
        self.assertIn("stop legacy hermes-gateway.service", out["data"]["actions"])
        self.assertTrue(out["data"]["requires_confirm"])

    @patch("sandbox.core._hermes._ssh")
    def test_gateway_ownership_rejects_deactivating_legacy_unit(self, ssh):
        ssh.return_value = _completed(stdout=json.dumps({
            "gateway_pids": [123],
            "units": {
                "hermes-gateway-sandbox.service": {
                    "active_state": "active", "unit_file_state": "enabled", "restart_count": 0,
                },
                "hermes-gateway.service": {
                    "active_state": "deactivating", "unit_file_state": "disabled", "restart_count": 99,
                },
            },
        }))
        out = hermes._gateway_ownership({})
        self.assertFalse(out["healthy"])
        self.assertTrue(out["conflict"])

    @patch("sandbox.core._hermes._checked", return_value=_completed())
    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._gateway_ownership")
    @patch("sandbox.core._hermes._paths", return_value={"repo_root": "/home/u/repos", "launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_gateway_convergence_observes_scheduler_and_restart_stability(
            self, require_remote, paths, ownership, ssh, checked):
        unhealthy = {"healthy": False, "gateway_process_count": 2,
                     "units": {"hermes-gateway.service": {"active_state": "active", "unit_file_state": "enabled"}}}
        healthy = {"healthy": True, "gateway_process_count": 1, "units": {
            hermes.GATEWAY_UNIT: {"restart_count": 3},
            "hermes-gateway.service": {"active_state": "inactive", "unit_file_state": "disabled"},
        }}
        ownership.side_effect = [unhealthy, healthy]
        samples = [{"managed": {"active_state": "active", "unit_file_state": "enabled", "restart_count": 3},
                    "legacy": {"active_state": "inactive", "unit_file_state": "disabled", "restart_count": 0},
                    "gateway_process_count": 1, "scheduler_ok": True} for _ in range(13)]
        ssh.return_value = _completed(stdout=json.dumps({"samples": samples}))
        sleeps = []
        out = hermes.gateway_converge("test", True, stability_seconds=120, sample_interval=10,
                                      sleeper=sleeps.append)
        self.assertTrue(out["ok"])
        self.assertEqual(sleeps, [])
        self.assertEqual(out["data"]["stability"]["sample_count"], 13)
        self.assertTrue(out["data"]["stability"]["scheduler_present"])
        self.assertEqual(out["data"]["stability"]["restart_counts"], [3] * 13)
        ssh.assert_called_once()
        self.assertEqual(ssh.call_args.kwargs["timeout"], 150)
        self.assertIn("time.sleep", ssh.call_args.args[1])
        self.assertIn("while elapsed < 120", ssh.call_args.args[1])
        self.assertIn('[LAUNCHER, "cron", "status"]', ssh.call_args.args[1])

    @patch("sandbox.core._hermes._checked", return_value=_completed())
    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._gateway_ownership")
    @patch("sandbox.core._hermes._paths", return_value={"repo_root": "/home/u/repos", "launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_gateway_convergence_rejects_restart_growth(self, require_remote, paths, ownership, ssh, checked):
        unhealthy = {"healthy": False, "gateway_process_count": 2,
                     "units": {"hermes-gateway.service": {"active_state": "active", "unit_file_state": "enabled"}}}
        healthy = {"healthy": True, "gateway_process_count": 1, "units": {
            hermes.GATEWAY_UNIT: {"restart_count": 3},
            "hermes-gateway.service": {"active_state": "inactive", "unit_file_state": "disabled"},
        }}
        ownership.side_effect = [unhealthy, healthy]
        samples = [{"managed": {"active_state": "active", "unit_file_state": "enabled", "restart_count": 3},
                    "legacy": {"active_state": "inactive", "unit_file_state": "disabled", "restart_count": 0},
                    "gateway_process_count": 1, "scheduler_ok": True} for _ in range(13)]
        samples[-1]["managed"]["restart_count"] = 4
        ssh.return_value = _completed(stdout=json.dumps({"samples": samples}))
        out = hermes.gateway_converge("test", True, stability_seconds=120, sample_interval=10,
                                      sleeper=lambda _: None)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "gateway_stability_failed")
        self.assertEqual(out["data"]["stability"]["restart_counts"], [3] * 12 + [4])
        ssh.assert_called_once()

    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._gateway_ownership")
    @patch("sandbox.core._hermes._paths", return_value={"repo_root": "/home/u/repos", "launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_gateway_convergence_rejects_scheduler_status_failure(self, require_remote, paths, ownership, ssh):
        healthy = {"healthy": True, "gateway_process_count": 1, "units": {
            hermes.GATEWAY_UNIT: {"restart_count": 3},
            "hermes-gateway.service": {"active_state": "inactive", "unit_file_state": "disabled"},
        }}
        ownership.return_value = healthy
        ssh.return_value = _completed(stdout=json.dumps({"samples": [{
            "managed": {"active_state": "active", "unit_file_state": "enabled", "restart_count": 3},
            "legacy": {"active_state": "inactive", "unit_file_state": "disabled", "restart_count": 0},
            "gateway_process_count": 1, "scheduler_ok": False,
        }]}))
        out = hermes.gateway_converge("test", True, stability_seconds=0)
        self.assertFalse(out["ok"])
        self.assertFalse(out["data"]["stability"]["scheduler_present"])
        ssh.assert_called_once()

    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._gateway_ownership")
    @patch("sandbox.core._hermes._paths", return_value={"repo_root": "/home/u/repos", "launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_gateway_convergence_bounds_transient_ownership_probe_failure(
            self, require_remote, paths, ownership, ssh):
        healthy = {"healthy": True, "gateway_process_count": 1, "units": {
            hermes.GATEWAY_UNIT: {"restart_count": 3},
            "hermes-gateway.service": {"active_state": "inactive", "unit_file_state": "disabled"},
        }}
        ownership.return_value = healthy
        ssh.return_value = _completed(stdout=json.dumps({"samples": [{
            "managed": {"active_state": "active", "unit_file_state": "enabled", "restart_count": 3},
            "legacy": {"active_state": "active", "unit_file_state": "disabled", "restart_count": 0},
            "gateway_process_count": 2, "scheduler_ok": True,
        }]}))
        out = hermes.gateway_converge("test", True, stability_seconds=0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "gateway_stability_failed")
        self.assertFalse(out["data"]["stability"]["ownership_present"])
        ssh.assert_called_once()

    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._gateway_ownership")
    @patch("sandbox.core._hermes._paths", return_value={"repo_root": "/home/u/repos", "launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_gateway_convergence_rejects_malformed_remote_evidence_without_echoing_command(
            self, require_remote, paths, ownership, ssh):
        ownership.return_value = {"healthy": True, "gateway_process_count": 1, "units": {
            hermes.GATEWAY_UNIT: {"restart_count": 3},
            "hermes-gateway.service": {"active_state": "inactive", "unit_file_state": "disabled"},
        }}
        raw_command = "rm -rf /private/hermes --token should-not-appear"
        ssh.return_value = _completed(stdout=json.dumps({"samples": [{"command": raw_command}]}))
        out = hermes.gateway_converge("test", True, stability_seconds=0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "gateway_stability_failed")
        self.assertTrue(out["data"]["stability"]["malformed_evidence"])
        self.assertNotIn(raw_command, json.dumps(out))
        ssh.assert_called_once()

    @patch("sandbox.core._hermes._install_cron_scripts")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read", return_value={})
    @patch("sandbox.core._hermes._checked", return_value=_completed())
    @patch("sandbox.core._hermes._paths", return_value={
        "launcher": "$HOME/.local/bin/hermes", "repo_root": "/home/u/repos",
        "sandbox_home": "/home/u/sandbox", "sb": "/home/u/sandbox/sb-src/sb",
    })
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_setup_bounds_and_labels_each_hermes_cli_step(
            self, require_remote, paths, checked, state_read, state_write, install_scripts):
        hermes.setup("test")
        command = checked.call_args_list[0].args[1]
        self.assertIn('timeout --foreground 45 "$hermes_bin" "$@"', command)
        self.assertIn("HERMES_SETUP_STEP_FAILED", command)
        self.assertIn("run_hermes kanban init", command)
        self.assertNotIn("$HOME/.local/bin/hermes kanban init", command)
        self.assertNotIn("run_hermes mcp add", command)
        self.assertIn("merge_owned(config, integration)", command)

    @patch("sandbox.core._hermes._worktree_snapshot", return_value=[{
        "repository": "sandbox", "dirty": True, "dirty_paths": ["file.py"],
    }])
    @patch("sandbox.core._hermes._paths", return_value={})
    @patch("sandbox.core._hermes._require_remote", return_value={})
    def test_worktree_inventory_surfaces_dirty_state(self, require_remote, paths, snapshot):
        out = hermes.worktree_list("test")
        self.assertEqual(out["status"], "dirty")
        self.assertEqual(out["data"]["dirty_count"], 1)

    def test_cron_audit_rejects_effort_appended_to_model(self):
        self.assertIsNotNone(invalid_model_reason("gpt-5.6-terra/high"))
        self.assertIsNone(invalid_model_reason("gpt-5.6-terra"))
        invalid = audit_jobs([
            {"id": "3359664aaf91", "model_snapshot": "gpt-5.6-terra/high"},
            {"id": "fallback", "model_snapshot": None, "model": "gpt-5.6-sol/high"},
            {"id": "healthy", "model_snapshot": "gpt-5.6-terra"},
        ])
        self.assertEqual(invalid, [
            {"job_id": "3359664aaf91",
             "reason": "reasoning effort was appended to the model identifier"},
            {"job_id": "fallback",
             "reason": "reasoning effort was appended to the model identifier"},
        ])

    def test_cron_execution_guard_is_idempotent_and_uses_supported_runner(self):
        once = guarded_prompt("Do bounded work.")
        twice = guarded_prompt(once)
        self.assertEqual(once, twice)
        self.assertEqual(once.count(SCHEDULE_GUARD_START), 1)
        self.assertIn(".cli-venv/bin/python -m unittest", once)
        self.assertIn("never create a .hermes", once)

    def test_public_dashboard_is_exact_and_caddy_stays_loopback(self):
        fragment = hermes._public_caddy_fragment("hermes.asb.bd", False)
        self.assertIn("http://:9120", fragment)
        self.assertIn("bind 127.0.0.1", fragment)
        self.assertIn("reverse_proxy 127.0.0.1:9119", fragment)
        self.assertIn("header_up Host {upstream_hostport}", fragment)
        self.assertIn("header_up Origin http://127.0.0.1:9119", fragment)
        self.assertIn("handle {\n        respond 404", fragment)
        self.assertNotIn("0.0.0.0", fragment)
        with self.assertRaises(hermes.HermesError):
            hermes._public_plan({}, {}, {}, "other.asb.bd")

    def test_public_plan_reports_missing_configuration_without_network_access(self):
        with patch("sandbox.core._hermes._public_config", return_value={}):
            out = hermes._public_validate_cloudflare({}, "hermes.asb.bd")
        self.assertFalse(out["configured"])
        self.assertIn("account_id", out["missing"])
        self.assertIn("dns_record_id", out["missing"])

    @patch("sandbox.core._hermes._dashboard_listeners")
    @patch("sandbox.core._hermes._dashboard_status")
    @patch("sandbox.core._hermes._public_config")
    def test_public_plan_is_attach_only_and_sanitized(self, config, status, listeners):
        config.return_value = {}
        status.return_value = {"active": True}
        listeners.return_value = {"expected_loopback": True, "public_listener": False}
        plan = hermes._public_plan({}, {}, {"public_exposure": {}}, "hermes.asb.bd")
        self.assertTrue(plan["attach_only"])
        self.assertTrue(plan["ready"] is False)
        self.assertNotIn("eyj", json.dumps(plan).lower())


class TestPublicExposureLifecycle(unittest.TestCase):
    @patch("sandbox.core._hermes.cloudflare_access.Client")
    @patch("sandbox.core._hermes.cloudflare_tunnel.Client")
    @patch("sandbox.core._hermes.cloudflare_zone.Client")
    def test_public_adoption_requires_one_exact_existing_route(self, zone_client, tunnel_client, access_client):
        zone = zone_client.return_value
        zone.token = "test-token"
        zone.zone.return_value = {"id": "zone-id", "account": {"id": "account-id"}}
        zone.tunnels.return_value = [{"id": "tunnel-id"}]
        zone.access_applications.return_value = [{"id": "app-id", "type": "self_hosted", "domain": "hermes.asb.bd"}]
        zone.records.return_value = [{"id": "dns-id", "name": "hermes.asb.bd", "proxied": True}]
        tunnel_client.return_value.configuration.return_value = {"config": {"ingress": [
            {"hostname": "hermes.asb.bd", "service": "http://127.0.0.1:9120"},
            {"service": "http_status:404"},
        ]}}
        access = access_client.return_value
        access.application.return_value = {"policies": [{"id": "policy-id"}]}
        access.policy.return_value = {
            "id": "policy-id", "decision": "allow", "include": [{"email": {"email": "operator@example.test"}}],
            "mfa_config": {"mfa_disabled": False},
        }

        discovered = hermes._public_adopt_existing("hermes.asb.bd")

        self.assertEqual(discovered["tunnel_id"], "tunnel-id")
        self.assertEqual(discovered["access_application_id"], "app-id")
        self.assertEqual(discovered["access_policy_id"], "policy-id")
        self.assertEqual(discovered["connector_token_secret"], "HERMES_CLOUDFLARE_TUNNEL_CONNECTOR_TOKEN")

    def test_public_adoption_persists_only_non_secret_references(self):
        local = {"hermes": {"existing": "kept"}}
        discovered = {
            "account_id": "account-id", "access_application_id": "app-id", "access_policy_id": "policy-id",
            "tunnel_id": "tunnel-id", "zone_id": "zone-id", "dns_record_id": "dns-id",
            "access_token_secret": "CLOUDFLARE_API_TOKEN", "tunnel_api_token_secret": "CLOUDFLARE_API_TOKEN",
            "zone_token_secret": "CLOUDFLARE_API_TOKEN", "connector_token_secret": "HERMES_CLOUDFLARE_TUNNEL_CONNECTOR_TOKEN",
            "route": {"hostname": "hermes.asb.bd"}, "policy": {"decision": "allow"},
        }
        with patch("sandbox.core._hermes._local_yaml", return_value=local), \
             patch("sandbox.core._hermes._write_local_yaml") as write:
            hermes._public_adopt_config(discovered)
        saved = write.call_args.args[0]
        self.assertEqual(saved["hermes"]["existing"], "kept")
        self.assertNotIn("route", saved["hermes"]["public_access"])
        self.assertNotIn("policy", saved["hermes"]["public_access"])
        self.assertNotIn("test-token", json.dumps(saved))

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._public_install_connector")
    @patch("sandbox.core._hermes._public_caddy_apply")
    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._public_require_ready", return_value="connector-secret")
    @patch("sandbox.core._hermes._public_config", return_value={})
    @patch("sandbox.core._hermes._public_plan")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._dashboard_gate", return_value={"commit": "a" * 40})
    @patch("sandbox.core._hermes._paths", return_value={"state": "/tmp/hermes.json"})
    @patch("sandbox.core._hermes._require_remote", return_value={"ssh": "u@example.test"})
    def test_confirmed_exposure_uses_local_proxy_and_redacts_connector(
            self, require_remote, paths, gate, read_state, public_plan, config, require_ready,
            ssh, caddy, connector, write_state):
        state = {"dashboard": {}, "public_exposure": {"basic_auth": {"enabled": False}}}
        read_state.return_value = state
        public_plan.return_value = {"ready": True, "fqdn": "hermes.asb.bd"}
        ssh.side_effect = [_completed(stdout=""), _completed()]
        out = hermes.dashboard_action("test", "expose", fqdn="hermes.asb.bd", confirm=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "public")
        self.assertNotIn("connector-secret", json.dumps(out))
        caddy.assert_called_once()
        connector.assert_called_once_with(require_remote.return_value, "connector-secret")
        write_state.assert_called_once()

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._public_caddy_remove")
    @patch("sandbox.core._hermes._public_stop_connector")
    @patch("sandbox.core._hermes._remote_state_read", return_value={"dashboard": {}, "public_exposure": {"fqdn": "hermes.asb.bd", "mode": "public"}})
    @patch("sandbox.core._hermes._dashboard_gate", return_value={"commit": "a" * 40})
    @patch("sandbox.core._hermes._paths", return_value={"state": "/tmp/hermes.json"})
    @patch("sandbox.core._hermes._require_remote", return_value={"ssh": "u@example.test"})
    def test_unexpose_only_removes_local_resources(self, require_remote, paths, gate, read_state,
                                                    stop_connector, caddy_remove, write_state):
        out = hermes.dashboard_action("test", "unexpose", confirm=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ssh-only")
        stop_connector.assert_called_once()
        caddy_remove.assert_called_once()
        write_state.assert_called_once()
    def test_managed_repository_names_are_not_paths(self):
        self.assertEqual(hermes.validate_repo_name("my.repo_2"), "my.repo_2")
        for value in ("../escape", "/tmp/repo", "", "two words", ".hidden"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_repo_name(value)

    def test_backup_source_policy_rejects_nested_runtime_credentials(self):
        forbidden = (
            "config/auth.json", "secrets/.env.local", "app/credentials.json",
            "nested/cookies.txt", "tls/private.key", "tls/cert.pem",
        )
        for path in forbidden:
            self.assertTrue(hermes._backup_forbidden_source_path(path), path)
        for path in (".env.example", "hermes_cli/dashboard_auth/cookies.py", "docs/credentials.md"):
            self.assertFalse(hermes._backup_forbidden_source_path(path), path)

    def test_repository_url_rejects_userinfo_and_sanitizes(self):
        self.assertEqual(
            hermes.validate_repo_url("https://github.com/acme/example.git"),
            "https://github.com/acme/example.git",
        )
        with self.assertRaises(hermes.HermesError):
            hermes.validate_repo_url("https://user:token@github.com/acme/example.git")

    def test_state_repository_is_credential_free_github_url(self):
        self.assertEqual(
            hermes.validate_state_repo("https://github.com/alimuzzaman/hermes-agent-state.git"),
            "https://github.com/alimuzzaman/hermes-agent-state.git",
        )
        for value in (
            "git@github.com:alimuzzaman/hermes-agent-state.git",
            "https://user:token@github.com/alimuzzaman/hermes-agent-state.git",
            "https://gitlab.com/alimuzzaman/hermes-agent-state.git",
        ):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_state_repo(value)

    def test_drive_destination_is_bounded_rclone_path(self):
        self.assertEqual(hermes.validate_drive_destination("gdrive:hermes-backups"), "gdrive:hermes-backups")
        self.assertEqual(hermes.validate_drive_destination("gdrive:"), "gdrive:")
        for value in ("https://drive.google.com/x", "gdrive:../escape", "gdrive:folder;rm", "gdrive:folder space"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_drive_destination(value)

    def test_release_requires_immutable_tag_and_full_commit(self):
        self.assertEqual(hermes.validate_release("v2026.7.7.2", "a" * 40),
                         ("v2026.7.7.2", "a" * 40))
        for tag, commit in (("main", "a" * 40), ("v1", "short")):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_release(tag, commit)

    def test_default_supported_release_has_an_audited_full_commit(self):
        self.assertEqual(len(hermes.SUPPORTED_COMMIT), 40)
        self.assertEqual(hermes._expected_commit(hermes.SUPPORTED_TAG, None), hermes.SUPPORTED_COMMIT)
        self.assertEqual(hermes._expected_commit("v999.0.0", None), None)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_update_rejects_moving_branch_before_release_resolution(self, get_remote, ssh_run):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.update_plan("test", "main")
        self.assertEqual(caught.exception.code, "invalid_release")
        ssh_run.assert_not_called()

    def test_result_redacts_sensitive_values(self):
        data = hermes.result(False, "status", "test", error=hermes.HermesError(
            "failed with token=secret Authorization: Bearer secret-bearer ssh://user@host", "remote_failed"))
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("secret-bearer", json.dumps(data))
        self.assertNotIn("user@host", json.dumps(data))

    def test_result_recursively_redacts_bare_provider_and_cookie_values(self):
        github = "github_pat_" + "a" * 30
        openai = "sk-proj-" + "b" * 30
        slack = "xoxb-" + "c" * 30
        payload = hermes.result(True, "logs", "test", data={
            "output": f"{github} {openai} {slack} cookie=session-value",
            "nested": ["ya29." + "d" * 30],
        })
        rendered = json.dumps(payload)
        for secret in (github, openai, slack, "session-value", "ya29." + "d" * 30):
            self.assertNotIn(secret, rendered)
        self.assertIn("[redacted]", rendered)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_remote_timeout_becomes_a_retryable_sanitized_error(self, ssh_run):
        secret_command = ["ssh", "host", "Authorization: Bearer should-not-appear"]
        ssh_run.side_effect = subprocess.TimeoutExpired(secret_command, 30)
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._ssh({"ssh": "ubuntu@example.test"}, "true", timeout=30)
        self.assertEqual(caught.exception.code, "remote_unavailable")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(str(caught.exception), "remote command timed out after 30 seconds")
        self.assertNotIn("should-not-appear", str(caught.exception))

    @patch("sandbox.core._hermes.remote.ssh_command_args", return_value=["ssh", "host"])
    @patch("sandbox.core._hermes.subprocess.Popen")
    def test_streaming_ssh_timeout_terminates_its_child(self, popen, _args):
        class TimedOutProcess:
            args = ["ssh", "host"]
            stdin, stdout, stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            killed = False

            def wait(self, timeout):
                raise subprocess.TimeoutExpired(self.args, timeout)

            def poll(self):
                return None

            def kill(self):
                self.killed = True

            def communicate(self):
                return b"", b""

        process = TimedOutProcess()
        popen.return_value = process
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._ssh_stdin_with_progress({"ssh": "ubuntu@example.test"}, "safe", b"data", timeout=1)
        self.assertEqual(caught.exception.code, "remote_unavailable")
        self.assertTrue(process.killed)

    @patch("sandbox.core._hermes.remote.resolve_sandbox_home")
    def test_sandbox_home_timeout_becomes_a_retryable_sanitized_error(self, resolve_home):
        resolve_home.side_effect = subprocess.TimeoutExpired(["ssh"], 15)
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._sandbox_home({"ssh": "ubuntu@example.test"})
        self.assertEqual(caught.exception.code, "sandbox_home_unavailable")
        self.assertTrue(caught.exception.retryable)

    def test_job_payload_keeps_the_public_result_envelope(self):
        payload = _job_payload("test", "status", {
            "job_id": "0123456789abcdef", "status": "running", "stdout": "safe",
        })
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "job_status")
        self.assertEqual(payload["job_id"], "0123456789abcdef")
        missing = _job_payload("test", "status", {
            "job_id": "0123456789abcdef", "status": "not_found",
        })
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "job_not_found")

    @patch("sandbox.commands.hermes.sys.stdin")
    @patch("sandbox.commands.hermes.subprocess.run")
    @patch("sandbox.commands.hermes.remote.get_remote")
    def test_provider_auth_reports_missing_github_cli(self, get_remote, run, stdin):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        stdin.isatty.return_value = False
        stdin.buffer = io.BytesIO(b"github_pat_repository_scoped_token")
        run.return_value = _completed(returncode=127)
        with self.assertRaises(hermes.HermesError) as caught:
            _repo_action(SimpleNamespace(subaction="auth", target="github", remote="test", token_stdin=True))
        self.assertEqual(caught.exception.code, "github_cli_missing")
        self.assertIn("command -v gh", run.call_args.args[0][-1])

    @patch("sandbox.commands.hermes.remote.get_remote")
    def test_provider_auth_rejects_broad_browser_oauth_before_remote_lookup(self, get_remote):
        with self.assertRaises(hermes.HermesError) as caught:
            _repo_action(SimpleNamespace(subaction="auth", target="github", remote="test", token_stdin=False))
        self.assertEqual(caught.exception.code, "fine_grained_token_required")
        get_remote.assert_not_called()

    @patch("sandbox.commands.hermes.sys.stdin")
    @patch("sandbox.commands.hermes.subprocess.run")
    @patch("sandbox.commands.hermes.remote.get_remote")
    def test_provider_auth_accepts_fine_grained_token_only_on_stdin(self, get_remote, run, stdin):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        stdin.isatty.return_value = False
        stdin.buffer = io.BytesIO(b"github_pat_repository_scoped_token")
        run.side_effect = [_completed(), _completed(), _completed()]
        out = _repo_action(SimpleNamespace(subaction="auth", target="github", remote="test", token_stdin=True))
        self.assertFalse(out["data"]["existing"])
        self.assertEqual(run.call_count, 3)
        login = run.call_args_list[1]
        self.assertIn("gh auth login --hostname github.com --git-protocol https --with-token", login.args[0][-1])
        self.assertNotIn("github_pat_repository_scoped_token", " ".join(login.args[0]))
        self.assertEqual(login.kwargs["input"], b"github_pat_repository_scoped_token")


class TestProfileRendering(unittest.TestCase):
    def test_routing_profile_declares_coordinator_and_specialist_workers(self):
        routing = hermes.render_routing_profile()
        self.assertEqual(routing["delegation"], {
            "provider": "openai-codex",
            "model": "gpt-5.6-terra",
            "max_concurrent_children": 1,
            "max_spawn_depth": 1,
            "orchestrator_enabled": False,
        })
        self.assertEqual(routing["kanban"]["default_assignee"], "terra")
        self.assertEqual(routing["auxiliary"]["kanban_decomposer"]["model"], "gpt-5.3-codex-spark")
        self.assertEqual(routing["auxiliary"]["triage_specifier"]["model"], "gpt-5.6-sol")
        self.assertEqual(
            {worker["name"]: worker["model"] for worker in routing["workers"]},
            {"luna": "gpt-5.6-luna", "terra": "gpt-5.6-terra", "sol": "gpt-5.6-sol"},
        )
        luna = next(worker for worker in routing["workers"] if worker["name"] == "luna")
        self.assertEqual(luna["toolsets"], ["safe", "file"])
        self.assertIn("Never call write, patch, or rename", luna["soul"])
        self.assertIn("SANDBOX_ROUTING_BEGIN", routing["coordinator_soul"])

    def test_routing_setup_expands_the_remote_hermes_launcher(self):
        command = hermes._routing_setup_command({
            "launcher": "$HOME/.local/bin/hermes",
            "sandbox_home": "/home/u/sandbox",
            "sb": "/home/u/sandbox/sb-src/sb",
        })
        self.assertNotIn(" config set ", command)
        self.assertIn("merge_owned(root_config, integration)", command)
        self.assertIn("merge_owned(config, integration)", command)
        self.assertIn('config["model"]', command)
        self.assertNotIn("'$HOME/.local/bin/hermes'", command)
        self.assertLess(command.index("kanban init"), command.index('root_soul = root / "SOUL.md"'))
        self.assertIn('existing.rstrip() + "\\n\\n" + block + "\\n"', command)
        self.assertIn('worker["soul"] + "\\n"', command)
        embedded_python = command.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
        compile(embedded_python, "routing_setup.py", "exec")

    def test_profile_has_full_sequential_sandbox_mcp_access(self):
        rendered = hermes.render_profile("/home/u/sandbox", "/home/u/sandbox/sb-src/sb")
        self.assertEqual(rendered["mcp_servers"]["sandbox"]["command"],
                         "/home/u/sandbox/sb-src/sb")
        self.assertFalse(rendered["mcp_servers"]["sandbox"]["supports_parallel_tool_calls"])
        self.assertNotIn("include", rendered["mcp_servers"]["sandbox"]["tools"])
        self.assertNotIn("exclude", rendered["mcp_servers"]["sandbox"]["tools"])
        self.assertEqual(rendered["approvals"]["mode"], "manual")
        self.assertEqual(rendered["approvals"]["cron_mode"], "deny")

    def test_profile_snapshot_has_expected_non_secret_owned_settings(self):
        rendered = hermes.render_profile("/home/u/sandbox", "/home/u/sandbox/sb-src/sb")
        self.assertEqual(rendered, {
            "model": {"default": "gpt-5.3-codex-spark", "provider": "openai-codex"},
            "terminal": {"backend": "local", "home_mode": "real", "cwd": "/home/u/sandbox/hermes-repos"},
            "approvals": {"mode": "manual", "cron_mode": "deny", "mcp_reload_confirm": True,
                          "destructive_slash_confirm": True},
            "checkpoints": {"enabled": True},
            "mcp_servers": {"sandbox": {
                "command": "/home/u/sandbox/sb-src/sb", "args": ["mcp"],
                "env": {"SANDBOX_HOME": "/home/u/sandbox"}, "enabled": True,
                "connect_timeout": 60, "timeout": 1200,
                "supports_parallel_tool_calls": False, "tools": {"resources": True, "prompts": True},
            }},
        })
        self.assertNotRegex(json.dumps(rendered).lower(), r"token|password|secret")

    def test_gateway_allowlist_fails_closed(self):
        for value in ([], ["*"], ["all"]):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_gateway_allowlist(value)
        self.assertEqual(hermes.validate_gateway_allowlist(["123", "team"]), ["123", "team"])

    def test_gateway_user_unit_uses_systemd_home_specifier(self):
        body = hermes._gateway_unit({"repo_root": "/home/ubuntu/sandbox/hermes-repos"})
        self.assertIn("Environment=HERMES_HOME=%h/.hermes", body)
        self.assertIn("ExecStart=%h/.local/bin/hermes gateway run --replace", body)
        self.assertNotIn("$HOME", body)

    def test_gateway_install_command_restores_prior_unit_on_failure(self):
        command = hermes._gateway_install_command("hermes-gateway-sandbox.service", "[Service]\nExecStart=/bin/true\n")
        self.assertIn("rollback()", command)
        self.assertIn("loginctl enable-linger", command)
        self.assertIn("mv \"$backup\" \"$target\"", command)

    def test_dashboard_validators_and_loopback_unit(self):
        self.assertEqual(hermes.validate_dashboard_port(None), 9119)
        self.assertEqual(hermes.validate_dashboard_fqdn("Hermes.Example.com."), "hermes.example.com")
        for port in (80, 65536, "not-a-port"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_dashboard_port(port)
        with self.assertRaises(hermes.HermesError):
            hermes.validate_dashboard_fqdn("https://hermes.example.com")
        unit = hermes._dashboard_unit(9120)
        self.assertIn("--host 127.0.0.1 --port 9120 --no-open --tui", unit)
        self.assertIn("TimeoutStartSec=180", unit)
        self.assertNotIn("--insecure", unit)
        self.assertIn("NoNewPrivileges=true", unit)

    def test_dashboard_install_command_has_rollback(self):
        command = hermes._dashboard_install_command(hermes.DASHBOARD_UNIT, hermes._dashboard_unit(9119))
        self.assertIn("rollback()", command)
        self.assertIn("systemctl --user enable", command)
        self.assertIn("loginctl enable-linger", command)

    @patch("sandbox.core._hermes._ssh")
    def test_dashboard_listener_probe_distinguishes_loopback_and_public(self, ssh):
        ssh.return_value = _completed(stdout=(
            "LISTEN 0 2048 127.0.0.1:9119 0.0.0.0:*\n"
            "LISTEN 0 2048 0.0.0.0:9222 0.0.0.0:*\n"
        ))
        observed = hermes._dashboard_listeners({"ssh": "ubuntu@example.test"}, 9119)
        self.assertTrue(observed["expected_loopback"])
        self.assertFalse(observed["public_listener"])
        ssh.return_value = _completed(stdout="LISTEN 0 2048 0.0.0.0:9119 0.0.0.0:*\n")
        observed = hermes._dashboard_listeners({"ssh": "ubuntu@example.test"}, 9119)
        self.assertFalse(observed["expected_loopback"])
        self.assertTrue(observed["public_listener"])

    def test_dashboard_lifecycle_waits_for_loopback_and_stops_on_failure(self):
        command = hermes._dashboard_lifecycle_command("start", 9119)
        self.assertIn("seq 1 30", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("systemctl --user stop", command)
        self.assertNotIn("public_listener=1 }}", command)

    def test_drive_backup_command_is_full_and_passphrase_stdin_only(self):
        command = hermes._drive_backup_command(
            {"sandbox_home": "/home/u/sandbox", "sb": "/home/u/sandbox/sb-src/sb", "state": "/home/u/sandbox/runtime/hermes.json"},
            "gdrive:hermes-backups", "20260711T000000Z-deadbeef",
        )
        self.assertNotIn("<<'PY'", command)
        self.assertIn("base64.b64decode(sys.argv[1])", command)
        encoded = command.rsplit(" ", 1)[-1].strip()
        generated = base64.b64decode(encoded).decode()
        self.assertIn('SANDBOX = pathlib.Path("/home/u/sandbox")', generated)
        command = generated
        compile(generated, "<drive-backup>", "exec")
        self.assertIn('f"{HOME}/.hermes"', command)
        self.assertIn('f"{HOME}/.config/gh"', command)
        self.assertIn('f"{HOME}/.config/rclone"', command)
        self.assertIn('"--batch"', command)
        self.assertIn('"copyto"', command)
        self.assertIn("instance", command)
        self.assertIn('"docker", "cp"', command)
        self.assertIn("drive-volume-fallbacks", command)
        self.assertIn('"--exclude", f"{SANDBOX}/runtime/.drive-volume-fallbacks-*"', command)
        self.assertIn('"snapshot"', command)
        self.assertIn("archive_bytes", command)
        self.assertIn("atexit.register(shutil.rmtree", command)
        self.assertIn("str(fallback)", command)
        self.assertIn("ignore_errors=True", command)
        self.assertNotIn("passphrase=", command)
        self.assertNotIn('"--ignore-failed-read"', command)
        archive_upload = command.index('f"{DESTINATION}/{BACKUP_ID}.tar.gz.gpg"')
        state_upload = command.index('f"{DESTINATION}/{BACKUP_ID}.state.snar"')
        manifest_upload = command.index('f"{DESTINATION}/{BACKUP_ID}.manifest.json"')
        self.assertLess(archive_upload, state_upload)
        self.assertLess(state_upload, manifest_upload)
        self.assertIn("id_pattern = re.compile", command)
        self.assertIn("name.strip()[:-14]", command)
        self.assertIn('base_manifest.get("id") != base_id', command)
        self.assertIn("database container unavailable for {instance}", command)
        self.assertIn("database snapshot unavailable for {instance}", command)
        self.assertNotIn("database container unavailable for {instance}; continuing", command)
        self.assertNotIn("database snapshot unavailable for {instance}; continuing", command)

    def test_drive_restore_reinstates_github_auth_and_services(self):
        command = hermes._drive_restore_command(
            {"sandbox_home": "/home/u/sandbox", "sb": "/home/u/sandbox/sb-src/sb", "state": "/home/u/sandbox/runtime/hermes.json"},
            "gdrive:hermes-full-recovery", "20260711T000000Z-deadbeef",
        )
        self.assertIn(".config/gh", command)
        self.assertIn(".config/rclone", command)
        self.assertIn("gpg --batch", command)
        self.assertIn("drive-volume-fallbacks", command)
        self.assertIn("id_pattern.fullmatch", command)
        self.assertIn("data.get('id') != current", command)
        self.assertIn("data.get('archive') != f'{current}.tar.gz.gpg'", command)
        self.assertIn("cipher_sha256", command)
        self.assertIn("elif data['chain_id'] != chain_id", command)
        self.assertIn("print('CHAIN_ID=' + chain_id)", command)
        self.assertIn("tarfile.open(archive_path, 'r:gz')", command)
        self.assertIn("member.linkname", command)
        self.assertIn("--no-same-owner", command)
        self.assertIn("docker run --rm", command)
        self.assertIn("systemctl --user start hermes-gateway-sandbox.service", command)
        self.assertIn("restore_path()", command)
        self.assertIn("trap rollback EXIT", command)
        self.assertIn("committed=1", command)

    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._dashboard_listeners")
    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._dashboard_port_preflight")
    @patch("sandbox.core._hermes._dashboard_status")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._dashboard_gate")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_dashboard_start_rolls_back_when_service_is_not_healthy(
            self, require_remote, paths, gate, read_state, status, preflight, checked, listeners, ssh):
        require_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        paths.return_value = {"state": "/tmp/hermes.json"}
        gate.return_value = {"commit": hermes.SUPPORTED_COMMIT}
        read_state.return_value = {"dashboard": {"installed": True}}
        status.side_effect = [
            {"active": False, "port": 9119},
            {"active": False, "port": 9119},
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.dashboard_action("test", "start")
        self.assertEqual(caught.exception.code, "dashboard_start_failed")
        preflight.assert_called_once()
        self.assertIn("seq 1 30", checked.call_args.args[1])
        self.assertIn("systemctl --user stop", ssh.call_args.args[1])

    @patch("sandbox.core._hermes._dashboard_listeners")
    @patch("sandbox.core._hermes._dashboard_status")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._dashboard_gate")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_dashboard_doctor_rejects_public_listener(
            self, require_remote, paths, gate, read_state, status, listeners):
        require_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        paths.return_value = {"state": "/tmp/hermes.json"}
        gate.return_value = {"commit": hermes.SUPPORTED_COMMIT}
        read_state.return_value = {"dashboard": {"installed": True, "auth_mode": "upstream"}}
        status.return_value = {"active": True, "port": 9119}
        listeners.return_value = {"expected_loopback": True, "public_listener": True, "listeners": ["0.0.0.0:9119"]}
        out = hermes.dashboard_action("test", "doctor")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "dashboard_health_failed")

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_dashboard_install_expands_remote_home(self, require_remote, paths, read_state, write_state, checked):
        require_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        paths.return_value = {"state": "/tmp/hermes.json"}
        read_state.return_value = {
            "schema_version": 1,
            "installation": {"commit": hermes.SUPPORTED_COMMIT},
            "gates": {"v2_operations": {"status": "passed", "commit": hermes.SUPPORTED_COMMIT,
                "integration_schema": hermes.STATE_SCHEMA,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS}}},
        }
        hermes.dashboard_action("test", "install")
        command = checked.call_args.args[1]
        self.assertIn('cd "$HOME/.hermes/hermes-agent"', command)
        self.assertIn("python3 -m venv .venv", command)
        self.assertIn(".venv/bin/pip install", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_dashboard_status_uses_current_v2_gate(self, get_remote, ssh_run):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        state = {"schema_version": 1, "installation": {"commit": hermes.SUPPORTED_COMMIT},
                 "gates": {"v2_operations": {"status": "passed", "commit": hermes.SUPPORTED_COMMIT,
                    "integration_schema": hermes.STATE_SCHEMA,
                    "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS}}},
                 "dashboard": {"installed": True, "auth_mode": "upstream"}}
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout=json.dumps(state)),
                               _completed(stdout=json.dumps(state)),
                               _completed(stdout="active=active\nenabled=enabled\npid=123\nport=9119\n")]
        out = hermes.dashboard_action("test", "status")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["host"], "127.0.0.1")
        self.assertIn("<configured-test-ssh-target>", out["data"]["ssh_forward"])

    def test_dashboard_exposure_plan_requires_feature_015(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.validate_dashboard_fqdn("bad host")
        self.assertEqual(caught.exception.code, "invalid_dashboard_fqdn")


class TestRemoteCommands(unittest.TestCase):
    def setUp(self):
        self.entry = {"ssh": "ubuntu@example.test", "provisioned": True}

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cron_validate_reports_invalid_routing_without_prompt_data(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.return_value = _completed(stdout=json.dumps({"jobs": [{
            "id": "3359664aaf91", "name": "work", "model_snapshot": "gpt-5.6-terra/high",
        }]}))
        out = hermes.cron_validate("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "invalid_cron_routing")
        self.assertEqual(out["data"]["invalid"][0]["job_id"], "3359664aaf91")
        self.assertNotIn("prompt", json.dumps(out).lower())

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cron_route_writes_validated_model_atomically(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.return_value = _completed(stdout='{"job_id":"3359664aaf91"}\n')
        out = hermes.cron_route("test", "3359664aaf91", "terra", True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["model"], "gpt-5.6-terra")
        command = ssh_run.call_args.args[1]
        self.assertIn("model_snapshot", command)
        self.assertIn("os.replace", command)
        self.assertIn("base64.b64decode", command)
        self.assertNotIn("terra/high", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cron_mutations_require_confirmation_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.cron_route("test", "3359664aaf91", "terra", False)
        self.assertEqual(caught.exception.code, "confirmation_required")
        ssh_run.assert_not_called()

    @patch("sandbox.core._hermes.remote.resolve_sandbox_home", return_value="/home/ubuntu/sandbox")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cron_run_preserves_the_queued_trigger_result(self, get_remote, ssh_run, _resolve_home):
        get_remote.return_value = self.entry
        ssh_run.return_value = _completed(stdout="triggered\n")
        out = hermes.cron_run("test", "3359664aaf91", True)
        self.assertTrue(out["ok"])
        command = ssh_run.call_args.args[1]
        self.assertEqual(command, "$HOME/.local/bin/hermes cron run --accept-hooks 3359664aaf91")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_install_uses_pinned_noninteractive_installer(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="a" * 40 + "\n"),
            _completed(),
            _completed(stdout="hermes 0.18.2\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertTrue(out["ok"])
        command = ssh_run.call_args_list[2].args[1]
        self.assertIn("--branch v2026.7.7.2", command)
        self.assertIn("--commit " + "a" * 40, command)
        self.assertIn("--non-interactive", command)
        self.assertIn('remote get-url origin', command)
        self.assertIn('remote add origin https://github.com/NousResearch/hermes-agent.git', command)
        self.assertIn("--skip-setup", command)
        self.assertIn("verify-tag", command)
        self.assertIn("allowed_signers", command)
        self.assertNotIn("curl -fsSL", command)
        self.assertIn("rev-parse HEAD", command)
        self.assertIn("venv/bin/hermes", command)
        self.assertIn("launcher_tmp", command)
        self.assertIn("$HOME/.local/bin/hermes", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_install_rejects_failed_release_provenance_before_launcher_check(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="a" * 40 + "\n"),
            _completed(returncode=42, stderr="HERMES_RELEASE_PROVENANCE_FAILED\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertEqual(caught.exception.code, "release_provenance_failed")
        self.assertEqual(ssh_run.call_count, 3)

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_install_reconciles_partial_state_to_the_pinned_release(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="a" * 40 + "\n"),
            _completed(), _completed(stdout="hermes 0.18.2\n"),
        ]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {},
                                   "installation": {"status": "partial"}}
        hermes.install("test", "v2026.7.7.2", "a" * 40)
        persisted = write_state.call_args.args[2]
        self.assertEqual(persisted["installation"], {"release_tag": "v2026.7.7.2", "commit": "a" * 40, "status": "installed"})

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_identical_reinstall_uses_the_same_pinned_installer_invocation(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="a" * 40 + "\n"), _completed(), _completed(stdout="hermes 0.18.2\n"),
            _completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="a" * 40 + "\n"), _completed(), _completed(stdout="hermes 0.18.2\n"),
        ]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}}
        hermes.install("test", "v2026.7.7.2", "a" * 40)
        hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertEqual(ssh_run.call_args_list[2].args[1], ssh_run.call_args_list[6].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_status_distinguishes_configured_and_running_lifecycle(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="hermes 0.18.2\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {},
                                          "profile": {"sandbox_home": "/home/ubuntu/sandbox"}, "sessions": {}})),
        ]
        out = hermes.status("test")
        self.assertEqual(out["status"], "configured")
        self.assertEqual(out["data"]["running_sessions"], 0)

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_setup_preserves_installed_revision_metadata(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed()]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {},
                                   "installation": {"release_tag": "v2026.7.7.2", "commit": "a" * 40, "status": "installed"}}
        with patch.object(hermes, "_install_cron_scripts"):
            hermes.setup("test")
        persisted = write_state.call_args.args[2]
        self.assertEqual(persisted["installation"], {"release_tag": "v2026.7.7.2", "commit": "a" * 40, "status": "configured"})
        self.assertIn("sandbox-integration.json.backup", ssh_run.call_args_list[1].args[1])
        self.assertIn("merge_owned(root_config, integration)", ssh_run.call_args_list[1].args[1])
        self.assertIn('config["model"]', ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_setup_persists_effective_unfiltered_sandbox_mcp_config(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed()]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}}

        with patch.object(hermes, "_install_cron_scripts"):
            hermes.setup("test")

        command = ssh_run.call_args_list[1].args[1]
        self.assertNotIn("mcp add", command)
        self.assertNotIn("mcp remove", command)
        self.assertIn("merge_owned(root_config, integration)", command)
        self.assertIn("merge_owned(config, integration)", command)
        self.assertIn("integration_payload=", command)
        self.assertIn("if config_path.exists() else {}", command)

    @patch("sandbox.core._hermes._ssh_stdin")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_openrouter_setup_writes_key_only_over_stdin_and_verifies_model(self, get_remote, paths, ssh_stdin):
        get_remote.return_value = self.entry
        paths.return_value = {"launcher": "$HOME/.local/bin/hermes"}
        ssh_stdin.return_value = _completed(stdout=b"openrouter\tstealth/ox-alpha\tmax\n")

        output = hermes.configure_openrouter("test", "credential-value")

        self.assertTrue(output["ok"])
        self.assertEqual(output["data"]["model"], "stealth/ox-alpha")
        self.assertEqual(output["data"]["reasoning_effort"], "max")
        command = ssh_stdin.call_args.args[1]
        self.assertIn("OPENROUTER_API_KEY=", command)
        self.assertIn("read_raw_config", command)
        self.assertIn("atomic_yaml_write", command)
        self.assertIn('agent["reasoning_effort"] =', command)
        self.assertIn("max", command)
        self.assertEqual(ssh_stdin.call_args.args[2], b"credential-value")

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_setup_converges_worker_routing_without_auth_or_gateway_activation(
            self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed()]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}}

        with patch.object(hermes, "_install_cron_scripts"):
            hermes.setup("test")

        command = ssh_run.call_args_list[1].args[1]
        for expected in (
            "profile create luna",
            "profile create terra",
            "profile create sol",
            'root_config["delegation"] = routing["delegation"]',
            'root_config["kanban"] = routing["kanban"]',
            'root_config["auxiliary"] = routing["auxiliary"]',
            'config["model"]',
            'config.setdefault("agent", {})["reasoning_effort"]',
        ):
            self.assertIn(expected, command)
        self.assertNotIn("gateway install", command)
        self.assertNotIn("gateway start", command)
        self.assertNotIn(" auth add ", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_rejects_path_escape_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        with self.assertRaises(hermes.HermesError):
            hermes.clone_repo("test", "https://github.com/acme/repo.git", "../escape")
        ssh_run.assert_not_called()

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_places_ref_before_repository_and_destination(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
            _completed(),
            _completed(),
        ]
        hermes.clone_repo("test", "https://github.com/acme/repo.git", "repo", "v1.2.3")
        command = ssh_run.call_args_list[1].args[1]
        clone = command[command.rindex("git clone"):]
        self.assertLess(clone.index("--branch v1.2.3"), clone.index("https://github.com/acme/repo.git"))
        self.assertIn("submodule update --init --recursive", command)
        self.assertIn("lfs pull", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_matching_existing_origin_is_idempotent(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="EXISTS_MATCH\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.clone_repo("test", "https://github.com/acme/repo.git", "repo")
        self.assertTrue(out["data"]["existing"])
        self.assertIn("EXISTS_MATCH", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_derives_canonical_name_and_uses_a_temporary_destination(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}})),
            _completed(),
        ]
        out = hermes.clone_repo("test", "https://github.com/acme/example.git")
        self.assertEqual(out["repo"], "example")
        command = ssh_run.call_args_list[1].args[1]
        self.assertIn(".example.clone-", command)
        self.assertIn("mv", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_provider_failure_is_sanitized(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(returncode=1, stderr="token=not-for-output"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.clone_repo("test", "https://github.com/acme/example.git", "example")
        self.assertEqual(caught.exception.code, "clone_failed")
        self.assertNotIn("not-for-output", str(caught.exception))

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_async_run_uses_worktree_by_default(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=""),
            _completed(stdout="disk_mb=4096\nmemory_mb=4096\njobs=0\nworktrees=0\n"),
            _completed(stdout="0123456789abcdef\t/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/abcd\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.run("test", "repo", "inspect", async_=True)
        self.assertTrue(out["data"]["worktree"])
        self.assertEqual(out["job_id"], "0123456789abcdef")
        self.assertIn("git worktree add", ssh_run.call_args_list[3].args[1])
        self.assertIn("flock -w 30", ssh_run.call_args_list[3].args[1])
        self.assertIn("attempt=$((attempt + 1))", ssh_run.call_args_list[3].args[1])
        self.assertIn("setsid sh -c", ssh_run.call_args_list[3].args[1])

    def test_no_worktree_command_never_creates_a_worktree(self):
        command = hermes._worktree_command({
            "repo_root": "/home/ubuntu/sandbox/hermes-repos", "locks": "/home/ubuntu/sandbox/runtime/hermes-locks",
            "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "hermes_home": "$HOME/.hermes",
            "launcher": "$HOME/.local/bin/hermes",
        }, "repo", "inspect", worktree=False, async_=True)
        self.assertIn("worktree=false", command)
        self.assertNotIn("git worktree add", command)
        self.assertNotIn("ensure_instance", command)

    def test_worktree_setup_uses_integration_owned_root(self):
        command = hermes._worktree_setup({
            "repo_root": "/home/ubuntu/sandbox/hermes-repos",
            "locks": "/home/ubuntu/sandbox/runtime/hermes-locks",
            "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees",
        }, "repo")
        self.assertIn("/home/ubuntu/sandbox/runtime/hermes-worktrees/repo", command)
        self.assertNotIn("mkdir -p .worktrees", command)
        self.assertNotIn('cwd="$PWD/.worktrees/', command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_dashboard_refuses_without_v2_gate_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}})),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.dashboard_action("test", "install")
        self.assertEqual(caught.exception.code, "v2_gate_required")
        self.assertEqual(ssh_run.call_count, 2)

    def test_v2_gate_requires_complete_current_revision_evidence(self):
        state = {
            "schema_version": 1,
            "installation": {"commit": "a" * 40},
            "gates": {"v2_operations": {
                "status": "passed", "commit": "a" * 40,
                "integration_schema": hermes.STATE_SCHEMA,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS},
            }},
        }
        self.assertEqual(hermes._v2_gate(state)["status"], "passed")
        state["installation"]["commit"] = "b" * 40
        gate = hermes._v2_gate(state)
        self.assertEqual(gate["status"], "pending")
        self.assertFalse(gate["revision_matches"])

    def test_v2_gate_requires_current_integration_schema(self):
        state = {
            "schema_version": 1,
            "installation": {"commit": "a" * 40},
            "gates": {"v2_operations": {
                "status": "passed", "commit": "a" * 40,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS},
            }},
        }
        gate = hermes._v2_gate(state)
        self.assertEqual(gate["status"], "pending")
        self.assertIn("integration_schema", gate["missing_checks"])
        self.assertFalse(gate["integration_schema_matches"])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_v2_acceptance_never_fabricates_a_passing_record(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}})),
        ]
        out = hermes.acceptance_v2("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "v2_gate_incomplete")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_update_plan_is_read_only_and_reports_immutable_target(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="b" * 40 + "\n"),
            _completed(stdout="a" * 40 + "\n"),
        ]
        out = hermes.update_plan("test", "v2026.7.7.2", "b" * 40)
        self.assertEqual(out["status"], "update_available")
        self.assertEqual(out["data"]["current_commit"], "a" * 40)
        self.assertEqual(out["data"]["backup"], "create verified backup before apply")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_release_provenance_plan_verifies_signature_in_disposable_checkout(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="b" * 40 + "\n"),
            _completed(stdout="HEAD=" + "a" * 40 + "\nORIGIN=https://github.com/NousResearch/hermes-agent.git\n"),
            _completed(stdout="PROVENANCE_VERIFIED:v2026.7.7.2:b" + "b" * 39 + "\n"),
            _completed(stdout="HEAD=" + "a" * 40 + "\nORIGIN=https://github.com/NousResearch/hermes-agent.git\n"),
        ]
        out = hermes.release_provenance_plan("test", "v2026.7.7.2", "b" * 40)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "verified")
        self.assertEqual(out["data"]["origin"], "https://github.com/NousResearch/hermes-agent.git")
        self.assertTrue(out["data"]["installed_checkout_unchanged"])
        command = ssh_run.call_args_list[3].args[1]
        self.assertIn("mktemp -d", command)
        self.assertIn("fetch -q --depth=1", command)
        self.assertIn("verify-tag", command)
        self.assertIn("HERMES_RELEASE_PROVENANCE_FAILED", command)
        self.assertNotIn("hermes-agent/;", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_release_provenance_plan_rejects_checkout_change(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="b" * 40 + "\n"),
            _completed(stdout="HEAD=" + "a" * 40 + "\nORIGIN=https://github.com/NousResearch/hermes-agent.git\n"),
            _completed(stdout="PROVENANCE_VERIFIED:v2026.7.7.2:b" + "b" * 39 + "\n"),
            _completed(stdout="HEAD=" + "c" * 40 + "\nORIGIN=https://github.com/NousResearch/hermes-agent.git\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.release_provenance_plan("test", "v2026.7.7.2", "b" * 40)
        self.assertEqual(caught.exception.code, "installed_checkout_changed")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_release_provenance_plan_rejects_noncanonical_origin(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="b" * 40 + "\n"),
            _completed(stdout="HEAD=" + "a" * 40 + "\nORIGIN=https://example.invalid/other.git\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.release_provenance_plan("test", "v2026.7.7.2", "b" * 40)
        self.assertEqual(caught.exception.code, "invalid_installed_origin")

    def test_update_apply_quiesces_and_resumes_an_active_gateway(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        installed = {"version": "v2026.7.7.2", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", return_value=installed), \
             patch.object(hermes, "_install_cron_scripts"), \
             patch.object(hermes, "health", return_value={"ok": True, "data": {"degraded_reasons": []}}), \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", side_effect=[_completed(stdout="active\n"), _completed()] ) as ssh_run:
            out = hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertTrue(out["data"]["gateway_resumed"])
        self.assertIn("systemctl --user stop", ssh_run.call_args_list[0].args[1])
        self.assertIn("systemctl --user start", ssh_run.call_args_list[1].args[1])

    def test_update_apply_attempts_restore_after_install_failure(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", side_effect=hermes.HermesError("broken", "install_failed")), \
             patch.object(hermes, "backup_restore") as restore, \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", return_value=_completed(stdout="inactive\n")):
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertEqual(caught.exception.code, "update_rolled_back")
        restore.assert_called_once_with("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)

    def test_update_rollback_resumes_a_previously_active_gateway(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", side_effect=hermes.HermesError("broken", "install_failed")), \
             patch.object(hermes, "backup_restore") as restore, \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", return_value=_completed(stdout="active\n")) as ssh_run:
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertEqual(caught.exception.code, "update_rolled_back")
        restore.assert_called_once_with("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)
        self.assertIn("systemctl --user start", ssh_run.call_args_list[1].args[1])

    def test_update_apply_attempts_restore_after_health_failure(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", return_value={"version": "v2026.7.7.2", "commit": "b" * 40}), \
             patch.object(hermes, "_install_cron_scripts"), \
             patch.object(hermes, "health", return_value={"ok": False, "data": {"degraded_reasons": ["gateway_ownership"]}}), \
             patch.object(hermes, "backup_restore") as restore, \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", return_value=_completed(stdout="inactive\n")):
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertEqual(caught.exception.code, "update_rolled_back")
        restore.assert_called_once_with("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_backup_list_discovers_archives_without_state_metadata(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="20260711T000000Z-deadbeef.tar.gz\t123\n"),
        ]
        out = hermes.backup_list("test")
        backup = out["data"]["backups"]["20260711T000000Z-deadbeef"]
        self.assertEqual(backup["size_bytes"], 123)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_backup_create_returns_archive_digest_without_state_round_trip(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="2048\n"),
            _completed(stdout="a" * 64 + "  archive.tar.gz\n"),
        ]
        out = hermes.backup_create("test")
        self.assertEqual(out["data"]["sha256"], "a" * 64)
        self.assertEqual(out["data"]["free_mb"], 2048)
        self.assertEqual(ssh_run.call_count, 3)
        command = ssh_run.call_args_list[2].args[1]
        self.assertIn(".sha256", command)
        self.assertIn("tail -n +11", command)
        self.assertIn("runtime/hermes.json", command)
        self.assertIn("git -C \"$repo\" pack-objects --stdout --revs", command)
        self.assertIn("hermes-agent.pack", command)
        self.assertIn("hermes-agent.tag", command)
        self.assertIn("hermes-agent.commit", command)
        self.assertIn("tar -C \"$repo\" -cf - venv", command)
        self.assertIn("tar -C \"$repo\" -cf - .venv", command)
        self.assertIn("$stage/launcher/hermes", command)
        self.assertIn("home runtime units launcher", command)
        self.assertIn("_backup_forbidden_source_path", command)
        self.assertIn("PYTHONPATH=", command)
        self.assertNotIn("tar -C \"$HOME\"", command)
        self.assertIn("auth\\.json", command)
        self.assertIn("credentials?", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_backup_create_refuses_insufficient_disk_before_archive_creation(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="511\n")]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.backup_create("test")
        self.assertEqual(caught.exception.code, "backup_insufficient_space")
        self.assertEqual(ssh_run.call_count, 2)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_restore_creates_pre_restore_backup_and_verifies_digest(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="2048\n"),
            _completed(stdout="a" * 64 + "  archive.tar.gz\n"),
            _completed(),
        ]
        with patch.object(hermes, "setup") as setup:
            out = hermes.backup_restore("test", "20260711T000000Z-deadbeef", True)
        setup.assert_called_once_with("test")
        self.assertIn("pre_restore_backup_id", out["data"])
        command = ssh_run.call_args_list[5].args[1]
        self.assertIn(".sha256", command)
        self.assertIn("sha256sum", command)
        self.assertIn("tar -tzf", command)
        self.assertIn("$stage/home/.hermes", command)
        self.assertIn("hermes-agent.pack", command)
        self.assertIn("index-pack --stdin --fix-thin", command)
        self.assertIn("remote add origin https://github.com/NousResearch/hermes-agent.git", command)
        self.assertIn("refs/tags/", command)
        self.assertIn("$restore/.git/shallow", command)
        self.assertIn("checkout -q --detach", command)
        self.assertIn("tar -C \"$source/.hermes/hermes-agent\" -cf - venv", command)
        self.assertIn("hermes-agent.previous", command)
        self.assertIn("launcher_previous", command)
        self.assertIn("if test -f \"$stage/launcher/hermes\"", command)
        self.assertIn("exec \"$HOME/.hermes/hermes-agent/venv/bin/hermes\"", command)
        self.assertIn("dashboard_active", command)
        self.assertIn("$restore/venv/bin/hermes", command)
        self.assertNotIn("pip install", command)
        self.assertIn("runtime/hermes.json", command)

    def test_restore_requires_confirmation_before_remote_access(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.backup_restore("test", "20260711T000000Z-deadbeef", False)
        self.assertEqual(caught.exception.code, "confirmation_required")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_rollback_restore_skips_pre_restore_backup_when_runtime_is_missing(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
        ]
        with patch.object(hermes, "backup_create") as create, \
             patch.object(hermes, "setup") as setup:
            out = hermes.backup_restore("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)
        create.assert_not_called()
        setup.assert_called_once_with("test")
        self.assertIsNone(out["data"]["pre_restore_backup_id"])
        command = ssh_run.call_args_list[1].args[1]
        self.assertIn("had_previous=0", command)
        self.assertIn("had_launcher=0", command)
        self.assertNotIn('test -d "$HOME/.hermes/hermes-agent"; test -f', command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_public_restore_skips_pre_restore_backup_when_runtime_is_missing(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(returncode=1),
            _completed(),
        ]
        with patch.object(hermes, "backup_create") as create, \
             patch.object(hermes, "_record_v2_evidence"), \
             patch.object(hermes, "setup") as setup:
            out = hermes.backup_restore("test", "20260711T000000Z-deadbeef", True)
        create.assert_not_called()
        setup.assert_called_once_with("test")
        self.assertIsNone(out["data"]["pre_restore_backup_id"])
        self.assertIn("hermes-agent.restore", ssh_run.call_args_list[2].args[1])

    def test_update_apply_requires_confirmation_before_remote_access(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.update_apply("test", "v2026.7.7.2", "a" * 40, False)
        self.assertEqual(caught.exception.code, "confirmation_required")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_setup_persists_explicit_allowlist(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
        ]
        out = hermes.gateway("test", "setup", ["operator-1"])
        self.assertEqual(out["data"]["allowlist_entries"], 1)
        self.assertIn("sandbox-gateway-allowlist.json", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_start_rejects_missing_recorded_allowlist(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=""),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.gateway("test", "start")
        self.assertEqual(caught.exception.code, "unsafe_gateway_allowlist")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_install_enables_user_lingering_for_reboot_recovery(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"allowlist": ["operator-1"]})),
            _completed(),
        ]
        out = hermes.gateway("test", "install")
        self.assertTrue(out["ok"])
        self.assertIn("loginctl enable-linger", ssh_run.call_args_list[2].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_status_reports_inactive_without_treating_it_as_a_command_error(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="inactive\n")]
        out = hermes.gateway("test", "status")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "inactive")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_logs_are_bounded_and_report_truncation(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="x" * 5000)]
        out = hermes.gateway("test", "logs", lines=1000)
        self.assertTrue(out["data"]["truncated"])
        self.assertEqual(len(out["data"]["output"]), 4000)
        self.assertIn("journalctl", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cleanup_defaults_to_dry_run_and_retains_dirty_worktrees(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "running", "worktree_path": "/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/a"},
            }})),
            _completed(stdout="running\t0123456789abcdef\n"),
            _completed(stdout="clean\t/home/ubuntu/sandbox/hermes-repos/repo\t/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/a\n"
                              "dirty\t/home/ubuntu/sandbox/hermes-repos/repo\t/home/ubuntu/sandbox/hermes-repos/repo/.worktrees/b\n"),
        ]
        out = hermes.cleanup("test", confirm=False)
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(len(out["data"]["clean_candidates"]), 0)
        self.assertEqual(len(out["data"]["dirty_retained"]), 1)
        self.assertEqual(len(out["data"]["active_retained"]), 1)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_reconciliation_marks_only_provably_dead_jobs_stale(self, ssh_run):
        entry = self.entry
        paths = {"jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "state": "/home/ubuntu/sandbox/runtime/hermes.json",
                 "sandbox_home": "/home/ubuntu/sandbox"}
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
            "0123456789abcdef": {"state": "running", "worktree_path": "/worktree/a"},
        }}
        ssh_run.side_effect = [_completed(stdout="stale\t0123456789abcdef\n"), _completed()]
        reconciled, stale = hermes._reconcile_sessions(entry, paths, state)
        self.assertEqual(stale, ["0123456789abcdef"])
        self.assertEqual(reconciled["sessions"]["0123456789abcdef"]["state"], "stale")
        self.assertTrue(reconciled["sessions"]["0123456789abcdef"]["requires_manual_review"])

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_resource_preflight_refuses_concurrent_job_limit(self, ssh_run):
        paths = {"policy": "/home/ubuntu/.hermes/policy.json", "sandbox_home": "/home/ubuntu/sandbox",
                 "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "repo_root": "/home/ubuntu/sandbox/hermes-repos",
                 "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees"}
        ssh_run.side_effect = [
            _completed(stdout=json.dumps({"max_jobs": 1, "max_worktrees": 8, "min_free_disk_mb": 1024, "min_free_memory_mb": 512})),
            _completed(stdout="disk_mb=4096\nmemory_mb=4096\njobs=1\nworktrees=0\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._resource_preflight(self.entry, paths)
        self.assertEqual(caught.exception.code, "resource_limit")

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_resource_preflight_counts_only_worktree_roots(self, ssh_run):
        paths = {"policy": "/home/ubuntu/.hermes/policy.json", "sandbox_home": "/home/ubuntu/sandbox",
                 "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "repo_root": "/home/ubuntu/sandbox/hermes-repos",
                 "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees"}
        ssh_run.side_effect = [
            _completed(stdout="{}"),
            _completed(stdout="disk_mb=4096\nmemory_mb=4096\njobs=0\nworktrees=1\n"),
        ]
        preflight = hermes._resource_preflight(self.entry, paths)
        self.assertEqual(preflight["metrics"]["worktrees"], 1)
        probe = ssh_run.call_args_list[1].args[1]
        self.assertIn("/home/ubuntu/sandbox/runtime/hermes-worktrees -mindepth 2 -maxdepth 2", probe)
        self.assertIn("-path '*/.worktrees/*' -prune -print", probe)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_resource_preflight_refuses_disk_and_memory_thresholds(self, ssh_run):
        paths = {"policy": "/home/ubuntu/.hermes/policy.json", "sandbox_home": "/home/ubuntu/sandbox",
                 "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "repo_root": "/home/ubuntu/sandbox/hermes-repos",
                 "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees"}
        ssh_run.side_effect = [
            _completed(stdout="{}"),
            _completed(stdout="disk_mb=100\nmemory_mb=100\njobs=0\nworktrees=0\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._resource_preflight(self.entry, paths)
        self.assertEqual(caught.exception.code, "resource_limit")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_confirmed_cleanup_prunes_only_completed_job_artifacts(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {}})),
            _completed(stdout="clean\t/home/ubuntu/sandbox/hermes-repos/repo\t/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/a\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.cleanup("test", confirm=True)
        self.assertEqual(out["data"]["completed_job_retention_days"], 7)
        self.assertIn("-name '*.status'", ssh_run.call_args_list[4].args[1])

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cleanup_resolves_only_explicitly_confirmed_stale_sessions(self, get_remote, ssh_run, write_state):
        get_remote.return_value = self.entry
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
            "0123456789abcdef": {"state": "stale", "worktree_path": None},
        }}
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps(state)),
            _completed(stdout=""),
            _completed(),
        ]
        out = hermes.cleanup("test", confirm=True, resolve_stale=True)
        self.assertEqual(out["data"]["resolved_stale_sessions"], ["0123456789abcdef"])
        written = write_state.call_args.args[2]
        self.assertEqual(written["sessions"]["0123456789abcdef"]["state"], "dismissed")
        self.assertEqual(written["sessions"]["0123456789abcdef"]["resolution"], "operator_confirmed")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_job_status_reads_bounded_incremental_output(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="completed\n0\noutput\t11\t3\t8\nfinished\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "running"},
            }})),
            _completed(),
        ]
        out = hermes.job_status("test", "0123456789abcdef", 3)
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["stdout"], "finished")
        self.assertEqual(out["next_offset"], 11)
        self.assertFalse(out["truncated"])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_job_kill_marks_running_job_cancelled(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="killed\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "running"},
            }})),
            _completed(),
        ]
        out = hermes.job_kill("test", "0123456789abcdef")
        self.assertTrue(out["killed"])
        self.assertEqual(out["status"], "cancelled")
        self.assertIn("kill -- -", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_doctor_uses_stable_check_names_for_home_relative_paths(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=1\nsandbox_mcp=1\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
        ]
        out = hermes.doctor("test")
        self.assertTrue(out["ok"])
        self.assertTrue(out["data"]["direct_sb"])
        self.assertTrue(out["data"]["mcp_configured"])
        self.assertTrue(out["data"]["mcp_contract_complete"])
        self.assertTrue(out["data"]["mcp_catalog_complete"])
        command = ssh_run.call_args_list[1].args[1]
        self.assertIn("sandbox_mcp_contract", command)
        self.assertIn("$HOME/.hermes/config.yaml", command)
        self.assertIn("mcp_servers", command)
        self.assertIn("supports_parallel_tool_calls", command)
        self.assertIn("include|exclude", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_doctor_refuses_an_incomplete_mcp_catalog(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=1\nsandbox_mcp=0\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
        ]
        out = hermes.doctor("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "doctor_failed")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_doctor_refuses_an_invalid_effective_mcp_contract(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=0\n"
                               "sandbox_mcp=1\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
        ]
        out = hermes.doctor("test")
        self.assertFalse(out["ok"])
        self.assertFalse(out["data"]["mcp_contract_complete"])
        self.assertEqual(out["error"]["code"], "doctor_failed")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_status_reports_absent_when_direct_cli_check_fails(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(returncode=1)]
        out = hermes.status("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "absent")
        self.assertEqual(out["error"]["code"], "not_ready")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_health_aggregates_gateway_sessions_and_gate_without_repair(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=1\nsandbox_mcp=1\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "stale"},
            }})),
            _completed(stdout="active\nyes\n"),
        ]
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
            "0123456789abcdef": {"state": "stale"},
        }}
        diagnostic = {"ok": True, "data": {"checks": {}}, "error": None}
        with patch.object(hermes, "doctor", return_value=diagnostic), \
             patch.object(hermes, "_paths", return_value={"state": "/tmp/hermes.json"}), \
             patch.object(hermes, "_remote_state_read", return_value=state), \
             patch.object(hermes, "_reconcile_sessions", return_value=(state, [])), \
             patch.object(hermes, "_ssh", return_value=_completed(stdout="active\nyes\nenabled\n")), \
             patch.object(hermes, "_gateway_ownership", return_value={"healthy": True, "conflict": False}), \
             patch.object(hermes, "_cron_snapshot", return_value={"jobs": []}), \
             patch.object(hermes, "_worktree_snapshot", return_value=[]), \
             patch.object(hermes, "reconciliation_plan", return_value={"changes": False, "catalog_fingerprint": "a" * 64}):
            out = hermes.health("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["data"]["gateway"]["state"], "active")
        self.assertEqual(out["data"]["gateway"]["linger"], "yes")
        self.assertEqual(out["data"]["sessions"]["stale"], 1)
        self.assertIn("stale_session", out["data"]["reasons"])

    def test_health_observes_state_without_reconciliation_or_persistence(self):
        before = {
            "schema_version": 1,
            "last_boot_id": "11111111-1111-1111-1111-111111111111",
            "installation": {"commit": hermes.SUPPORTED_COMMIT},
            "sessions": {},
            "gates": {"v2_operations": {"commit": hermes.SUPPORTED_COMMIT,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS if name != "reboot_recovery"}}},
        }
        diagnostic = {"ok": True, "data": {"checks": {}}, "error": None}
        with patch.object(hermes, "doctor", return_value=diagnostic), \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch.object(hermes, "_paths", return_value={"state": "/tmp/hermes.json"}), \
             patch.object(hermes, "_remote_state_read", return_value=before), \
             patch.object(hermes, "_reconcile_sessions") as reconcile, \
             patch.object(hermes, "_remote_state_write") as write_state, \
             patch.object(hermes, "_ssh", return_value=_completed(stdout="inactive\nno\n22222222-2222-2222-2222-222222222222\n")), \
             patch.object(hermes, "_gateway_ownership", return_value={"healthy": True, "conflict": False}), \
             patch.object(hermes, "_cron_snapshot", return_value={"jobs": []}), \
             patch.object(hermes, "_worktree_snapshot", return_value=[]), \
             patch.object(hermes, "reconciliation_plan", return_value={"changes": False, "catalog_fingerprint": "a" * 64}):
            out = hermes.health("test")
        reconcile.assert_not_called()
        write_state.assert_not_called()
        self.assertEqual(before["last_boot_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(out["data"]["v2_gate"]["status"], "pending")

    def test_health_degrades_when_scheduler_probe_is_unavailable(self):
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {}}
        diagnostic = {"ok": True, "data": {"checks": {}}, "error": None}
        with patch.object(hermes, "doctor", return_value=diagnostic), \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch.object(hermes, "_paths", return_value={"state": "/tmp/hermes.json"}), \
             patch.object(hermes, "_remote_state_read", return_value=state), \
             patch.object(hermes, "_ssh", return_value=_completed(stdout="active\nyes\nenabled\n")), \
             patch.object(hermes, "_gateway_ownership", return_value={"healthy": True, "conflict": False}), \
             patch.object(hermes, "_cron_snapshot", side_effect=hermes.HermesError("scheduler unavailable", "remote_command_failed")), \
             patch.object(hermes, "_worktree_snapshot", return_value=[]):
            out = hermes.health("test")
        self.assertFalse(out["ok"])
        self.assertIn("scheduler_unavailable", out["data"]["reasons"])
        self.assertFalse(out["data"]["components"]["scheduler"]["evidence"]["available"])

    def test_health_preserves_recovered_terminal_wrapper_error_without_degrading(self):
        entry = {**self.entry, "mcp_service": {"service_name": "sandbox-mcp-remote.service"}}
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {}}
        diagnostic = {"ok": True, "data": {"checks": {}}, "error": None}
        service = {"installed": True, "enabled": True, "active": True, "linger": True,
                   "ownership": "proven", "listener_state": "expected", "listener_expected": True,
                   "auth_state": "ok", "authenticated": True}
        observed = {"jobs": [{"id": "job-1", "name": "catalog-job", "last_status": "error",
                                "last_run_at": "now", "last_error": "RuntimeError: COMPLETED_SPEC_TASK"}]}
        with patch.object(hermes, "doctor", return_value=diagnostic), \
             patch.object(hermes, "_require_remote", return_value=entry), \
             patch.object(hermes, "_paths", return_value={"state": "/tmp/hermes.json"}), \
             patch.object(hermes, "_remote_state_read", return_value=state), \
             patch.object(hermes, "_ssh", return_value=_completed(stdout="active\nyes\nenabled\n")), \
             patch.object(hermes, "_gateway_ownership", return_value={"healthy": True, "conflict": False}), \
             patch.object(hermes, "_cron_snapshot", return_value=observed), \
             patch.object(hermes, "_worktree_snapshot", return_value=[]), \
             patch.object(hermes, "reconciliation_plan", return_value={"changes": False, "catalog_fingerprint": "a" * 64}), \
             patch.object(hermes.remote, "remote_mcp_service_status", return_value=service):
            out = hermes.health("test")
        self.assertTrue(out["ok"])
        self.assertNotIn("cron_result_protocol_error", out["data"]["reasons"])
        self.assertEqual(out["data"]["cron"]["recovered_protocol_results"], ["job-1"])

    def test_health_does_not_recover_terminal_marker_without_transition(self):
        entry = {**self.entry, "mcp_service": {"service_name": "sandbox-mcp-remote.service"}}
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {}}
        diagnostic = {"ok": True, "data": {"checks": {}}, "error": None}
        service = {"installed": True, "enabled": True, "active": True, "linger": True,
                   "ownership": "proven", "listener_state": "expected", "listener_expected": True,
                   "auth_state": "ok", "authenticated": True}
        observed = {"jobs": [{"id": "job-1", "name": "catalog-job", "last_status": "error",
                                "last_run_at": None, "last_error": "RuntimeError: COMPLETED_SPEC_TASK"}]}
        with patch.object(hermes, "doctor", return_value=diagnostic), \
             patch.object(hermes, "_require_remote", return_value=entry), \
             patch.object(hermes, "_paths", return_value={"state": "/tmp/hermes.json"}), \
             patch.object(hermes, "_remote_state_read", return_value=state), \
             patch.object(hermes, "_ssh", return_value=_completed(stdout="active\nyes\nenabled\n")), \
             patch.object(hermes, "_gateway_ownership", return_value={"healthy": True, "conflict": False}), \
             patch.object(hermes, "_cron_snapshot", return_value=observed), \
             patch.object(hermes, "_worktree_snapshot", return_value=[]), \
             patch.object(hermes, "reconciliation_plan", return_value={"changes": False, "catalog_fingerprint": "a" * 64}), \
             patch.object(hermes.remote, "remote_mcp_service_status", return_value=service):
            out = hermes.health("test")
        self.assertFalse(out["ok"])
        self.assertIn("cron_failure", out["data"]["reasons"])
        self.assertIn("cron_result_protocol_error", out["data"]["reasons"])
        self.assertEqual(out["data"]["cron"]["recovered_protocol_results"], [])

    @patch("sandbox.core._hermes.subprocess.run")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_chat_opens_tty_in_a_worktree(self, get_remote, ssh_run, process):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="/home/ubuntu/sandbox/hermes-repos/repo/.worktrees/123\n"),
        ]
        process.return_value = _completed()
        out = hermes.chat("test", "repo")
        self.assertTrue(out["data"]["worktree"])
        self.assertIn("-tt", process.call_args.args[0])
        self.assertIn("git worktree add", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes._checked", return_value=_completed(stdout='{"chats": [{"elapsed_seconds": 42, "pid": 123, "state": "Ss+"}]}'))
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_chat_status_reports_only_live_process_metadata(self, get_remote, checked):
        get_remote.return_value = self.entry
        out = hermes.chat_status("test")
        self.assertEqual(out["status"], "running")
        self.assertEqual(out["data"]["chats"], [{"elapsed_seconds": 42, "pid": 123, "state": "Ss+"}])
        self.assertIn('ps", "-u"', checked.call_args.args[1])
        self.assertNotIn("args\": items", checked.call_args.args[1])

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._paths", return_value={"launcher": "$HOME/.local/bin/hermes", "sandbox_home": "/home/u/sandbox"})
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_enabling_sandbox_skills_preserves_external_dirs_and_verifies_discovery(self, get_remote, paths, checked):
        get_remote.return_value = self.entry
        checked.side_effect = [
            _completed(stdout="configured\n"),
            _completed(),
            _completed(stdout="sandbox-cli\nfix\nsnapshot\nsecret-inspection\nspeckit-refine\nwp-debug\n__SANDBOX_HERMES_PLUGINS__\n"),
        ]
        out = hermes.skills_action("test", "enable-sandbox", confirm=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "enabled")
        self.assertEqual(out["data"]["external_dir"], "/home/u/sandbox/sb-src/skills")
        setup = checked.call_args_list[0].args[1]
        self.assertIn("external_dirs", setup)
        self.assertIn("sandbox-cli", setup)
        self.assertIn("plugins enable security-guidance", checked.call_args_list[1].args[1])
        self.assertEqual(out["data"]["enabled_plugins"], ["security-guidance"])

    @patch("sandbox.core._hermes._checked", return_value=_completed(stdout="sandbox-cli\n__SANDBOX_HERMES_PLUGINS__\n"))
    @patch("sandbox.core._hermes._paths", return_value={"launcher": "$HOME/.local/bin/hermes"})
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_skills_status_is_read_only(self, get_remote, paths, checked):
        get_remote.return_value = self.entry
        out = hermes.skills_action("test", "status")
        self.assertEqual(out["status"], "ready")
        self.assertIn("plugins list", checked.call_args.args[1])


class TestLocalState(unittest.TestCase):
    def test_unversioned_legacy_state_migrates_in_memory(self):
        state = hermes._normalize_state({"repositories": {"repo": {}}})
        self.assertEqual(state["schema_version"], hermes.STATE_SCHEMA)
        self.assertEqual(state["sessions"], {})

    def test_state_round_trip_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hermes.json"
            hermes.write_state(path, {"schema_version": hermes.STATE_SCHEMA, "repositories": {}})
            self.assertEqual(hermes.read_state(path)["repositories"], {})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.with_name("hermes.json.lock").stat().st_mode & 0o777, 0o600)

    def test_state_write_cleans_temp_file_when_serialization_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hermes.json"
            state = {"schema_version": hermes.STATE_SCHEMA, "repositories": {}}
            with patch.object(hermes.json, "dump", side_effect=RuntimeError("serialization failed")):
                with self.assertRaises(RuntimeError):
                    hermes.write_state(path, state)
            self.assertEqual(sorted(item.name for item in Path(directory).iterdir()), ["hermes.json.lock"])

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_remote_state_writes_hold_a_bounded_lock(self, ssh_run):
        ssh_run.return_value = _completed()
        hermes._remote_state_write(
            {"ssh": "ubuntu@example.test"},
            {"sandbox_home": "/home/ubuntu/sandbox", "state": "/home/ubuntu/sandbox/runtime/hermes.json"},
            {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}},
            expected_digest="a" * 64,
        )
        command = ssh_run.call_args.args[1]
        self.assertIn("flock -w 30", command)
        self.assertIn("state_conflict", command)
        self.assertIn("python3 -c", command)
        self.assertIn("trap 'rm -f", command)
        self.assertIn("os.fsync", command)
        self.assertIn("elif test", command)


if __name__ == "__main__":
    unittest.main(verbosity=2)

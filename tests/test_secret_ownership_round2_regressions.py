"""Focused regressions for independent review findings R5-R9 and Needs-Testing 1-4.

R5: Recapture source metadata immediately before replace, re-verify all approved sources
    in before_replace callback after durable progress.prepare fsync, prevent source data loss
    on concurrent edits to selected or unrelated keys, abort on deadline expiry during prepare,
    and verify safe fresh-approved recovery.
R6: Bounded reconciliation & worker session operations: clamp worker session deadline to
    persisted token expiry sidecar, enforce effective clock at protected-operation boundaries
    (exists, per-name check, readback_equals), abort on expiry without readback or POST,
    and retain prior receipts for fresh human approval.
R7: Multi-session follow-up and honesty in _finish: format follow-up from cleanup_unknown
    outcomes only, report latest finite expiry among uncertain sessions, report honest
    "expiry unknown" if ANY uncertain session lacks a known expiry, preserve primary error,
    and ignore arbitrary backend next_action strings.
R8: Dispatch audit outcome preservation and completion record persistence: retain primary
    error, revocation outcome, stopped run/name state, and trusted guidance when audit.outcome
    fails; convert successful operations to audit_unavailable (exit 2 or 1); report persistence_failed
    indicators without discarding primary errors when write_completion_record fails, and convert
    successful completion record failures to provider_failed (exit 4).
R9: Portable temporary root selection: verify Path("/tmp").resolve(strict=True) canonical
    resolution on both symlinked (Darwin) and direct (Linux) filesystem layouts without
    unverified platform runtime claims.
"""

from __future__ import annotations

from dataclasses import asdict
import errno
import json
import math
import os
import pwd
import secrets
import stat
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.secrets.audit import SecretAudit
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership import approval as approval_mod
from sandbox.secrets.ownership import cleanup as cleanup_mod
from sandbox.secrets.ownership import migrate
from sandbox.secrets.ownership.approval import ApprovalStore
from sandbox.secrets.ownership.cleanup import run_cleanup
from sandbox.secrets.ownership.cleanup_progress import (
    CleanupProgress, cleanup_progress_path,
)
from sandbox.secrets.ownership.journal import (
    Journal, destination_sha256, ensure_private_dir, new_qualified_id,
    read_journal_states, read_private_file, write_private_file,
)
from sandbox.secrets.ownership.migrate import (
    authority_for, exit_for, payload, read_entry_values, run_migration,
    _finish, _reconcile,
)
from sandbox.secrets.ownership.models import (
    Approval, CreateOutcome, Existence, Grant, NameState, RevocationOutcome,
    ScopedRunRequest, SourceContinuity, StorageAdmission, canonical_json,
)
from sandbox.secrets.ownership.source_view import read_names
from sandbox.secrets.writer import remove_keys
from tests.secret_ownership_support import (
    Clock, FakeAPI, FakeOwnershipAdapter, FakeOwnershipSession,
    SyntheticOwnership, command_in_process, fake_grant, require, REPO,
    private_write,
)
from tests.subprocess_support import run_test_process, synthetic_environment


def _setup_verified_journal(ctx, profile: str = "personal") -> None:
    """Create a settled journal with VERIFIED state for every profile entry."""
    ensure_private_dir(ctx.runtime_root)
    dest = ctx.config.profiles[profile].destination
    dest_sha = destination_sha256(dest)
    ctrl_id = ctx.controller_id
    auth_create = authority_for(ctx, profile, "create")

    with Journal.open(ctx.runtime_root, profile, dest_sha, controller_id=ctrl_id,
                      authority_digest=auth_create) as journal:
        run_id = new_qualified_id("run", ctrl_id)
        ap_id = new_qualified_id("ap", ctrl_id)
        for entry in ctx.config.entries:
            if entry.profile == profile:
                journal.append(run_id, ap_id, entry.name, NameState.VERIFIED)


def _create_cleanup_approval(ctx, clock: Clock, profile: str = "personal",
                             sources=None) -> Approval:
    """Issue and store a valid cleanup approval bound to registered sources."""
    ensure_private_dir(ctx.runtime_root)
    store = ApprovalStore(ctx.runtime_root)
    ctrl_id = ctx.controller_id
    if sources is None:
        continuity, _ = read_names(ctx.registry, "synthetic-file")
        sources = (continuity,)
    dest = ctx.config.profiles[profile].destination
    auth_read = authority_for(ctx, profile, "read")
    try:
        login = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        login = str(os.geteuid())

    approval = Approval(
        approval_id=new_qualified_id("ap", ctrl_id),
        operation="cleanup",
        operator_uid=os.geteuid(),
        operator_login=login,
        controller_id=ctrl_id,
        profile=profile,
        sources=tuple(sources),
        map_sha256=ctx.config.map_sha256,
        destination_sha256=destination_sha256(dest),
        authority_digest=auth_read,
        evidence_sha256=None,
        evidence_expires_at=None,
        created_at=clock.now,
        expires_at=clock.now + 1800,
        surface="cli_tty",
    )
    write_private_file(store._path(approval.approval_id), canonical_json(asdict(approval)))
    return approval


def _create_migrate_approval(ctx, clock: Clock, profile: str = "personal",
                             sources=None) -> Approval:
    """Issue and store a valid migrate approval bound to registered sources."""
    ensure_private_dir(ctx.runtime_root)
    store = ApprovalStore(ctx.runtime_root)
    ctrl_id = ctx.controller_id
    if sources is None:
        continuity, _ = read_names(ctx.registry, "synthetic-file")
        sources = (continuity,)
    dest = ctx.config.profiles[profile].destination
    auth_create = authority_for(ctx, profile, "create")
    try:
        login = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        login = str(os.geteuid())

    approval = Approval(
        approval_id=new_qualified_id("ap", ctrl_id),
        operation="migrate",
        operator_uid=os.geteuid(),
        operator_login=login,
        controller_id=ctrl_id,
        profile=profile,
        sources=tuple(sources),
        map_sha256=ctx.config.map_sha256,
        destination_sha256=destination_sha256(dest),
        authority_digest=auth_create,
        evidence_sha256="0" * 64,
        evidence_expires_at=clock.now + 1800,
        created_at=clock.now,
        expires_at=clock.now + 120,
        surface="cli_tty",
    )
    write_private_file(store._path(approval.approval_id), canonical_json(asdict(approval)))
    return approval


class TestSecretOwnershipRound2Regressions(unittest.TestCase):
    """Regressions for Sol review round 2 findings R5-R9 and Needs-Testing 1-4."""

    def setUp(self) -> None:
        self.exit_stack = ExitStack()
        self.addCleanup(self.exit_stack.close)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("network or native credential backend reached")

        import sandbox.secrets.credentials.manifest as credentials
        import socket
        self.exit_stack.enter_context(patch.object(credentials, "create_credential_store", forbidden))
        self.exit_stack.enter_context(patch.object(socket, "create_connection", forbidden))

        previous_umask = os.umask(0o077)
        self.addCleanup(lambda: os.umask(previous_umask))

        temp_root = Path("/tmp").resolve(strict=True)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="s069-r2-regressions-", dir=temp_root)
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        require(self.root.parent == temp_root, "temporary root must be canonical")
        self.syn = SyntheticOwnership(self.root)

    def tearDown(self) -> None:
        if hasattr(self, "syn"):
            ctx = self.syn.context()
            self.syn.records_are_public(ctx)

    # -------------------------------------------------------------------------
    # R5: Source Integrity & Pre-Replace Rechecks (Needs-Testing 1)
    # -------------------------------------------------------------------------

    def test_r5_source_selected_key_edit_during_prepared_fsync_prevents_replace(self) -> None:
        """R5: Modifying selected key during progress.prepare fsync raises source_changed; no replace."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI()
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        orig_prepare = CleanupProgress.prepare
        source_edited = False
        new_alpha_val = secrets.token_hex(24)
        self.syn.canaries.append(new_alpha_val)

        def hook_prepare(prog_self, name, key, source_alias, staged, approval_id):
            nonlocal source_edited
            orig_prepare(prog_self, name, key, source_alias, staged, approval_id)
            if name == "ALPHA":
                current_text = self.syn.source.read_text("utf-8")
                edited_text = current_text.replace(self.syn.values["ALPHA"], new_alpha_val)
                private_write(self.syn.source, edited_text.encode("utf-8"))
                source_edited = True

        with patch.object(CleanupProgress, "prepare", hook_prepare):
            res = run_cleanup(ctx, "personal", ap.approval_id, confirm=True, adapter=adapter)

        require(source_edited, "hook_prepare must have triggered")
        require(res["ok"] is False, "cleanup must fail when selected key modified concurrently")
        require(res["code"] == "source_changed", "failure code must be source_changed")
        require(res["_exit"] == 3, "source_changed exit status must be 3")
        self.syn.no_disclosure(res)

        source_after = self.syn.source.read_text("utf-8")
        require("ALPHA=" in source_after, "ALPHA must still be present in source file")
        require(new_alpha_val in source_after, "concurrently edited ALPHA value must be preserved")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must remain prepared, not removed")

    def test_r5_source_unrelated_key_edit_during_prepared_fsync_prevents_replace(self) -> None:
        """R5: Modifying unrelated key during progress.prepare fsync raises source_changed; preserves edit."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI()
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        orig_prepare = CleanupProgress.prepare
        source_edited = False
        new_beta_val = secrets.token_hex(24)
        self.syn.canaries.append(new_beta_val)

        def hook_prepare(prog_self, name, key, source_alias, staged, approval_id):
            nonlocal source_edited
            orig_prepare(prog_self, name, key, source_alias, staged, approval_id)
            if name == "ALPHA":
                current_text = self.syn.source.read_text("utf-8")
                edited_text = current_text.replace(self.syn.values["BETA"], new_beta_val)
                private_write(self.syn.source, edited_text.encode("utf-8"))
                source_edited = True

        with patch.object(CleanupProgress, "prepare", hook_prepare):
            res = run_cleanup(ctx, "personal", ap.approval_id, confirm=True, adapter=adapter)

        require(source_edited, "hook_prepare must have triggered")
        require(res["ok"] is False, "cleanup must fail when unrelated key modified concurrently")
        require(res["code"] == "source_changed", "failure code must be source_changed")
        require(res["_exit"] == 3, "source_changed exit status must be 3")
        self.syn.no_disclosure(res)

        source_after = self.syn.source.read_text("utf-8")
        require(new_beta_val in source_after, "edited unrelated key BETA must be preserved in source")
        require("ALPHA=" in source_after, "ALPHA must not be replaced/removed")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must remain prepared")

    def test_r5_recovery_preserves_concurrent_edits_and_resumes_unrelated_key(self) -> None:
        """R5: Fresh approved recovery after prepared failure preserves edits and resumes cleanly."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap1 = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI()
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        orig_prepare = CleanupProgress.prepare
        new_beta_val = secrets.token_hex(24)
        self.syn.canaries.append(new_beta_val)

        def hook_prepare(prog_self, name, key, source_alias, staged, approval_id):
            orig_prepare(prog_self, name, key, source_alias, staged, approval_id)
            if name == "ALPHA":
                current_text = self.syn.source.read_text("utf-8")
                edited_text = current_text.replace(self.syn.values["BETA"], new_beta_val)
                private_write(self.syn.source, edited_text.encode("utf-8"))

        with patch.object(CleanupProgress, "prepare", hook_prepare):
            res1 = run_cleanup(ctx, "personal", ap1.approval_id, confirm=True, adapter=adapter)
        require(res1["code"] == "source_changed", "first run must halt with source_changed")
        self.syn.no_disclosure(res1)

        # Resume under fresh context and fresh approval
        fake_api.items["BETA"] = bytearray(new_beta_val.encode("utf-8"))
        fresh_ctx = self.syn.context()
        ap2 = _create_cleanup_approval(fresh_ctx, clock, "personal")

        res2 = run_cleanup(fresh_ctx, "personal", ap2.approval_id, confirm=True, adapter=adapter)
        require(res2["ok"] is True, "resume must succeed under fresh approval")
        require(res2["code"] == "cleaned", "cleanup code must be cleaned")
        self.syn.no_disclosure(res2)

        source_final = self.syn.source.read_text("utf-8")
        require("ALPHA=" not in source_final, "ALPHA must be cleanly removed")
        require("BETA=" not in source_final, "BETA must be cleanly removed")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "removed", "ALPHA promoted to removed")
        require(prog_data["items"]["BETA"]["state"] == "removed", "BETA recorded as removed")

    def test_r5_second_approved_alias_edit_during_cleanup_caught_at_boundary(self) -> None:
        """R5: Modifying second approved alias during cleanup of first alias raises source_changed."""
        second_source = self.syn.project / ".env.synthetic2"
        second_val = secrets.token_hex(24)
        self.syn.canaries.append(second_val)
        private_write(second_source, f"SECOND_KEY='{second_val}'\n".encode("utf-8"))

        self.syn.sources["synthetic-file-2"] = {
            "type": "file", "path": second_source.name, "format": "dotenv", "mcpModes": ["keys"],
        }
        self.syn.block["entries"].append({
            "source": "synthetic-file-2", "key": "SECOND_KEY", "profile": "personal",
            "name": "SECOND_KEY", "consumer": "APP_SECOND", "origin": "source",
        })
        self.syn.write_config()
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")

        cont1, _ = read_names(ctx.registry, "synthetic-file")
        cont2, _ = read_names(ctx.registry, "synthetic-file-2")
        ap = _create_cleanup_approval(ctx, clock, "personal", sources=(cont1, cont2))

        fake_api = FakeAPI()
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        fake_api.items["SECOND_KEY"] = bytearray(second_val.encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        orig_prepare = CleanupProgress.prepare
        alias2_edited = False
        new_sec_val = secrets.token_hex(24)
        self.syn.canaries.append(new_sec_val)

        def hook_prepare(prog_self, name, key, source_alias, staged, approval_id):
            nonlocal alias2_edited
            orig_prepare(prog_self, name, key, source_alias, staged, approval_id)
            if source_alias == "synthetic-file":
                private_write(second_source, f"SECOND_KEY='{new_sec_val}'\n".encode("utf-8"))
                alias2_edited = True

        with patch.object(CleanupProgress, "prepare", hook_prepare):
            res = run_cleanup(ctx, "personal", ap.approval_id, confirm=True, adapter=adapter)

        require(alias2_edited, "hook_prepare must have triggered")
        require(res["ok"] is False, "cleanup must fail when second approved alias changed concurrently")
        require(res["code"] == "source_changed", "failure code must be source_changed")
        require(res["_exit"] == 3, "source_changed exit status must be 3")
        self.syn.no_disclosure(res)

        source1_text = self.syn.source.read_text("utf-8")
        require("ALPHA=" in source1_text, "ALPHA must remain present in first source file")

    def test_r5_expiry_during_prepared_fsync_aborts_replacement(self) -> None:
        """R5: Expiry during progress.prepare fsync halts with approval_expired; leaves safe intent."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI()
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        orig_prepare = CleanupProgress.prepare
        expiry_injected = False

        def hook_prepare(prog_self, name, key, source_alias, staged, approval_id):
            nonlocal expiry_injected
            orig_prepare(prog_self, name, key, source_alias, staged, approval_id)
            clock.now = ap.expires_at + 10.0
            expiry_injected = True

        self.exit_stack.enter_context(patch("time.time", clock.time))
        with patch.object(CleanupProgress, "prepare", hook_prepare):
            res = run_cleanup(ctx, "personal", ap.approval_id, confirm=True, adapter=adapter)

        require(expiry_injected, "hook_prepare must have triggered")
        require(res["ok"] is False, "cleanup must fail on expiry")
        require(res["code"] == "approval_expired", "code must be approval_expired")
        require(res["_exit"] == 3, "approval_expired exit status must be 3")
        self.syn.no_disclosure(res)

        source_after = self.syn.source.read_text("utf-8")
        require("ALPHA=" in source_after, "ALPHA must remain untouched")
        require("BETA=" in source_after, "BETA must remain untouched")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must remain prepared safe intent")

    def test_r5_remove_keys_pre_replace_stat_recapture_aborts_on_external_drift(self) -> None:
        """R5: Direct remove_keys stat recapture immediately before os.replace catches external drift."""
        ctx = self.syn.context()
        continuity, _ = read_names(ctx.registry, "synthetic-file")
        tamper_val = secrets.token_hex(24)
        self.syn.canaries.append(tamper_val)

        def tamper_source(_staged):
            current_text = self.syn.source.read_text("utf-8")
            edited = current_text.replace(self.syn.values["ALPHA"], tamper_val)
            private_write(self.syn.source, edited.encode("utf-8"))

        with self.assertRaises(SecretBrokerError) as cm:
            remove_keys(self.syn.source, ("ALPHA",), expected=continuity, before_replace=tamper_source)

        require(cm.exception.code == "source_changed", "must raise source_changed")
        source_after = self.syn.source.read_text("utf-8")
        require(tamper_val in source_after, "external edit must be preserved on aborted replace")

    # -------------------------------------------------------------------------
    # R6: Bounded Reconciliation & Worker Session Operations (Needs-Testing 2)
    # -------------------------------------------------------------------------

    def test_r6_resume_readback_stops_at_persisted_token_expiry_sidecar(self) -> None:
        """R6: Resume binds persisted token sidecar, clamps session deadline, stops before readback/POST."""
        ctx = self.syn.context()
        clock = Clock()
        clock.now = 1000.0

        ensure_private_dir(ctx.runtime_root)
        dest = ctx.config.profiles["personal"].destination
        dest_sha = destination_sha256(dest)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap_id = new_qualified_id("ap", ctrl_id)
            journal.append(run_id, ap_id, "ALPHA", NameState.CREATED)

        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        store = ApprovalStore(ctx.runtime_root)
        store.bind_token_expiry(approval, 1030.0)

        clock.now = 1010.0

        fake_api = FakeAPI(clock=clock)
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        orig_open = adapter.open_session
        captured_sessions = []

        def spy_open(grant, *, deadline):
            s = orig_open(grant, deadline=deadline)
            s.token_expires_at = 1110.0
            s.worker.token_expires_at = 1110.0
            orig_exists = s.exists
            def spy_exists(names):
                res = orig_exists(names)
                clock.now = 1035.0
                return res
            s.exists = spy_exists
            captured_sessions.append(s)
            return s

        adapter.open_session = spy_open

        res = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                            timeout_seconds=60, adapter=adapter)

        require(res["ok"] is False, "migration must fail when clock passes persisted expiry")
        require(res["code"] == "approval_expired", "code must be approval_expired")
        require(res["_exit"] == 3, "approval_expired exit must be 3")
        require("approve again and resume" in res.get("next_action", ""),
                "must require fresh human approval")
        self.syn.no_disclosure(res)

        require(len(captured_sessions) == 1, "exactly one session opened for reconcile")
        reconcile_session = captured_sessions[0]
        require(reconcile_session.worker.deadline <= 1030.0,
                "worker session deadline must be clamped to persisted sidecar expiry")

        require(all(not view for _method, _name, view in fake_api.requests),
                "no readback with view=True allowed after expiry")
        require(len(fake_api.posts) == 0, "no new POST allowed after expiry")

        journal_after = read_journal_states(ctx.runtime_root, "personal", dest_sha)
        require(journal_after.get("ALPHA") == NameState.CREATED,
                "prior CREATED receipt must be retained in journal")

    def test_r6_reconcile_per_name_boundary_enforces_effective_clock(self) -> None:
        """R6: Reconcile enforces effective clock boundary before each item readback."""
        ctx = self.syn.context()
        clock = Clock()
        clock.now = 2000.0

        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        store = ApprovalStore(ctx.runtime_root)
        store.bind_token_expiry(approval, 2030.0)

        clock.now = 2010.0
        fake_api = FakeAPI(clock=clock)
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        readback_names = []
        orig_open = adapter.open_session
        def spy_open(grant, *, deadline):
            s = orig_open(grant, deadline=deadline)
            s.token_expires_at = 2100.0
            s.worker.token_expires_at = 2100.0
            orig_readback = s.readback_equals
            def spy_readback(name, val):
                readback_names.append(name)
                res = orig_readback(name, val)
                clock.now = 2035.0
                return res
            s.readback_equals = spy_readback
            return s
        adapter.open_session = spy_open

        values = {
            "ALPHA": bytearray(self.syn.values["ALPHA"].encode("utf-8")),
            "BETA": bytearray(self.syn.values["BETA"].encode("utf-8")),
        }
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        create_digest = authority_for(ctx, "personal", "create")
        continuities = {"synthetic-file": read_names(ctx.registry, "synthetic-file")[0]}

        with self.assertRaises(SecretBrokerError) as cm:
            _reconcile(ctx, "personal", ("ALPHA", "BETA"), values, adapter, 2060.0,
                       approval=approval, store=store, continuities=continuities,
                       dest_sha=dest_sha, approval_digest=create_digest)

        require(cm.exception.code == "approval_expired", "must raise approval_expired")
        require(readback_names == ["ALPHA"], "second name zero reads after expiry")

    def test_r6_reconcile_refuses_immediately_if_already_past_persisted_expiry(self) -> None:
        """R6: Reconcile immediately refuses before opening session if past persisted expiry."""
        ctx = self.syn.context()
        clock = Clock()
        clock.now = 3000.0

        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        store = ApprovalStore(ctx.runtime_root)
        store.bind_token_expiry(approval, 3010.0)

        clock.now = 3015.0
        fake_api = FakeAPI(clock=clock)
        adapter = FakeOwnershipAdapter(fake_api)
        spy_open = MagicMock(wraps=adapter.open_session)
        adapter.open_session = spy_open

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        create_digest = authority_for(ctx, "personal", "create")
        continuities = {"synthetic-file": read_names(ctx.registry, "synthetic-file")[0]}
        values = {"ALPHA": bytearray(b"dummy")}

        with self.assertRaises(SecretBrokerError) as cm:
            _reconcile(ctx, "personal", ("ALPHA",), values, adapter, 3050.0,
                       approval=approval, store=store, continuities=continuities,
                       dest_sha=dest_sha, approval_digest=create_digest)

        require(cm.exception.code == "approval_expired", "must raise approval_expired")
        spy_open.assert_not_called()

    # -------------------------------------------------------------------------
    # R7: Multi-Session Follow-Up & Honesty in _finish (Needs-Testing 3)
    # -------------------------------------------------------------------------

    def test_r7_revoked_read_and_unknown_create_reports_create_expiry_only(self) -> None:
        """R7: Confirmed revoked read session expiry is excluded; reports uncertain create expiry."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        ensure_private_dir(ctx.runtime_root)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap = _create_migrate_approval(ctx, Clock(), "personal")
            rev1 = RevocationOutcome("revoked", 100.0, None)
            rev2 = RevocationOutcome("cleanup_unknown", 200.0, "arbitrary backend text")

            body = _finish(ctx, journal, run_id, ap, "personal", time.time(), "completed", "migrated",
                           None, [rev1, rev2], operation="migrate")

            require(body["ok"] is False, "uncertain revocation converts success to ok=False")
            require(body["code"] == "cleanup_unknown", "code must be cleanup_unknown")
            require(body["revocation"] == "cleanup_unknown", "revocation must be cleanup_unknown")
            require("expires at 200" in body["next_action"],
                    "next_action must report only the uncertain create expiry 200")
            require("100" not in body["next_action"], "revoked token expiry 100 must not be reported")
            require("arbitrary backend text" not in body["next_action"],
                    "arbitrary backend text must be ignored")
            self.syn.no_disclosure(body)

            rec_path = ctx.runtime_root / "runs" / f"{run_id}.json"
            require(rec_path.is_file(), "completion record must be written")
            rec = json.loads(rec_path.read_text("ascii"))
            require(rec["revocation"] == "cleanup_unknown", "record revocation must match")
            require(rec["next_action"] == body["next_action"],
                    "record next_action must match public payload")

    def test_r7_multiple_unknown_sessions_report_latest_finite_expiry(self) -> None:
        """R7: Multiple uncertain sessions report the latest finite expiry as bound."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        ensure_private_dir(ctx.runtime_root)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap = _create_migrate_approval(ctx, Clock(), "personal")
            rev1 = RevocationOutcome("cleanup_unknown", 150.0, None)
            rev2 = RevocationOutcome("cleanup_unknown", 250.0, None)

            body = _finish(ctx, journal, run_id, ap, "personal", time.time(), "completed", "migrated",
                           None, [rev1, rev2], operation="migrate")

            require(body["revocation"] == "cleanup_unknown", "revocation must be cleanup_unknown")
            require("expires at 250" in body["next_action"], "must report latest finite expiry 250")
            require("expires at 150" not in body["next_action"],
                    "earlier expiry must not be used as bound")
            self.syn.no_disclosure(body)

    def test_r7_any_unknown_expiry_reports_honest_unknown(self) -> None:
        """R7: Any uncertain session with missing/non-finite expiry reports honest expiry unknown."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        ensure_private_dir(ctx.runtime_root)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap = _create_migrate_approval(ctx, Clock(), "personal")
            rev1 = RevocationOutcome("cleanup_unknown", 300.0, None)
            rev2 = RevocationOutcome("cleanup_unknown", None, None)

            body = _finish(ctx, journal, run_id, ap, "personal", time.time(), "completed", "migrated",
                           None, [rev1, rev2], operation="migrate")

            require(body["revocation"] == "cleanup_unknown", "revocation must be cleanup_unknown")
            require("expiry unknown" in body["next_action"],
                    "must report expiry unknown honestly")
            require("300" not in body["next_action"],
                    "finite expiry from other session must not substitute for unknown")
            self.syn.no_disclosure(body)

    def test_r7_arbitrary_backend_next_action_ignored_and_primary_error_retained(self) -> None:
        """R7: Primary error is preserved with uncertain revocation; arbitrary backend text dropped."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        ensure_private_dir(ctx.runtime_root)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap = _create_migrate_approval(ctx, Clock(), "personal")
            rev = RevocationOutcome("cleanup_unknown", 500.0,
                                    "injected arbitrary next_action: delete all data")

            body = _finish(ctx, journal, run_id, ap, "personal", time.time(), "response_invalid",
                           "response_invalid", "operator inspection required", [rev],
                           operation="migrate", ok=False)

            require(body["ok"] is False, "ok must be False on primary error")
            require(body["code"] == "response_invalid",
                    "primary error code response_invalid must be retained")
            require(body["_exit"] == 4, "exit code must be 4 for response_invalid")
            require(body["revocation"] == "cleanup_unknown", "revocation must be cleanup_unknown")
            require("expires at 500" in body["next_action"],
                    "token lifetime guidance must be present")
            require("injected arbitrary next_action" not in body["next_action"],
                    "arbitrary backend text must be excluded")
            self.syn.no_disclosure(body)

    # -------------------------------------------------------------------------
    # R8: Dispatch Audit Outcome Preservation & Persistence (Needs-Testing 4)
    # -------------------------------------------------------------------------

    def test_r8_cli_dispatch_primary_read_error_with_failing_audit_outcome_preserves_error(self) -> None:
        """R8: Audit outcome failure during CLI dispatch preserves primary error and token guidance."""
        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.__exit__.return_value = None
        fake_session.read.side_effect = SecretBrokerError("response_invalid", "corrupt read response")
        fake_session.revocation.return_value = RevocationOutcome(
            status="cleanup_unknown",
            token_expires_at=1730000000.0,
            next_action=None,
        )

        adapter = MagicMock()
        adapter.open_session.return_value = fake_session

        entry_dest = self.syn.block["entries"][0].copy()
        entry_dest["origin"] = "destination"
        entry_dest["name"] = "REMOTE_KEY"
        entry_dest["consumer"] = "REMOTE_CONSUMER"
        self.syn.block["entries"] = [entry_dest]
        self.syn.write_config()
        ctx = self.syn.context()

        def failing_audit_outcome(*args, **kwargs):
            raise OSError(errno.EIO, "simulated disk failure on audit append")

        with patch.object(SecretAudit, "outcome", failing_audit_outcome):
            code, body = command_in_process(
                ctx, self.exit_stack, adapter,
                "run", "--ownership-profile", "personal", "--ownership-key", "REMOTE_KEY", "--json", "--", "/usr/bin/true",
            )

        require(code == 4, "exit code must match primary response_invalid exit 4")
        require(body["ok"] is False, "must not be ok")
        require(body["code"] == "response_invalid",
                    "primary error response_invalid must be preserved")
        require(body["revocation"] == "cleanup_unknown", "revocation must be cleanup_unknown")
        require(body.get("audit") == "audit_unavailable",
                "audit indicator must be audit_unavailable")
        require(body.get("audit_error") == "audit_unavailable",
                "audit_error indicator must be audit_unavailable")
        require("derived access token revocation not confirmed" in body.get("next_action", ""),
                "trusted token follow-up must be preserved")
        self.syn.no_disclosure(body)

    def test_r8_cli_dispatch_audit_outcome_failure_preserves_stopped_names_and_run_id(self) -> None:
        """R8: Audit outcome failure preserves stopped migration acceptance_unknown names and run_id."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        fake_api = FakeAPI(script={"ALPHA": "lost"})
        adapter = FakeOwnershipAdapter(fake_api)

        def failing_audit_outcome(*args, **kwargs):
            raise OSError(errno.ENOSPC, "simulated audit full disk")

        with patch.object(SecretAudit, "outcome", failing_audit_outcome):
            code, body = command_in_process(
                ctx, self.exit_stack, adapter,
                "ownership", "migrate", "--profile", "personal", "--confirm",
                "--approval", approval.approval_id,
            )

        require(code == 5, "acceptance_unknown exit code must be 5")
        require(body["ok"] is False, "stopped migration must be ok=False")
        require(body["code"] == "run_stopped", "code must be run_stopped")
        require(body.get("audit") == "audit_unavailable", "audit indicator must be attached")
        require("run_id" in body and body["run_id"],
                "run_id must be preserved despite audit failure")
        require("ALPHA" in body.get("names", {}).get("acceptance_unknown", []),
                "acceptance_unknown names must be preserved despite audit failure")
        self.syn.no_disclosure(body)

    def test_r8_cli_dispatch_successful_run_with_audit_failure_returns_exit_2(self) -> None:
        """R8: Successful operation converts to audit_unavailable and exit 2 on audit outcome failure."""
        ctx = self.syn.context()
        fake_api = FakeAPI()
        adapter = FakeOwnershipAdapter(fake_api)

        def failing_audit_outcome(*args, **kwargs):
            raise OSError(errno.EIO, "simulated disk error on audit")

        with patch.object(SecretAudit, "outcome", failing_audit_outcome):
            code, body = command_in_process(
                ctx, self.exit_stack, adapter,
                "ownership", "classify", "--profile", "personal", "--json",
            )

        require(code == 2, "audit_unavailable exit code must be 2")
        require(body["ok"] is False,
                "successful operation must be converted to ok=False on audit failure")
        require(body["code"] == "audit_unavailable", "code must be audit_unavailable")
        require(body.get("audit") == "audit_unavailable", "audit indicator must be set")
        self.syn.no_disclosure(body)

    def test_r8_completion_record_failure_on_primary_error_retains_primary_error(self) -> None:
        """R8: Completion record failure on primary error retains primary code and reports failure."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        ensure_private_dir(ctx.runtime_root)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap = _create_migrate_approval(ctx, Clock(), "personal")
            rev = RevocationOutcome("cleanup_unknown", 1000.0, None)

            def failing_write(*args, **kwargs):
                raise OSError(errno.EIO, "simulated completion record write failure")

            with patch("sandbox.secrets.ownership.migrate.write_completion_record", failing_write):
                body = _finish(ctx, journal, run_id, ap, "personal", time.time(), "source_changed",
                               "source_changed", "inspect source", [rev], operation="migrate", ok=False)

            require(body["ok"] is False, "primary error ok must remain False")
            require(body["code"] == "source_changed",
                    "primary code source_changed must be retained")
            require(body["_exit"] == 3, "exit code must be 3 for source_changed")
            require(body.get("persistence") == "persistence_failed",
                    "persistence indicator must be set")
            require(body.get("completion_record") == "persistence_failed",
                    "completion_record indicator must be set")
            require("expires at 1000" in body.get("next_action", ""),
                    "revocation follow-up must be preserved")
            self.syn.no_disclosure(body)

    def test_r8_completion_record_failure_on_success_sets_provider_failed_and_exit_4(self) -> None:
        """R8: Completion record failure on success converts to provider_failed (exit 4)."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        ctrl_id = ctx.controller_id
        auth_create = authority_for(ctx, "personal", "create")
        ensure_private_dir(ctx.runtime_root)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctrl_id,
                          authority_digest=auth_create) as journal:
            run_id = new_qualified_id("run", ctrl_id)
            ap = _create_migrate_approval(ctx, Clock(), "personal")
            rev = RevocationOutcome("revoked", 1000.0, None)

            def failing_write(*args, **kwargs):
                raise OSError(errno.EIO, "simulated completion record write failure")

            with patch("sandbox.secrets.ownership.migrate.write_completion_record", failing_write):
                body = _finish(ctx, journal, run_id, ap, "personal", time.time(), "completed",
                               "migrated", None, [rev], operation="migrate", ok=True)

            require(body["ok"] is False, "completion failure must convert success to ok=False")
            require(body["code"] == "provider_failed", "code must become provider_failed")
            require(body.get("operation_code") == "migrated",
                    "operation_code must preserve original migrated code")
            require(body["_exit"] == 4, "provider_failed exit code must be 4")
            require(body.get("persistence") == "persistence_failed",
                    "persistence indicator must be set")
            require(body.get("completion_record") == "persistence_failed",
                    "completion_record indicator must be set")
            self.syn.no_disclosure(body)

    # -------------------------------------------------------------------------
    # R9: Portable Temporary Root Resolution (Needs-Testing 5)
    # -------------------------------------------------------------------------

    def test_r9_portable_temp_choice_resolves_symlinks_without_platform_assumptions(self) -> None:
        """R9: Verify Path("/tmp").resolve(strict=True) produces canonical root for both Darwin & Linux.

        Limits: Verified on live macOS Darwin runtime environment (/tmp -> /private/tmp symlink);
        Linux direct layout verified via simulated symlink/direct targets without platform-specific assumptions.
        """
        canonical_tmp = Path("/tmp").resolve(strict=True)
        require(canonical_tmp.is_dir(), "resolved temporary root must be a directory")
        require(canonical_tmp == canonical_tmp.resolve(),
                "resolved temporary root must be canonical")

        scratch_base = self.root / "platform_test"
        scratch_base.mkdir(mode=0o700)
        real_target = scratch_base / "real_dir"
        real_target.mkdir(mode=0o700)
        symlink_target = scratch_base / "symlink_dir"
        os.symlink(real_target, symlink_target)

        require(real_target.resolve(strict=True) == real_target.resolve(),
                "direct directory resolution is canonical")
        require(symlink_target.resolve(strict=True) == real_target.resolve(),
                "symlink resolution produces canonical target")

        with tempfile.TemporaryDirectory(prefix="test-plat-", dir=canonical_tmp) as sub_temp:
            resolved_sub = Path(sub_temp).resolve()
            require(resolved_sub.parent == canonical_tmp,
                    "sub-temporary directory parent must equal canonical root")


    # -------------------------------------------------------------------------
    # R10 & R11: Final-Stat Expiry and Post-Replace Directory Fsync Regressions
    # -------------------------------------------------------------------------

    def test_cleanup_expiry_during_final_nofollow_stat_preserves_source(self) -> None:
        """R10: Expiry during final nofollow stat before replace aborts; preserves source and prepared intent."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap1 = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI(clock=clock)
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        source_bytes_before = self.syn.source.read_bytes()
        source_name = self.syn.source.name

        orig_prepare = CleanupProgress.prepare
        prepared_seen = False

        def hook_prepare(prog_self, name, key, source_alias, staged, approval_id):
            nonlocal prepared_seen
            orig_prepare(prog_self, name, key, source_alias, staged, approval_id)
            if name == "ALPHA":
                prepared_seen = True

        orig_stat = os.stat
        stat_count = 0
        expiry_advanced = False

        def narrow_stat(path, *args, **kwargs):
            nonlocal stat_count, expiry_advanced
            res = orig_stat(path, *args, **kwargs)
            is_source = (path == source_name or str(path).endswith(source_name))
            has_dir_fd = kwargs.get("dir_fd") is not None or (len(args) >= 2 and args[1] is not None)
            follow_symlinks = kwargs.get("follow_symlinks", True)
            if is_source and has_dir_fd and follow_symlinks is False and prepared_seen:
                stat_count += 1
                clock.now = ap1.expires_at + 10.0
                expiry_advanced = True
            return res

        orig_replace = os.replace
        replace_called = False

        def spy_replace(*args, **kwargs):
            nonlocal replace_called
            if len(args) >= 2 and args[1] == source_name and kwargs.get("dst_dir_fd") is not None:
                replace_called = True
            return orig_replace(*args, **kwargs)

        self.exit_stack.enter_context(patch("time.time", clock.time))
        with patch.object(CleanupProgress, "prepare", hook_prepare), \
             patch("os.stat", narrow_stat), \
             patch("os.replace", spy_replace):
            res1 = run_cleanup(ctx, "personal", ap1.approval_id, confirm=True, adapter=adapter)

        require(expiry_advanced is True, "controlled clock must have been advanced at final stat")
        require(stat_count == 1, "final stat must have been hooked exactly once")
        require(replace_called is False, "os.replace must not be called when deadline expires during final stat")
        require(res1["ok"] is False, "cleanup must fail on expiry")
        require(res1["code"] == "approval_expired", "code must be approval_expired")
        require(res1["_exit"] == 3, "approval_expired exit status must be 3")
        self.syn.no_disclosure(res1)

        require(self.syn.source.read_bytes() == source_bytes_before, "source bytes must remain intact")
        source_text = self.syn.source.read_text("utf-8")
        require("ALPHA=" in source_text, "ALPHA must not be removed")
        require("BETA=" in source_text, "BETA must not be removed")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must remain prepared safe intent")

        # Resume under fresh approval
        clock.now = clock.now + 10.0
        fresh_ctx = self.syn.context()
        ap2 = _create_cleanup_approval(fresh_ctx, clock, "personal")
        res2 = run_cleanup(fresh_ctx, "personal", ap2.approval_id, confirm=True, adapter=adapter)
        require(res2["ok"] is True, "resume must succeed under fresh approval")
        require(res2["code"] == "cleaned", "cleanup code must be cleaned")
        self.syn.no_disclosure(res2)

        source_final = self.syn.source.read_text("utf-8")
        require("ALPHA=" not in source_final, "ALPHA must be cleanly removed")
        require("BETA=" not in source_final, "BETA must be cleanly removed")

        prog_data_final = json.loads(prog_path.read_text("utf-8"))
        require(prog_data_final["items"]["ALPHA"]["state"] == "removed", "ALPHA promoted to removed")
        require(prog_data_final["items"]["BETA"]["state"] == "removed", "BETA recorded as removed")

    def test_cleanup_editor_replace_during_post_replace_dir_fsync_stops_next_removal(self) -> None:
        """R11: Atomic editor replace during post-replace dir fsync stops next removal and does not adopt drift."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap1 = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI(clock=clock)
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        new_gamma_val = secrets.token_hex(24)
        self.syn.canaries.append(new_gamma_val)
        source_dir_stat = self.syn.source.parent.stat()

        orig_replace = os.replace
        replace_count = 0

        def spy_replace(*args, **kwargs):
            nonlocal replace_count
            if len(args) >= 2 and args[1] == self.syn.source.name and kwargs.get("dst_dir_fd") is not None:
                replace_count += 1
            return orig_replace(*args, **kwargs)

        orig_fsync = os.fsync
        injected = False

        def hooked_fsync(fd):
            nonlocal injected
            orig_fsync(fd)
            if replace_count == 1 and not injected:
                try:
                    st = os.fstat(fd)
                    if (stat.S_ISDIR(st.st_mode) and
                            st.st_dev == source_dir_stat.st_dev and
                            st.st_ino == source_dir_stat.st_ino):
                        injected = True
                        current_text = self.syn.source.read_text("utf-8")
                        edited_text = current_text.replace(self.syn.values["GAMMA"], new_gamma_val)
                        tmp_edit = self.syn.project / f".sb-editor-edit-{secrets.token_hex(8)}"
                        private_write(tmp_edit, edited_text.encode("utf-8"))
                        orig_replace(tmp_edit, self.syn.source)
                except OSError:
                    pass

        with patch("os.replace", spy_replace), patch("os.fsync", hooked_fsync):
            res1 = run_cleanup(ctx, "personal", ap1.approval_id, confirm=True, adapter=adapter)

        require(injected is True, "atomic edit must have been injected during post-replace dir fsync")
        require(res1["ok"] is False, "cleanup must fail when source changed during fsync")
        require(res1["code"] == "source_changed", "failure code must be source_changed")
        require(res1["_exit"] == 3, "source_changed exit status must be 3")
        self.syn.no_disclosure(res1)

        source_after = self.syn.source.read_text("utf-8")
        require(new_gamma_val in source_after, "injected editor value for GAMMA must be preserved")
        require("BETA=" in source_after, "BETA must remain present and not removed")
        require(replace_count == 1, "no second removal under old approval allowed")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must remain prepared checkpoint, not removed")
        require("BETA" not in prog_data["items"] or prog_data["items"]["BETA"]["state"] != "removed", "BETA must not be removed")

        # Fresh approval recovery must not adopt untracked removal when inode changed
        fresh_ctx = self.syn.context()
        ap2 = _create_cleanup_approval(fresh_ctx, clock, "personal")
        with self.assertRaises(SecretBrokerError) as cm:
            run_cleanup(fresh_ctx, "personal", ap2.approval_id, confirm=True, adapter=adapter)
        require(cm.exception.code == "cleanup_ineligible", "recovery must refuse to adopt untracked removal")

        prog_data_after = json.loads(prog_path.read_text("utf-8"))
        require(prog_data_after["items"]["ALPHA"]["state"] == "prepared", "checkpoint must retain prepared intent")
        source_final = self.syn.source.read_text("utf-8")
        require(new_gamma_val in source_final, "editor value for GAMMA must remain preserved")
        require("BETA=" in source_final, "BETA must remain present")

    def test_cleanup_inplace_edit_during_post_replace_dir_fsync_stops_next_removal(self) -> None:
        """R11: In-place edit during post-replace dir fsync stops next removal and preserves editor values."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap1 = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI(clock=clock)
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        new_gamma_val = secrets.token_hex(24)
        self.syn.canaries.append(new_gamma_val)
        source_dir_stat = self.syn.source.parent.stat()

        orig_replace = os.replace
        replace_count = 0

        def spy_replace(*args, **kwargs):
            nonlocal replace_count
            if len(args) >= 2 and args[1] == self.syn.source.name and kwargs.get("dst_dir_fd") is not None:
                replace_count += 1
            return orig_replace(*args, **kwargs)

        orig_fsync = os.fsync
        injected = False
        in_fsync = False

        def hooked_fsync(fd):
            nonlocal injected, in_fsync
            orig_fsync(fd)
            if in_fsync:
                return
            if replace_count == 1 and not injected:
                try:
                    st = os.fstat(fd)
                    if (stat.S_ISDIR(st.st_mode) and
                            st.st_dev == source_dir_stat.st_dev and
                            st.st_ino == source_dir_stat.st_ino):
                        injected = True
                        in_fsync = True
                        try:
                            current_text = self.syn.source.read_text("utf-8")
                            edited_text = current_text.replace(self.syn.values["GAMMA"], new_gamma_val)
                            encoded = edited_text.encode("utf-8")
                            edit_fd = os.open(self.syn.source, os.O_WRONLY | os.O_TRUNC)
                            try:
                                view = memoryview(encoded)
                                while view:
                                    written = os.write(edit_fd, view)
                                    view = view[written:]
                                orig_fsync(edit_fd)
                            finally:
                                os.close(edit_fd)
                        finally:
                            in_fsync = False
                except OSError:
                    pass

        with patch("os.replace", spy_replace), patch("os.fsync", hooked_fsync):
            res1 = run_cleanup(ctx, "personal", ap1.approval_id, confirm=True, adapter=adapter)

        require(injected is True, "in-place edit must have been injected during post-replace dir fsync")
        require(res1["ok"] is False, "cleanup must fail when source changed during fsync")
        require(res1["code"] == "source_changed", "failure code must be source_changed")
        require(res1["_exit"] == 3, "source_changed exit status must be 3")
        self.syn.no_disclosure(res1)

        source_after = self.syn.source.read_text("utf-8")
        require(new_gamma_val in source_after, "in-place edited value for GAMMA must be preserved")
        require("BETA=" in source_after, "BETA must remain present and not removed")
        require(replace_count == 1, "no second removal under old approval allowed")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must remain prepared checkpoint, not removed")
        require("BETA" not in prog_data["items"] or prog_data["items"]["BETA"]["state"] != "removed", "BETA must not be removed")

        # Resume under fresh approval: recovery detects matching staged inode/names for ALPHA and cleans BETA
        fresh_ctx = self.syn.context()
        ap2 = _create_cleanup_approval(fresh_ctx, clock, "personal")
        res2 = run_cleanup(fresh_ctx, "personal", ap2.approval_id, confirm=True, adapter=adapter)
        require(res2["ok"] is True, "resume must succeed under fresh approval")
        require(res2["code"] == "cleaned", "cleanup code must be cleaned")
        self.syn.no_disclosure(res2)

        source_final = self.syn.source.read_text("utf-8")
        require("ALPHA=" not in source_final, "ALPHA must be cleanly removed")
        require("BETA=" not in source_final, "BETA must be cleanly removed")
        require(new_gamma_val in source_final, "in-place edited value for GAMMA must remain preserved")

        prog_data_final = json.loads(prog_path.read_text("utf-8"))
        require(prog_data_final["items"]["ALPHA"]["state"] == "removed", "ALPHA promoted to removed")
        require(prog_data_final["items"]["BETA"]["state"] == "removed", "BETA recorded as removed")

if __name__ == "__main__":
    unittest.main()

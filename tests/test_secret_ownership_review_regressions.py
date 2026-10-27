"""Focused regressions for independent review findings R1-R4 and Needs-Testing 5 & 6.

R1: Registered source value edit with restored mtime between creates blocks next GO,
    preserving prior verified receipt; before_send source tampering before first GO aborts;
    two-alias fixture blocks when unmigrated alias modified.
R2: Durable token expiry sidecar, strict validation fail-closed, require_current enforcement;
    token expiry blocks resume before session/entry values; matching fresh approval ID resumes
    with narrow READ reconcile and only BETA post; altered/wider approval rejected.
R3: Complete run_cleanup partial crash recovery after ALPHA os.replace before checkpoint;
    fresh context + new cleanup approval recovers proven removed ALPHA without source reinsertion;
    completion record failure preserves progress; unexpected missing ALPHA without intent refuses;
    reintroduced names not skipped blindly; dry-run leaves bytes/stat unchanged; retention preserves.
R4: Unknown create + revoke unknown preserves exit 5 / acceptance_unknown plus derived-token
    human revocation guidance; classify --check-destination revoke unknown includes follow-up;
    scoped read success + revoke fail blocks child with cleanup_unknown; primary read error +
    revoke fail retains primary code + both actions; arbitrary backend next_action ignored.
Needs-Testing 5: BrokerOwnershipSession.create with fake private channel READY and before_send
    journal fsync failure sends abort, never GO, zero POST; frame loss after GO preserves
    acceptance_unknown without blind retry.
Needs-Testing 6: Real ./sb secrets run child timeout exits 124 with timed_out termination
    and kills child process group, verified via os.kill(0).
"""

from __future__ import annotations

from dataclasses import asdict
import errno
import json
import math
import os
import pwd
import secrets
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.secrets.models import MAX_OUTPUT_BYTES, SecretBrokerError
from sandbox.secrets.ownership import approval as approval_mod
from sandbox.secrets.ownership import cleanup as cleanup_mod
from sandbox.secrets.ownership import migrate
from sandbox.secrets.ownership.approval import ApprovalStore
from sandbox.secrets.ownership.broker import (
    BrokerOwnershipAdapter, BrokerOwnershipSession,
)
from sandbox.secrets.ownership.cleanup import run_cleanup
from sandbox.secrets.ownership.cleanup_progress import (
    CleanupProgress, cleanup_progress_path,
)
from sandbox.secrets.ownership.journal import (
    Journal, destination_sha256, ensure_private_dir, new_qualified_id,
    read_private_file, retention, write_private_file,
)
from sandbox.secrets.ownership.manifest import OwnershipAdapter
from sandbox.secrets.ownership.migrate import (
    authority_for, exit_for, payload, read_entry_values, run_migration,
)
from sandbox.secrets.ownership.models import (
    Approval, CreateOutcome, Existence, Grant, NameState, RevocationOutcome,
    ScopedRunRequest, SourceContinuity, StorageAdmission, canonical_json,
)
from sandbox.secrets.ownership.protocol import ProtocolError
from sandbox.secrets.ownership.scoped_run import resolve_scoped_bindings
from sandbox.secrets.ownership.source_view import read_names, read_values
from sandbox.secrets.writer import remove_keys
from tests.secret_ownership_support import (
    Clock, FakeAPI, FakeOwnershipAdapter, FakeOwnershipSession,
    SyntheticOwnership, command_in_process, fake_grant, require, REPO,
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


def _create_cleanup_approval(ctx, clock: Clock, profile: str = "personal") -> Approval:
    """Issue and store a valid cleanup approval bound to current registered source."""
    ensure_private_dir(ctx.runtime_root)
    store = ApprovalStore(ctx.runtime_root)
    ctrl_id = ctx.controller_id
    continuity, _ = read_names(ctx.registry, "synthetic-file")
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
        sources=(continuity,),
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


class FakeFrameChannel:
    """In-memory framing channel for testing BrokerOwnershipSession protocol interactions."""

    def __init__(self, *, break_after_go: bool = False) -> None:
        self.sent: list[dict] = []
        self.values: list[bytearray] = []
        self.break_after_go = break_after_go

    def send_json(self, obj: dict) -> None:
        self.sent.append(dict(obj))

    def send_value(self, val: bytearray | bytes) -> None:
        self.values.append(bytearray(val))

    def recv_json(self, expected_op: str) -> dict:
        if expected_op == "ready":
            return {"op": "ready", "name": "ALPHA"}
        if expected_op == "created":
            if self.break_after_go:
                raise ProtocolError()
            last_op = self.sent[-1].get("op") if self.sent else None
            if last_op == "abort":
                return {"op": "created", "name": "ALPHA", "state": "aborted", "reason": "aborted"}
            return {"op": "created", "name": "ALPHA", "state": "created", "reason": None}
        raise ProtocolError()


class TestSecretOwnershipReviewRegressions(unittest.TestCase):
    """Regressions for findings R1-R4 and Needs-Testing 5 & 6."""

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
        self.temp_dir = tempfile.TemporaryDirectory(prefix="s069-regressions-", dir=temp_root)
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        require(self.root.parent == temp_root, "temporary root must be canonical")
        self.syn = SyntheticOwnership(self.root)

    def tearDown(self) -> None:
        if hasattr(self, "syn"):
            ctx = self.syn.context()
            if ctx.runtime_root.exists():
                self.syn.records_are_public(ctx)

    # =========================================================================
    # R1: Source Continuity Re-Check & Tampering Detection
    # =========================================================================

    def test_r1_source_value_edit_restored_mtime_between_creates_blocks_next_go(self) -> None:
        """R1: Value edit with restored mtime between ALPHA and BETA blocks BETA, preserving ALPHA."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        fake_api = FakeAPI()

        def tamper_on_post(name: str) -> None:
            if name == "ALPHA":
                st = self.syn.source.stat()
                raw = self.syn.source.read_text("utf-8")
                rep = secrets.token_hex(len(self.syn.values["BETA"]) // 2)
                modified = raw.replace(f"BETA='{self.syn.values['BETA']}'", f"BETA='{rep}'")
                self.syn.source.write_text(modified, encoding="utf-8")
                os.utime(self.syn.source, (st.st_atime, st.st_mtime))

        fake_api.on_post = tamper_on_post
        adapter = FakeOwnershipAdapter(fake_api)

        res = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                            timeout_seconds=30, adapter=adapter)

        require(res["ok"] is False, "migration must stop when source is modified between creates")
        require(res["code"] == "source_changed", "failure code must be source_changed")
        require("ALPHA" in fake_api.posts, "ALPHA must be posted and verified")
        require("BETA" not in fake_api.posts, "BETA must never be posted after source tamper")

        # Verify ALPHA's verified receipt in journal was preserved and BETA remains durable PENDING
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        with Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctx.controller_id,
                          authority_digest=authority_for(ctx, "personal", "create")) as journal:
            states = journal.states()
            require(states.get("ALPHA") == NameState.VERIFIED, "ALPHA verified state must be preserved")
            require(states.get("BETA") == NameState.PENDING, "BETA must remain durable PENDING in journal")

    def test_r1_source_edit_after_ready_before_first_go_blocks_create(self) -> None:
        """R1: Modifying source after worker READY before first GO aborts with zero POSTs."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        fake_api = FakeAPI()
        adapter = FakeOwnershipAdapter(fake_api)

        orig_open_session = adapter.open_session

        def open_hooked_session(grant, deadline):
            session = orig_open_session(grant, deadline=deadline)
            orig_create = session.create

            def hooked_create(name, value, *, comment, before_send):
                def wrapped_before_send():
                    with open(self.syn.source, "a", encoding="utf-8") as f:
                        f.write("\nEXTRA='tampered'\n")
                    before_send()
                return orig_create(name, value, comment=comment, before_send=wrapped_before_send)

            session.create = hooked_create
            return session

        adapter.open_session = open_hooked_session

        res = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                            timeout_seconds=30, adapter=adapter)

        require(res["ok"] is False, "migration must fail before first GO")
        require(res["code"] == "source_changed", "must fail with source_changed")
        require(len(fake_api.posts) == 0, "zero POSTs must occur when before_send fails")

    def test_r1_two_aliases_source_edit_other_alias_blocks_create(self) -> None:
        """R1: In multi-source profile, modifying the other approved alias at READY blocks GO/POST."""
        src2_path = self.syn.project / ".env.secondary"
        gamma_val = secrets.token_hex(24)
        src2_path.write_text(f"GAMMA='{gamma_val}'\n", encoding="utf-8")
        os.chmod(src2_path, 0o600)

        sources = dict(self.syn.sources)
        sources["secondary-file"] = {
            "type": "file",
            "format": "dotenv",
            "path": src2_path.name,
            "mcpModes": ["keys"],
        }
        block = {
            "profiles": {
                name: dict(profile) for name, profile in self.syn.block["profiles"].items()
            },
            "entries": [
                *self.syn.block["entries"],
                {"source": "secondary-file", "key": "GAMMA", "name": "GAMMA",
                 "consumer": "APP_GAMMA", "profile": "personal", "origin": "source"},
            ],
        }

        self.syn.sources = sources
        self.syn.block = block
        self.syn.write_config()
        ctx = self.syn.context()

        clock = Clock()
        cont1, _ = read_names(ctx.registry, "synthetic-file")
        cont2, _ = read_names(ctx.registry, "secondary-file")
        dest = ctx.config.profiles["personal"].destination
        approval = Approval(
            new_qualified_id("ap", ctx.controller_id), "migrate", os.geteuid(),
            pwd.getpwuid(os.geteuid()).pw_name, ctx.controller_id, "personal",
            (cont1, cont2), ctx.config.map_sha256, destination_sha256(dest),
            authority_for(ctx, "personal", "create"), "0" * 64,
            clock.now + 120, clock.now, clock.now + 120, "cli_tty",
        )
        self.exit_stack.enter_context(patch.object(ApprovalStore, "load", lambda _s, _id: approval))
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "load_admission", lambda *_a: StorageAdmission(
            "0" * 64, dest.endpoint, dest.project_id, clock.now, clock.now + 120, "operator_attested",
        )))

        fake_api = FakeAPI()
        adapter = FakeOwnershipAdapter(fake_api)

        orig_open_session = adapter.open_session

        def open_hooked_session(grant, deadline):
            session = orig_open_session(grant, deadline=deadline)
            orig_create = session.create

            def hooked_create(name, value, *, comment, before_send):
                def wrapped_before_send():
                    # Modify other approved alias at READY
                    st = src2_path.stat()
                    raw = src2_path.read_text("utf-8")
                    rep = secrets.token_hex(24)
                    src2_path.write_text(raw.replace(f"GAMMA='{gamma_val}'", f"GAMMA='{rep}'"), encoding="utf-8")
                    os.utime(src2_path, (st.st_atime, st.st_mtime))
                    before_send()
                return orig_create(name, value, comment=comment, before_send=wrapped_before_send)

            session.create = hooked_create
            return session

        adapter.open_session = open_hooked_session

        res = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                            timeout_seconds=30, adapter=adapter)

        require(res["ok"] is False, "migration must block when secondary alias changes at READY")
        require(res["code"] == "source_changed", "code must be source_changed")
        require(len(fake_api.posts) == 0, "no posts allowed when any approved source changed")

    # =========================================================================
    # R2: Strict Token Expiry Sidecar, Gated Resume & Fresh Approval
    # =========================================================================

    def test_r2_expired_token_live_approval_cannot_resume(self) -> None:
        """R2: Short-lived token expiry halts migration; resume is blocked before session or values."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        # Initial run: FakeAPI issues short 1s token expiry and advances clock by 2s on post
        fake_api = FakeAPI(clock=clock, expires=True)
        adapter = FakeOwnershipAdapter(fake_api)

        res1 = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                             timeout_seconds=30, adapter=adapter)
        require(res1["ok"] is False, "initial run must stop when token expires")
        require(res1["code"] == "run_stopped", "code must be run_stopped")
        require(fake_api.posts == ["ALPHA"], "only ALPHA posted in run 1")

        # Nominal approval is still valid (120s), but token expiry sidecar is expired
        require(clock.now < approval.expires_at, "nominal approval must still be live")
        sidecar_path = ctx.runtime_root / "approvals" / f"{approval.approval_id}.token_expiry.json"
        require(sidecar_path.is_file(), "token expiry sidecar must exist")
        require(oct(sidecar_path.stat().st_mode & 0o777) == "0o600", "sidecar mode must be 0600")

        # Resume attempt with same approval ID: verify adapter.open_session and read_entry_values are NOT called
        spy_open = MagicMock(wraps=adapter.open_session)
        adapter.open_session = spy_open
        with patch("sandbox.secrets.ownership.migrate.read_entry_values",
                   side_effect=AssertionError("read_entry_values must not be called")) as mock_rev:
            try:
                res_resume = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                                           timeout_seconds=30, adapter=adapter)
                code = res_resume.get("code")
                require(res_resume.get("ok") is False, "resume must fail when token sidecar is expired")
            except SecretBrokerError as exc:
                code = exc.code

        require(code == "approval_expired", "resume code must be approval_expired")
        spy_open.assert_not_called()
        mock_rev.assert_not_called()
        require(fake_api.posts == ["ALPHA"], "no additional POSTs on failed resume")

    def test_r2_matching_fresh_approval_resumes_without_repeated_confirmed_posts(self) -> None:
        """R2: Fresh approval ID with matching authority resumes, reconciling ALPHA and posting only BETA."""
        ctx = self.syn.context()
        clock = Clock()
        approval1 = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        fake_api = FakeAPI(clock=clock, expires=True)
        adapter = FakeOwnershipAdapter(fake_api)

        run_migration(ctx, "personal", approval1.approval_id, confirm=True,
                      timeout_seconds=30, adapter=adapter)
        require(fake_api.posts == ["ALPHA"], "run 1 must only post ALPHA")

        # Issue NEW synthetic approval ID with fresh window, same authority and continuities
        continuity, _ = read_names(ctx.registry, "synthetic-file")
        dest = ctx.config.profiles["personal"].destination
        approval2 = Approval(
            new_qualified_id("ap", ctx.controller_id), "migrate", os.geteuid(),
            pwd.getpwuid(os.geteuid()).pw_name, ctx.controller_id, "personal", (continuity,),
            ctx.config.map_sha256, destination_sha256(dest),
            authority_for(ctx, "personal", "create"), "0" * 64,
            clock.now + 120, clock.now, clock.now + 120, "cli_tty",
        )

        def load_approvals(_store, identifier: str) -> Approval:
            if identifier == approval2.approval_id:
                return approval2
            return approval1

        self.exit_stack.enter_context(patch.object(ApprovalStore, "load", load_approvals))

        # Fresh FakeAPI with long-lived tokens
        fresh_api = FakeAPI(clock=clock, expires=False)
        fresh_api.items["ALPHA"] = fake_api.items["ALPHA"]  # ALPHA exists in remote store
        fresh_adapter = FakeOwnershipAdapter(fresh_api)

        res2 = run_migration(ctx, "personal", approval2.approval_id, confirm=True,
                             timeout_seconds=30, adapter=fresh_adapter)

        require(res2["ok"] is True, "resume with fresh approval must succeed")
        require(res2["code"] == "migrated", "code must be migrated")
        require(fresh_api.posts == ["BETA"], "ALPHA must be reconciled under READ; only BETA posted")
        require(any(g.capability == "read" and g.names == ("ALPHA",) for g in fresh_api.grants),
                "reconcile must use narrow READ grant")

    def test_r2_altered_or_wider_approval_rejected_on_resume(self) -> None:
        """R2: Resuming with altered map_sha256 or wrong authority digest is rejected."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        # Altered approval with wrong map_sha256
        continuity, _ = read_names(ctx.registry, "synthetic-file")
        dest = ctx.config.profiles["personal"].destination
        tampered_approval = Approval(
            new_qualified_id("ap", ctx.controller_id), "migrate", os.geteuid(),
            pwd.getpwuid(os.geteuid()).pw_name, ctx.controller_id, "personal", (continuity,),
            "f" * 64, destination_sha256(dest), authority_for(ctx, "personal", "create"),
            "0" * 64, clock.now + 120, clock.now, clock.now + 120, "cli_tty",
        )
        self.exit_stack.enter_context(patch.object(ApprovalStore, "load", lambda _s, _id: tampered_approval))

        fake_api = FakeAPI()
        adapter = FakeOwnershipAdapter(fake_api)

        spy_open = MagicMock(wraps=adapter.open_session)
        adapter.open_session = spy_open
        with patch("sandbox.secrets.ownership.migrate.read_entry_values",
                   side_effect=AssertionError("read_entry_values must not be called")) as mock_rev:
            try:
                res = run_migration(ctx, "personal", tampered_approval.approval_id, confirm=True,
                                    timeout_seconds=30, adapter=adapter)
                code = res.get("code")
                require(res.get("ok") is False, "altered approval must be rejected")
            except SecretBrokerError as exc:
                code = exc.code

        require(code in ("authority_mismatch", "approval_mismatch"),
                "altered map_sha256 must fail closed")
        spy_open.assert_not_called()
        mock_rev.assert_not_called()
        require(len(fake_api.posts) == 0, "no worker posts on altered approval")

    def test_r2_token_expiry_sidecar_strict_schema_fail_closed(self) -> None:
        """R2: Strict sidecar validation fails closed on invalid types, nonfinites, and malformed JSON."""
        ctx = self.syn.context()
        clock = Clock()
        store = ApprovalStore(ctx.runtime_root)
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)

        # Non-finite or bool or None expires_at must fail with response_invalid
        for bad_exp in (None, True, False, float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(SecretBrokerError) as cm:
                store.bind_token_expiry(approval, bad_exp)
            require(cm.exception.code == "response_invalid", "invalid expiry must raise response_invalid")

        # Earliest expiry never extends
        eff1 = store.bind_token_expiry(approval, clock.now + 100.0)
        require(eff1 == clock.now + 100.0, "initial effective expiry must match token")
        eff2 = store.bind_token_expiry(approval, clock.now + 200.0)
        require(eff2 == clock.now + 100.0, "later expiry must not extend effective lifetime")

        # Sidecar file corruption: build valid baseline from existing sidecar
        sidecar_path = ctx.runtime_root / "approvals" / f"{approval.approval_id}.token_expiry.json"
        raw_valid = sidecar_path.read_bytes()
        valid_dict = json.loads(raw_valid.decode("ascii"))

        continuities = {sc.alias: sc for sc in approval.sources}
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        auth_create = authority_for(ctx, "personal", "create")
        store.require_current(approval, operation="migrate", profile="personal",
                              config=ctx.config, continuities=continuities,
                              destination_sha256=dest_sha, authority_digest=auth_create,
                              now=clock.now + 10.0)

        # Duplicate keys in JSON
        require(raw_valid.endswith(b"}"), "sidecar baseline must be a JSON object")
        dup_json = raw_valid[:-1] + b',"version":1}'
        write_private_file(sidecar_path, dup_json, exclusive=False)
        with self.assertRaises(SecretBrokerError) as cm_dup:
            store.require_current(approval, operation="migrate", profile="personal",
                                  config=ctx.config, continuities=continuities,
                                  destination_sha256=dest_sha, authority_digest=auth_create,
                                  now=clock.now + 10.0)
        require(cm_dup.exception.code == "approval_mismatch", "duplicate keys must raise approval_mismatch")

        # Missing key in sidecar
        missing_data = dict(valid_dict)
        del missing_data["version"]
        write_private_file(sidecar_path, canonical_json(missing_data), exclusive=False)
        with self.assertRaises(SecretBrokerError) as cm_missing:
            store.require_current(approval, operation="migrate", profile="personal",
                                  config=ctx.config, continuities=continuities,
                                  destination_sha256=dest_sha, authority_digest=auth_create,
                                  now=clock.now + 10.0)
        require(cm_missing.exception.code == "approval_mismatch", "missing key must raise approval_mismatch")

        # Extra key in sidecar
        extra_data = dict(valid_dict)
        extra_data["extra_unsupported"] = True
        write_private_file(sidecar_path, canonical_json(extra_data), exclusive=False)
        with self.assertRaises(SecretBrokerError) as cm_extra:
            store.require_current(approval, operation="migrate", profile="personal",
                                  config=ctx.config, continuities=continuities,
                                  destination_sha256=dest_sha, authority_digest=auth_create,
                                  now=clock.now + 10.0)
        require(cm_extra.exception.code == "approval_mismatch", "extra keys must raise approval_mismatch")

    # =========================================================================
    # R3: Cleanup Crash Recovery, Writer Hook & Proven Removed Tracking
    # =========================================================================

    def test_r3_cleanup_partial_crash_after_replace_before_checkpoint_resumes_cleanly(self) -> None:
        """R3: Partial crash after ALPHA os.replace recovers proven removed ALPHA and cleans BETA."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap1 = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI()
        # Prepopulate fake_api with expected remote secret values for readback
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        # Patch checkpoint_removed to fail once after ALPHA's os.replace
        checkpoint_calls = []
        orig_checkpoint = CleanupProgress.checkpoint_removed

        def failing_checkpoint(self_prog, *args, **kwargs):
            checkpoint_calls.append(args[0])
            if len(checkpoint_calls) == 1:
                raise RuntimeError("simulated crash after os.replace before checkpoint")
            return orig_checkpoint(self_prog, *args, **kwargs)

        with patch.object(CleanupProgress, "checkpoint_removed", failing_checkpoint):
            res_crashed = run_cleanup(ctx, "personal", ap1.approval_id, confirm=True, adapter=adapter)

        require(res_crashed["ok"] is False, "first cleanup run must fail at simulated crash")
        require(checkpoint_calls == ["ALPHA"], "checkpoint must be called for ALPHA")

        # Observe on-disk state: ALPHA was replaced out of dotenv; cleanup.json has state='prepared'
        source_after_crash = self.syn.source.read_text("utf-8")
        require("ALPHA=" not in source_after_crash, "ALPHA must be removed from dotenv by os.replace")
        require("BETA=" in source_after_crash, "BETA must still be present in dotenv")

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "prepared", "ALPHA must be in prepared state on disk")

        # Resume under fresh context and NEW cleanup approval ID
        fresh_ctx = self.syn.context()
        ap2 = _create_cleanup_approval(fresh_ctx, clock, "personal")

        res_resumed = run_cleanup(fresh_ctx, "personal", ap2.approval_id, confirm=True, adapter=adapter)

        require(res_resumed["ok"] is True, "cleanup resume must succeed")
        require(res_resumed["code"] == "cleaned", "cleanup code must be cleaned")

        source_final = self.syn.source.read_text("utf-8")
        require("ALPHA=" not in source_final, "ALPHA must remain absent")
        require("BETA=" not in source_final, "BETA must be cleanly removed")

        prog_data_final = json.loads(prog_path.read_text("utf-8"))
        require(prog_data_final["items"]["ALPHA"]["state"] == "removed", "ALPHA promoted to removed")
        require(prog_data_final["items"]["BETA"]["state"] == "removed", "BETA recorded as removed")

    def test_r3_cleanup_completion_record_failure_preserves_progress(self) -> None:
        """R3: I/O failure while writing completion record does not corrupt cleanup.json progress."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")
        ap = _create_cleanup_approval(ctx, clock, "personal")

        fake_api = FakeAPI()
        fake_api.items["ALPHA"] = bytearray(self.syn.values["ALPHA"].encode("utf-8"))
        fake_api.items["BETA"] = bytearray(self.syn.values["BETA"].encode("utf-8"))
        adapter = FakeOwnershipAdapter(fake_api)

        def failing_completion(*args, **kwargs):
            raise OSError(errno.EIO, "simulated disk failure on completion record")

        with patch("sandbox.secrets.ownership.migrate.write_completion_record", failing_completion):
            res = run_cleanup(ctx, "personal", ap.approval_id, confirm=True, adapter=adapter)

        require(res["ok"] is False, "completion record failure must convert ok to False")
        require(res["code"] == "provider_failed", "completion record failure code must be provider_failed")
        require(res.get("operation_code") == "cleaned", "original operation code cleaned must be preserved in operation_code")
        require(res.get("_exit") == 4, "completion record failure exit must be 4")
        require(res.get("persistence") == "persistence_failed", "persistence indicator must be persistence_failed")
        require(res.get("completion_record") == "persistence_failed", "completion_record indicator must be persistence_failed")

        # Cleanup progress sidecar on disk was already committed
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        require(prog_path.is_file(), "cleanup.json must exist despite completion failure")
        prog_data = json.loads(prog_path.read_text("utf-8"))
        require(prog_data["items"]["ALPHA"]["state"] == "removed", "ALPHA state preserved")
        require(prog_data["items"]["BETA"]["state"] == "removed", "BETA state preserved")

        # Resume under fresh approval confirms durable progress and succeeds
        fresh_ctx = self.syn.context()
        ap_resume = _create_cleanup_approval(fresh_ctx, clock, "personal")
        res_resumed = run_cleanup(fresh_ctx, "personal", ap_resume.approval_id, confirm=True, adapter=adapter)
        require(res_resumed["ok"] is True, "resume after completion record failure must succeed")
        require(res_resumed["code"] == "cleaned", "resume code must be cleaned")

    def test_r3_unexpected_missing_source_key_without_intent_refuses(self) -> None:
        """R3: Source key missing without matching cleanup.json intent refuses as cleanup_ineligible."""
        ctx = self.syn.context()
        clock = Clock()
        _setup_verified_journal(ctx, "personal")

        # Tamper dotenv by deleting ALPHA without any progress record
        lines = [line for line in self.syn.source.read_text("utf-8").splitlines() if not line.startswith("ALPHA=")]
        self.syn.source.write_text("\n".join(lines) + "\n", encoding="utf-8")

        fake_api = FakeAPI()
        adapter = FakeOwnershipAdapter(fake_api)

        res = run_cleanup(ctx, "personal", approval_id=None, confirm=False, adapter=adapter)

        require(res["ok"] is False, "unexpected missing key must refuse")
        require(res["code"] == "cleanup_ineligible", "refusal code must be cleanup_ineligible")
        require(res["_exit"] == 3, "exit code must be 3")

    def test_r3_reintroduced_names_not_skipped_blindly(self) -> None:
        """R3: Reintroduced key present in source is excluded from proven_removed_names."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        auth_create = authority_for(ctx, "personal", "create")

        # Manually create cleanup.json marking ALPHA as removed
        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        ensure_private_dir(prog_path.parent)
        data = {
            "cleanup": 1,
            "controller_id": ctx.controller_id,
            "profile": "personal",
            "destination_sha256": dest_sha,
            "map_sha256": ctx.config.map_sha256,
            "authority_digest": auth_create,
            "items": {
                "ALPHA": {
                    "name": "ALPHA",
                    "key": "ALPHA",
                    "source_alias": "synthetic-file",
                    "state": "removed",
                    "approval_id": new_qualified_id("ap", ctx.controller_id),
                    "dev": 0,
                    "ino": 0,
                    "names_sha256": "0" * 64,
                    "updated_at": 100.0,
                }
            },
        }
        write_private_file(prog_path, canonical_json(data))

        # ALPHA is currently present in syn.source
        progress = CleanupProgress.load_or_recover(
            ctx.runtime_root, "personal", dest_sha,
            controller_id=ctx.controller_id, authority_digest=auth_create,
            map_sha256=ctx.config.map_sha256, registry=ctx.registry,
            config=ctx.config,
        )

        require("ALPHA" in progress.reintroduced_names, "ALPHA must be tracked as reintroduced")
        require("ALPHA" not in progress.proven_removed_names(), "ALPHA must not be proven removed")

    def test_r3_dry_run_leaves_all_state_unchanged(self) -> None:
        """R3: Dry-run cleanup plan leaves source bytes and stats completely untouched."""
        ctx = self.syn.context()
        _setup_verified_journal(ctx, "personal")

        st_before = self.syn.source.stat()
        bytes_before = self.syn.source.read_bytes()

        fake_api = FakeAPI()
        adapter = FakeOwnershipAdapter(fake_api)

        res = run_cleanup(ctx, "personal", approval_id=None, confirm=False, adapter=adapter)

        require(res["ok"] is True, "dry-run must be ok")
        require(res["code"] == "cleanup_plan", "code must be cleanup_plan")

        bytes_after = self.syn.source.read_bytes()
        st_after = self.syn.source.stat()
        require(bytes_before == bytes_after, "source bytes must not change during dry-run")
        require((st_before.st_dev, st_before.st_ino, st_before.st_size, st_before.st_mtime_ns)
                == (st_after.st_dev, st_after.st_ino, st_after.st_size, st_after.st_mtime_ns),
                "source stat must be strictly unchanged")

    def test_r3_retention_preserves_cleanup_json(self) -> None:
        """R3: Retention policy preserves journal directories containing cleanup.json."""
        ctx = self.syn.context()
        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        _setup_verified_journal(ctx, "personal")

        prog_path = cleanup_progress_path(ctx.runtime_root, "personal", dest_sha)
        ensure_private_dir(prog_path.parent)
        write_private_file(prog_path, b'{"cleanup": 1}\n')

        res = retention(ctx.runtime_root, "personal", confirm=True)

        require(dest_sha in res["journals"]["kept"], "journal with cleanup.json must be kept")
        require(dest_sha not in res["journals"]["removed"], "journal must not be removed")
        require(prog_path.is_file(), "cleanup.json must still exist")

    # =========================================================================
    # R4: Revocation Failures, Exit Codes, Operator Guidance & CLI Actions
    # =========================================================================

    def test_r4_unknown_create_and_revoke_failure_preserves_acceptance_unknown_and_actions(self) -> None:
        """R4: acceptance_unknown create failure with cleanup_unknown revocation preserves exit 5 and guidance."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)
        self.exit_stack.enter_context(patch.object(approval_mod, "time", clock))
        self.exit_stack.enter_context(patch.object(migrate, "time", clock))

        # Revocation fails with cleanup_unknown and finite token expiry
        token_exp = clock.now + 3600.0
        fake_api = FakeAPI(clock=clock, revoke_unknown=True, script={"ALPHA": "lost"})
        adapter = FakeOwnershipAdapter(fake_api)

        orig_open_session = adapter.open_session

        def open_future_exp_session(grant, deadline):
            session = orig_open_session(grant, deadline=deadline)
            session.token_expires_at = token_exp
            session.worker.token_expires_at = token_exp
            return session

        adapter.open_session = open_future_exp_session

        res = run_migration(ctx, "personal", approval.approval_id, confirm=True,
                            timeout_seconds=30, adapter=adapter)

        require(res["ok"] is False, "migration must be marked failed")
        require(res["_exit"] == 5, "exit code must be 5 for acceptance_unknown")
        require(res["revocation"] == "cleanup_unknown", "revocation status must be cleanup_unknown")
        next_action = res.get("next_action", "")
        require("derived access token revocation not confirmed" in next_action,
                "next_action must contain derived access token warning")
        require(f"expires at {int(token_exp)}" in next_action,
                "next_action must format finite token expiry")
        require("native bootstrap entries and Cloudflare credentials remain operator-managed" in next_action,
                "next_action must include operator guidance")

    def test_r4_classify_check_destination_revoke_failure_cli(self) -> None:
        """R4: CLI classify --check-destination with revoke failure formats follow-up action and returns exit 6."""
        ctx = self.syn.context()
        fake_api = FakeAPI(revoke_unknown=True)
        adapter = FakeOwnershipAdapter(fake_api)

        code, body = command_in_process(
            ctx, self.exit_stack, adapter,
            "ownership", "classify", "--profile", "personal", "--check-destination",
        )

        require(code == 6, f"cleanup_unknown exit must be 6, got {code}")
        require(body["ok"] is False, "classification must be marked not ok")
        require(body["code"] == "cleanup_unknown", "code must be cleanup_unknown")
        require(body["revocation"] == "cleanup_unknown", "revocation must be cleanup_unknown")
        require("derived access token revocation not confirmed" in body.get("next_action", ""),
                "next_action must include token revocation follow-up")

    def test_r4_scoped_run_read_success_and_revoke_failure_blocks_child(self) -> None:
        """R4: In scoped run, read success + revocation failure raises cleanup_unknown and never executes child."""
        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.__exit__.return_value = None
        fake_session.read.return_value = {"REMOTE_KEY": bytearray(b"remote_val")}
        fake_session.revocation.return_value = RevocationOutcome(
            status="cleanup_unknown",
            token_expires_at=1720000000.0,
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

        req = ScopedRunRequest(
            profile="personal",
            keys=("REMOTE_KEY",),
            argv=("echo", "should_not_run"),
            timeout_seconds=5,
            max_output_bytes=1024,
        )

        with self.assertRaises(SecretBrokerError) as cm:
            resolve_scoped_bindings(ctx, req, adapter=adapter)

        require(cm.exception.code == "cleanup_unknown", "must raise cleanup_unknown")
        require(getattr(cm.exception, "revocation", None) == "cleanup_unknown",
                "revocation=cleanup_unknown must be attached")

    def test_r4_primary_read_error_and_revoke_failure_retains_primary_code(self) -> None:
        """R4: In scoped run, read failure + revocation failure preserves primary code with attached outcome."""
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

        req = ScopedRunRequest(
            profile="personal",
            keys=("REMOTE_KEY",),
            argv=("echo", "should_not_run"),
            timeout_seconds=5,
            max_output_bytes=1024,
        )

        with self.assertRaises(SecretBrokerError) as cm:
            resolve_scoped_bindings(ctx, req, adapter=adapter)

        require(cm.exception.code == "response_invalid", "primary error response_invalid must be preserved")
        require(getattr(cm.exception, "revocation", None) == "cleanup_unknown",
                "revocation status cleanup_unknown must be attached")

    def test_r4_arbitrary_backend_next_action_ignored(self) -> None:
        """R4: Arbitrary provider backend next_action string is ignored and never exposed to caller."""
        ctx = self.syn.context()
        fake_api = FakeAPI(revoke_unknown=True)
        adapter = FakeOwnershipAdapter(fake_api)

        orig_open_session = adapter.open_session

        def open_tampered_session(grant, deadline):
            session = orig_open_session(grant, deadline=deadline)
            orig_revocation = session.revocation

            def tampered_revocation():
                res = orig_revocation()
                return RevocationOutcome(res.status, res.token_expires_at,
                                         next_action="ATTACKER_INJECTED_BACKEND_PAYLOAD")

            session.revocation = tampered_revocation
            return session

        adapter.open_session = open_tampered_session

        code, body = command_in_process(
            ctx, self.exit_stack, adapter,
            "ownership", "classify", "--profile", "personal", "--check-destination",
        )

        next_action = body.get("next_action", "")
        require("ATTACKER_INJECTED_BACKEND_PAYLOAD" not in next_action,
                "arbitrary backend next_action must never be rendered")
        require("native bootstrap entries and Cloudflare credentials remain operator-managed" in next_action,
                "standard operator guidance must be present")

    # =========================================================================
    # Needs-Testing 5: Broker Session Fsync Abort & Frame Loss Handling
    # =========================================================================

    def test_needs_testing_5_broker_fsync_failure_sends_abort_never_go(self) -> None:
        """Needs-Testing 5: Journal fsync failure in before_send sends abort, never GO, zero POSTs."""
        ctx = self.syn.context()
        clock = Clock()
        approval = self.syn.synthetic_approval(ctx, self.exit_stack, clock)

        adapter = BrokerOwnershipAdapter()
        grant = fake_grant(ctx, capability="create", names=("ALPHA", "BETA"))
        session = BrokerOwnershipSession(adapter, grant, deadline=clock.now + 60.0)

        fake_channel = FakeFrameChannel()
        session._channel = fake_channel
        session._opened = True
        session._broken = False

        dest_sha = destination_sha256(ctx.config.profiles["personal"].destination)
        journal = Journal.open(ctx.runtime_root, "personal", dest_sha, controller_id=ctx.controller_id,
                               authority_digest=authority_for(ctx, "personal", "create"))
        self.addCleanup(journal.close)
        run_id = new_qualified_id("run", ctx.controller_id)

        real_fsync = os.fsync

        def failing_fsync(fd):
            raise OSError(errno.EIO, "simulated journal fsync failure")

        def before_send():
            with patch("os.fsync", failing_fsync):
                journal.append(run_id, approval.approval_id, "ALPHA", NameState.ATTEMPTED)

        val = bytearray(b"secret_bytes")
        with self.assertRaises(OSError):
            session.create("ALPHA", val, comment="test", before_send=before_send)

        sent_ops = [msg.get("op") for msg in fake_channel.sent]
        require("create" in sent_ops, "create frame must be sent")
        require("abort" in sent_ops, "abort frame must be sent after before_send failure")
        require("go" not in sent_ops, "go frame must NEVER be sent when before_send fails")

    def test_needs_testing_5_frame_loss_after_go_preserves_acceptance_unknown(self) -> None:
        """Needs-Testing 5: Frame loss after GO yields ACCEPTANCE_UNKNOWN with worker_lost, no blind retry."""
        ctx = self.syn.context()
        clock = Clock()
        adapter = BrokerOwnershipAdapter()
        grant = fake_grant(ctx, capability="create", names=("ALPHA",))
        session = BrokerOwnershipSession(adapter, grant, deadline=clock.now + 60.0)

        fake_channel = FakeFrameChannel(break_after_go=True)
        session._channel = fake_channel
        session._opened = True
        session._broken = False

        val = bytearray(b"secret_bytes")
        outcome = session.create("ALPHA", val, comment="test", before_send=lambda: None)

        require(outcome.name == "ALPHA", "outcome name must match")
        require(outcome.state == NameState.ACCEPTANCE_UNKNOWN, "state must be ACCEPTANCE_UNKNOWN")
        require(outcome.reason == "worker_lost", "reason must be worker_lost")
        require(session._broken is True, "session must be marked broken to prevent retries")

    # =========================================================================
    # Needs-Testing 6: Real Subprocess Scoped Run Timeout & Process Group Reap
    # =========================================================================

    def test_needs_testing_6_scoped_child_timeout_returns_124_with_group_cleanup(self) -> None:
        """Needs-Testing 6: Real CLI scoped run with timed-out child exits 124 and reaps process group."""
        pid_file = self.syn.tmp / "child_pid.txt"
        helper_script = self.syn.tmp / "sleep_helper.py"
        # Helper prints no values/env; only records its own PID before sleeping
        helper_code = (
            "import os, sys, time\n"
            "with open(sys.argv[1], 'w') as f:\n"
            "    f.write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        helper_script.write_text(helper_code, encoding="utf-8")

        start_time = time.monotonic()
        cmd = [
            str(REPO / "sb"), "secrets", "run",
            "--ownership-profile", "personal",
            "--ownership-key", "ALPHA",
            "--project-dir", str(self.syn.project),
            "--json",
            "--timeout-seconds", "1",
            "--",
            sys.executable, str(helper_script), str(pid_file),
        ]

        proc = run_test_process(
            cmd,
            env=self.syn.env,
            cwd=REPO,
            timeout=5,
            capture_output=True,
            text=True,
            input="",
        )
        elapsed = time.monotonic() - start_time

        require(proc.returncode == 124, f"expected exit code 124 on timeout, got {proc.returncode}")
        require(elapsed < 5.0, f"test must execute in < 5 seconds, took {elapsed:.2f}s")

        body = json.loads(proc.stdout)
        require("_exit" not in body, "public json payload intentionally omits internal _exit")
        require(body.get("ok") is False, "timed out child ok must be False")
        require(body.get("code") == "child_failed", "payload code must be child_failed")
        require(body.get("result", {}).get("termination") == "timed_out",
                "result termination must be timed_out")

        # Verify child process group was terminated using os.kill(0)
        require(pid_file.is_file(), "child process must have recorded its PID")
        child_pid = int(pid_file.read_text("utf-8").strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

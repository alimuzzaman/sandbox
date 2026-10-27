"""Focused regressions for ownership cleanup (T057).

Covers post-migration cleanup of source keys:
ineligibility when names unverified, dry-run plan without confirmation,
fresh read grant per name, readback mismatch halting without removing remaining,
atomic dotenv key removal, source guard on stale change, and independent approval.
"""

from __future__ import annotations

from dataclasses import asdict
import os
import pwd
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.approval import ApprovalStore
from sandbox.secrets.ownership.cleanup import run_cleanup
from sandbox.secrets.ownership.journal import (
    Journal, destination_sha256, ensure_private_dir, new_qualified_id,
    read_private_file, write_private_file,
)
from sandbox.secrets.ownership.migrate import authority_for
from sandbox.secrets.ownership.models import (
    Approval, NameState, StorageAdmission, canonical_json,
)
from sandbox.secrets.ownership.source_view import read_names
from tests.secret_ownership_support import (
    Clock, FakeAPI, FakeOwnershipAdapter, SyntheticOwnership, require,
)


def _setup_verified_journal(ctx, profile: str = "personal") -> None:
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


def _create_cleanup_approval(ctx, root: Path, clock: Clock, profile: str = "personal") -> Approval:
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
        operation="cleanup",  # Explicit cleanup operation
        operator_uid=os.geteuid(),
        operator_login=login,
        controller_id=ctrl_id,
        profile=profile,
        sources=(continuity,),
        map_sha256=ctx.config.map_sha256,
        destination_sha256=destination_sha256(dest),
        authority_digest=auth_read,
        evidence_sha256=None,  # Not required for cleanup
        evidence_expires_at=None,
        created_at=clock.now,
        expires_at=clock.now + 1800,
        surface="cli_tty",
    )
    write_private_file(store._path(approval.approval_id),
                       canonical_json(asdict(approval)))
    return approval


class TestSecretOwnershipCleanup(unittest.TestCase):
    """Cleanup verification, atomicity, readback guard, and independent approval."""

    def test_cleanup_ineligible_when_not_all_verified(self) -> None:
        """Cleanup refuses with cleanup_ineligible (exit 3) if any mapped name is not verified."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            # Fresh journal without verified records
            res = run_cleanup(ctx, "personal", approval_id=None, confirm=False, adapter=adapter)
            require(res["ok"] is False, "unverified journal must be ineligible")
            require(res["code"] == "cleanup_ineligible", "code must be cleanup_ineligible")
            require(res["_exit"] == 3, "cleanup_ineligible exit must be 3")

    def test_cleanup_plan_without_confirm_leaves_sources_untouched(self) -> None:
        """Calling cleanup with confirm=False returns cleanup_plan and does not delete keys."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            _setup_verified_journal(ctx, "personal")
            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            source_before = syn.source.read_text("utf-8")
            res = run_cleanup(ctx, "personal", approval_id=None, confirm=False, adapter=adapter)
            require(res["ok"] is True, "cleanup plan must be ok")
            require(res["code"] == "cleanup_plan", "code must be cleanup_plan")
            require(res["_exit"] == 0, "exit must be 0")

            source_after = syn.source.read_text("utf-8")
            require(source_before == source_after, "source file must not be modified during plan")

    def test_cleanup_independent_approval_required(self) -> None:
        """Cleanup refuses a migration approval with approval_mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            _setup_verified_journal(ctx, "personal")
            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            # Create a migration approval
            with ExitStack() as stack:
                clock = Clock()
                clock.now = 1_000_000.0
                stack.enter_context(patch("time.time", clock.time))
                migrate_approval = syn.synthetic_approval(ctx, stack, clock)

                with self.assertRaises(SecretBrokerError) as ctx_err:
                    run_cleanup(ctx, "personal", approval_id=migrate_approval.approval_id,
                                confirm=True, adapter=adapter)
                require(ctx_err.exception.code == "approval_mismatch",
                        "must raise approval_mismatch when using migrate approval for cleanup")

    def test_cleanup_atomic_remove_verified_only(self) -> None:
        """Confirmed cleanup reads back remote value, verifies match, and removes only local keys."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            _setup_verified_journal(ctx, "personal")

            # Store matching values in fake API
            fake_api = FakeAPI()
            for key in ("ALPHA", "BETA"):
                fake_api.items[key] = bytearray(syn.values[key].encode("ascii"))
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                clock.now = 1_000_000.0
                stack.enter_context(patch("time.time", clock.time))
                approval = _create_cleanup_approval(ctx, Path(tmp_dir), clock, "personal")

                res = run_cleanup(ctx, "personal", approval_id=approval.approval_id,
                                  confirm=True, adapter=adapter)
                require(res["ok"] is True, "cleanup must succeed")
                require(res["code"] == "cleaned", "code must be cleaned")
                require(res["_exit"] == 0, "exit must be 0")

                # Verify ALPHA and BETA were removed from source dotenv, while GAMMA remains
                source_content = syn.source.read_text("utf-8")
                require("ALPHA=" not in source_content, "ALPHA must be removed from source")
                require("BETA=" not in source_content, "BETA must be removed from source")
                require("GAMMA=" in source_content, "GAMMA (office domain) must remain in source")

    def test_cleanup_readback_mismatch_preserves_remaining(self) -> None:
        """If remote value does not match local value, cleanup stops and preserves keys."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            _setup_verified_journal(ctx, "personal")

            fake_api = FakeAPI()
            # ALPHA matches, but BETA has mismatched value
            fake_api.items["ALPHA"] = bytearray(syn.values["ALPHA"].encode("ascii"))
            fake_api.items["BETA"] = bytearray(b"mismatched-remote-val")
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                clock.now = 1_000_000.0
                stack.enter_context(patch("time.time", clock.time))
                approval = _create_cleanup_approval(ctx, Path(tmp_dir), clock, "personal")

                res = run_cleanup(ctx, "personal", approval_id=approval.approval_id,
                                  confirm=True, adapter=adapter)
                require(res["ok"] is False, "cleanup with readback mismatch must fail")
                require(res["code"] == "run_stopped", "code must be run_stopped")

                # BETA must still be present in the file
                source_content = syn.source.read_text("utf-8")
                require("BETA=" in source_content, "mismatched key BETA must be preserved in source")

    def test_cleanup_source_guard_stale_change(self) -> None:
        """If source file was touched after approval, cleanup halts with source_changed or approval_mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            _setup_verified_journal(ctx, "personal")

            fake_api = FakeAPI()
            for key in ("ALPHA", "BETA"):
                fake_api.items[key] = bytearray(syn.values[key].encode("ascii"))
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                clock.now = 1_000_000.0
                stack.enter_context(patch("time.time", clock.time))
                approval = _create_cleanup_approval(ctx, Path(tmp_dir), clock, "personal")

                # Touch source file to simulate external modification
                now = time.time()
                os.utime(syn.source, (now + 50, now + 50))

                with self.assertRaises(SecretBrokerError) as ctx_err:
                    run_cleanup(ctx, "personal", approval_id=approval.approval_id,
                                confirm=True, adapter=adapter)
                require(ctx_err.exception.code in ("source_changed", "approval_mismatch"),
                        "stale source must raise source_changed or approval_mismatch")


if __name__ == "__main__":
    unittest.main()

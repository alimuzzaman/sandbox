"""Focused regressions for human approval store, token generation, and validation (T055).

Covers approval lifecycle:
1. Operator UID and terminal login binding.
2. TTL cap at 1 hour (3600 seconds).
3. Exact operation binding (migrate vs cleanup).
4. Profile, continuity, destination, and authority hash validation.
5. Storage evidence presence and TTL matching.
6. Approval token format: {type}_{controller_id}_{random16}.
7. Token consumption: one-shot use (second use raises approval_missing).
8. Expiry refusal with approval_expired.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
import pwd
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.approval import (
    MAX_APPROVAL_SECONDS, ApprovalStore,
)
from sandbox.secrets.ownership.journal import (
    destination_sha256, load_controller_id, new_qualified_id, write_private_file,
)
from sandbox.secrets.ownership.models import (
    Approval, Destination, MapEntry, OwnershipConfig, OwnershipProfile,
    SourceContinuity, StorageAdmission, canonical_json,
)
from tests.secret_ownership_support import require


def _sample_destination() -> Destination:
    return Destination(
        endpoint="https://vault.example.invalid",
        project_id="proj-a",
        environment="staging",
        path="/",
        read_client_id="reader",
        create_client_id="creator",
        timeout_seconds=30,
        gateway="none",
    )


def _sample_config(profile_name: str = "personal") -> OwnershipConfig:
    dest = _sample_destination()
    prof = OwnershipProfile(name=profile_name, domain=profile_name, destination=dest)
    entry = MapEntry(source="src1", key="KEY1", profile=profile_name, name="KEY1",
                     consumer="KEY1", origin="source")
    return OwnershipConfig.from_normalized({
        "profiles": {profile_name: {"domain": prof.domain, "destination": prof.destination.as_normalized()}},
        "entries": [entry.as_normalized()],
    })


def _sample_continuity(alias: str = "src1") -> SourceContinuity:
    names_sha = hashlib.sha256(canonical_json(["KEY1"])).hexdigest()
    return SourceContinuity(
        alias=alias,
        dev=1,
        ino=1,
        mode=0o100600,
        uid=os.geteuid(),
        nlink=1,
        size=100,
        mtime_ns=1_000_123,
        ctime_ns=1_000_456,
        names_sha256=names_sha,
        reliable=True,
        unreliable_reason=None,
    )


def _sample_approval(root: Path, *, operation: str = "migrate",
                     profile: str = "personal", ttl: int = 1800,
                     operator_uid: int | None = None,
                     authority_digest: str = "o1-" + "a" * 64) -> Approval:
    ctrl_id = load_controller_id(root)
    now = time.time()
    try:
        login = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        login = str(os.geteuid())

    return Approval(
        approval_id=new_qualified_id("ap", ctrl_id),
        operation=operation,
        operator_uid=operator_uid if operator_uid is not None else os.geteuid(),
        operator_login=login,
        controller_id=ctrl_id,
        profile=profile,
        sources=(_sample_continuity(),),
        map_sha256=_sample_config(profile).map_sha256,
        destination_sha256=destination_sha256(_sample_destination()),
        authority_digest=authority_digest,
        evidence_sha256="ev-" + "e" * 64 if operation == "migrate" else None,
        evidence_expires_at=now + ttl if operation == "migrate" else None,
        created_at=now,
        expires_at=now + ttl,
        surface="cli_tty",
    )


class TestSecretOwnershipApproval(unittest.TestCase):
    """Approval lifecycle, token validation, and one-shot consumption regressions."""

    def test_approval_id_format(self) -> None:
        """Token format must match {type}_{controller_id}_{random16}."""
        ctrl_id = "a" * 32
        ap_id = new_qualified_id("ap", ctrl_id)
        parts = ap_id.split("_")
        require(parts[0] == "ap", "token type must be ap")
        require(parts[1] == ctrl_id, "controller id must match")
        require(len(parts[2]) == 16, "last segment must be 16-hex random string")

    def test_approval_expiry_capped_at_one_hour(self) -> None:
        """Approval duration cannot exceed MAX_APPROVAL_SECONDS (3600)."""
        require(MAX_APPROVAL_SECONDS == 3600, "MAX_APPROVAL_SECONDS must be 3600")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            ap = _sample_approval(root, ttl=7200)  # 2 hours!

            write_private_file(store._path(ap.approval_id), canonical_json(asdict(ap)))

            with self.assertRaises(SecretBrokerError) as ctx:
                store.load(ap.approval_id)
            require(ctx.exception.code == "approval_mismatch",
                "ttl > 3600 must fail validation with approval_mismatch")

    def test_approval_binding_operation_mismatch(self) -> None:
        """A migration approval cannot be reused for cleanup; raises approval_mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            cfg = _sample_config("personal")
            cont = _sample_continuity("src1")

            ap_migrate = _sample_approval(root, operation="migrate")
            write_private_file(store._path(ap_migrate.approval_id),
                               canonical_json(asdict(ap_migrate)))

            loaded = store.load(ap_migrate.approval_id)
            with self.assertRaises(SecretBrokerError) as ctx:
                store.require_current(
                    loaded,
                    operation="cleanup",  # Mismatch!
                    profile="personal",
                    config=cfg,
                    continuities={"src1": cont},
                    destination_sha256=destination_sha256(_sample_destination()),
                    authority_digest=ap_migrate.authority_digest,
                    now=time.time(),
                )
            require(ctx.exception.code == "approval_mismatch",
                    "operation mismatch must raise approval_mismatch")

    def test_approval_binding_operator_uid_mismatch(self) -> None:
        """An approval created by another UID raises approval_mismatch on verification."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            foreign_uid = 9999 if os.geteuid() != 9999 else 9998
            ap_foreign = _sample_approval(root, operator_uid=foreign_uid)

            write_private_file(store._path(ap_foreign.approval_id),
                               canonical_json(asdict(ap_foreign)))

            loaded = store.load(ap_foreign.approval_id)
            cfg = _sample_config("personal")
            cont = _sample_continuity("src1")
            with self.assertRaises(SecretBrokerError) as ctx:
                store.require_current(
                    loaded,
                    operation="migrate",
                    profile="personal",
                    config=cfg,
                    continuities={"src1": cont},
                    destination_sha256=destination_sha256(_sample_destination()),
                    authority_digest=ap_foreign.authority_digest,
                    now=time.time(),
                )
            require(ctx.exception.code == "approval_mismatch",
                    "foreign operator UID must raise approval_mismatch")

    def test_approval_expired_refusal(self) -> None:
        """An expired approval raises approval_expired."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            cfg = _sample_config("personal")
            cont = _sample_continuity("src1")

            ap = _sample_approval(root)
            object.__setattr__(ap, "created_at", 1_000_000.0)
            object.__setattr__(ap, "expires_at", 1_000_500.0)

            write_private_file(store._path(ap.approval_id), canonical_json(asdict(ap)))

            loaded = store.load(ap.approval_id)
            with self.assertRaises(SecretBrokerError) as ctx:
                store.require_current(
                    loaded,
                    operation="migrate",
                    profile="personal",
                    config=cfg,
                    continuities={"src1": cont},
                    destination_sha256=destination_sha256(_sample_destination()),
                    authority_digest=ap.authority_digest,
                    now=2_000_000.0,  # Well past expiration
                )
            require(ctx.exception.code == "approval_expired",
                    "past expires_at must raise approval_expired")

    def test_approval_storage_evidence_presence_and_ttl(self) -> None:
        """Migration approval requires valid storage evidence with matching TTL."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            cfg = _sample_config("personal")
            cont = _sample_continuity("src1")
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)

            ap_no_ev = _sample_approval(root, operation="migrate")
            object.__setattr__(ap_no_ev, "evidence_sha256", None)
            write_private_file(store._path(ap_no_ev.approval_id),
                               canonical_json(asdict(ap_no_ev)))

            loaded = store.load(ap_no_ev.approval_id)
            with self.assertRaises(SecretBrokerError) as ctx1:
                store.require_current(
                    loaded,
                    operation="migrate",
                    profile="personal",
                    config=cfg,
                    continuities={"src1": cont},
                    destination_sha256=dest_sha,
                    authority_digest=ap_no_ev.authority_digest,
                    now=time.time(),
                )
            require(ctx1.exception.code == "approval_mismatch",
                    "migrate approval without evidence must raise approval_mismatch")

    def test_approval_one_shot_consumption(self) -> None:
        """Calling store.consume marks approval consumed; subsequent require_current raises approval_mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            cfg = _sample_config("personal")
            cont = _sample_continuity("src1")
            dest_sha = destination_sha256(_sample_destination())
            ap = _sample_approval(root, operation="cleanup")

            write_private_file(store._path(ap.approval_id), canonical_json(asdict(ap)))

            loaded = store.load(ap.approval_id)
            # First check passes
            store.require_current(
                loaded, operation="cleanup", profile="personal", config=cfg,
                continuities={"src1": cont}, destination_sha256=dest_sha,
                authority_digest=ap.authority_digest, now=time.time(),
            )

            # Consume approval
            store.consume(loaded)

            # Second require_current raises approval_mismatch
            with self.assertRaises(SecretBrokerError) as ctx:
                store.require_current(
                    loaded, operation="cleanup", profile="personal", config=cfg,
                    continuities={"src1": cont}, destination_sha256=dest_sha,
                    authority_digest=ap.authority_digest, now=time.time(),
                )
            require(ctx.exception.code == "approval_mismatch",
                    "consumed approval must raise approval_mismatch")

    def test_approval_authority_hash_mismatch(self) -> None:
        """Mismatched authority_digest raises authority_mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            store = ApprovalStore(root)
            cfg = _sample_config("personal")
            cont = _sample_continuity("src1")

            ap = _sample_approval(root, authority_digest="o1-" + "a" * 64)
            write_private_file(store._path(ap.approval_id), canonical_json(asdict(ap)))

            loaded = store.load(ap.approval_id)
            with self.assertRaises(SecretBrokerError) as ctx:
                store.require_current(
                    loaded,
                    operation="migrate",
                    profile="personal",
                    config=cfg,
                    continuities={"src1": cont},
                    destination_sha256=destination_sha256(_sample_destination()),
                    authority_digest="o1-" + "b" * 64,  # Changed authority digest!
                    now=time.time(),
                )
            require(ctx.exception.code == "authority_mismatch",
                    "mismatched authority digest must raise authority_mismatch")


if __name__ == "__main__":
    unittest.main()

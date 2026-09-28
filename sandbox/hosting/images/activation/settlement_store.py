"""Immutable machine-installed approvals; private keys never enter this store."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..provisioning import _read_owner_only_json, install_owner_only_json
from .models import _closed, _digest, activation_digest
from .settlement_forward import ForwardSettlementApproval
from .settlement_models import SettlementApproval, SettlementPlan
from .settlement_policy import SettlementApprovalVerifier
from .settlement_service import SettlementError


_FIELDS = frozenset({"schema_version", "kind", "target", "approval", "public_key",
    "authority_id", "authority_revision", "verification_digest", "installation_digest"})


class SettlementApprovalStore:
    def __init__(self, root: Path):
        if not isinstance(root, Path) or not root.is_absolute():
            raise SettlementError("path_unsafe")
        self.root = root

    def _path(self, kind, target, approval_digest):
        if kind not in {"settlement", "forward"}:
            raise SettlementError("artifact_invalid")
        _digest(approval_digest)
        scope = activation_digest("sandbox.hosting.images.settlement-store-target.v1", target)[7:]
        return self.root / scope / (kind + "-" + approval_digest[7:] + ".json")

    @staticmethod
    def _authority(approval, public_key):
        # The normalizer rejects line injection; comments never become key authority.
        from .settlement_policy import _safe_public_key
        public = _safe_public_key(public_key)
        return SettlementApprovalVerifier(public, approval.authority_id, approval.authority_revision,
            "sha256:" + hashlib.sha256(public.encode("ascii")).hexdigest())

    def install_settlement(self, plan: SettlementPlan, approval: SettlementApproval,
                           public_key: str, *, now: int):
        if type(plan) is not SettlementPlan or type(approval) is not SettlementApproval:
            raise SettlementError("artifact_invalid")
        authority = self._authority(approval, public_key)
        if not authority.verify(approval, plan, now=now):
            raise SettlementError("authority_mismatch")
        return self._install("settlement", plan.target.as_mapping(), approval, authority)

    def install_forward(self, approval: ForwardSettlementApproval, public_key: str, *, now: int):
        if type(approval) is not ForwardSettlementApproval:
            raise SettlementError("artifact_invalid")
        authority = self._authority(approval, public_key)
        if not authority.verify_forward(approval, now=now):
            raise SettlementError("authority_mismatch")
        return self._install("forward", approval.target.as_mapping(), approval, authority)

    def _install(self, kind, target, approval, authority):
        body = {"schema_version": 1, "kind": kind, "target": target,
            "approval": approval.as_mapping(), "public_key": authority.public_key,
            "authority_id": authority.authority_id, "authority_revision": authority.authority_revision,
            "verification_digest": authority.verification_digest}
        value = {**body, "installation_digest": activation_digest(
            "sandbox.hosting.images.installed-settlement-approval.v1", body)}
        disposition = install_owner_only_json(
            self._path(kind, target, approval.approval_digest), value)
        return {"schema_version": 1, "ok": True, "code": disposition,
            "kind": kind, "approval_digest": approval.approval_digest,
            "installation_digest": value["installation_digest"]}

    def _read(self, kind, target, approval_digest):
        raw = _read_owner_only_json(self._path(kind, target, approval_digest))
        if raw is None:
            raise SettlementError("authority_missing")
        raw = _closed(raw, _FIELDS)
        if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1
                or raw["kind"] != kind or raw["target"] != target):
            raise SettlementError("authority_mismatch")
        body = {key: value for key, value in raw.items() if key != "installation_digest"}
        if raw["installation_digest"] != activation_digest(
                "sandbox.hosting.images.installed-settlement-approval.v1", body):
            raise SettlementError("authority_mismatch")
        approval = (SettlementApproval if kind == "settlement" else ForwardSettlementApproval).from_mapping(
            raw["approval"])
        if approval.approval_digest != approval_digest:
            raise SettlementError("authority_mismatch")
        authority = SettlementApprovalVerifier(raw["public_key"], raw["authority_id"],
            raw["authority_revision"], raw["verification_digest"])
        return approval, authority

    def read_settlement(self, plan: SettlementPlan, approval_digest: str, *, now: int):
        approval, authority = self._read("settlement", plan.target.as_mapping(), approval_digest)
        if not authority.verify(approval, plan, now=now):
            raise SettlementError("authority_mismatch")
        return approval

    def read_forward(self, target: dict, approval_digest: str, *, now: int):
        approval, authority = self._read("forward", target, approval_digest)
        if not authority.verify_forward(approval, now=now):
            raise SettlementError("authority_mismatch")
        return approval

    def find_forward(self, subject: dict, *, now: int):
        """Resolve an exact prepared successor using only verified installed records."""
        target = subject["target"]
        directory = self._path("forward", target, "sha256:" + "0" * 64).parent
        if not directory.exists():
            return None
        from ..provisioning import _owned_directory
        _owned_directory(directory, create=False)
        paths = list(directory.glob("forward-*.json"))
        if len(paths) > 200:
            raise SettlementError("authority_mismatch")
        matches = []
        for path in paths:
            digest = "sha256:" + path.name[len("forward-"):-len(".json")]
            approval = self.read_forward_claim(target, digest)
            raw = approval.as_mapping()
            if all(raw.get(key) == value for key, value in subject.items()):
                if approval.expires_at > now:
                    matches.append(self.read_forward(target, digest, now=now))
        if len(matches) > 1:
            raise SettlementError("authority_mismatch")
        return matches[0] if matches else None

    def read_forward_claim(self, target: dict, approval_digest: str):
        """Identify a retained request; current admission must verify again."""
        approval, authority = self._read("forward", target, approval_digest)
        if not authority.verify_forward(approval, now=approval.issued_at):
            raise SettlementError("authority_mismatch")
        return approval

    def verify_forward(self, approval: ForwardSettlementApproval, *, now: int) -> bool:
        try:
            return self.read_forward(approval.target.as_mapping(), approval.approval_digest, now=now) == approval
        except (OSError, TypeError, ValueError, RuntimeError):
            return False

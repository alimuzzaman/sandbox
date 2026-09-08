"""Machine-owned verification policy for explicit Feature 051 settlement."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .models import ActivationContractError
from .settlement_models import SettlementApproval, SettlementPlan


SETTLEMENT_NAMESPACE = "sandbox-feature-051-settlement"
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_PUBLIC_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,3}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}


def _safe_principal(value: object) -> str:
    if type(value) is not str or _PRINCIPAL.fullmatch(value) is None:
        raise ActivationContractError("authority_mismatch")
    return value


def _safe_public_key(value: object) -> str:
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ActivationContractError("authority_mismatch")
    pieces = value.split()
    if len(pieces) < 2:
        raise ActivationContractError("authority_mismatch")
    canonical = " ".join(pieces[:2])
    if _PUBLIC_KEY.fullmatch(canonical) is None:
        raise ActivationContractError("authority_mismatch")
    try:
        raw = base64.b64decode(canonical.split(" ", 1)[1], validate=True)
    except (ValueError, UnicodeEncodeError):
        raise ActivationContractError("authority_mismatch") from None
    if not raw:
        raise ActivationContractError("authority_mismatch")
    return canonical


@dataclass(frozen=True, slots=True)
class SettlementApprovalVerifier:
    """Verify a settlement approval against installed public authority only."""

    public_key: str
    authority_id: str
    authority_revision: str
    verification_digest: str

    def __post_init__(self) -> None:
        public_key = _safe_public_key(self.public_key)
        authority_id = _safe_principal(self.authority_id)
        authority_revision = _safe_principal(self.authority_revision)
        if type(self.verification_digest) is not str or not _DIGEST.fullmatch(
                self.verification_digest):
            raise ActivationContractError("authority_mismatch")
        expected = "sha256:" + hashlib.sha256(public_key.encode("ascii")).hexdigest()
        if self.verification_digest != expected:
            raise ActivationContractError("authority_mismatch")
        # Validate the normalized values before retaining them.  The dataclass
        # is frozen, so constructor inputs cannot later become an allowed-
        # signers principal or public-key line injection.
        object.__setattr__(self, "public_key", public_key)
        object.__setattr__(self, "authority_id", authority_id)
        object.__setattr__(self, "authority_revision", authority_revision)

    def verify(self, approval: object, plan: object, *, now: int | None = None) -> bool:
        """Return true only for one exact, currently valid signed approval."""
        try:
            if type(approval) is not SettlementApproval or type(plan) is not SettlementPlan:
                return False
            checked_at = int(time.time()) if now is None else now
            if (type(checked_at) is not int
                    or approval.authority_id != self.authority_id
                    or approval.authority_revision != self.authority_revision
                    or approval.plan_digest != plan.plan_digest
                    or approval.expires_at - approval.issued_at > 3600
                    or not approval.issued_at <= checked_at < approval.expires_at):
                return False
            signature = base64.b64decode(approval.signature.encode("ascii"), validate=True)
            if not 1 <= len(signature) <= 4096:
                return False
            with tempfile.TemporaryDirectory(prefix="sandbox-settlement-verify-") as directory:
                root = Path(directory)
                allowed = root / "allowed_signers"
                proof = root / "approval.sig"
                allowed.write_text(self.authority_id + " " + self.public_key + "\n",
                                   encoding="ascii")
                proof.write_bytes(signature)
                result = subprocess.run(
                    ("/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(allowed),
                     "-I", self.authority_id, "-n", SETTLEMENT_NAMESPACE,
                     "-s", str(proof)), input=approval.signature_payload(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False, env=_ENVIRONMENT)
            return result.returncode == 0
        except (OSError, ValueError, TypeError, UnicodeError, subprocess.SubprocessError):
            return False


def verify_settlement_approval(approval: object, plan: object,
                               verifier: object, *, now: int | None = None) -> bool:
    """Fail closed when the supplied verifier or settlement values are invalid."""
    try:
        if type(verifier) is not SettlementApprovalVerifier:
            return False
        return verifier.verify(approval, plan, now=now) is True
    except (AttributeError, OSError, ValueError, TypeError, UnicodeError,
            subprocess.SubprocessError):
        return False


__all__ = (
    "SETTLEMENT_NAMESPACE", "SettlementApprovalVerifier",
    "verify_settlement_approval",
)

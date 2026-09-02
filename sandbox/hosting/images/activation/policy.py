"""Machine-owned admission policy for Feature 051."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

from .models import (
    ActivationAuthorityBinding, ActivationContractError, ActivationPolicy,
    ActivationRequest, ForwardRollbackSubject, RollbackCompatibilityGrant,
    activation_digest, validate_activation_artifacts,
)


@dataclass(frozen=True, slots=True)
class ActivationAdmission:
    ok: bool
    code: str
    plan: object | None = None
    proof: object | None = None


class SshRollbackGrantVerifier:
    """Verify machine-authorized grants with a bound public Ed25519 key."""

    def __init__(self, public_key: str, authority_id: str) -> None:
        pieces = public_key.split()
        if len(pieces) != 2 or pieces[0] != "ssh-ed25519" \
                or type(authority_id) is not str or not authority_id:
            raise ActivationContractError("rollback_grant_mismatch")
        self._public_key = public_key
        self._authority_id = authority_id
        self.verification_digest = "sha256:" + hashlib.sha256(public_key.encode()).hexdigest()

    def verify(self, grant: RollbackCompatibilityGrant) -> bool:
        message = json.dumps(grant.unsigned_mapping(), sort_keys=True,
                             separators=(",", ":")).encode()
        try:
            signature = base64.b64decode(grant.authority_proof, validate=True)
            if len(signature) > 4096:
                return False
            with tempfile.TemporaryDirectory(prefix="sandbox-rollback-verify-") as directory:
                root = Path(directory)
                allowed = root / "allowed_signers"
                proof = root / "grant.sig"
                allowed.write_text(f"{self._authority_id} {self._public_key}\n")
                proof.write_bytes(signature)
                result = subprocess.run(
                    ("/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(allowed),
                     "-I", self._authority_id, "-n", "sandbox-feature-051-rollback",
                     "-s", str(proof)), input=message, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
            return result.returncode == 0
        except (OSError, ValueError, subprocess.SubprocessError):
            return False


def admit_activation(request: object, policy: object, binding: object,
                     *, capability: str) -> ActivationAdmission:
    if type(request) is not ActivationRequest or type(policy) is not ActivationPolicy \
            or type(binding) is not ActivationAuthorityBinding:
        return ActivationAdmission(False, "artifact_invalid")
    if capability != request.operation:
        return ActivationAdmission(False, "policy_mismatch")
    try:
        plan, proof = validate_activation_artifacts(request.plan, request.proof)
    except ActivationContractError as exc:
        return ActivationAdmission(False, exc.code)
    topology = plan.delivery_identity_projection.topology
    if request.policy_digest != policy.policy_digest \
            or request.authority_binding_digest != binding.binding_digest \
            or binding.policy_digest != policy.policy_digest \
            or binding.plan_digest != plan.plan_digest \
            or binding.proof_digest != proof.proof_digest \
            or binding.stage_request_id != proof.request_id \
            or binding.stage_request_digest != proof.request_digest \
            or binding.staging_policy_digest != proof.staging_policy_digest \
            or binding.staging_generation != proof.staging_generation \
            or binding.target != proof.target.as_mapping() \
            or binding.target != policy.target \
            or binding.delivery_identity_projection != plan.delivery_identity_projection.as_mapping():
        return ActivationAdmission(False, "authority_mismatch")
    allowed = set(topology.persistent_services) | set(topology.one_shot_services)
    if not set(policy.selected_services) <= allowed:
        return ActivationAdmission(False, "topology_mismatch")
    declared_init = tuple(item["service"] for item in policy.init_declarations)
    if request.operation != "adopt" \
            and set(declared_init) != set(topology.one_shot_services):
        return ActivationAdmission(False, "policy_mismatch")
    if request.operation == "adopt" and policy.init_declarations:
        return ActivationAdmission(False, "adoption_requires_zero_init")
    return ActivationAdmission(True, "accepted", plan, proof)


def create_forward_rollback_subject(*, target: dict[str, str],
                                    rollback_target_generation_digest: str,
                                    candidate_plan_digest: str,
                                    candidate_proof_digest: str,
                                    activation_authority_digest: str,
                                    configuration_digest: str,
                                    topology_digest: str,
                                    init_data_contract_digest: str,
                                    policy_revision: str) -> ForwardRollbackSubject:
    body = {"target": target,
            "rollback_target_generation_digest": rollback_target_generation_digest,
            "candidate_plan_digest": candidate_plan_digest,
            "candidate_proof_digest": candidate_proof_digest,
            "activation_authority_digest": activation_authority_digest,
            "configuration_digest": configuration_digest,
            "topology_digest": topology_digest,
            "init_data_contract_digest": init_data_contract_digest,
            "policy_revision": policy_revision}
    return ForwardRollbackSubject(**body, subject_digest=activation_digest(
        "sandbox.hosting.images.forward-rollback-subject.v1", body))


def validate_rollback_grant(grant: object, subject: object, *, accepted_at: int,
                            policy_revision: str, authority_binding: ActivationAuthorityBinding,
                            verifier: object,
                            now: int | None = None) -> None:
    if type(grant) is not RollbackCompatibilityGrant \
            or type(subject) is not ForwardRollbackSubject \
            or grant.subject.as_mapping() != subject.as_mapping() \
            or grant.subject.subject_digest != subject.subject_digest \
            or grant.policy_revision != policy_revision \
            or grant.authority_id != authority_binding.rollback_grant_authority_id \
            or grant.authority_revision != authority_binding.rollback_grant_authority_revision \
            or getattr(verifier, "verification_digest", None) != authority_binding.rollback_grant_verification_digest \
            or not callable(getattr(verifier, "verify", None)) \
            or verifier.verify(grant) is not True \
            or grant.issued_at >= accepted_at or grant.revoked:
        raise ActivationContractError("rollback_grant_mismatch")
    checked_at = int(time.time()) if now is None else now
    if grant.expires_at is not None and checked_at >= grant.expires_at:
        raise ActivationContractError("rollback_grant_mismatch")

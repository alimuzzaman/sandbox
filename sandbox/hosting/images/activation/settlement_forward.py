"""Separate reviewed authority for one new activation after settlement."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..staging_models import StagingTarget
from .models import ActivationContractError, _closed, _digest, _integer, _text, activation_digest, canonical_bytes
from .settlement_models import SettlementDataAssessment, _validate_signature


@dataclass(frozen=True, slots=True)
class ForwardSettlementApproval:
    schema_version: int
    authority_id: str
    authority_revision: str
    operation: str
    target: StagingTarget
    request_id: str
    expected_generation: int
    predecessor_digest: str
    transaction_digest: str
    application_revision: str
    plan_set_digest: str
    proof_set_digest: str
    compose_snapshot_digest: str
    policy_digest: str
    rollback_grant_digest: str
    data_assessment: SettlementDataAssessment
    issued_at: int
    expires_at: int
    signature: str
    approval_digest: str

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or self.operation != "activate" or type(self.target) is not StagingTarget
                or type(self.data_assessment) is not SettlementDataAssessment):
            raise ActivationContractError("authority_mismatch")
        for value in (self.authority_id, self.authority_revision, self.request_id):
            _text(value, identity=True)
        for value in (self.predecessor_digest, self.transaction_digest, self.plan_set_digest,
                      self.proof_set_digest, self.compose_snapshot_digest, self.policy_digest,
                      self.rollback_grant_digest, self.approval_digest):
            _digest(value)
        _integer(self.expected_generation); _integer(self.issued_at); _integer(self.expires_at)
        if (not 0 < self.expires_at - self.issued_at <= 3600
                or type(self.application_revision) is not str
                or re.fullmatch(r"[0-9a-f]{40}", self.application_revision) is None
                or self.data_assessment.target != self.target
                or self.data_assessment.transaction_digest != self.transaction_digest
                or self.data_assessment.application_revision != self.application_revision):
            raise ActivationContractError("authority_mismatch")
        _validate_signature(self.signature)
        if self.approval_digest != activation_digest(
                "sandbox.hosting.images.settlement-forward-approval.v1", self.body_mapping()):
            raise ActivationContractError("authority_mismatch")

    def unsigned_mapping(self):
        return {name: value.as_mapping() if name in {"target", "data_assessment"} else value
            for name, value in ((name, getattr(self, name)) for name in self.__dataclass_fields__)
            if name not in {"signature", "approval_digest"}}

    def signature_payload(self) -> bytes:
        return canonical_bytes(self.unsigned_mapping())

    def body_mapping(self):
        return {**self.unsigned_mapping(), "signature": self.signature}

    def as_mapping(self):
        return {**self.body_mapping(), "approval_digest": self.approval_digest}

    @classmethod
    def create(cls, **values):
        _closed(values, frozenset(cls.__dataclass_fields__) - {"schema_version", "approval_digest"})
        body = {"schema_version": 1, **values}
        for name in ("target", "data_assessment"):
            if hasattr(body[name], "as_mapping"):
                body[name] = body[name].as_mapping()
        return cls.from_mapping({**body, "approval_digest": activation_digest(
            "sandbox.hosting.images.settlement-forward-approval.v1", body)})

    @classmethod
    def from_mapping(cls, value):
        raw = _closed(value, frozenset(cls.__dataclass_fields__))
        return cls(**{**raw, "target": StagingTarget.from_mapping(raw["target"]),
                      "data_assessment": SettlementDataAssessment.from_mapping(raw["data_assessment"])})

    def validate_request(self, request) -> None:
        if (request.operation != "activate" or request.request_id != self.request_id
                or request.expected_generation != self.expected_generation
                or request.policy_digest != self.policy_digest
                or request.plan_set.plan_set_digest != self.plan_set_digest
                or request.plan_set.receipt.source_sha != self.application_revision
                or request.proof_set["proof_digest"] != self.proof_set_digest
                or request.compose_snapshot.snapshot_digest != self.compose_snapshot_digest
                or request.rollback_grant_digest != self.rollback_grant_digest
                or request.proof_set["target"] != self.target.as_mapping()
                or request.compose_snapshot.init_contract is None
                or request.compose_snapshot.init_contract.graph is None):
            raise ActivationContractError("authority_mismatch")


def required_predecessor(state):
    if "settlements" not in state:
        return None
    from .settlement_repository import latest_settlement
    head = latest_settlement(state)
    if head is not None and state["generation"] == head["generation"]:
        return head
    return None


def validate_retained_forward(value, *, target, snapshot, grant,
                              expected_generation, request_id=None, operation=None):
    """Cross-bind retained authority without reauthorizing an expired decision."""
    approval = ForwardSettlementApproval.from_mapping(value)
    if (approval.target.as_mapping() != target
            or approval.expected_generation != expected_generation
            or approval.plan_set_digest != snapshot.plan_set_digest
            or approval.compose_snapshot_digest != snapshot.snapshot_digest
            or approval.proof_set_digest != grant.candidate_proof_set_digest
            or approval.policy_digest != grant.policy_digest
            or approval.rollback_grant_digest != grant.grant_digest
            or snapshot.init_contract is None or snapshot.init_contract.graph is None
            or (request_id is not None and approval.request_id != request_id)
            or (operation is not None and operation != "activate")):
        raise ActivationContractError("authority_mismatch")
    return approval.as_mapping()


def validate_forward_binding(state, request):
    """Require a distinct exact successor; cryptography belongs to admission."""
    predecessor = required_predecessor(state)
    raw = getattr(request, "settlement_forward", None)
    if predecessor is None:
        if raw is not None:
            raise ActivationContractError("authority_mismatch")
        return None
    if raw is None:
        raise ActivationContractError("authority_mismatch")
    approval = ForwardSettlementApproval.from_mapping(raw)
    approval.validate_request(request)
    if request.request_digest != activation_digest(
            "sandbox.hosting.images.activation-request.v2", request.body_mapping()):
        raise ActivationContractError("request_conflict")
    if (approval.predecessor_digest != predecessor["terminal_receipt"]["terminal_digest"]
            or approval.transaction_digest != predecessor["transaction_digest"]
            or approval.target.as_mapping() != predecessor["plan"]["target"]
            or request.request_id == predecessor["active_request_id"]):
        raise ActivationContractError("authority_mismatch")
    return approval

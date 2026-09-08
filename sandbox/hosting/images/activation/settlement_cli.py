"""Closed CLI phases for reviewable settlement and successor authority."""

import json
import os
from pathlib import Path
import stat
import time

from .models import _digest, _integer, _text
from .settlement_forward import ForwardSettlementApproval, required_predecessor
from .settlement_models import SettlementApproval, SettlementDataAssessment, SettlementPlan
from .settlement_service import SettlementError, SettlementService, _code


def read_document(path, *, text=False):
    if type(path) is not str or not path:
        raise SettlementError("artifact_invalid")
    descriptor = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
            raise SettlementError("path_unsafe")
        data = bytearray()
        while len(data) <= 1024 * 1024:
            block = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")
        if len(data) > 1024 * 1024 or any(getattr(before, key) != getattr(after, key) for key in fields):
            raise SettlementError("path_unsafe")
    finally:
        os.close(descriptor)
    if text:
        return data.decode("ascii").rstrip("\n")
    from ..plan_set import _load_json_bytes
    return _load_json_bytes(bytes(data))


def _plan(args, target):
    plan = SettlementPlan.from_mapping(read_document(getattr(args, "settlement_plan", None)))
    if (plan.target.target_identity != target or plan.request_id != args.request_id
            or plan.generation != args.expected_generation
            or (getattr(args, "activation_transaction", None) is not None
                and plan.transaction_digest != args.activation_transaction)):
        raise SettlementError("request_conflict")
    return plan


def run_settlement(args, *, target, repository, approval_store, observer, clock=None, signer=None):
    now = clock or time.time
    phase = getattr(args, "settlement_phase", None)
    try:
        _text(args.request_id, identity=True); _integer(args.expected_generation)
        if phase not in {"observe", "plan", "containment-plan", "containment-apply", "sign-approval", "sign-forward-approval", "install-approval", "install-forward-approval", "apply"}:
            raise SettlementError("artifact_invalid")
        if phase not in {"observe", "plan", "containment-plan"} and getattr(args, "confirm", False) is not True:
            raise SettlementError("authority_missing")
        if phase in {"containment-plan", "containment-apply"}:
            from .settlement_containment import containment
            return containment(args, target=target, repository=repository, observer=observer, store=approval_store)
        service = SettlementService(repository=repository, observer=observer,
            approval_store=approval_store, clock=now)
        if phase == "observe":
            _digest(getattr(args, "activation_transaction", None))
            with repository.operation_transaction(target):
                state = repository.snapshot(target)
                active = service._active(state, target=target,
                    transaction_digest=args.activation_transaction, expected_generation=args.expected_generation)
                observation = service._observe(active, args.expected_generation)
                return {"schema_version": 1, "ok": True, "code": "observed",
                        "observation": observation.as_mapping()}
        if phase == "plan":
            _digest(getattr(args, "activation_transaction", None))
            assessment = SettlementDataAssessment.from_mapping(read_document(
                getattr(args, "settlement_data_assessment", None)))
            plan = service.plan(target=target, request_id=args.request_id,
                transaction_digest=args.activation_transaction,
                expected_generation=args.expected_generation, data_assessment=assessment)
            return {"schema_version": 1, "ok": True, "code": "planned", "plan": plan.as_mapping()}
        if phase == "sign-forward-approval":
            if signer is None: raise SettlementError("authority_missing")
            review = read_document(getattr(args, "forward_review", None))
            subject = review.get("subject") if type(review) is dict else None
            assessment = SettlementDataAssessment.from_mapping(read_document(
                getattr(args, "settlement_data_assessment", None)))
            with repository.operation_transaction(target):
                state = repository.snapshot(target); head = required_predecessor(state)
                if (type(subject) is not dict or head is None or state["active"] is not None
                        or subject.get("target") != head["plan"]["target"]
                        or subject.get("request_id") != args.request_id
                        or subject.get("expected_generation") != args.expected_generation
                        or state["generation"] != args.expected_generation
                        or subject.get("predecessor_digest") != head["terminal_receipt"]["terminal_digest"]
                        or subject.get("transaction_digest") != head["transaction_digest"]):
                    raise SettlementError("authority_mismatch")
                instant = int(now())
                approval = signer.forward(subject, assessment, issued_at=instant, expires_at=instant + 3600)
                return {**approval_store.install_forward(approval, signer.public_key, now=int(now())),
                        "approval": approval.as_mapping()}
        if phase == "install-forward-approval":
            approval = ForwardSettlementApproval.from_mapping(read_document(getattr(args, "approval_file", None)))
            public = read_document(getattr(args, "approval_public_key", None), text=True)
            with repository.operation_transaction(target):
                state = repository.snapshot(target)
                head = required_predecessor(state)
                if (head is None or state["active"] is not None
                        or approval.target.target_identity != target
                        or approval.target.as_mapping() != head["plan"]["target"]
                        or approval.request_id != args.request_id
                        or approval.expected_generation != args.expected_generation
                        or state["generation"] != approval.expected_generation
                        or approval.predecessor_digest != head["terminal_receipt"]["terminal_digest"]
                        or approval.transaction_digest != head["transaction_digest"]
                        or any(approval.request_id in state.get(name, {}) for name in (
                            "results", "tombstones", "recovery_results", "settlements"))):
                    raise SettlementError("authority_mismatch")
                return approval_store.install_forward(approval, public, now=int(now()))
        plan = _plan(args, target)
        if phase in {"install-approval", "sign-approval"}:
            approval = None if phase == "sign-approval" else SettlementApproval.from_mapping(read_document(getattr(args, "approval_file", None)))
            public = None if phase == "sign-approval" else read_document(getattr(args, "approval_public_key", None), text=True)
            with repository.operation_transaction(target):
                state = repository.snapshot(target)
                active = service._active(state, target=target, transaction_digest=plan.transaction_digest,
                    expected_generation=plan.generation)
                if (active["request_id"] != plan.active_request_id
                        or active["request_digest"] != plan.active_request_digest
                        or active["recovery_context"]["target"] != plan.target.as_mapping()
                        or (state.get("current") or {}).get("generation_digest") != plan.current_generation_digest):
                    raise SettlementError("settlement_conflict")
                if phase == "sign-approval":
                    if signer is None: raise SettlementError("authority_missing")
                    instant = int(now())
                    approval = signer.settlement(plan, issued_at=instant, expires_at=instant + 3600)
                    public = signer.public_key
                return {**approval_store.install_settlement(plan, approval, public, now=int(now())),
                        "approval": approval.as_mapping()}
        return service.apply(plan, approval_digest=getattr(args, "settlement_approval", None))
    except Exception as exc:
        return {"schema_version": 1, "ok": False, "code": _code(exc, "artifact_invalid"),
                "operation": "settle", "phase": phase}

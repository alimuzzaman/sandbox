"""Nested activation codec and candidate coordinator.

This module never opens, parses, locks, writes, replaces, or fsyncs hosts.json.
The shared recovery repository supplies the only outer-state transaction port.
"""

from __future__ import annotations

from dataclasses import replace
import json
import re
from typing import Any
from sandbox.hosting.recovery.models import ActivationRecoveryObservation

from .models import (
    MAX_ACTIVATION_BYTES, MAX_RECOVERY_RESULTS, MAX_RESULTS, MAX_TOMBSTONES, ActivationContractError,
    ActivationRecoveryProvisional, ActivationRequest, ActivationResult, SECRET_FIELDS,
    ActivationTransaction, RESULT_CLASSES, RESULT_CODES, TERMINAL_PHASES,
    VerifiedActivationGeneration,
    _safe_mapping, activation_digest, canonical_bytes,
    validate_transition,
)


class ActivationRepositoryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


RECOVERY_CLASSES = frozenset({"exact_new", "exact_prior", "neither", "ambiguous"})
_LEASE_ID = re.compile(r"activation-lease/[0-9a-f]{48}\Z")
_HOLDER_ID = re.compile(r"activation-owner/[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_ACCEPTANCE_ID = re.compile(r"host-acceptance/[0-9a-f]{64}\Z")


def validate_retained_proof_pin(value: object) -> dict[str, Any]:
    pin = _safe_mapping(value, forbidden=SECRET_FIELDS)
    if set(pin) != {"lease_id", "holder", "phase", "proof_digest",
                    "host_acceptance_receipt"} or pin.get("phase") != "accepted" \
            or type(pin.get("lease_id")) is not str or _LEASE_ID.fullmatch(pin["lease_id"]) is None \
            or type(pin.get("holder")) is not str or _HOLDER_ID.fullmatch(pin["holder"]) is None \
            or type(pin.get("proof_digest")) is not str \
            or re.fullmatch(r"sha256:[0-9a-f]{64}", pin["proof_digest"]) is None \
            or type(pin.get("host_acceptance_receipt")) is not str \
            or _ACCEPTANCE_ID.fullmatch(pin["host_acceptance_receipt"]) is None:
        raise ActivationContractError()
    return pin


def empty_activation_state() -> dict[str, Any]:
    return {"schema_version": 1, "generation": 0, "current": None, "previous": None,
            "active": None, "results": {}, "tombstones": {},
            "recovery_provisional": None, "recovery_results": {},
            "reserved_terminal_bytes": 0}


def decode_activation_state(value: object | None) -> dict[str, Any]:
    if value is None:
        return empty_activation_state()
    required = {"schema_version", "generation", "current", "previous", "active", "results",
                "tombstones", "recovery_provisional", "recovery_results",
                "reserved_terminal_bytes"}
    if type(value) is not dict or set(value) != required or value["schema_version"] != 1 \
            or type(value["generation"]) is not int or value["generation"] < 0 \
            or any(type(value[name]) is not dict for name in ("results", "tombstones", "recovery_results")) \
            or len(value["results"]) > MAX_RESULTS or len(value["tombstones"]) > MAX_TOMBSTONES \
            or len(value["recovery_results"]) > MAX_RECOVERY_RESULTS \
            or type(value["reserved_terminal_bytes"]) is not int \
            or not 0 <= value["reserved_terminal_bytes"] <= 16384:
        raise ActivationRepositoryError("persistence_uncertain")
    safe = json.loads(canonical_bytes(value))
    for name in ("current", "previous"):
        if safe[name] is not None:
            try: VerifiedActivationGeneration.from_mapping(safe[name])
            except (TypeError, ActivationContractError):
                raise ActivationRepositoryError("persistence_uncertain") from None
    if safe["active"] is not None:
        try:
            ActivationTransaction(**{
                **safe["active"], "init_receipts": tuple(safe["active"]["init_receipts"]),
                "init_steps": tuple(safe["active"]["init_steps"])
            })
        except (TypeError, KeyError, ActivationContractError):
            raise ActivationRepositoryError("persistence_uncertain") from None
    provisional = safe["recovery_provisional"]
    if provisional is not None:
        try: ActivationRecoveryProvisional(**provisional)
        except (TypeError, ActivationContractError):
            raise ActivationRepositoryError("persistence_uncertain") from None
    try:
        for request_id, result in safe["results"].items():
            if type(result) is not dict or set(result) != {
                    "result", "holder", "proof_digest", "proof_pin"}:
                raise ActivationContractError()
            terminal = ActivationResult.from_mapping(result["result"])
            if terminal.request_id != request_id or result["holder"] != result["proof_pin"].get("holder") \
                    or result["proof_digest"] != result["proof_pin"].get("proof_digest"):
                raise ActivationContractError()
            validate_retained_proof_pin(result["proof_pin"])
            if type(result["holder"]) is not str or not result["holder"].startswith("activation-owner/") \
                    or type(result["proof_digest"]) is not str \
                    or len(result["proof_digest"]) != 71:
                raise ActivationContractError()
        for request_id, tombstone in safe["tombstones"].items():
            if type(tombstone) is not dict or set(tombstone) != {
                    "request_id", "request_digest", "result_class", "code"} \
                    or tombstone["request_id"] != request_id:
                raise ActivationContractError()
            if tombstone["result_class"] not in RESULT_CLASSES \
                    or tombstone["code"] not in RESULT_CODES:
                raise ActivationContractError()
            if not isinstance(tombstone["request_digest"], str) \
                    or not tombstone["request_digest"].startswith("sha256:"):
                raise ActivationContractError()
        for request_id, result in safe["recovery_results"].items():
            if type(result) is not dict or set(result) != {"schema_version", "ok", "request_id",
                    "activation_request_id",
                    "request_digest", "code", "promoted", "starting_generation",
                    "resulting_generation"} or result["request_id"] != request_id \
                    or result["schema_version"] != 1 or type(result["ok"]) is not bool \
                    or type(result["activation_request_id"]) is not str \
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}",
                                    result["activation_request_id"]) is None \
                    or not isinstance(result["request_digest"], str) \
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", result["request_digest"]) is None \
                    or type(result["promoted"]) is not bool \
                    or result["code"] not in {"committed", "recovery_ineligible",
                        "recovery_no_effect", "recovery_conflict", "evidence_changed",
                        "observation_unavailable", "effect_unknown"}:
                raise ActivationContractError()
            if type(result["starting_generation"]) is not int \
                    or type(result["resulting_generation"]) is not int \
                    or result["starting_generation"] < 0 or result["resulting_generation"] < 0 \
                    or result["resulting_generation"] > safe["generation"]:
                raise ActivationContractError()
            successful = result["code"] == "committed"
            if result["ok"] is not successful \
                    or result["promoted"] is not (result["code"] == "committed") \
                    or (result["promoted"] and result["resulting_generation"] !=
                        result["starting_generation"] + 1) \
                    or (not result["promoted"] and result["resulting_generation"] !=
                        result["starting_generation"]):
                raise ActivationContractError()
    except (AttributeError, TypeError, ActivationContractError):
        raise ActivationRepositoryError("persistence_uncertain") from None
    if safe["current"] is not None and safe["current"]["generation"] != safe["generation"]:
        raise ActivationRepositoryError("persistence_uncertain")
    if safe["previous"] is not None and (safe["current"] is None or
            safe["previous"]["generation"] != safe["current"]["generation"] - 1):
        raise ActivationRepositoryError("persistence_uncertain")
    if safe["active"] is not None and safe["active"]["starting_generation"] != safe["generation"]:
        raise ActivationRepositoryError("persistence_uncertain")
    identities = [set(safe["results"]), set(safe["tombstones"]), set(safe["recovery_results"])]
    if any(left & right for index, left in enumerate(identities)
           for right in identities[index + 1:]):
        raise ActivationRepositoryError("persistence_uncertain")
    if provisional is not None and any(provisional["request_id"] in group for group in identities):
        raise ActivationRepositoryError("persistence_uncertain")
    active_request = safe["active"].get("request_id") if safe["active"] is not None else None
    if active_request is not None:
        active_terminal = safe["results"].get(active_request)
        uncertain_pair = (isinstance(active_terminal, dict)
            and isinstance(active_terminal.get("result"), dict)
            and safe["active"].get("phase") == "uncertain"
            and active_terminal["result"].get("result_class") == "uncertain"
            and safe["active"].get("result") == active_terminal["result"]
            and active_terminal["result"].get("request_digest") == safe["active"].get("request_digest")
            and active_terminal["result"].get("transaction_digest") == safe["active"].get("transaction_digest")
            and active_terminal.get("proof_pin") == safe["active"].get("proof_pin"))
        if active_request in safe["tombstones"] or active_request in safe["recovery_results"] \
                or (active_request in safe["results"] and not uncertain_pair) \
                or (provisional is not None and provisional["request_id"] == active_request):
            raise ActivationRepositoryError("persistence_uncertain")
    recovery_subjects = [item["activation_request_id"]
                         for item in safe["recovery_results"].values()]
    terminal_or_active = set(safe["results"]) | set(safe["tombstones"])
    if active_request is not None: terminal_or_active.add(active_request)
    if len(recovery_subjects) != len(set(recovery_subjects)) \
            or any(subject not in terminal_or_active for subject in recovery_subjects):
        raise ActivationRepositoryError("persistence_uncertain")
    return safe


def encode_activation_state(value: object) -> dict[str, Any]:
    safe = decode_activation_state(value)
    canonical_bytes(safe)
    return safe


def ensure_recovery_capacity(state: object) -> dict[str, Any]:
    """Reserve the sole provisional's result slot before either live observation."""
    safe = decode_activation_state(state)
    if len(safe["recovery_results"]) >= MAX_RECOVERY_RESULTS:
        raise ActivationRepositoryError("retention_full")
    if len(safe["results"]) >= MAX_RESULTS and len(safe["tombstones"]) >= MAX_TOMBSTONES:
        raise ActivationRepositoryError("retention_full")
    return safe


def _transaction(request: ActivationRequest, *, holder: str, authority_binding_digest: str,
                 rollback_subject_digest: str, rollback_grant_digest: str,
                 proof_pin: dict[str, Any], edge_required: bool) -> dict[str, Any]:
    body = {"schema_version": 1, "request_id": request.request_id,
            "request_digest": request.request_digest, "operation": request.operation,
            "holder": holder, "starting_generation": request.expected_generation,
            "phase": "accepted", "effect_entered": False,
            "authority_binding_digest": authority_binding_digest, "proof_pin": proof_pin,
            "rollback_subject_digest": rollback_subject_digest,
            "rollback_grant_digest": rollback_grant_digest, "init_receipts": [],
            "init_steps": [], "edge_required": edge_required,
            "running_observation": None, "edge_result": None,
            "candidate_generation": None, "result": None}
    body["transaction_digest"] = activation_digest(
        "sandbox.hosting.images.activation-transaction.v1", body)
    return body


def accept_candidate(state: object, request: ActivationRequest, *, holder: str,
                     authority_binding_digest: str, rollback_subject_digest: str,
                     rollback_grant_digest: str, proof_pin: dict[str, Any],
                     edge_required: bool) -> tuple[str, dict[str, Any], dict | None]:
    current = decode_activation_state(state)
    stored = current["results"].get(request.request_id)
    if stored is not None:
        result = stored.get("result") if isinstance(stored, dict) else None
        if not isinstance(result, dict) or result.get("request_digest") != request.request_digest:
            return "conflict", current, None
        return "replay", current, result
    if request.request_id in current["tombstones"]:
        return "conflict", current, None
    if current["active"] is not None:
        active = current["active"]
        if active.get("request_id") == request.request_id and active.get("request_digest") == request.request_digest:
            return "replay", current, active.get("result")
        return "busy", current, None
    if current["generation"] != request.expected_generation:
        return "generation_conflict", current, None
    if len(current["results"]) >= MAX_RESULTS and len(current["tombstones"]) >= MAX_TOMBSTONES:
        return "retention_full", current, None
    candidate = json.loads(canonical_bytes(current))
    candidate["active"] = _transaction(
        request, holder=holder, authority_binding_digest=authority_binding_digest,
        rollback_subject_digest=rollback_subject_digest,
        rollback_grant_digest=rollback_grant_digest, proof_pin=proof_pin,
        edge_required=edge_required)
    candidate["reserved_terminal_bytes"] = 16384
    return "accepted", encode_activation_state(candidate), None


def transition_candidate(state: object, request: ActivationRequest, phase: str, *,
                         effect_entered: bool | None = None, init_receipt: dict | None = None,
                         init_step: dict | None = None,
                         running_observation: dict | None = None,
                         edge_result: dict | None = None,
                         candidate_generation: dict | None = None) -> dict[str, Any]:
    candidate = decode_activation_state(state)
    active = candidate["active"]
    if type(active) is not dict or active.get("request_id") != request.request_id \
            or active.get("request_digest") != request.request_digest:
        raise ActivationRepositoryError("request_conflict")
    validate_transition(active["phase"], phase, effect_entered=active["effect_entered"],
                        terminal_receipt=(init_receipt is not None or init_step is not None or running_observation is not None
                                          or edge_result is not None))
    if effect_entered is not None:
        if active["effect_entered"] and effect_entered is False:
            raise ActivationRepositoryError("request_conflict")
        active["effect_entered"] = effect_entered
    if init_receipt is not None:
        active["init_receipts"].append(json.loads(canonical_bytes(init_receipt)))
    if init_step is not None:
        step = json.loads(canonical_bytes(init_step))
        index = step.get("index")
        if type(index) is not int or not 0 <= index < 16:
            raise ActivationRepositoryError("request_conflict")
        if index == len(active["init_steps"]):
            active["init_steps"].append(step)
        elif index < len(active["init_steps"]):
            active["init_steps"][index] = step
        else:
            raise ActivationRepositoryError("request_conflict")
    if running_observation is not None:
        active["running_observation"] = json.loads(canonical_bytes(running_observation))
    if edge_result is not None:
        active["edge_result"] = json.loads(canonical_bytes(edge_result))
    if candidate_generation is not None:
        active["candidate_generation"] = json.loads(canonical_bytes(candidate_generation))
    active["phase"] = phase
    return encode_activation_state(candidate)


def commit_candidate(state: object, request: ActivationRequest, result: ActivationResult,
                     generation: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = decode_activation_state(state)
    active = candidate["active"]
    if type(active) is not dict or active.get("request_id") != request.request_id \
            or active.get("request_digest") != request.request_digest:
        raise ActivationRepositoryError("request_conflict")
    if result.ok:
        if active["phase"] not in {"runtime_proven", "edge_pending"} or generation is None:
            raise ActivationRepositoryError("effect_unknown")
        candidate["previous"] = candidate["current"]
        candidate["current"] = json.loads(canonical_bytes(generation))
        candidate["generation"] = result.resulting_generation
        active["phase"] = "committed"
    else:
        active["phase"] = result.result_class
    active["result"] = result.as_mapping()
    candidate["results"][request.request_id] = {
        "result": result.as_mapping(), "holder": active["holder"],
        "proof_digest": active["proof_pin"]["proof_digest"],
        "proof_pin": json.loads(canonical_bytes(active["proof_pin"]))}
    if result.result_class != "uncertain":
        candidate["reserved_terminal_bytes"] = 0
    if result.result_class != "uncertain":
        candidate["active"] = None
    while len(candidate["results"]) > MAX_RESULTS:
        request_id = next(iter(candidate["results"]))
        terminal = candidate["results"].pop(request_id)["result"]
        if len(candidate["tombstones"]) >= MAX_TOMBSTONES:
            raise ActivationRepositoryError("retention_full")
        candidate["tombstones"][request_id] = {
            "request_id": request_id, "request_digest": terminal["request_digest"],
            "result_class": terminal["result_class"], "code": terminal["code"]}
    return encode_activation_state(candidate)


def recovery_decision(transaction: dict[str, Any], classification: str) -> tuple[str, bool, bool]:
    """Return result code, promote, and close-active for the exhaustive contract matrix."""
    if classification not in RECOVERY_CLASSES:
        raise ActivationRepositoryError("recovery_conflict")
    operation = transaction.get("operation")
    phase = transaction.get("phase")
    entered = transaction.get("effect_entered") is True
    if operation not in {"activate", "rollback"}:
        return "recovery_ineligible", False, False
    if phase in {"accepted", "preflight", "init_pending"} and not entered:
        if classification == "exact_prior": return "recovery_no_effect", False, True
        return "recovery_conflict", False, False
    if (phase == "init_pending" and entered) or phase == "runtime_pending":
        return "effect_unknown", False, False
    if phase == "runtime_proven":
        complete_init = all(item.get("termination_complete") is True and item.get("cleanup_complete") is True
                            for item in transaction.get("init_receipts", []))
        edge = transaction.get("edge_result")
        edge_complete = (not transaction.get("edge_required") or
                         (isinstance(edge, dict) and edge.get("terminal") is True
                          and isinstance(edge.get("receipt_digest"), str)))
        if classification == "exact_new" and complete_init and edge_complete:
            return "committed", True, True
        return "recovery_conflict", False, False
    if phase == "edge_pending":
        edge = transaction.get("edge_result") or {}
        runtime = transaction.get("running_observation") or {}
        if classification == "exact_new" and edge.get("terminal") is True \
                and isinstance(edge.get("receipt_digest"), str) \
                and runtime.get("observation_digest"):
            return "committed", True, True
        return "recovery_conflict", False, False
    if phase == "committed":
        return ("committed" if classification == "exact_new" else "recovery_conflict"), False, False
    if phase in TERMINAL_PHASES:
        return "recovery_conflict", False, False
    return "recovery_conflict", False, False


class ActivationRepository:
    """Coordinate nested candidates through the two owner repositories."""

    def __init__(self, *, host_state_port, stage_repository, target_mutation_port) -> None:
        self.host_state = host_state_port
        self.stage_repository = stage_repository
        self.target_mutation = target_mutation_port

    def operation_transaction(self, target: str):
        """Hold the shared target owner across admission, effects, and result."""
        return self.target_mutation.target_mutation_transaction(target)

    def lookup_terminal(self, target: str, *, request_id: str, request_digest: str) -> dict | None:
        terminal = None
        with self.target_mutation.target_mutation_transaction(target):
            with self.host_state.atomic_host_state_transaction(target):
                state = decode_activation_state(self.host_state.read_activation_nested(target))
                stored = state["results"].get(request_id)
                if stored is None:
                    if request_id in state["tombstones"]:
                        raise ActivationRepositoryError("request_conflict")
                    return None
                    return None
                result = stored.get("result") if isinstance(stored, dict) else None
                if not isinstance(result, dict) or result.get("request_digest") != request_digest:
                    raise ActivationRepositoryError("request_conflict")
                terminal = stored
        if terminal["result"].get("result_class") != "uncertain":
            self.release_recovered_terminal_pin(target, terminal)
        return json.loads(canonical_bytes(terminal["result"]))

    def accept(self, request: ActivationRequest, *, authority_binding_digest: str,
               rollback_subject_digest: str, rollback_grant_digest: str,
               admission_deadline: str, edge_required: bool = True) -> tuple[str, dict | None, object | None]:
        holder = f"activation-owner/{request.request_id}"
        target = request.proof.target.target_identity
        lease_id = "activation-lease/" + request.request_digest.split(":", 1)[1][:48]
        with self.stage_repository.proof_custody_transaction(
                target, target_mutation_port=self.target_mutation,
                host_state_port=self.host_state) as custody:
            preexisting = decode_activation_state(
                self.host_state.read_activation_nested(target))
            same_active = isinstance(preexisting.get("active"), dict) \
                and preexisting["active"].get("request_id") == request.request_id \
                and preexisting["active"].get("request_digest") == request.request_digest
            same_result = isinstance(preexisting["results"].get(request.request_id), dict) \
                and isinstance(preexisting["results"][request.request_id].get("result"), dict) \
                and preexisting["results"][request.request_id]["result"].get(
                    "request_digest") == request.request_digest
            if not same_active and not same_result \
                    and len(preexisting["results"]) >= MAX_RESULTS \
                    and len(preexisting["tombstones"]) >= MAX_TOMBSTONES:
                return "retention_full", None, None
            pre_receipt = self.host_state.activation_acceptance_receipt(
                target, holder=holder, request_id=request.request_id,
                request_digest=request.request_digest, proof_digest=request.proof.proof_digest)
            pre_pin = {"lease_id": lease_id, "holder": holder, "phase": "accepted",
                       "proof_digest": request.proof.proof_digest,
                       "host_acceptance_receipt": pre_receipt}
            pre_status, reserved_candidate, _ = accept_candidate(
                preexisting, request, holder=holder,
                authority_binding_digest=authority_binding_digest,
                rollback_subject_digest=rollback_subject_digest,
                rollback_grant_digest=rollback_grant_digest, proof_pin=pre_pin,
                edge_required=edge_required)
            if pre_status == "accepted":
                try: canonical_bytes(reserved_candidate, maximum=MAX_ACTIVATION_BYTES - 16384)
                except ActivationContractError:
                    return "retention_full", None, None
            prior_evidence = self.host_state.lookup_activation_acceptance(
                target, holder=holder, request_id=request.request_id,
                request_digest=request.request_digest, proof_digest=request.proof.proof_digest)
            existing_lease = custody.lookup(lease_id)
            lease = existing_lease or custody.prepare(
                lease_id=lease_id, holder=holder, admission_deadline=admission_deadline,
                activation_request_id=request.request_id,
                activation_request_digest=request.request_digest,
                stage_request_id=request.proof.request_id,
                stage_request_digest=request.proof.request_digest,
                proof_digest=request.proof.proof_digest,
                stage_generation=request.proof.staging_generation)
            if existing_lease is not None and (
                    existing_lease.holder != holder or
                    existing_lease.activation_request_digest != request.request_digest or
                    existing_lease.proof_digest != request.proof.proof_digest):
                return "conflict", None, None
            if prior_evidence.state == "absent" and lease.phase == "prepared" and lease.expired:
                custody.cancel(lease, prior_evidence)
                return "lease_expired", None, None
            if prior_evidence.state == "ambiguous":
                return "conflict", None, None
            acceptance_receipt = self.host_state.activation_acceptance_receipt(
                target, holder=holder, request_id=request.request_id,
                request_digest=request.request_digest, proof_digest=request.proof.proof_digest)
            proof_pin = {"lease_id": lease.lease_id, "holder": holder, "phase": "accepted",
                         "proof_digest": lease.proof_digest,
                         "host_acceptance_receipt": acceptance_receipt}
            current = self.host_state.read_activation_nested(target)
            status, candidate, replay = accept_candidate(
                current, request, holder=holder,
                authority_binding_digest=authority_binding_digest,
                rollback_subject_digest=rollback_subject_digest,
                rollback_grant_digest=rollback_grant_digest, proof_pin=proof_pin,
                edge_required=edge_required)
            if status == "accepted":
                evidence = self.host_state.compare_and_commit_activation(
                    target, expected_generation=request.expected_generation,
                    candidate=candidate, holder=holder, request_id=request.request_id,
                    request_digest=request.request_digest, proof_digest=request.proof.proof_digest,
                    acceptance_receipt=acceptance_receipt)
            elif status == "replay":
                evidence = prior_evidence
            else:
                if lease.expired:
                    absent = self.host_state.absent_activation_evidence(
                        target, holder=holder, request_id=request.request_id,
                        request_digest=request.request_digest, proof_digest=request.proof.proof_digest)
                    custody.cancel(lease, absent)
                return status, replay, None
            accepted = custody.promote(lease, evidence)
            if status == "replay" and replay is None:
                current = decode_activation_state(self.host_state.read_activation_nested(target))
                active = current.get("active")
                status = "resume" if isinstance(active, dict) else "busy"
            return status, replay, accepted

    def release_terminal_pin(self, lease: object, *, terminal_receipt: str) -> None:
        target = lease.target_identity
        with self.stage_repository.proof_custody_transaction(
                target, target_mutation_port=self.target_mutation,
                host_state_port=self.host_state) as custody:
            evidence = self.host_state.durable_terminal_authority_evidence(
                lease, terminal_receipt=terminal_receipt)
            custody.release(lease, evidence)

    def release_recovered_terminal_pin(self, target: str, terminal: dict) -> None:
        pin = validate_retained_proof_pin(terminal["proof_pin"])
        with self.stage_repository.proof_custody_transaction(
                target, target_mutation_port=self.target_mutation,
                host_state_port=self.host_state) as custody:
            lease = custody.lookup(pin["lease_id"])
            if lease is None:
                return
            evidence = self.host_state.durable_terminal_authority_evidence(
                lease, terminal_receipt=terminal["result"]["transaction_digest"])
            custody.release(lease, evidence)

    def transition(self, target: str, request: ActivationRequest, phase: str, **values: object) -> dict:
        with self.target_mutation.target_mutation_transaction(target):
            with self.host_state.atomic_host_state_transaction(target):
                return self.host_state.update_activation_nested(
                    target, request.expected_generation,
                    lambda current: transition_candidate(current, request, phase, **values))

    def snapshot(self, target: str) -> dict:
        with self.target_mutation.target_mutation_transaction(target):
            with self.host_state.atomic_host_state_transaction(target):
                return decode_activation_state(self.host_state.read_activation_nested(target))

    def commit(self, target: str, request: ActivationRequest, result: ActivationResult,
               generation: dict | None = None) -> dict:
        with self.target_mutation.target_mutation_transaction(target):
            with self.host_state.atomic_host_state_transaction(target):
                return self.host_state.update_activation_nested(
                    target, request.expected_generation,
                    lambda current: commit_candidate(current, request, result, generation))

    def recover(self, target: str, *, request_id: str, request_digest: str,
                expected_generation: int, observer) -> dict:
        """Two read-only observations around one non-authorizing provisional."""
        release_terminal = None
        outcome = None
        with self.target_mutation.target_mutation_transaction(target):
            with self.host_state.atomic_host_state_transaction(target):
                current = decode_activation_state(self.host_state.read_activation_nested(target))
                stored = current["recovery_results"].get(request_id)
                if stored is not None:
                    if stored.get("request_digest") != request_digest:
                        raise ActivationRepositoryError("request_conflict")
                    terminal = current["results"].get(stored["activation_request_id"])
                    release_terminal = terminal if isinstance(terminal, dict) else None
                    outcome = stored
                existing = current.get("recovery_provisional")
                if outcome is not None:
                    pass
                elif existing is not None:
                    if existing.get("request_id") != request_id \
                            or existing.get("request_digest") != request_digest \
                            or existing.get("expected_generation") != expected_generation:
                        raise ActivationRepositoryError("request_conflict")
                    first = ActivationRecoveryObservation(
                        transaction_digest=existing["transaction_digest"],
                        expected_generation=existing["expected_generation"],
                        classification=existing["classification"],
                        target_epoch_start=existing["target_epoch_start"],
                        target_epoch_end=existing["target_epoch_end"],
                        runtime_epoch_start=existing["runtime_epoch_start"],
                        runtime_epoch_end=existing["runtime_epoch_end"],
                        evidence_identity=existing["evidence_identity"])
                else:
                    ensure_recovery_capacity(current)
                    first = observer()
                    provisional = ActivationRecoveryProvisional(
                        request_id=request_id, request_digest=request_digest,
                        transaction_digest=first.transaction_digest,
                        expected_generation=expected_generation, owner=f"activation-owner/{request_id}",
                        evidence_identity=first.evidence_identity, classification=first.classification,
                        target_epoch_start=first.target_epoch_start, target_epoch_end=first.target_epoch_end,
                        runtime_epoch_start=first.runtime_epoch_start, runtime_epoch_end=first.runtime_epoch_end)
                    self.host_state.store_activation_recovery_provisional(
                        target, expected_generation, provisional.as_mapping())
                if outcome is None:
                    try:
                        second = observer()
                    except Exception:
                        second = None
                    if second is None:
                        outcome = self.host_state.commit_activation_recovery_result(
                            target, expected_generation, request_id, request_digest,
                            code="observation_unavailable", promote=False, close_active=False)
                    elif first.as_mapping() != second.as_mapping():
                        outcome = self.host_state.commit_activation_recovery_result(
                            target, expected_generation, request_id, request_digest,
                            code="evidence_changed", promote=False, close_active=False)
                    else:
                        current = decode_activation_state(self.host_state.read_activation_nested(target))
                        active = current.get("active")
                        if type(active) is not dict or active.get("transaction_digest") != first.transaction_digest:
                            outcome = self.host_state.commit_activation_recovery_result(
                                target, expected_generation, request_id, request_digest,
                                code="recovery_conflict", promote=False, close_active=False)
                        else:
                            code, promote, close_active = recovery_decision(active, first.classification)
                            outcome = self.host_state.commit_activation_recovery_result(
                                target, expected_generation, request_id, request_digest,
                                code=code, promote=promote, close_active=close_active)
                            if code in {"committed", "recovery_no_effect"}:
                                refreshed = decode_activation_state(
                                    self.host_state.read_activation_nested(target))
                                release_terminal = refreshed["results"].get(active["request_id"])
        if release_terminal is not None:
            self.release_recovered_terminal_pin(target, release_terminal)
        return outcome

"""Pure fail-closed recovery eligibility decisions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .models import (
    MAX_PHASES, MAX_RECEIPT_BYTES, MAX_SERVICES, ActivationRecoveryObservation,
    ActivationTransitionProjection, RecoveryRequest, RecoveryAction, canonical_digest,
    validate_edge_intent, validate_observation,
)


_PHASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


def _validate_manifest_service_partition(operation: dict, evidence: dict) -> str | None:
    persistent = operation.get("expected_persistent_services")
    initializers = operation.get("expected_initializer_services")
    topology = evidence.get("topology")
    if (not isinstance(persistent, list) or not 1 <= len(persistent) <= MAX_SERVICES or
            not isinstance(initializers, list) or len(initializers) > MAX_SERVICES or
            any(not isinstance(item, str) or not _PHASE_ID.fullmatch(item)
                for item in [*persistent, *initializers]) or
            len(persistent) != len(set(persistent)) or
            len(initializers) != len(set(initializers)) or
            set(persistent) & set(initializers) or
            not isinstance(topology, list) or
            any(not isinstance(item, str) or not _PHASE_ID.fullmatch(item)
                for item in topology) or
            len(topology) != len(set(topology)) or
            set(topology) != set(persistent) | set(initializers) or
            operation.get("expected_one_shot_phases") != [
                f"init:{item}" for item in initializers]):
        return "partial_evidence"
    return None


def _validate_one_shot_phases(operation: dict, evidence: dict) -> str | None:
    expected = operation.get("expected_one_shot_phases")
    topology = evidence.get("topology")
    phases = evidence.get("one_shot_phases")
    if (not isinstance(expected, list) or len(expected) > MAX_PHASES or
            any(not isinstance(item, str) or not _PHASE_ID.fullmatch(item)
                for item in expected) or len(expected) != len(set(expected)) or
            not isinstance(topology, list) or
            any(not item.startswith("init:") or item[5:] not in topology
                for item in expected) or
            not isinstance(phases, list) or len(phases) > MAX_PHASES):
        return "partial_evidence"
    identities = []
    for item in phases:
        if (not isinstance(item, dict) or set(item) != {"phase", "state"} or
                not isinstance(item.get("phase"), str) or
                not _PHASE_ID.fullmatch(item["phase"]) or
                item.get("state") not in {"pending", "complete"}):
            return "partial_evidence"
        identities.append(item["phase"])
    if len(identities) != len(set(identities)) or set(identities) != set(expected):
        return "partial_evidence"
    if any(item["state"] != "complete" for item in phases):
        return "mutation_required"
    return None


def _host_apply_arguments(argv: object) -> dict | None:
    if not isinstance(argv, list) or len(argv) < 3 or any(
            not isinstance(item, str) or not item for item in argv):
        return None
    try:
        host_index = argv.index("host")
    except ValueError:
        return None
    if host_index + 1 >= len(argv) or argv[host_index + 1] != "apply":
        return None
    if host_index == 0 or Path(argv[host_index - 1]).name != "sb":
        return None
    values = {}
    flags = set()
    items = argv[host_index + 2:]
    index = 0
    value_flags = {"--project-dir", "--environment", "--remote"}
    while index < len(items):
        item = items[index]
        matching = next((flag for flag in value_flags if item.startswith(flag + "=")), None)
        if matching:
            if matching in values or not item[len(matching) + 1:]:
                return None
            values[matching] = item[len(matching) + 1:]
        elif item in value_flags:
            if item in values or index + 1 >= len(items) or items[index + 1].startswith("--"):
                return None
            values[item] = items[index + 1]
            index += 1
        elif item.startswith("--"):
            if item in flags:
                return None
            flags.add(item)
        else:
            return None
        index += 1
    if "--confirm" not in flags or not all(flag in values for flag in value_flags):
        return None
    return {"project_dir": values["--project-dir"],
            "environment": values["--environment"],
            "remote": values["--remote"]}


def validate_job_binding(request: RecoveryRequest, job: object,
                         operation: object) -> str | None:
    if not isinstance(job, dict):
        return "job_ineligible"
    if job.get("job_id") != request.job_id:
        return "binding_mismatch"
    if job.get("lifecycle") != "failed":
        return "job_ineligible"
    submission = job.get("submission")
    if not isinstance(submission, dict) or submission.get("version") != 1:
        return "legacy_evidence"
    if submission.get("request_id") != request.original_request_id:
        return "binding_mismatch"
    if not isinstance(operation, dict) or operation.get("schema_version") != 1:
        return "legacy_evidence"
    expected = {
        "job_id": request.job_id,
        "request_id": request.original_request_id,
        "target": request.target.as_dict(),
    }
    if any(operation.get(key) != value for key, value in expected.items()):
        return "binding_mismatch"
    if not operation.get("accepted_before_effects"):
        return "legacy_evidence"
    required_start = (request.expected_generation if
                      request.action is RecoveryAction.OBSERVE_RECONCILE else
                      request.expected_generation - 1)
    if operation.get("starting_generation") != required_start:
        return "generation_conflict"
    exact_evidence = operation.get("evidence")
    if not isinstance(exact_evidence, dict):
        return "legacy_evidence"
    if "machine_identity" not in exact_evidence:
        return "legacy_evidence"
    required_evidence = (
        "host_identity", "machine_identity", "runtime_identity",
        "edge_intent", "edge_intent_digest",
        "source_revision", "source_branch",
        "manifest_digest",
        "secret_binding_key_version", "secret_binding_metadata_id",
        "secret_binding_revision", "topology", "images",
        "config_file_digests", "phase_receipt_digest", "one_shot_phases",
    )
    if any(field not in exact_evidence for field in required_evidence):
        return "partial_evidence"
    partition_refusal = _validate_manifest_service_partition(operation, exact_evidence)
    if partition_refusal:
        return partition_refusal
    phase_refusal = _validate_one_shot_phases(operation, exact_evidence)
    if phase_refusal:
        return phase_refusal
    try:
        edge_intent = validate_edge_intent(exact_evidence.get("edge_intent"))
    except ValueError:
        return "partial_evidence"
    if (exact_evidence.get("edge_intent_digest") != canonical_digest(edge_intent) or
            len(json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()) >
            MAX_RECEIPT_BYTES):
        return "binding_mismatch"
    compose_file_count = operation.get("compose_file_count")
    if (isinstance(compose_file_count, bool) or
            not isinstance(compose_file_count, int) or
            not 1 <= compose_file_count <= 61):
        return "partial_evidence"
    if (not exact_evidence.get("source_revision") or
            not exact_evidence.get("phase_receipt_digest") or
            not isinstance(exact_evidence.get("images"), list) or
            not exact_evidence.get("images") or
            not isinstance(exact_evidence.get("config_file_digests"), list) or
            len(exact_evidence["config_file_digests"]) != compose_file_count + 3):
        return "partial_evidence"
    source = operation.get("source")
    if not isinstance(source, dict) or source.get("clean") is not True:
        return "dirty_source"
    submitted_source = submission.get("source")
    if (not isinstance(submitted_source, dict) or
            submitted_source.get("identity") != source.get("identity") or
            submitted_source.get("commit") != source.get("commit") or
            submitted_source.get("dirty_digest") is not None):
        return "binding_mismatch" if isinstance(submitted_source, dict) else "legacy_evidence"
    if (submission.get("project_identity") != operation.get("project_identity") or
            "sha256:" + hashlib.sha256(
                str(submission.get("project_root") or "").encode()).hexdigest() !=
            operation.get("project_root_digest")):
        return "binding_mismatch"
    arguments = _host_apply_arguments(submission.get("argv"))
    if (arguments is None or arguments["environment"] != request.target.environment or
            arguments["remote"] != request.target.remote):
        return "binding_mismatch"
    project_argument = Path(arguments["project_dir"]).expanduser()
    if not project_argument.is_absolute():
        project_argument = (Path(str(submission.get("project_root") or "")) /
                            str(submission.get("cwd_relative") or ".") /
                            project_argument)
    if ("sha256:" + hashlib.sha256(str(project_argument.resolve()).encode()).hexdigest()
            != operation.get("project_root_digest")):
        return "binding_mismatch"
    digest = operation.get("digest")
    unsigned = {key: value for key, value in operation.items() if key != "digest"}
    if digest != canonical_digest(unsigned):
        return "binding_mismatch"
    return None


def classify_observation(operation: dict, observation: object) -> tuple[str | None, dict | None]:
    try:
        evidence = validate_observation(observation)
    except ValueError:
        return "partial_evidence", None
    if evidence.get("complete") is not True or evidence.get("bounded") is not True:
        return "partial_evidence", evidence
    if evidence.get("epoch_start") != evidence.get("epoch_end"):
        return "evidence_changed", evidence
    exact = operation.get("evidence")
    if not isinstance(exact, dict):
        return "legacy_evidence", evidence
    partition_refusal = _validate_manifest_service_partition(operation, exact)
    if partition_refusal:
        return partition_refusal, evidence
    phase_refusal = _validate_one_shot_phases(operation, exact)
    if phase_refusal:
        return phase_refusal, evidence
    fresh_phase_refusal = _validate_one_shot_phases(operation, evidence)
    if fresh_phase_refusal:
        return fresh_phase_refusal, evidence
    services = evidence.get("services")
    service_ids = [item.get("service") for item in services] if isinstance(services, list) else []
    if (not isinstance(services, list) or
            len(service_ids) != len(set(service_ids)) or
            set(service_ids) != set(operation["expected_persistent_services"])):
        return "mutation_required", evidence
    for field in ("host_identity", "machine_identity", "runtime_identity",
                  "edge_intent_digest", "source_revision",
                  "source_branch", "manifest_digest", "secret_binding_key_version",
                  "secret_binding_metadata_id", "secret_binding_revision", "source_clean",
                  "topology", "images", "config_file_digests",
                  "phase_receipt_digest"):
        if evidence.get(field) != exact.get(field):
            return "changed_target" if field in {
                "host_identity", "machine_identity", "runtime_identity",
                "edge_intent_digest"
            } else "mutation_required", evidence
    declared = exact.get("topology")
    images = evidence.get("images")
    if (not isinstance(declared, list) or not isinstance(images, list) or
            sorted(item.get("name") for item in images if isinstance(item, dict)) !=
            sorted(declared)):
        return "mutation_required", evidence
    for item in images:
        image_id = item.get("id") if isinstance(item, dict) else None
        if (not isinstance(image_id, str) or
                not image_id.startswith("sha256:") or len(image_id) != 71 or
                any(character not in "0123456789abcdef" for character in image_id[7:])):
            return "mutation_required", evidence
    if any(item.get("state") != "ready" for item in evidence.get("services", [])):
        return "mutation_required", evidence
    if any(item.get("state") != "complete" for item in evidence.get("phases", [])):
        return "mutation_required", evidence
    return None, evidence


def validate_edge_request(request: RecoveryRequest, observation_attempt: object,
                          *, generation: int, governance_authorized: bool,
                          now: int | None = None) -> str | None:
    if request.action is not RecoveryAction.CONTINUE_EDGE:
        return "binding_mismatch"
    if not request.confirmed:
        return "confirmation_required"
    if not governance_authorized:
        return "governance_unavailable"
    if generation != request.expected_generation:
        return "generation_conflict"
    if not isinstance(observation_attempt, dict):
        return "expired_evidence"
    if observation_attempt.get("request_id") != request.observation_request_id:
        return "binding_mismatch"
    if observation_attempt.get("result_class") not in {
            "observation_reconciled", "already_reconciled"}:
        return "expired_evidence"
    if (observation_attempt.get("evidence") or {}).get("id") != request.evidence_id:
        return "evidence_changed"
    expires_at = (observation_attempt.get("evidence") or {}).get("expires_at")
    if (not isinstance(expires_at, int) or isinstance(expires_at, bool) or
            now is not None and now >= expires_at):
        return "expired_evidence"
    pending = [item.get("phase") for item in observation_attempt.get("phases", [])
               if isinstance(item, dict) and item.get("state") != "complete"]
    if pending != ["edge"]:
        return "mutation_required"
    return None


def classify_activation_transition(
        projection: ActivationTransitionProjection, observation: object
) -> ActivationRecoveryObservation:
    """Classify exact new/prior/neither/ambiguous from one coherent epoch."""
    if type(projection) is not ActivationTransitionProjection or type(observation) is not dict:
        raise ValueError("activation observation is invalid")
    required = {"target_epoch_start", "target_epoch_end", "target_identity_start",
                "target_identity_end", "runtime_epoch_start", "runtime_epoch_end",
                "generation_digest", "services"}
    if set(observation) != required:
        raise ValueError("activation observation is invalid")
    epochs = tuple(observation[name] for name in (
        "target_epoch_start", "target_epoch_end", "target_identity_start",
        "target_identity_end", "runtime_epoch_start", "runtime_epoch_end"))
    if any(not isinstance(value, str) or not _PHASE_ID.fullmatch(value) for value in epochs):
        raise ValueError("activation observation is invalid")
    services = observation["services"]
    ambiguous = (epochs[0] != epochs[1] or epochs[2] != epochs[3] or
                 epochs[4] != epochs[5] or
                 epochs[0] != projection.target["machine_identity"] or
                 epochs[2] != projection.target["target_identity"] or
                 epochs[4] != projection.target["daemon_identity"] or
                 not isinstance(services, list) or len(services) > MAX_SERVICES or
                 (not services and (projection.new_services or projection.prior_services)) or
                 any(not isinstance(item, dict) for item in services))
    if not ambiguous:
        identities = [item.get("service") for item in services]
        ambiguous = (len(identities) != len(set(identities)) or
                     any(not isinstance(item, str) for item in identities))
    observed_digest = observation.get("generation_digest")
    normalized = tuple(sorted(services, key=lambda item: item.get("service", ""))) \
        if not ambiguous else ()
    exact_new = projection.new_generation_digest is not None and normalized == tuple(sorted(
        projection.new_services, key=lambda item: item["service"]))
    exact_prior = normalized == tuple(sorted(
        projection.prior_services, key=lambda item: item["service"])) and (
            projection.prior_generation_digest is not None or
            (projection.expected_generation == 0 and not projection.prior_services))
    if ambiguous or (exact_new and exact_prior):
        classification = "ambiguous"
    elif observed_digest == projection.new_generation_digest and exact_new:
        classification = "exact_new"
    elif exact_prior and observed_digest == projection.prior_generation_digest:
        classification = "exact_prior"
    elif isinstance(observed_digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", observed_digest):
        classification = "neither"
    else:
        classification = "ambiguous"
    body = {"transaction_digest": projection.transaction_digest,
            "expected_generation": projection.expected_generation,
            "classification": classification,
            "target_epoch_start": epochs[0], "target_epoch_end": epochs[1],
            "target_identity_start": epochs[2], "target_identity_end": epochs[3],
            "runtime_epoch_start": epochs[4], "runtime_epoch_end": epochs[5]}
    return ActivationRecoveryObservation(
        **body, evidence_identity=canonical_digest({
            "projection": projection.as_mapping(), "observation": observation,
            "classification": classification}))

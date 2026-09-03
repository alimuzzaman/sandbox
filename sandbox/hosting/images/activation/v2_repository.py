"""Unified target-state codec and custody coordinator for activation v2."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import (
    MAX_ACTIVATION_BYTES, MAX_RESULTS, MAX_TOMBSTONES, ActivationContractError,
    RESULT_CODES, SECRET_FIELDS, _closed, _digest, _integer, _safe_mapping, _text,
    activation_digest, canonical_bytes,
)
from .v2_models import (
    ActivationRequestV2, GenerationBoundEdgeReceiptV2,
    ReplacementIntentV2, VerifiedActivationGenerationV2, _local_image_id,
)


V2_PHASES = frozenset({
    "accepted", "runtime_pending", "runtime_proven", "edge_pending",
    "committed", "refused", "uncertain"})

_PLATFORM = {"os": "linux", "architecture": "amd64"}
_IMAGE_FIELDS = frozenset({
    "name", "image_ref", "config_digest", "platform", "local_image_id"})
_BINDING_FIELDS = frozenset({
    "service", "image", "image_ref", "environment_variable"})
_COMPOSE_FIELDS = frozenset({
    "service", "image", "build", "pull_policy", "platform", "dependencies",
    "topology_identity", "compose_config_hash", "configuration_digest"})
_SERVICE_FIELDS = frozenset({
    "service", "runtime_identity", "compose_project", "declared_image",
    "repository_digest", "local_image_id", "config_digest", "platform",
    "topology_identity", "compose_config_hash", "healthy"})
_ENVIRONMENT_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,127}")


def _validate_service_projection(value: object, *, services: list[str],
                                 compose_project: str,
                                 topology_digest: str) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) != len(services):
        raise ActivationContractError()
    rows = []
    for item in value:
        row = _closed(item, _SERVICE_FIELDS)
        for key in ("service", "runtime_identity", "compose_project", "declared_image",
                    "repository_digest"):
            _text(row[key], identity=(key in {
                "service", "runtime_identity", "compose_project"}))
        for key in ("config_digest", "topology_identity", "compose_config_hash"):
            _digest(row[key])
        _local_image_id(row["local_image_id"], row["repository_digest"])
        if (row["platform"] != _PLATFORM or row["healthy"] is not True
                or row["compose_project"] != compose_project
                or row["topology_identity"] != topology_digest
                or row["local_image_id"] not in {
                    row["config_digest"], row["repository_digest"]}):
            raise ActivationContractError()
        rows.append(row)
    if [row["service"] for row in rows] != services:
        raise ActivationContractError()
    return rows


def _validate_subject_v2(value: object, *, request_digest: str, target: dict,
                         starting_generation: int, compose_project: str,
                         services: list[str], observation: dict) -> dict[str, Any]:
    subject = _safe_mapping(value, forbidden=SECRET_FIELDS)
    required = {"schema_version", "generation", "plan_set_digest",
        "proof_set_digest", "policy_digest", "request_digest", "target",
        "topology_digest", "configuration_digest", "compose_snapshot_digest",
        "compose_project", "images", "service_image_bindings", "compose_projection",
        "service_projection", "running_observation_digest",
        "rollback_from_generation_digest", "generation_subject_digest"}
    if (set(subject) != required or type(subject["schema_version"]) is not int
            or subject["schema_version"] != 2
            or type(subject["generation"]) is not int
            or subject["generation"] != starting_generation + 1
            or subject["request_digest"] != request_digest or subject["target"] != target
            or subject["compose_project"] != compose_project):
        raise ActivationContractError()
    for key in ("plan_set_digest", "proof_set_digest", "policy_digest",
                "request_digest", "topology_digest", "configuration_digest",
                "compose_snapshot_digest", "running_observation_digest",
                "rollback_from_generation_digest", "generation_subject_digest"):
        _digest(subject[key])
    if (subject["running_observation_digest"] != observation["observation_digest"]
            or subject["topology_digest"] != observation["topology_digest"]):
        raise ActivationContractError()
    images_value = subject["images"]
    if type(images_value) is not list or not 1 <= len(images_value) <= 64:
        raise ActivationContractError()
    images = {}
    for item in images_value:
        row = _closed(item, _IMAGE_FIELDS)
        _text(row["name"], identity=True); _text(row["image_ref"])
        _digest(row["config_digest"])
        _local_image_id(row["local_image_id"], row["image_ref"])
        if row["platform"] != _PLATFORM or row["local_image_id"] not in {
                row["config_digest"], row["image_ref"]}:
            raise ActivationContractError()
        images[row["name"]] = row
    if len(images) != len(images_value):
        raise ActivationContractError()
    bindings_value = subject["service_image_bindings"]
    if type(bindings_value) is not list or len(bindings_value) != len(services):
        raise ActivationContractError()
    bindings = {}
    for item in bindings_value:
        row = _closed(item, _BINDING_FIELDS)
        _text(row["service"], identity=True); _text(row["image"], identity=True)
        image = images.get(row["image"])
        if (image is None or row["image_ref"] != image["image_ref"]
                or type(row["environment_variable"]) is not str
                or _ENVIRONMENT_VARIABLE.fullmatch(row["environment_variable"]) is None):
            raise ActivationContractError()
        bindings[row["service"]] = row
    if list(bindings) != services:
        raise ActivationContractError()
    compose_value = subject["compose_projection"]
    if type(compose_value) is not list or len(compose_value) != len(services):
        raise ActivationContractError()
    compose = {}
    for item in compose_value:
        row = _closed(item, _COMPOSE_FIELDS)
        binding = bindings.get(row["service"])
        if (binding is None or row["image"] != binding["image_ref"]
                or row["build"] is not None or row["pull_policy"] != "never"
                or row["platform"] != _PLATFORM or type(row["dependencies"]) is not list
                or row["topology_identity"] != subject["topology_digest"]):
            raise ActivationContractError()
        for dependency in row["dependencies"]:
            _text(dependency, identity=True)
        _digest(row["compose_config_hash"]); _digest(row["configuration_digest"])
        compose[row["service"]] = row
    if list(compose) != services:
        raise ActivationContractError()
    projected = _validate_service_projection(
        subject["service_projection"], services=services,
        compose_project=compose_project, topology_digest=subject["topology_digest"])
    if projected != observation["services"]:
        raise ActivationContractError()
    for row in projected:
        binding = bindings[row["service"]]; image = images[binding["image"]]
        if (row["declared_image"] != binding["image_ref"]
                or row["repository_digest"] != binding["image_ref"]
                or row["local_image_id"] != image["local_image_id"]
                or row["config_digest"] != image["config_digest"]
                or row["compose_config_hash"] != compose[row["service"]]["compose_config_hash"]):
            raise ActivationContractError()
    body = {key: item for key, item in subject.items()
            if key != "generation_subject_digest"}
    if subject["generation_subject_digest"] != activation_digest(
            "sandbox.hosting.images.activation-generation-subject.v2", body):
        raise ActivationContractError()
    return subject


def validate_result_v2(value: object) -> dict[str, Any]:
    raw = _safe_mapping(value, forbidden=SECRET_FIELDS)
    required = {"schema_version", "ok", "result_class", "code", "request_id",
                "request_digest", "starting_generation", "resulting_generation",
                "generation_digest", "transaction_digest"}
    if (set(raw) != required or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 2 or type(raw["ok"]) is not bool
            or raw["result_class"] not in {"success", "refused", "uncertain"}
            or raw["code"] not in RESULT_CODES
            or raw["ok"] is not (raw["result_class"] == "success")
            or type(raw["starting_generation"]) is not int
            or type(raw["resulting_generation"]) is not int
            or raw["starting_generation"] < 0
            or raw["resulting_generation"] != raw["starting_generation"] + int(raw["ok"])):
        raise ActivationContractError()
    _text(raw["request_id"], identity=True)
    _digest(raw["request_digest"]); _digest(raw["transaction_digest"])
    if raw["ok"]:
        if raw["code"] != "committed":
            raise ActivationContractError()
        _digest(raw["generation_digest"])
    elif raw["generation_digest"] is not None:
        raise ActivationContractError()
    return raw


def validate_transaction_v2(value: object) -> dict[str, Any]:
    raw = _safe_mapping(value, forbidden=SECRET_FIELDS)
    required = {"schema_version", "transaction_digest", "request_id", "request_digest",
                "operation", "holder", "starting_generation", "phase", "effect_entered",
                "proof_pin", "prior_generation_digest", "recovery_context",
                "replacement_intent", "running_observation", "generation_subject", "edge_result",
                "candidate_generation", "result"}
    if (set(raw) != required or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 2 or raw["operation"] not in {"activate", "rollback"}
            or raw["phase"] not in V2_PHASES or type(raw["effect_entered"]) is not bool
            or type(raw["starting_generation"]) is not int or raw["starting_generation"] < 0):
        raise ActivationContractError()
    _text(raw["request_id"], identity=True); _text(raw["holder"], identity=True)
    if raw["holder"] != f"activation-owner/{raw['request_id']}":
        raise ActivationContractError()
    for key in ("transaction_digest", "request_digest", "prior_generation_digest"):
        _digest(raw[key])
    from .repository import validate_retained_proof_pin
    pin = validate_retained_proof_pin(raw["proof_pin"])
    if pin["holder"] != raw["holder"]:
        raise ActivationContractError()
    context = _closed(raw["recovery_context"], frozenset({
        "target", "compose_project", "selected_services"}))
    target = _closed(context["target"], frozenset({
        "machine_identity", "target_identity", "daemon_identity"}))
    for identity in target.values(): _text(identity, identity=True)
    _text(context["compose_project"], identity=True)
    services = context["selected_services"]
    if (type(services) is not list or not 1 <= len(services) <= 64
            or services != sorted(services) or len(services) != len(set(services))):
        raise ActivationContractError()
    for service in services: _text(service, identity=True)

    replacement = raw["replacement_intent"]
    if replacement is not None:
        replacement = ReplacementIntentV2.from_mapping(replacement)
        if (replacement.request_digest != raw["request_digest"]
                or replacement.generation != raw["starting_generation"] + 1
                or replacement.prior_generation_digest != raw["prior_generation_digest"]
                or replacement.target != target
                or replacement.compose_project != context["compose_project"]
                or list(replacement.compose_snapshot["selected_services"]) != services):
            raise ActivationContractError()
    observation = raw["running_observation"]
    subject = raw["generation_subject"]
    edge = raw["edge_result"]
    candidate = raw["candidate_generation"]
    if observation is not None:
        observation = _safe_mapping(observation, forbidden=SECRET_FIELDS)
        required_observation = {"schema_version", "target", "target_epoch_start",
            "target_epoch_end", "target_identity_start", "target_identity_end",
            "runtime_epoch_start", "runtime_epoch_end", "services", "topology_digest",
            "edge_identity", "observation_digest"}
        if (set(observation) != required_observation
                or type(observation["schema_version"]) is not int
                or observation["schema_version"] != 2 or observation["target"] != target):
            raise ActivationContractError()
        for key in ("target_epoch_start", "target_epoch_end", "target_identity_start",
                    "target_identity_end", "runtime_epoch_start", "runtime_epoch_end"):
            _text(observation[key], identity=True)
        if (observation["target_epoch_start"] != target["machine_identity"]
                or observation["target_epoch_end"] != target["machine_identity"]
                or observation["target_identity_start"] != target["target_identity"]
                or observation["target_identity_end"] != target["target_identity"]
                or observation["runtime_epoch_start"] != target["daemon_identity"]
                or observation["runtime_epoch_end"] != target["daemon_identity"]):
            raise ActivationContractError()
        _digest(observation["topology_digest"]); _digest(observation["edge_identity"])
        _digest(observation["observation_digest"])
        observation["services"] = _validate_service_projection(
            observation["services"], services=services,
            compose_project=context["compose_project"],
            topology_digest=observation["topology_digest"])
        observed_body = {key: item for key, item in observation.items()
                         if key != "observation_digest"}
        if observation["observation_digest"] != activation_digest(
                "sandbox.hosting.images.running-observation.v2", observed_body):
            raise ActivationContractError()
    if subject is not None:
        if observation is None:
            raise ActivationContractError()
        subject = _validate_subject_v2(
            subject, request_digest=raw["request_digest"], target=target,
            starting_generation=raw["starting_generation"],
            compose_project=context["compose_project"], services=services,
            observation=observation)
    if edge is not None:
        edge = _safe_mapping(edge, forbidden=SECRET_FIELDS)
        if edge.get("terminal") is True:
            GenerationBoundEdgeReceiptV2.from_mapping(edge)
        else:
            prepared_fields = {"schema_version", "phase", "request_digest", "generation",
                "generation_subject_digest", "route_digest", "observation_digest",
                "terminal", "receipt_digest"}
            if set(edge) != prepared_fields or edge.get("schema_version") != 2 \
                    or edge.get("phase") != "prepared" or edge.get("terminal") is not False \
                    or edge.get("receipt_digest") is not None:
                raise ActivationContractError()
            for key in ("request_digest", "generation_subject_digest", "route_digest",
                        "observation_digest"):
                _digest(edge[key])
            _integer(edge["generation"], minimum=1)
    if raw["candidate_generation"] is not None:
        candidate = VerifiedActivationGenerationV2.from_mapping(candidate)
    if raw["phase"] == "accepted" and (replacement is not None or raw["effect_entered"]
            or any(item is not None for item in (observation, subject, edge, candidate))):
        raise ActivationContractError()
    if raw["phase"] == "runtime_pending" and (replacement is None
            or raw["effect_entered"] is not True
            or any(item is not None for item in (observation, subject, edge, candidate))):
        raise ActivationContractError()
    if raw["phase"] == "runtime_proven" \
            and (replacement is None or observation is None or subject is None
                 or edge is not None or candidate is not None):
        raise ActivationContractError()
    if raw["phase"] == "edge_pending":
        if replacement is None or observation is None or subject is None or edge is None:
            raise ActivationContractError()
        if (edge.get("terminal") is True) is not (candidate is not None):
            raise ActivationContractError()
        if (edge["request_digest"] != raw["request_digest"]
                or edge["generation"] != subject["generation"]
                or edge["generation_subject_digest"] != subject["generation_subject_digest"]
                or edge["route_digest"] != observation["edge_identity"]
                or edge["observation_digest"] != observation["observation_digest"]):
            raise ActivationContractError()
    if raw["effect_entered"] and replacement is None:
        raise ActivationContractError()
    if subject is not None:
        expected = replacement.as_mapping()
        for key in ("generation", "plan_set_digest", "proof_set_digest", "policy_digest",
                    "request_digest", "target", "topology_digest", "configuration_digest",
                    "compose_project", "images", "service_image_bindings", "compose_projection",
                    "rollback_from_generation_digest"):
            replacement_key = ("prior_generation_digest"
                               if key == "rollback_from_generation_digest" else key)
            if subject[key] != expected[replacement_key]:
                raise ActivationContractError()
        if subject["compose_snapshot_digest"] != replacement.compose_snapshot["snapshot_digest"]:
            raise ActivationContractError()
    if candidate is not None and (candidate.request_digest != raw["request_digest"]
            or candidate.target != target or candidate.service_projection != tuple(
                observation["services"])
            or candidate.subject_mapping() != {key: item for key, item in subject.items()
                                               if key != "generation_subject_digest"}
            or candidate.edge_receipt != edge):
        raise ActivationContractError()
    if raw["result"] is not None:
        result = validate_result_v2(raw["result"])
        if result["request_id"] != raw["request_id"] \
                or result["request_digest"] != raw["request_digest"] \
                or result["transaction_digest"] != raw["transaction_digest"]:
            raise ActivationContractError()
    initial = {**raw, "phase": "accepted", "effect_entered": False,
               "replacement_intent": None, "running_observation": None,
               "generation_subject": None,
               "edge_result": None, "candidate_generation": None, "result": None}
    transaction_digest = initial.pop("transaction_digest")
    if transaction_digest != activation_digest(
            "sandbox.hosting.images.activation-transaction.v2", initial):
        raise ActivationContractError()
    return raw


def transaction_v2(request: ActivationRequestV2, *, holder: str, proof_pin: dict,
                   recovery_context: dict, prior_generation_digest: str) -> dict[str, Any]:
    body = {"schema_version": 2, "request_id": request.request_id,
            "request_digest": request.request_digest, "operation": request.operation,
            "holder": holder, "starting_generation": request.expected_generation,
            "phase": "accepted", "effect_entered": False, "proof_pin": proof_pin,
            "prior_generation_digest": prior_generation_digest,
            "recovery_context": json.loads(canonical_bytes(recovery_context)),
            "replacement_intent": None, "running_observation": None, "generation_subject": None,
            "edge_result": None, "candidate_generation": None, "result": None}
    body["transaction_digest"] = activation_digest(
        "sandbox.hosting.images.activation-transaction.v2", body)
    return validate_transaction_v2(body)


def accept_candidate_v2(state: dict, request: ActivationRequestV2, *, holder: str,
                        proof_pin: dict, recovery_context: dict,
                        prior_generation_digest: str):
    if state["generation"] != request.expected_generation:
        return "generation_conflict", state, None
    stored = state["results"].get(request.request_id)
    if stored is not None:
        result = stored.get("result") if type(stored) is dict else None
        if type(result) is not dict or result.get("request_digest") != request.request_digest:
            return "conflict", state, None
        return "replay", state, result
    active = state.get("active")
    if active is not None:
        if active.get("schema_version") == 2 \
                and active.get("request_id") == request.request_id \
                and active.get("request_digest") == request.request_digest:
            return "resume", state, active.get("result")
        return "busy", state, None
    if len(state["results"]) >= MAX_RESULTS and len(state["tombstones"]) >= MAX_TOMBSTONES:
        return "retention_full", state, None
    candidate = json.loads(canonical_bytes(state))
    candidate["active"] = transaction_v2(
        request, holder=holder, proof_pin=proof_pin,
        recovery_context=recovery_context,
        prior_generation_digest=prior_generation_digest)
    candidate["reserved_terminal_bytes"] = 16384
    return "accepted", candidate, None


def transition_candidate_v2(state: dict, request: ActivationRequestV2, phase: str,
                            *, effect_entered=None, replacement_intent=None,
                            running_observation=None,
                            generation_subject=None, edge_result=None,
                            candidate_generation=None):
    candidate = json.loads(canonical_bytes(state)); active = candidate.get("active")
    if type(active) is not dict or active.get("schema_version") != 2 \
            or active.get("request_digest") != request.request_digest:
        raise ActivationContractError("request_conflict")
    if phase not in V2_PHASES or active["phase"] in {"committed", "refused"}:
        raise ActivationContractError("request_conflict")
    if effect_entered is not None:
        if type(effect_entered) is not bool or (active["effect_entered"] and not effect_entered):
            raise ActivationContractError("request_conflict")
        active["effect_entered"] = effect_entered
    if replacement_intent is not None:
        active["replacement_intent"] = ReplacementIntentV2.from_mapping(
            replacement_intent).as_mapping()
    if running_observation is not None:
        active["running_observation"] = _safe_mapping(
            running_observation, forbidden=SECRET_FIELDS)
    if generation_subject is not None:
        active["generation_subject"] = _safe_mapping(
            generation_subject, forbidden=SECRET_FIELDS)
    if edge_result is not None:
        active["edge_result"] = _safe_mapping(edge_result, forbidden=SECRET_FIELDS)
    if candidate_generation is not None:
        active["candidate_generation"] = VerifiedActivationGenerationV2.from_mapping(
            candidate_generation).as_mapping()
    active["phase"] = phase
    validate_transaction_v2(active)
    return candidate


def commit_candidate_v2(state: dict, request: ActivationRequestV2, result: dict,
                        generation: dict | None):
    candidate = json.loads(canonical_bytes(state)); active = candidate.get("active")
    terminal = validate_result_v2(result)
    if type(active) is not dict or active.get("schema_version") != 2 \
            or active.get("request_digest") != request.request_digest:
        raise ActivationContractError("request_conflict")
    if terminal["ok"]:
        parsed = VerifiedActivationGenerationV2.from_mapping(generation)
        if active["phase"] not in {"runtime_proven", "edge_pending"} \
                or parsed.generation != request.expected_generation + 1:
            raise ActivationContractError("effect_unknown")
        candidate["previous"] = candidate["current"]
        candidate["current"] = parsed.as_mapping()
        candidate["generation"] = parsed.generation
        active["phase"] = "committed"
    else:
        active["phase"] = terminal["result_class"]
    active["result"] = terminal
    candidate["results"][request.request_id] = {
        "result": terminal, "holder": active["holder"],
        "proof_digest": active["proof_pin"]["proof_digest"],
        "proof_pin": active["proof_pin"]}
    if terminal["result_class"] != "uncertain":
        candidate["active"] = None; candidate["reserved_terminal_bytes"] = 0
    return candidate


def activation_recovery_intent_v2(state: object) -> dict[str, Any]:
    """Return the closed public selector needed for a fresh-process v2 read."""
    from .repository import decode_activation_state
    safe = decode_activation_state(state)
    active = safe.get("active")
    if type(active) is not dict or active.get("schema_version") != 2:
        raise ActivationContractError("recovery_ineligible")
    intent = active.get("replacement_intent")
    if intent is None:
        raise ActivationContractError("recovery_ineligible")
    return ReplacementIntentV2.from_mapping(intent).as_mapping()


def _intent_projection_services(intent: ReplacementIntentV2,
                                observed_services: object) -> tuple[dict, ...]:
    bindings = {row["service"]: row for row in intent.service_image_bindings}
    images = {row["name"]: row for row in intent.images}
    compose = {row["service"]: row for row in intent.compose_projection}
    expected_names = list(bindings)
    exact = None
    if observed_services is not None:
        try:
            exact = _validate_service_projection(
                observed_services, services=expected_names,
                compose_project=intent.compose_project,
                topology_digest=intent.topology_digest)
            for row in exact:
                binding = bindings[row["service"]]; image = images[binding["image"]]
                if (row["declared_image"] != binding["image_ref"]
                        or row["repository_digest"] != binding["image_ref"]
                        or row["local_image_id"] != image["local_image_id"]
                        or row["config_digest"] != image["config_digest"]
                        or row["compose_config_hash"] != compose[row["service"]][
                            "compose_config_hash"]):
                    exact = None
                    break
        except (TypeError, ValueError):
            exact = None
    if exact is not None:
        return tuple(exact)
    # Runtime identities do not exist before replacement.  This closed sentinel
    # can never accidentally equal a Docker container id.  It keeps the generic
    # recovery projection well-formed while exact-prior/neither are classified.
    return tuple({"service": service,
        "runtime_identity": f"replacement-intent-{service}",
        "declared_image": bindings[service]["image_ref"],
        "repository_digest": bindings[service]["image_ref"],
        "local_image_id": images[bindings[service]["image"]]["local_image_id"],
        "config_digest": images[bindings[service]["image"]]["config_digest"],
        "platform": {"os": "linux", "architecture": "amd64"},
        "topology_identity": intent.topology_digest,
        "compose_project": intent.compose_project,
        "compose_config_hash": compose[service]["compose_config_hash"],
        "healthy": True} for service in expected_names)


def activation_recovery_projection(state: object, *, observed_services=None):
    """Project retained state, using a pre-read to bind a pending v2 intent."""
    from sandbox.hosting.recovery.models import ActivationTransitionProjection
    from .repository import decode_activation_state
    from .v2_models import validate_activation_generation
    safe = decode_activation_state(state)
    active = safe.get("active")
    if type(active) is not dict or active.get("schema_version") not in {1, 2}:
        raise ActivationContractError("recovery_ineligible")
    new = active.get("candidate_generation")
    replacement = active.get("replacement_intent")
    prior = safe.get("current")
    new_generation = validate_activation_generation(new) if new is not None else None
    prior_generation = validate_activation_generation(prior) if prior is not None else None
    intent = (ReplacementIntentV2.from_mapping(replacement)
              if new_generation is None and replacement is not None else None)
    new_digest = (new_generation.generation_digest if new_generation is not None
                  else intent.replacement_intent_digest if intent is not None else None)
    new_services = (tuple(new_generation.service_projection)
                    if new_generation is not None else
                    _intent_projection_services(intent, observed_services)
                    if intent is not None else ())
    return ActivationTransitionProjection(
        transaction_digest=active["transaction_digest"],
        request_digest=active["request_digest"], operation=active["operation"],
        phase=active["phase"], effect_entered=active["effect_entered"],
        expected_generation=active["starting_generation"],
        new_generation_digest=new_digest,
        prior_generation_digest=(None if prior_generation is None
                                 else prior_generation.generation_digest),
        target=active["recovery_context"]["target"],
        new_services=new_services,
        prior_services=(() if prior_generation is None else
                        tuple(prior_generation.service_projection)))


__all__ = ("accept_candidate_v2", "activation_recovery_intent_v2",
           "activation_recovery_projection",
           "commit_candidate_v2", "transaction_v2",
           "transition_candidate_v2", "validate_result_v2", "validate_transaction_v2")

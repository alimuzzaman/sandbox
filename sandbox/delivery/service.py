"""Read-only delivery diagnosis from injected owner projections.

No constructor in this module creates a store or discovers another controller.
Owner callbacks must be read-only; only current_readers may perform network
observations, and they are called solely for an explicit observe request.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from .models import (
    DeliveryError, MAX_RESPONSE_BYTES, REASONS, TERMINAL, canonical_digest,
    encoded, evidence, identifier, request_identifier, now, operation_summary, request_scope,
    serialize_projection, target_digest, timestamp, validate_operation,
    validate_target,
)
from .repository import RETENTION


EVIDENCE_KEYS = ("admission", "creation", "runtime", "routes", "edge", "authority")


def _reason(code):
    if code not in REASONS:
        code = "delivery_record_incomplete"
    messages = {
        "none": "The declared evidence is available.",
        "missing": "No matching retained evidence is available.",
        "failed": "The delivery failed.",
        "partial": "Some required evidence is unavailable.",
        "unsupported": "This owner cannot provide the required evidence.",
        "conflicting": "The retained identities disagree.",
        "binding_mismatch": "The evidence belongs to a different target or attempt.",
        "required_evidence_missing": "A required delivery check remains unverified.",
        "authority_pending": "The original owner has not established completion.",
        "initializer_refused": "The initializer did not establish success.",
        "delivery_request_expired": "The original request remains reserved; its detail has expired.",
        "delivery_cursor_expired": "Retention invalidated this history cursor.",
        "delivery_cursor_invalid": "This cursor does not match the selected history.",
        "delivery_store_unavailable": "The delivery history could not be read.",
        "unsupported_capability": "This controller does not support the requested evidence.",
        "bounds": "Optional detail was omitted to keep the response bounded.",
    }
    return {"code": code, "message": messages.get(code, "Delivery evidence is incomplete.")}


def _base(kind, target, *, state="missing", result="unknown", code="missing", observed_at=None):
    return {"source_kind": kind, "target_digest": target_digest(target),
            "applicability": "required", "state": state, "result": result,
            "reason": _reason(code), "observed_at": observed_at}


def _stamp(value):
    if type(value) is int and value > 0:
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return timestamp(value)
    except (ValueError, TypeError):
        return None


def _unknown(kind, target, raw):
    state = raw.get("state") if type(raw) is dict else "partial"
    state = state if type(state) is str and state in {"missing", "partial", "unsupported", "expired", "conflicting"} else "partial"
    return _base(kind, target, state=state, code=state)


def normalize_recovery_projection(raw, target, operation=None, *, source_kind="admission"):
    """Normalize RecoveryRepository.read_delivery_projection; never grant authority."""
    target = validate_target(target)
    if source_kind not in {"admission", "authority"}:
        raise DeliveryError("delivery_contract_invalid")
    if type(raw) is not dict or raw.get("schema_version") != 1 or raw.get("admission") is None:
        return _unknown(source_kind, target, raw)
    try:
        admission = raw["admission"]
        declared = admission["target"]
        source = admission["source"]
        proof = admission["evidence"]
        matches = (admission["project_identity"] == target["project_identity"]
                   and admission["project_root_digest"] == target["project_root_digest"]
                   and declared["remote"] == target["remote_name"]
                   and declared["environment"] == target["environment"])
        if operation is not None:
            operation = validate_operation(operation)
            application = operation["requested_outcome"]["application"]
            matches = matches and admission["request_id"] == operation["request_id"] and admission["job_id"] == operation["job_id"]
            if application["commit"] is not None:
                matches = matches and source["commit"] == application["commit"]
            expected_artifact = application.get('source_artifact')
            actual_artifact = source.get('artifact')
            if expected_artifact is not None:
                matches = (matches and admission.get('source_schema') == 2
                           and actual_artifact == expected_artifact)
            elif actual_artifact is not None or admission.get('source_schema') != 1:
                matches = False
            if application["source_identity"] is not None:
                matches = matches and source["identity"] == application["source_identity"]
            if operation["requested_outcome"]["configuration_digest"] is not None:
                matches = matches and proof["config_digest"] == operation["requested_outcome"]["configuration_digest"]
        for target_key, proof_key in (("registered_host_digest", "host_identity"), ("runtime_identity", "runtime_identity"), ("machine_identity", "machine_identity")):
            if target[target_key] is not None:
                matches = matches and target[target_key] == proof[proof_key]
        if not matches:
            return _base(source_kind, target, state="conflicting", code="binding_mismatch")
        observed = _stamp(admission["accepted_at"])
        result = _base(source_kind, target, state="known" if observed else "partial",
                       result="passed" if source_kind == "admission" and admission["accepted_before_effects"] is True else "unknown",
                       code="none" if source_kind == "admission" else "authority_pending", observed_at=observed)
        result.update(request_id=admission["request_id"], job_id=admission["job_id"],
                      application_revision=(None if source_kind == 'authority' and source.get('artifact') is not None
                                            else source['commit']),
                      generation=admission["starting_generation"],
                      contract_digest=proof["config_digest"], proof_digest=admission["digest"],
                      references=[{"kind": "hosting_operation", "digest": admission["digest"]}])
        # An admission receipt is not terminal workload proof, even when no
        # current active owner is visible. Preserve uncertainty explicitly.
        if source_kind == "authority" and raw.get("uncertainty") is not None:
            result.update(state="partial", result="unknown", reason=_reason("effect_unknown"))
        return evidence(result)
    except (ValueError, TypeError, KeyError):
        return _base(source_kind, target, state="partial", code="partial")


def normalize_activation_projection(raw, target, operation=None):
    """Preserve the immutable owner's schema-specific decision, never upgrade it."""
    target = validate_target(target)
    if type(raw) is not dict or raw.get("schema_version") != 1 or raw.get("status") is None:
        return _unknown("authority", target, raw)
    try:
        status = raw["status"]
        request = status.get("request")
        if type(request) is not dict:
            return _base("authority", target, state="partial", code="authority_pending")
        if operation is not None and request["request_id"] != operation["request_id"]:
            return _base("authority", target, state="conflicting", code="binding_mismatch")
        retained = request.get("result")
        if request["state"] != "terminal" or type(retained) is not dict:
            state = "expired" if request["state"] == "retained_without_result" else "partial"
            result = _base("authority", target, state=state, result="pending", code="authority_pending")
            result["request_id"] = request["request_id"]
            return evidence(result)
        from sandbox.hosting.images.activation.models import ActivationResult
        if retained.get('schema_version') == 2:
            from sandbox.hosting.images.activation.v2_repository import validate_result_v2
            checked = validate_result_v2(retained)
        else:
            checked = ActivationResult.from_mapping(retained).as_mapping()
        if checked["request_id"] != request["request_id"]:
            return _base("authority", target, state="conflicting", code="binding_mismatch")
        # Existing status does not expose a terminal observation time or signed
        # source/config binding. Keep this projection partial, even when its
        # owner's terminal decision was success. It remains useful diagnosis.
        result = _base("authority", target, state="partial", result="passed" if checked["ok"] else "failed", code="partial")
        result.update(request_id=checked["request_id"], generation=checked["resulting_generation"],
                      proof_digest=checked.get("observation_digest") or checked["transaction_digest"],
                      references=[{"kind": "activation_transaction", "digest": checked["transaction_digest"]}])
        if "init" in checked["code"] and not checked["ok"]:
            result.update(initializer_result="failed", reason=_reason("initializer_refused"))
        return evidence(result)
    except (ValueError, TypeError, KeyError):
        return _base("authority", target, state="partial", code="partial")


def normalize_job_projection(raw, target, operation=None):
    """Normalize the payload-free job owner read without reconciliation."""
    target = validate_target(target)
    if type(raw) is not dict or raw.get("schema_version") != 1 or raw.get("job") is None:
        return _unknown("job", target, raw)
    try:
        job = raw["job"]
        root_digest = "sha256:" + hashlib.sha256(job["project_root"].encode("utf-8")).hexdigest()
        matches = job["project_identity"] == target["project_identity"] and root_digest == target["project_root_digest"]
        if operation is not None:
            matches = matches and job["job_id"] == operation["job_id"] and job["request_id"] == operation["request_id"]
            expected = operation["requested_outcome"]["application"]["commit"]
            if expected is not None:
                matches = matches and job["source_commit"] == expected
        if not matches:
            return _base("job", target, state="conflicting", code="binding_mismatch")
        lifecycle = job["lifecycle"]
        terminal_success = lifecycle == "succeeded"
        terminal_failure = lifecycle in {"failed", "cancelled", "timed_out", "interrupted"}
        observed = _stamp(job.get("finished_at") or job.get("started_at") or job.get("accepted_at"))
        result = _base("job", target, state="known" if observed else "partial",
                       result="passed" if terminal_success else "failed" if terminal_failure else "pending",
                       code="none" if terminal_success else "failed" if terminal_failure else "authority_pending", observed_at=observed)
        result.update(request_id=job["request_id"], job_id=job["job_id"],
                      application_revision=job.get("source_commit"),
                      references=[{"kind": "job", "identifier": job["job_id"]}])
        return evidence(result)
    except (ValueError, TypeError, KeyError):
        return _base("job", target, state="partial", code="partial")


def _creation_binding(raw, context, target, operation):
    from sandbox.server_config.models import validate_creation_context, validate_creation_receipt
    receipt = validate_creation_receipt(raw)
    context = validate_creation_context(context)
    if operation is None:
        raise DeliveryError("required_evidence_missing")
    if (context["intent_fields"]["delivery_intent_digest"] != operation["intent_digest"]
            or any(context[key] != operation[key] for key in ("operation_id", "request_id", "job_id"))
            or any(receipt[key] != context[key] for key in ("operation_id", "request_id", "job_id", "project_identity", "project_root_digest", "label", "intent_digest"))
            or receipt["label"] != target["label"]):
        raise DeliveryError("binding_mismatch")
    for key in ("instance_id", "instance_incarnation_id"):
        if target[key] is not None and receipt[key] != target[key]:
            raise DeliveryError("binding_mismatch")
    return receipt, context


def normalize_creation_receipt(raw, target, operation=None, *, creation_context=None):
    """Accept an instance-owner validated receipt; relation alone is not success."""
    target = validate_target(target)
    if type(raw) is not dict:
        return _base("creation", target)
    try:
        if creation_context is None and operation is not None:
            creation_context = (operation.get("creation") or {}).get("creation_context")
        if creation_context is None:
            return _base("creation", target, state="partial", code="required_evidence_missing")
        receipt, context = _creation_binding(raw, creation_context, target, operation)
        observed = _stamp(receipt.get("completed_at") or receipt["owner_commit_at"])
        completion = receipt["completion"]
        result = _base("creation", target, state="known" if observed and receipt["relation"] != "unknown" else "partial",
                       result="passed" if completion == "succeeded" else "failed" if completion == "failed" else "pending",
                       code="none" if completion == "succeeded" else "authority_pending", observed_at=observed)
        result.update(operation_id=receipt["operation_id"], request_id=receipt["request_id"], job_id=receipt["job_id"],
                      instance_incarnation_id=receipt["instance_incarnation_id"], proof_digest=canonical_digest(receipt),
                      creation_receipt=receipt, creation_context=context)
        return evidence(result)
    except DeliveryError as exc:
        return _base("creation", target, state="conflicting" if exc.code == "binding_mismatch" else "partial", code=exc.code)
    except (ImportError, ValueError, TypeError, KeyError):
        return _base("creation", target, state="partial", code="partial")


def _join(block, target, operation, key):
    """Fail closed on a changed identity; absent links stay explicitly partial."""
    checked = evidence(block)
    if checked["target_digest"] != target_digest(target):
        return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    if operation is None:
        # Without a frozen requested outcome, stored owner evidence cannot
        # establish a complete delivery for this query.
        if checked["state"] == "known":
            checked.update(state="partial", reason=_reason("partial"))
        return checked
    for name in ("project_identity", "project_root_digest", "remote_name", "machine_identity", "registered_host_digest", "runtime_identity", "environment", "label"):
        if operation["target"][name] is not None and target[name] != operation["target"][name]:
            return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    incomplete = False
    if (target['target_kind'] in {'deploy', 'preview'} and key in {'runtime', 'routes'}
            and checked.get('operation_id') != operation['operation_id']):
        incomplete = True
    for name in ("operation_id", "request_id", "job_id"):
        expected = operation[name]
        actual = checked.get(name)
        if actual is not None and actual != expected:
            return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    # Request is the common owner join. Admission/creation/job proof additionally
    # requires the original job; runtime/edge owners may bind by release instead.
    if operation["request_id"] is not None and checked.get("request_id") is None:
        incomplete = True
    if (key in {"admission", "creation"} or checked["source_kind"] == "job") and operation["job_id"] is not None and checked.get("job_id") is None:
        incomplete = True
    incarnation = operation["target"]["instance_incarnation_id"]
    if incarnation is None and operation["target"]["target_kind"] in {"deploy", "preview"} and key in {"creation", "runtime", "routes", "edge"}:
        creation = operation.get("creation") or {}
        try:
            receipt, _context = _creation_binding(creation.get("creation_receipt"), creation.get("creation_context"), operation["target"], operation)
            if receipt["relation"] == "unknown":
                incomplete = True
            else:
                incarnation = receipt["instance_incarnation_id"]
        except (ImportError, ValueError, TypeError, KeyError):
            incomplete = True
    if target["instance_incarnation_id"] is not None and incarnation is not None and target["instance_incarnation_id"] != incarnation:
        return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    if incarnation is not None and checked.get("instance_incarnation_id") is not None and checked["instance_incarnation_id"] != incarnation:
        return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    if incarnation is None and checked.get("instance_incarnation_id") is not None:
        incomplete = True
    if key in {"creation", "runtime", "routes"} and incarnation is not None and checked.get("instance_incarnation_id") is None:
        incomplete = True
    application = operation["requested_outcome"]["application"]
    actual_revision = checked.get("application_revision")
    expected_revision = application['commit']
    artifact = application.get('source_artifact')
    if (artifact is not None and checked['source_kind'] != 'job'
            and key in {'runtime', 'edge', 'authority'}):
        expected_revision = artifact['revision']
    if actual_revision is not None and expected_revision is not None and actual_revision != expected_revision:
        return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    identity_required = (target['target_kind'] == 'hosted' or any(
        item['kind'] == 'runtime_identity' and item['applicability'] == 'required'
        for item in operation['requested_outcome']['requirements']))
    if key in {"runtime", "authority"} and identity_required and application["commit"] is not None and actual_revision is None:
        incomplete = True
    expected_contract = (operation["requested_outcome"]["route_contract_digest"] if key == "routes"
        else operation["requested_outcome"]["configuration_digest"]
        if key in {"admission", "edge"} or (key == 'runtime' and identity_required) else None)
    if expected_contract is not None:
        if checked.get("contract_digest") is None:
            incomplete = True
        elif checked["contract_digest"] != expected_contract:
            return _base(checked["source_kind"], target, state="conflicting", code="binding_mismatch")
    if incomplete and checked["state"] == "known":
        checked.update(state="partial", reason=_reason("partial"))
    return checked


def _next_action(operation):
    """Offer only an existing owner read, never invent recovery authority."""
    if operation["execution_state"] not in TERMINAL and operation["job_id"] is not None:
        return {"command": "job-status", "selectors": {"job_id": operation["job_id"]}, "reason": _reason("authority_pending")}
    if operation["delivery_succeeded"] or operation["evidence_completeness"] == "complete":
        return None
    target = operation["target"]
    if (operation["kind"] == "immutable_activation" and target["target_kind"] == "hosted"
            and operation["request_id"] is not None):
        return {"command": "host image status", "selectors": {
            "remote": target["remote_name"], "environment": target["environment"],
            "request_id": operation["request_id"]}, "reason": _reason("required_evidence_missing")}
    return None


def _next_action_reason(operation, action):
    """Query-time explanation; retained terminal messages and digests stay intact."""
    if operation["delivery_succeeded"]:
        return {"code": "none", "message": "Delivery succeeded; no continuation is needed."}
    if operation["delivery_state"] == "failed":
        stages = {"admission": "admission", "creation": "instance creation",
                  "instance": "instance creation", "source": "source publication",
                  "initializer": "initialization", "runtime": "runtime verification",
                  "authority": "workload execution", "routes": "route verification",
                  "route": "route verification", "edge": "edge verification",
                  "cleanup": "cleanup"}
        stage = stages.get(operation["failure_stage"])
        message = "Delivery failed" + (" at " + stage if stage else "") + "."
        guidance = (" Read the original owner status; this does not authorize recovery."
                    if action is not None else " No authorized continuation is established.")
        return {"code": "failed", "message": message + guidance}
    if action is not None:
        return action["reason"]
    if operation["execution_state"] not in TERMINAL:
        return {"code": "authority_pending", "message":
                "The original owner has not established completion. No authorized continuation is established."}
    return {"code": "required_evidence_missing", "message":
            "Required evidence remains unavailable. No authorized continuation is established."}


def evaluate_operation(operation):
    """Compute diagnostic fields for writers; does not alter a terminal snapshot."""
    operation = validate_operation(operation)
    blocks = {key: None if operation[key] is None else _join(operation[key], operation["target"], operation, key) for key in EVIDENCE_KEYS}
    required = [item["kind"] for item in operation["requested_outcome"]["requirements"] if item["applicability"] == "required"]
    states = []
    failures = []
    prerequisite = "admission" if operation["kind"] == "hosted_apply" else "creation" if operation["kind"] in {"deploy_exposure", "preview_creation"} else None
    if prerequisite is not None:
        block = blocks[prerequisite]
        states.append(block["state"] if block is not None else "missing")
        if block is None or block["result"] not in {"passed", "failed"}:
            states.append("partial")
        elif block["result"] == "failed":
            failures.append(prerequisite)
    mapping = {"workload": "authority", "initializer": "runtime", "runtime_identity": "runtime", "runtime_health": "runtime", "exposure": "routes", "public_release_identity": "routes", "edge_proof": "edge"}
    for requirement in required:
        key = mapping[requirement]
        block = blocks[key]
        if block is None:
            states.append("missing")
            continue
        states.append(block["state"])
        if block["result"] == "failed":
            failures.append(key)
        if requirement == "initializer":
            if block.get("initializer_result") == "failed":
                failures.append("initializer")
            elif block.get("initializer_result") != "passed":
                states.append("partial")
        elif requirement == "public_release_identity":
            route = block.get("route_observation") or {}
            if route.get("release_identity_state") != "passed":
                states.append("partial")
                if route.get("release_identity_state") == "failed":
                    failures.append("route")
        elif requirement == "exposure":
            route = block.get("route_observation") or {}
            if route.get("result") not in {"verified", "failed"}:
                states.append("partial")
        elif requirement == "runtime_identity":
            application = operation["requested_outcome"]["application"]
            if application["artifact_digest"] is not None and application["artifact_digest"] not in block.get("images", []):
                states.append("partial")
        if block["result"] not in {"passed", "failed"}:
            states.append("partial")
    # A failed authoritative initializer/runtime decision cannot be erased by
    # successful optional route observations or a narrower requirement list.
    for key in ("authority", "runtime"):
        block = blocks[key]
        if block is not None and (block["result"] == "failed" or block.get("initializer_result") == "failed"):
            failures.append("initializer" if block.get("initializer_result") == "failed" else key)
    generations = {block["generation"] for key, block in blocks.items() if key in {"runtime", "routes", "edge", "authority"} and block is not None and block.get("generation") is not None}
    if len(generations) > 1:
        states.append("conflicting")
    completeness = next((state for state in ("conflicting", "missing", "unsupported", "expired", "partial") if state in states), "complete")
    execution = operation["execution_state"]
    failed = bool(failures) or execution in {"failed", "cancelled", "timed_out", "interrupted"}
    succeeded = execution == "succeeded" and not failed and completeness == "complete" and bool(required)
    state = "succeeded" if succeeded else "failed" if failed else "incomplete"
    code = "none" if succeeded else "initializer_refused" if "initializer" in failures else "conflicting" if completeness == "conflicting" else "failed" if failed else "required_evidence_missing"
    projected = dict(operation, delivery_state=state, delivery_succeeded=succeeded,
                     evidence_completeness=completeness)
    return {"delivery_state": state, "delivery_succeeded": succeeded, "evidence_completeness": completeness,
            "failure_stage": "initializer" if "initializer" in failures else failures[0] if failures else operation["failure_stage"],
            "reason": _reason(code), "next_action": _next_action(projected)}


def _empty_history(completeness="missing"):
    return {"completeness": completeness, "retention_policy": dict(RETENTION), "oldest_retained_at": None,
            "omitted": False, "expired": completeness == "expired", "returned_count": 0, "next_cursor": None,
            "limits": {"maximum_limit": 50, "operation_bytes": 128 * 1024, "response_bytes": MAX_RESPONSE_BYTES}, "operations": []}


class DeliveryService:
    def __init__(self, repository, *, target_resolver, owner_readers=None, current_readers=None):
        self.repository = repository
        self.target_resolver = target_resolver
        self.owner_readers = dict(owner_readers or {})
        self.current_readers = dict(current_readers or {})
        for readers in (self.owner_readers, self.current_readers):
            if set(readers) - set(EVIDENCE_KEYS) or any(not callable(reader) for reader in readers.values()):
                raise DeliveryError("delivery_contract_invalid")

    def _observe(self, readers, target, operation):
        if not readers:
            return None
        result = {}
        for key, reader in readers.items():
            try:
                block = reader(target, operation)
                result[key] = _join(block, target, operation, key) if block is not None else _base(key, target)
            except Exception:
                # Owner/network exceptions may contain URLs, environment or
                # private paths. Never serialize their arbitrary text.
                result[key] = _base(key, target, state="partial", code="partial")
        return result

    def inspect(self, *, project_dir, remote, environment=None, label=None,
                operation_id=None, request_id=None, observe=False, limit=10, cursor=None):
        if type(project_dir) is not str or not project_dir or "\x00" in project_dir or len(project_dir.encode("utf-8")) > 4096:
            raise DeliveryError("delivery_contract_invalid")
        identifier(remote)
        if (environment is None) == (label is None) or (operation_id is not None and request_id is not None):
            raise DeliveryError("delivery_contract_invalid")
        identifier(environment if environment is not None else label)
        if operation_id is not None:
            identifier(operation_id)
        if request_id is not None:
            request_identifier(request_id)
        if operation_id is not None:
            try:
                if str(uuid.UUID(operation_id)) != operation_id:
                    raise ValueError()
            except ValueError:
                raise DeliveryError("delivery_contract_invalid") from None
        if type(observe) is not bool or type(limit) is not int or not 1 <= limit <= 50 or (cursor is not None and (observe or operation_id is not None or request_id is not None)):
            raise DeliveryError("delivery_contract_invalid")
        target = validate_target(self.target_resolver(project_dir=project_dir, remote=remote, environment=environment, label=label))
        if target["remote_name"] != remote or target["environment"] != environment or target["label"] != label:
            raise DeliveryError("binding_mismatch")
        scope = {key: target[key] for key in ("project_identity", "project_root_digest", "remote_name", "target_kind", "environment", "label")}
        scope.update(target_digest=target_digest(target), operation_id=operation_id, request_id=request_id)
        result = {"schema_version": 1, "ok": True, "error": None, "query_scope": scope,
                  "recorded_at": now(), "observation_mode": "current_read_only" if observe else "recorded_only",
                  "latest_attempt": None, "latest_retained_complete_success": None, "selected_operation": None,
                  "recorded_source_evidence": None, "current_observation": None, "history": _empty_history(), "next_action": None}
        selected = None
        original_id = None
        try:
            history = self.repository.history_scope(request_scope(target), limit=limit, cursor=cursor)
            result["latest_attempt"] = history.pop("latest_attempt")
            result["latest_retained_complete_success"] = history.pop("latest_retained_complete_success")
            result["history"] = history
            if operation_id is not None or request_id is not None:
                lookup = self.repository.lookup_operation(request_scope(target), operation_id) if operation_id is not None else self.repository.lookup_request(request_scope(target), request_id)
                selected = lookup["operation"]
                original_id = lookup.get('operation_id')
                if lookup["status"] == "delivery_request_expired":
                    result["error"] = _reason("delivery_request_expired")
                    result["history"].update(completeness="expired", expired=True)
                elif selected is None:
                    result["error"] = _reason("missing")
                if selected is not None:
                    # Stable request lookup can survive incarnation drift, but
                    # it must never cross a project root or logical target.
                    old = selected["target"]
                    if any(old[key] != target[key] for key in ("project_identity", "project_root_digest", "remote_name", "target_kind", "environment", "label")):
                        raise DeliveryError("binding_mismatch")
                    result["selected_operation"] = selected
            elif result["latest_attempt"] is not None:
                selected = self.repository.get(result["latest_attempt"]["operation_id"])
        except DeliveryError as exc:
            code = exc.code if exc.code in REASONS else "delivery_store_unavailable"
            result["error"] = _reason(code)
            result["history"] = _empty_history("unsupported" if code == "unsupported_capability" else "expired" if code == "delivery_cursor_expired" else "partial")
            if code in {"delivery_cursor_invalid", "delivery_cursor_expired", "binding_mismatch", "delivery_contract_invalid"}:
                result["ok"] = False
                selected = None
                result["selected_operation"] = None
                return bounded_projection(result)
        # For a retained old incarnation, recorded projections are read using
        # its exact target; current observations always use the selected target.
        recorded_target = selected["target"] if selected is not None else target
        result["recorded_source_evidence"] = self._observe(self.owner_readers, recorded_target, selected)
        if observe:
            result["current_observation"] = self._observe(self.current_readers, recorded_target, selected)
            if result["current_observation"] is None:
                result["current_observation"] = {"runtime": _base("runtime", target, state="unsupported", code="unsupported")}
        if selected is not None:
            result["next_action"] = _next_action(selected)
            result["next_action_reason"] = _next_action_reason(selected, result["next_action"])
            original_id = selected['operation_id']
        if original_id is not None:
            try:
                from .trace_models import TraceQueryBudget
                import time
                related = self.repository.read_related_recoveries(
                    original_id, request_scope(recorded_target),
                    budget=TraceQueryBudget(time.monotonic() + 5))
                children = []
                for child in related['recoveries']:
                    if any(child['target'][key] != recorded_target[key] for key in
                           ('project_identity', 'project_root_digest', 'target_kind',
                            'remote_name', 'environment', 'label')):
                        related.update(state='partial', omitted=related['omitted'] + 1)
                        continue
                    children.append(operation_summary(child))
                result['recovery_operations'] = {
                    'completeness': related['state'], 'omitted': related['omitted'],
                    'operations': children}
            except Exception:
                result['recovery_operations'] = {'completeness': 'partial', 'omitted': 0, 'operations': []}
        if result["history"]["completeness"] == "missing" and result["recorded_source_evidence"]:
            result["history"]["completeness"] = "partial"
        return bounded_projection(result)


def bounded_projection(result):
    """Budget optional detail before the common closed/redacted serializer."""
    result = json.loads(encoded(result))
    if len(encoded(result)) > MAX_RESPONSE_BYTES and result.get('recovery_operations'):
        children = result['recovery_operations']
        children.update(completeness='partial', omitted=children['omitted'] + len(children['operations']), operations=[])
    for key in ("current_observation", "recorded_source_evidence", "selected_operation"):
        if len(encoded(result)) <= MAX_RESPONSE_BYTES:
            break
        if result.get(key) is not None:
            result[key] = None
            result["error"] = _reason("bounds")
            result["history"].update(completeness="partial", omitted=True)
    return json.loads(serialize_projection(result))


def inspect_with_factory(factory, **selectors):
    """Shared CLI/MCP adapter boundary; never echo an arbitrary exception."""
    try:
        if factory is None:
            raise DeliveryError("unsupported_capability")
        return json.loads(serialize_projection(factory().inspect(**selectors)))
    except Exception as exc:
        code = exc.code if isinstance(exc, DeliveryError) and exc.code in REASONS else "delivery_store_unavailable"
        return {"schema_version": 1, "ok": False, "error": _reason(code)}

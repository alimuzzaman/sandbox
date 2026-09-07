"""Validate retained execution and pre-forward compatibility as one binding."""

from .models import ActivationContractError, _closed


def validate_execution_evidence(value, *, subject):
    from .execution_runner import execution_step_subject
    from .execution_state import ExecutionProgressV2
    from .v2_models import PrivateComposeInputSnapshotV2, RollbackCompatibilityGrantV2
    raw = _closed(value, frozenset({"compose_snapshot", "compatibility_grant", "progress"}))
    snapshot = PrivateComposeInputSnapshotV2.from_mapping(raw["compose_snapshot"])
    grant = RollbackCompatibilityGrantV2.from_mapping(raw["compatibility_grant"])
    progress = ExecutionProgressV2.from_mapping(raw["progress"])
    contract = snapshot.init_contract
    if (contract is None or contract.graph != progress.graph or not progress.complete
            or progress.request_digest != subject["request_digest"]
            or progress.snapshot_digest != snapshot.snapshot_digest
            or snapshot.snapshot_digest != subject["compose_snapshot_digest"]
            or snapshot.configuration_digest != subject["configuration_digest"]
            or snapshot.target != subject["target"] or grant.target != subject["target"]
            or snapshot.plan_set_digest != subject["plan_set_digest"]
            or grant.compose_snapshot_digest != snapshot.snapshot_digest
            or grant.expected_generation != subject["generation"] - 1
            or grant.prior_generation_digest != subject["rollback_from_generation_digest"]
            or grant.candidate_plan_set_digest != subject["plan_set_digest"]
            or grant.candidate_proof_set_digest != subject["proof_set_digest"]
            or grant.policy_digest != subject["policy_digest"]
            or snapshot.selected_services != tuple(sorted(row["service"] for row in subject["service_image_bindings"]))):
        raise ActivationContractError("init_mismatch")
    images = {row["name"]: row for row in subject["images"]}
    for declaration in contract.declarations:
        image = images.get(declaration.image)
        if (image is None or image["image_ref"] != declaration.image_ref
                or image["config_digest"] != declaration.config_digest):
            raise ActivationContractError("init_mismatch")
    for event in progress.events:
        expected = execution_step_subject(progress=progress, contract=contract, index=event["step_index"])
        if event["subject_digest"] != expected["subject_digest"]:
            raise ActivationContractError("init_mismatch")
    return {"compose_snapshot": snapshot.as_mapping(), "compatibility_grant": grant.as_mapping(),
            "progress": progress.as_mapping()}

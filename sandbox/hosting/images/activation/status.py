"""Bounded public activation diagnostics; no runtime or authority access."""

from .repository import decode_activation_state


def read_delivery_status(repository, target_key: str, request_id: str | None = None) -> dict:
    """Read through the shared-state owner without a mutation-lock snapshot.

    The existing codec and status serializer retain schema-specific decisions.
    This adapter never promotes a route observation to activation success.
    """
    nested = repository.read_activation_nested(target_key)
    if nested is None:
        return {"schema_version": 1, "state": "missing", "reason": "activation_missing",
                "status": None}
    return {"schema_version": 1, "state": "known", "reason": None,
            "status": activation_status(nested, request_id)}


def activation_status(value: object, request_id: str | None = None) -> dict:
    state = decode_activation_state(value)
    active = state["active"]
    from .settlement_forward import required_predecessor
    predecessor = required_predecessor(state)
    result = {
        "schema_version": 1, "ok": True, "code": "observed",
        "state_schema_version": state["schema_version"],
        "generation": state["generation"],
        "active": None if active is None else {key: active[key] for key in (
            "schema_version", "request_id", "request_digest", "transaction_digest",
            "operation", "phase", "effect_entered")},
        "current_generation_digest": (state["current"] or {}).get("generation_digest"),
        "previous_generation_digest": (state["previous"] or {}).get("generation_digest"),
        "retained_result_count": len(state["results"]),
        "retained_recovery_count": len(state["recovery_results"]),
    }
    if "settlements" in state:
        result.update(retained_settlement_count=len(state["settlements"]),
            required_settlement_predecessor=None if predecessor is None else
                predecessor["terminal_receipt"]["terminal_digest"])
    if request_id is not None:
        from .models import _text
        _text(request_id, identity=True)
        retained = state["results"].get(request_id)
        if retained is not None:
            result["request"] = {"request_id": request_id, "state": "terminal", "result": retained["result"]}
        elif active is not None and active["request_id"] == request_id:
            result["request"] = {"request_id": request_id, "state": "active", "result": None}
        else:
            result["request"] = {"request_id": request_id,
                "state": "retained_without_result" if request_id in state["tombstones"] else "unknown", "result": None}
    return result


def read_trace_activation_evidence(request_id, scope, deadline, *, repository=None):
    """Read original native proof without resolving targets or observing effects.

    ``target_key`` is supplied by the composition owner, never a public locator.
    Native generations retain no source revision or wall-clock observation time;
    those dimensions remain missing until an independently exact owner joins them.
    """
    from sandbox.hosting.recovery.repository import RecoveryRepository
    repository = repository or RecoveryRepository()
    return read_trace_status(repository, scope['target_key'], request_id,
                             budget=deadline)


def read_trace_status(repository, target_key, request_id, *, budget):
    from .models import _text
    from .repository import ActivationRepositoryError
    from sandbox.hosting.recovery.models import canonical_digest
    _text(request_id, identity=True)
    empty = {'schema_version': 1, 'state': 'missing', 'reason': 'activation_missing',
             'request_id': request_id, 'request_digest': None, 'result': None,
             'target': None, 'detail': None, 'generation_reference': None}
    budget.check()
    try:
        state = repository.read_trace_activation_nested(target_key, budget=budget)
    except (ValueError, TypeError, KeyError, OSError, ActivationRepositoryError):
        budget.check()
        return {**empty, 'state': 'partial', 'reason': 'owner_unavailable'}
    if state is None:
        return empty
    retained = state['results'].get(request_id)
    active = state['active']
    result = retained['result'] if retained is not None else None
    if result is None and (active is None or active['request_id'] != request_id):
        return {**empty, 'state': 'partial' if request_id in state['tombstones'] else 'missing',
                'reason': 'history_unavailable'}
    request_digest = result['request_digest'] if result is not None else active['request_digest']
    generation = None
    if result is not None and result.get('generation_digest') is not None:
        for candidate in (state['current'], state['previous']):
            if (candidate is not None and candidate['generation_digest'] == result['generation_digest']
                    and candidate['request_digest'] == request_digest
                    and candidate['generation'] == result['resulting_generation']):
                generation = candidate
                break
    # An active candidate has not committed and cannot stand in for a generation.
    target = generation.get('target') if generation is not None else (
        active.get('target') if active is not None and active['request_id'] == request_id else None)
    target_id = canonical_digest(target) if target is not None else None
    def block(payload, *, known=False, passed=False, applicable='required', code=None):
        return {'source_kind': 'activation', 'observed_at': None,
                'target_digest': target_id, 'applicability': applicable,
                'state': 'known' if known else 'partial',
                'result': 'passed' if passed else 'unknown',
                'reason': {'code': code or ('known' if known else 'required_evidence_missing'),
                           'message': 'Retained native evidence.' if known else 'Native evidence does not retain this dimension.'},
                **payload}
    detail = {
        'source': block({'requested_revision': None, 'observed_revision': None,
                         'control_revision': None, 'source_artifact': None}),
        'artifact': block({'policy': None, 'receipt_digest': None, 'plan_digest': None,
                          'proof_digest': retained['proof_digest'] if retained else None,
                          'manifest_digests': []}),
        'target': block({'registered_host_digest': None, 'environment': None,
                        'incarnation_id': None}),
        'generation': block({'generation_id': None, 'digest': None}),
        'configuration': block({'digest': None}),
        'initializer': block({'status': None, 'receipt_digest': None}),
        'runtime': block({'source_revision': None, 'image_digests': [], 'health': None,
                         'verification_digest': None}),
        'public_verification': block({'hosts': [], 'edge_state': None, 'edge_proof_digest': None}),
        'job': block({'submission_digest': None, 'started_at': None, 'finished_at': None,
                      'exit_code': None, 'output_complete': None}, applicable='not_applicable'),
    }
    reference = None
    if generation is not None:
        v2 = generation.get('schema_version') == 2
        native_images = generation['images'] if v2 else [generation['image']]
        overflow = len(native_images) > 32
        # The owner codec has already validated repository-qualified references.
        manifests = [] if overflow else sorted(set(
            image['image_ref' if v2 else 'repository_qualified_digest'].rsplit('@', 1)[1]
            for image in native_images))
        services = generation['service_projection']
        runtime_images = [] if overflow else sorted(set(
            row['repository_digest'].rsplit('@', 1)[1] for row in services))
        reference = {'generation_digest': generation['generation_digest'],
                     'proof_digest': generation['proof_set_digest' if v2 else 'proof_digest']}
        detail['artifact'].update(plan_digest=generation['plan_set_digest' if v2 else 'plan_digest'],
                                  proof_digest=reference['proof_digest'], manifest_digests=manifests)
        if overflow:
            detail['artifact']['reason'] = {'code': 'bounds', 'message': 'Native image set exceeds 32; generation proof retained.'}
        detail['generation'] = block({'generation_id': generation['generation'],
            'digest': generation['generation_digest']}, known=True, passed=True)
        detail['configuration'] = block({'digest': generation['configuration_digest']}, known=True, passed=True)
        detail['runtime'].update(image_digests=runtime_images, health='healthy',
                                 verification_digest=generation['running_observation_digest'])
        if overflow:
            detail['runtime']['reason'] = {'code': 'bounds', 'message': 'Native image set exceeds 32; generation proof retained.'}
        # Native health/image proof does not invent a source revision.
        execution = generation.get('execution_evidence')
        if v2 and execution is not None:
            detail['initializer'] = block({'status': 'passed',
                'receipt_digest': execution['progress']['progress_digest']}, known=True, passed=True)
        elif not v2:
            receipts = generation['init_receipt_digests']
            detail['initializer'] = block({'status': 'passed' if receipts else 'not_applicable',
                'receipt_digest': receipts[0] if len(receipts) == 1 else None},
                known=len(receipts) <= 1, passed=bool(receipts),
                applicable='required' if receipts else 'not_applicable')
        edge_digest = generation['edge_receipt']['receipt_digest'] if v2 else generation['edge_receipt_digest']
        detail['public_verification'].update(edge_state='passed', edge_proof_digest=edge_digest)
        # Aggregate native edge proof has no per-host application route rows.
    budget.check()
    return {**empty, 'state': 'partial', 'reason': 'required_evidence_missing',
            'request_digest': request_digest, 'result': result, 'target': target,
            'detail': detail, 'generation_reference': reference}

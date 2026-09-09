"""Explicit executing-controller delivery capability contracts."""
from __future__ import annotations

# Each published capability requires its concrete owner ports. Import/contract
# failure disables admission; installed version metadata alone is insufficient.
DELIVERY_CAPABILITIES = (
    ('deployment_trace_v1', 1, (
        ('sandbox.delivery.trace_repository', 'TraceRepository', ('start', 'record', 'read', 'read_owner', 'record_owner')),
        ('sandbox.delivery.trace_service', 'TraceService', ('capabilities', 'inspect', 'owner_status')),
    )),
    ('lenzora_hosted_trace_v1', 1, (
        ('sandbox.delivery.producers.lenzora', 'decode_owner_projection', ()),
    )),
    ('delivery_outcomes_v1', 1, (
        ('sandbox.delivery.repository', 'DeliveryRepository', ('reserve_request', 'write_operation', 'history_scope')),
        ('sandbox.delivery.service', 'DeliveryService', ('inspect',)),
    )),
    ('ordinary_recovery_admission_v1', 1, (
        ('sandbox.delivery.admission', 'validate_durable_context', ()),
        ('sandbox.resources.context', 'authenticated_target_identity', ()),
        ('sandbox.hosting.recovery.repository', 'RecoveryRepository', ('read_committed_admission',)),
    )),
    ('ordinary_source_artifact_v1', 1, (
        ('sandbox.hosting.recovery.models', 'validate_source_artifact', ()),
        ('sandbox.hosting.recovery.repository', 'RecoveryRepository', ('read_committed_admission',)),
    )),
    ('instance_creation_receipt_v1', 1, (
        ('sandbox.server_config.models', 'validate_creation_context', ()),
        ('sandbox.server_config.models', 'validate_creation_receipt', ()),
        ('sandbox.server_config.creation_requests', 'lookup_creation_receipt', ()),
    )),
    ('delivery_route_verification_v1', 1, (
        ('sandbox.delivery.routes', 'prepare_route_verification', ()),
        ('sandbox.delivery.routes', 'observe_routes', ()),
    )),
)


def available_delivery_capabilities():
    import importlib
    result = {}
    for name, version, ports in DELIVERY_CAPABILITIES:
        try:
            for module, attribute, methods in ports:
                port = getattr(importlib.import_module(module), attribute)
                if not callable(port) or any(not callable(getattr(port, method, None)) for method in methods):
                    raise AttributeError('unsupported owner port')
        except (ImportError, AttributeError):
            continue
        result[name] = version
    return result

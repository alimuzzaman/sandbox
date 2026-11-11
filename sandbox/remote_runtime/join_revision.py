"""Explicit authenticated compatible revision-cache adoption, never pin breaking."""
import time

from sandbox.resources.host_memory.models import HEX24
from sandbox.resources.host_memory.remote import RemoteProtocolError
from sandbox.remote_runtime.join_identity import discover_join_identity
from sandbox.remote_runtime.verdict import COMPATIBLE, strict_requested


def adopt_compatible_join_revision(name, record, request, observe, *, budget_seconds=15):
    from sandbox.core._remote import put_remote
    from sandbox.resources.host_memory.remote import validate_request
    validate_request({'action': 'host_memory_status', 'remote_name': name, 'budget_seconds': budget_seconds})
    service, machine = record.get('mcp_service'), record.get('control_identity')
    if (record.get('joined') is not True or record.get('control_access') != 'tailnet'
            or record.get('control_transport') != 'tailscale'
            or not isinstance(service, dict) or not isinstance(machine, dict)
            or set(machine) != {'schema_version', 'target_identity'}
            or type(machine.get('schema_version')) is not int or machine['schema_version'] != 1
            or any(not isinstance(value, str) or not HEX24.fullmatch(value) for value in
                (service.get('ownership_marker'), service.get('runtime_revision'), machine.get('target_identity')))):
        raise RemoteProtocolError('remote_service_ownership_unknown', 'existing owner and machine pins are required for revision adoption')
    deadline = time.monotonic() + budget_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise RemoteProtocolError('remote_budget_exhausted', 'revision observation exhausted its original budget')
        return value
    evidence = discover_join_identity(name, record,
        lambda selected, payload: request(selected, payload, timeout_seconds=remaining()),
        budget_seconds=remaining(), allow_revision_cache=True)
    changed = evidence['mcp_service']['runtime_revision'] != service['runtime_revision']
    if changed:
        if strict_requested():
            raise RemoteProtocolError('remote_runtime_revision_mismatch',
                'strict runtime bindings cannot be replaced by compatible cache adoption')
        status = observe(record, timeout_seconds=remaining())
        verdict = status.get('compatibility') if isinstance(status, dict) else None
        protocol = status.get('control_protocol') if isinstance(status, dict) else None
        if (not isinstance(verdict, dict) or verdict.get('state') != COMPATIBLE
                or verdict.get('ok') is not True or verdict.get('strict') is True
                or not isinstance(protocol, dict) or not isinstance(protocol.get('installed'), dict)
                or status.get('installed_runtime_revision') != evidence['mcp_service']['runtime_revision']):
            raise RemoteProtocolError('remote_runtime_revision_mismatch',
                'the actual selected unit did not prove this observed revision serves the controller protocol')
        put_remote(name, _expected=record, _lock_timeout=remaining(), **evidence)
    return {**evidence, 'revision_cache_changed': changed}

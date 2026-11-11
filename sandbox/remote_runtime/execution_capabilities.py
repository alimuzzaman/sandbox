"""Adopt actual installed CLI contracts without granting an operator role."""
import re


class ExecutionCapabilityUnavailable(RuntimeError):
    code = 'remote_execution_capability_unavailable'


def adopt_execution_capabilities(name, record, request):
    """Read supported discovery, preserve pins and CAS only local metadata."""
    if record.get('joined') is not True or record.get('control_transport') != 'tailscale':
        return record
    try:
        observed = request(['job-capabilities'])
    except Exception:
        raise ExecutionCapabilityUnavailable(
            'installed job capability discovery is unavailable; this is not denied owner access') from None
    service = record.get('mcp_service') or {}
    identity = record.get('control_identity') or {}
    if not isinstance(service, dict) or not isinstance(identity, dict):
        raise ExecutionCapabilityUnavailable('registered execution identity is malformed')
    if not isinstance(observed, dict) or type(observed.get('schema_version')) is not int or observed['schema_version'] != 1:
        raise ExecutionCapabilityUnavailable('installed job capability schema is unavailable')
    for field, expected in (('ownership_marker', service.get('ownership_marker')),
                            ('runtime_revision', service.get('runtime_revision')),
                            ('target_identity', identity.get('target_identity'))):
        value = observed.get(field)
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{24}', expected) or value != expected:
            raise ExecutionCapabilityUnavailable('installed job service identity changed or is unavailable')
    values = observed.get('capabilities')
    from sandbox.jobs.acceptance import CONTRACT as ACCEPTANCE_CONTRACT
    known = {'job.exec', 'job.execution-policy.v1', ACCEPTANCE_CONTRACT, 'job.server-cli.v1', 'job.source-content.v1'}
    if (observed.get('ok') is not True or not isinstance(values, list)
            or any(not isinstance(value, str) or value not in known for value in values)
            or len(set(values)) != len(values)
            or not {'job.exec', 'job.execution-policy.v1'}.issubset(values)):
        raise ExecutionCapabilityUnavailable('installed job contracts are unavailable')
    old = record.get('capabilities', [])
    if not isinstance(old, list) or any(not isinstance(value, str) for value in old):
        raise ExecutionCapabilityUnavailable('registered job capability metadata is malformed')
    # Current discovery replaces known component claims. A prior installation
    # must not lend an absent contract to a downgraded/replaced service.
    capabilities = sorted((set(old) - known) | set(values))
    if capabilities == old:
        return record
    from sandbox.core._remote import put_remote
    saved = put_remote(name, _expected=record, capabilities=capabilities)
    return {**saved, '_remote_name': name}

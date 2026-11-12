"""Explicit local transition of a pinned owner registration to keyless Tailnet."""
import ipaddress
import re
import time

from sandbox.resources.host_memory.models import HEX24
from sandbox.remote_runtime.join_identity import discover_join_identity
from sandbox.remote_runtime.verdict import COMPATIBLE, strict_requested


def adopt_owner_tailnet(name, record, *, request, observe, connect, profile,
                       adopt_revision=False):
    from sandbox.core import _remote
    service = record.get('mcp_service')
    machine_pin = record.get('control_identity')
    historical = record.get('host_identity_pin')
    if (record.get('control_transport') != 'tailscale'
            or not isinstance(service, dict)
            or any(not isinstance(service.get(k), str) or not HEX24.fullmatch(service[k])
                   for k in ('ownership_marker', 'runtime_revision'))):
        raise ValueError('tailnet adoption requires an existing pinned Tailscale service')
    if machine_pin is None and 'control_identity' not in record:
        machine = historical.get('machine_identity') if isinstance(historical, dict) else None
        if not isinstance(machine, str) or not HEX24.fullmatch(machine):
            raise ValueError('tailnet adoption requires a saved machine identity')
        machine_pin = {'schema_version': 1, 'target_identity': machine}
    if (not isinstance(machine_pin, dict)
            or set(machine_pin) != {'schema_version', 'target_identity'}
            or type(machine_pin['schema_version']) is not int or machine_pin['schema_version'] != 1
            or not isinstance(machine_pin['target_identity'], str)
            or not HEX24.fullmatch(machine_pin['target_identity'])):
        raise ValueError('saved machine identity is invalid')
    if historical is not None and (not isinstance(historical, dict)
            or historical.get('machine_identity') != machine_pin['target_identity']):
        raise ValueError('saved machine identities disagree')
    address = ipaddress.ip_address(record.get('tailscale_host'))
    networks = (ipaddress.ip_network('100.64.0.0/10'),
                ipaddress.ip_network('fd7a:115c:a1e0::/48'))
    if not any(address.version == net.version and address in net for net in networks):
        raise ValueError('a saved numeric Tailscale address is required')
    parts = _remote.remote_ssh_parts(record)
    user, separator, _host = parts['target'].rpartition('@')
    if (not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*[$]?', user)
            or parts.get('port') not in (None, 22)):
        raise ValueError('Tailnet adoption preserves an explicit Linux user on port 22')
    port = record.get('mcp_port', service.get('port'))
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('the saved control port is invalid')
    host = '[' + str(address) + ']' if address.version == 6 else str(address)
    candidate = {**record, 'joined': True, 'control_access': 'tailnet',
        'ssh': user + '@' + host, 'tailscale_host': str(address), 'mcp_port': port,
        'control_url': f'http://{host}:{port}', 'control_identity': machine_pin}
    deadline = time.monotonic() + 90

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ValueError('Tailnet adoption observation budget expired')
        return value

    evidence = discover_join_identity(name, candidate,
        lambda selected, payload: request(selected, payload, timeout_seconds=min(15, remaining())),
        budget_seconds=min(15, remaining()), allow_revision_cache=adopt_revision)
    changed = evidence['mcp_service']['runtime_revision'] != service['runtime_revision']
    if changed and (not adopt_revision or strict_requested()):
        raise ValueError('revision change requires explicit compatible adoption without strict pins')
    observed = {**candidate, **evidence}
    if connect(observed) != 'ok':
        raise ValueError('keyless Tailnet SSH was not admitted; registration unchanged')
    evidence.update(profile(observed, evidence, record.get('front_door')))
    status = observe(observed, timeout_seconds=min(15, remaining()))
    if (not isinstance(status, dict) or status.get('ownership') != 'proven'
            or status.get('authenticated') is not True
            or status.get('probe_state') != 'complete'
            or status.get('installed_runtime_revision') != evidence['mcp_service']['runtime_revision']):
        raise ValueError('the selected Tailnet owner unit is not proven')
    if changed:
        verdict, protocol = status.get('compatibility'), status.get('control_protocol')
        if (not isinstance(verdict, dict) or verdict.get('state') != COMPATIBLE
                or verdict.get('ok') is not True or verdict.get('strict') is True
                or not isinstance(protocol, dict) or not isinstance(protocol.get('installed'), dict)):
            raise ValueError('the actual selected unit did not prove revision compatibility')
    updates = {key: candidate[key] for key in ('joined', 'control_access', 'ssh',
        'tailscale_host', 'mcp_port', 'control_url')}
    updates.update(evidence)
    _remote.put_remote(name, _expected=record, _lock_timeout=min(15, remaining()), **updates)
    return {**updates, 'revision_cache_changed': changed}

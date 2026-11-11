"""Explicit metadata-only adoption of an existing joined nginx front door."""
import re

from sandbox.hosting.front_door import FrontDoorError

MACHINE_PROGRAM = (
    "import hashlib,pathlib,re; m=pathlib.Path('/etc/machine-id').read_text().strip().lower(); "
    "assert re.fullmatch('[0-9a-f]{32}',m); "
    "print(hashlib.sha256(b'sandbox-host-machine-id-v1\\0'+m.encode('ascii')).hexdigest()[:24])"
)


def adopt_join_profile(record, evidence, requested, *, machine_probe, nginx_probe):
    if requested != 'nginx':
        raise FrontDoorError('joined_profile_unsupported', 'joined profile adoption supports explicit nginx only')
    if record.get('front_door') not in (None, 'nginx'):
        raise FrontDoorError('joined_profile_mismatch', 'an existing front-door pin cannot be replaced')
    observed = machine_probe(record)
    expected = evidence['control_identity']['target_identity']
    if not isinstance(observed, str) or not re.fullmatch(r'[0-9a-f]{24}', observed) or observed != expected:
        raise FrontDoorError('joined_profile_identity_mismatch', 'SSH and admitted control service identify different machines')
    facts = nginx_probe(record)
    if not isinstance(facts, dict):
        raise FrontDoorError('joined_profile_unavailable', 'nginx metadata probe is unavailable')
    caddy = facts.get('sandbox_caddy') or {}
    listeners = facts.get('listeners', [])
    if (not isinstance(caddy, dict) or not isinstance(listeners, list)
            or any(not isinstance(row, dict) or not isinstance(row.get('processes', []), list)
                   for row in listeners)):
        raise FrontDoorError('joined_profile_unavailable', 'nginx metadata shape is invalid')
    if (facts.get('nginx_installed') is not True or facts.get('active') is not True
            or facts.get('config_valid') is not True or facts.get('includes_conf_d') is not True
            or caddy.get('observed') is not True or facts.get('listeners_observed') is not True
            or caddy.get('active') is not False
            or any(not row.get('processes') or set(row['processes']) - {'nginx'} for row in listeners)):
        raise FrontDoorError('joined_profile_unproven', 'active valid incumbent nginx without active Caddy must be proven')
    return {'front_door': 'nginx'}

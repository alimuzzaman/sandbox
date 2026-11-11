"""Installed owner target metadata. These dictionaries never authorize execution."""
from __future__ import annotations

import re
import time

from sandbox.application.server_cli import ServerCLIUnavailable

CONTRACT = 'host.target-inspect.v1'


def installed_inspector():
    try:
        from sandbox.hosting.server_target_selection import inspect_server_exec_target
    except ImportError:
        raise ServerCLIUnavailable('installed owner target inspector is not assembled') from None
    if not callable(inspect_server_exec_target):
        raise ServerCLIUnavailable('installed owner target inspector is not assembled')
    return inspect_server_exec_target


def _text(value, limit=4096):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(ch) < 32 for ch in value)


def validate_selector_request(origin_remote_name, *, target=None, name=None, environment=None):
    if (not isinstance(origin_remote_name, str)
            or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', origin_remote_name) is None
            or bool(target) == bool(name)
            or (target is not None and not _text(target, 256))
            or (name is not None and (not isinstance(name, str) or re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', name) is None))
            or (environment is not None and not _text(environment, 128))):
        raise ServerCLIUnavailable('target selector is invalid')


def validate_target_snapshot(value, *, owner, origin_remote_name):
    from sandbox.jobs.server_cli_retained import target_snapshot
    try:
        target_snapshot(value)
    except (ValueError, TypeError):
        raise ServerCLIUnavailable('owner target metadata schema is invalid') from None
    if (value.get('origin_remote_name') != origin_remote_name
            or any(value.get(field) != getattr(owner, field) for field in
                   ('target_identity', 'ownership_marker', 'runtime_revision'))):
        raise ServerCLIUnavailable('owner target metadata identity changed or is unavailable')
    return value


def inspect_current_server_target(*, origin_remote_name, target=None, name=None, environment=None):
    from sandbox.application.server_cli import read_current_server_owner
    validate_selector_request(origin_remote_name, target=target, name=name, environment=environment)
    owner = read_current_server_owner()
    value = installed_inspector()(origin_remote_name=origin_remote_name,
        target=target, name=name, environment=environment)
    if read_current_server_owner() != owner:
        raise ServerCLIUnavailable('installed receiver changed during target observation')
    return {'ok': True, **validate_target_snapshot(value, owner=owner, origin_remote_name=origin_remote_name)}


def observe_server_cli_target(remote_name, *, target=None, name=None, environment=None, budget_seconds=45):
    """No local checkout, staging, secret preflight, job or execution grant."""
    import math
    from types import SimpleNamespace
    from sandbox.core import _remote
    from sandbox.resources.context import authenticated_target_identity
    from sandbox.transports.remote_jobs import RemoteJobTransport
    validate_selector_request(remote_name, target=target, name=name, environment=environment)
    if (isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float))
            or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 300):
        raise ServerCLIUnavailable('owner target observation budget is invalid')
    deadline = time.monotonic() + budget_seconds
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise ServerCLIUnavailable('owner target observation budget expired')
        return left
    identity = authenticated_target_identity(remote_name, budget_seconds=remaining())
    record = _remote.get_remote(remote_name, budget_seconds=remaining())
    if (not isinstance(record, dict) or record.get('joined') is not True
            or record.get('control_transport') != 'tailscale'
            or (record.get('control_identity') or {}).get('target_identity') != identity['target_identity']):
        raise ServerCLIUnavailable('joined owner target identity is unavailable')
    service = record.get('mcp_service') or {}
    expected = SimpleNamespace(target_identity=identity['target_identity'],
        ownership_marker=service.get('ownership_marker'), runtime_revision=service.get('runtime_revision'))
    argv = ['job-capabilities', '--inspect-host-target', '--origin-remote-name', remote_name,
            '--target' if target else '--name', target or name]
    if environment is not None:
        argv.extend(['--environment', environment])
    transport = RemoteJobTransport(deploy=None, ssh_run=_remote.ssh_run,
        remote_lookup=lambda selected: record if selected == remote_name else None,
        remote_sb_path=_remote.remote_sb_path)
    result = transport.control(remote_name, argv, timeout=remaining())
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise ServerCLIUnavailable('installed owner target observation is unavailable')
    snapshot = {key: value for key, value in result.items() if key != 'ok'}
    validate_target_snapshot(snapshot, owner=expected, origin_remote_name=remote_name)
    if ((name is not None and snapshot['name'] != name)
            or (environment is not None and snapshot['environment'] != environment)
            or (target is not None and target != snapshot['project'] + '/' + snapshot['environment'])):
        raise ServerCLIUnavailable('installed owner target selector changed')
    remaining()
    return snapshot

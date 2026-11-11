"""Automatic local repair of missing joined metadata, never a new role grant."""
import math
import numbers
import shlex
import subprocess
import time

from .join_identity import discover_join_identity
from .join_profile import MACHINE_PROGRAM, adopt_join_profile
from sandbox.hosting.front_door import FrontDoorError
from sandbox.resources.host_memory.remote import RemoteProtocolError


def joined(record):
    return isinstance(record, dict) and record.get('joined') is True and record.get('control_access') == 'tailnet'


def needs_adoption(record):
    if not joined(record):
        return False
    service = record.get('mcp_service')
    return (not isinstance(service, dict) or not all(key in service for key in ('ownership_marker', 'runtime_revision'))
            or 'control_identity' not in record or record.get('front_door') is None)


def adopt_missing(name, record, *, budget_seconds=15):
    """Called at ordinary registration lookup, with one bounded CAS repair.

    Unavailable profile facts do not deny admitted control access. Routing has
    its own unavailable-profile refusal rather than guessing Caddy. A real
    identity mismatch always propagates and no registration is written.
    """
    if not needs_adoption(record):
        return record
    if (isinstance(budget_seconds, bool) or not isinstance(budget_seconds, numbers.Real)
            or not math.isfinite(float(budget_seconds)) or not 0 < budget_seconds <= 300):
        raise ValueError('joined adoption budget is invalid')
    from sandbox.core import _remote
    from sandbox.hosting.front_door import nginx
    deadline = time.monotonic() + budget_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise RemoteProtocolError('remote_budget_exhausted', 'joined metadata discovery exhausted its operation budget')
        return value
    def request(selected, payload):
        try:
            return _remote.remote_host_memory_request(selected, payload, timeout_seconds=remaining())
        except RemoteProtocolError:
            raise
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            if 'HTTP 401' in str(exc) or 'HTTP 403' in str(exc):
                raise RemoteProtocolError('remote_access_denied', 'the control service denied this request') from None
            raise RemoteProtocolError('joined_metadata_unavailable',
                'authenticated joined metadata is unavailable; this is not evidence of denied Tailnet access') from None
    evidence = discover_join_identity(name, record, request, budget_seconds=remaining())
    if record.get('front_door') is None:
        profile_deadline = time.monotonic() + max(0, remaining() - 0.1)
        def profile_remaining():
            value = min(remaining(), profile_deadline - time.monotonic())
            if value <= 0:
                raise TimeoutError('joined profile observation budget expired')
            return value
        def machine_probe(selected):
            result = _remote.ssh_run(selected, 'python3 -c ' + shlex.quote(MACHINE_PROGRAM), timeout=profile_remaining())
            if result.returncode:
                raise FrontDoorError('joined_profile_unavailable', 'same-host SSH metadata is unavailable')
            return (result.stdout or '').strip()
        try:
            evidence.update(adopt_join_profile(record, evidence, None,
                machine_probe=machine_probe,
                nginx_probe=lambda selected: nginx.probe(selected, profile_only=True, timeout=profile_remaining())))
        except FrontDoorError as exc:
            if exc.code in {'joined_profile_identity_mismatch', 'joined_profile_mismatch'}:
                raise
        except (OSError, TimeoutError, subprocess.SubprocessError):
            pass  # Missing SSH/profile evidence is not denied Tailnet admission.
    # The persisted writer compares the original registration under its locks.
    # It never overwrites changed pins or unrelated controller state.
    changed = any(record.get(key) != value for key, value in evidence.items())
    if not changed:
        return record
    updated = _remote.put_remote(name, _lock_timeout=remaining(), _expected=record, **evidence)
    return {**updated, '_remote_name': name}

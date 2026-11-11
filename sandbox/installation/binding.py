"""Genuine durable installation identity. Reads never initialize state."""
from __future__ import annotations

from collections.abc import Mapping
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from sandbox.services.runtime_revision import runtime_revision
from .extensions import installed_capabilities
from .paths import selected_home, ManagedPaths

HEX24 = re.compile(r'[0-9a-f]{24}')
HEX32 = re.compile(r'[0-9a-f]{32}')
OWNER_POLICY = {'revision': 'admitted-owner-v1', 'admitted_operator': 'owner',
                'approved_sudo': True}


class InstallationBindingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _error(code, message):
    raise InstallationBindingError(code, message)


def _directory(path: Path, uid: int, *, private=False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        _error('installation_binding_uninitialized', 'installation identity is not initialized')
    except OSError:
        _error('installation_authority_invalid', 'installation directory authority is unavailable')
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) & (0o077 if private else 0o022)):
        _error('installation_authority_invalid', 'installation directory authority is invalid')


def _identity_root(home) -> Path:
    base = selected_home(home)
    # Check every in-base parent before opening private identity files.
    _directory(base, os.geteuid())
    _directory(base / 'runtime', os.geteuid())
    _directory(base / 'runtime/installation', os.geteuid(), private=True)
    return base / 'runtime/installation'


def _read(path: Path, uid: int, *, private=True) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                    or stat.S_IMODE(info.st_mode) & (0o077 if private else 0o022)
                    or info.st_size > 65536):
                _error('installation_authority_invalid', 'installation file authority is invalid')
            value = json.loads(stream.read(65537))
    except FileNotFoundError:
        _error('installation_binding_uninitialized', 'installation identity is not initialized')
    except (OSError, ValueError):
        _error('installation_binding_invalid', 'installation metadata is unavailable or invalid')
    if not isinstance(value, dict):
        _error('installation_binding_invalid', 'installation metadata must be an object')
    return value


def _write(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def machine_identity() -> str:
    try:
        raw = Path('/etc/machine-id').read_text().strip().lower()
    except OSError:
        _error('installation_machine_unavailable', 'stable machine identity is unavailable')
    if not HEX32.fullmatch(raw):
        _error('installation_machine_unavailable', 'stable machine identity is invalid')
    return hashlib.sha256(b'sandbox-host-machine-id-v1\0' + raw.encode('ascii')).hexdigest()[:24]


def _validate_identity(record):
    fields = {'schema_version', 'installation_id', 'incarnation', 'machine_identity',
              'ownership_marker', 'layout_kind'}
    if (set(record) != fields or type(record.get('schema_version')) is not int
            or record['schema_version'] != 1 or not isinstance(record.get('layout_kind'), str)
            or record['layout_kind'] not in {'owner', 'managed'}
            or any(not isinstance(record.get(k), str) or not HEX32.fullmatch(record[k])
                   for k in ('installation_id', 'incarnation'))
            or any(not isinstance(record.get(k), str) or not HEX24.fullmatch(record[k])
                   for k in ('machine_identity', 'ownership_marker'))):
        _error('installation_binding_invalid', 'installation identity fields are invalid')


def _read_authority(*, home=None) -> dict:
    root = _identity_root(home)
    record = _read(root / 'identity.json', os.geteuid())
    _validate_identity(record)
    if record['machine_identity'] != machine_identity():
        _error('installation_machine_mismatch', 'installation belongs to a different machine')
    marker = os.environ.get('SANDBOX_REMOTE_MCP_MARKER')
    if not isinstance(marker, str) or not HEX24.fullmatch(marker):
        _error('installation_service_unavailable', 'live service ownership is unavailable; use authenticated service discovery')
    if marker != record['ownership_marker']:
        _error('installation_service_mismatch', 'installation service ownership changed')
    if record['layout_kind'] == 'managed':
        config = ManagedPaths().config
        _directory(config, 0)
        policy = _read(config / 'server-policy.json', 0, private=False)
    else:
        policy = _read(root / 'policy.json', os.geteuid())
    if (set(policy) != set(OWNER_POLICY) or policy.get('admitted_operator') != 'owner'
            or policy.get('approved_sudo') is not True or not isinstance(policy.get('revision'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', policy['revision'])):
        _error('installation_policy_invalid', 'installation policy is unavailable or invalid')
    canonical = json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()
    binding = {**record, 'runtime_revision': runtime_revision(Path(__file__).resolve().parents[2]),
               'policy_revision': policy['revision'], 'policy_digest': hashlib.sha256(canonical).hexdigest()}
    return binding


def read_installation_binding(*, home=None) -> dict:
    binding = _read_authority(home=home)
    binding['execution_capabilities'] = installed_capabilities(binding)
    return binding


def _validate_authority(binding: Mapping, *, home=None) -> dict:
    """Extension verifiers compare core authority without recursively computing capabilities."""
    if not isinstance(binding, Mapping):
        _error('installation_binding_invalid', 'bound installation must be an object')
    current = _read_authority(home=home)
    if not _same_json({k: v for k, v in binding.items() if k != 'execution_capabilities'}, current):
        _error('installation_binding_drift', 'installation authority changed')
    return current


def _same_json(left, right) -> bool:
    try:
        return json.dumps(left, sort_keys=True, separators=(',', ':')) == json.dumps(
            right, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        return False


def is_managed_layout(*, home=None) -> bool:
    """Inspect only the owned layout record, without runtime/service discovery."""
    base = selected_home(home)
    path = base / 'runtime/installation/identity.json'
    if not os.path.lexists(path):
        return False
    root = _identity_root(home)
    record = _read(root / 'identity.json', os.geteuid())
    _validate_identity(record)
    return record['layout_kind'] == 'managed'


def validate_current(binding: Mapping, *, home=None) -> dict:
    if not isinstance(binding, Mapping):
        _error('installation_binding_invalid', 'bound installation must be an object')
    current = read_installation_binding(home=home)
    if not _same_json(dict(binding), current):
        _error('installation_binding_drift', 'installation, release, policy or capabilities changed')
    return current


def initialize_installation(*, home=None, layout_kind='owner', ownership_marker: str,
                            confirm=False) -> dict:
    if confirm is not True:
        _error('confirmation_required', 'installation initialization requires confirmation')
    if (not isinstance(layout_kind, str) or layout_kind not in {'owner', 'managed'} or not isinstance(ownership_marker, str)
            or not HEX24.fullmatch(ownership_marker)):
        _error('installation_binding_invalid', 'installation initialization fields are invalid')
    base = selected_home(home)
    if layout_kind == 'managed' and base != ManagedPaths().state:
        _error('installation_layout_invalid', 'managed state must use its approved path')
    _directory(base, os.geteuid())
    runtime = base / 'runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    _directory(runtime, os.geteuid())
    root = runtime / 'installation'
    root.mkdir(mode=0o700, exist_ok=True)
    _directory(root, os.geteuid(), private=True)
    fd = os.open(root / 'initialize.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            _error('installation_authority_invalid', 'installation lock authority is invalid')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _error('installation_busy', 'another initialization is active')
        identity_path = root / 'identity.json'
        if os.path.lexists(identity_path):
            record = _read(identity_path, os.geteuid())
            _validate_identity(record)
            if (record['layout_kind'] != layout_kind or record['ownership_marker'] != ownership_marker
                    or record['machine_identity'] != machine_identity()):
                _error('installation_binding_drift', 'existing installation cannot be silently replaced')
        else:
            record = {'schema_version': 1, 'installation_id': secrets.token_hex(16),
                      'incarnation': secrets.token_hex(16), 'machine_identity': machine_identity(),
                      'ownership_marker': ownership_marker, 'layout_kind': layout_kind}
            if layout_kind == 'owner':
                policy_path = root / 'policy.json'
                if os.path.lexists(policy_path):
                    if _read(policy_path, os.geteuid()) != OWNER_POLICY:
                        _error('installation_policy_invalid', 'pending policy differs from initialization')
                else:
                    _write(policy_path, OWNER_POLICY)
            else:
                _directory(ManagedPaths().config, 0)
                if _read(ManagedPaths().config / 'server-policy.json', 0, private=False) != OWNER_POLICY:
                    _error('installation_policy_invalid', 'managed policy is not provisioned')
            _write(identity_path, record)
    finally:
        os.close(fd)
    # Initialization establishes durable identity, not evidence of a running
    # service. Full current bindings come only from a service context.
    return dict(record)

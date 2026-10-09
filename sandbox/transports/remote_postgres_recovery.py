"""Registered transport for the closed PostgreSQL recovery helper."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex

from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.postgres_contract import (
    PostgresSource, RESTORE_INSPECTION_CODES, RESTORE_INSPECTION_PHASES,
    RESTORE_INSPECTION_STATUSES, recovery_source,
    restore_inspection_result_matches_diagnostic,
)

MAX_INSPECTION_RESPONSE_BYTES = 1024 * 1024


def _runtime_revision(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-f0-9]{12,40}', value) else None


def _inspection_bytes(source, request_id, archive, *, phase, status, code,
                      runtime_status=None):
    runtime_status = runtime_status if isinstance(runtime_status, dict) else {}
    local_revision = _runtime_revision(runtime_status.get('local_runtime_revision'))
    installed_revision = _runtime_revision(runtime_status.get('installed_runtime_revision'))
    if not isinstance(phase, str) or phase not in RESTORE_INSPECTION_PHASES:
        phase = 'response_validation'
    if not isinstance(status, str) or status not in RESTORE_INSPECTION_STATUSES:
        status = 'unknown'
    if not isinstance(code, str) or code not in RESTORE_INSPECTION_CODES:
        code = 'inspection_failed'
    revision_state = runtime_status.get('runtime_revision_state')
    if not isinstance(revision_state, str) or revision_state not in {'match', 'mismatch', 'unavailable', 'unknown'}:
        revision_state = 'unavailable' if not runtime_status else 'unknown'
    if revision_state in {'match', 'mismatch'} and (local_revision is None or installed_revision is None):
        revision_state = 'unknown'
    payload = {
        'schema_version': 1,
        'ok': status == 'complete',
        'code': code,
        'inspection_diagnostic': {
            'schema_version': 1,
            'correlation_id': request_id,
            'source_digest': source.source_digest,
            'archive_digest': 'sha256:' + hashlib.sha256(archive).hexdigest(),
            'local_runtime_revision': local_revision,
            'installed_runtime_revision': installed_revision,
            'runtime_revision_state': revision_state,
            'phase': phase,
            'status': status,
            'code': code,
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()


def _inspection_response(source, request_id, archive, payload, runtime_status):
    if not isinstance(payload, bytes):
        return _inspection_bytes(source, request_id, archive,
            phase='response_validation', status='unknown',
            code='inspection_response_invalid', runtime_status=runtime_status)
    if len(payload) > MAX_INSPECTION_RESPONSE_BYTES:
        return _inspection_bytes(source, request_id, archive,
            phase='response_validation', status='unknown',
            code='inspection_response_oversized', runtime_status=runtime_status)
    try:
        result = json.loads(payload.decode('utf-8'))
    except (AttributeError, UnicodeError, ValueError, json.JSONDecodeError):
        return _inspection_bytes(source, request_id, archive,
            phase='response_validation', status='unknown',
            code='inspection_response_invalid', runtime_status=runtime_status)
    if (type(result) is not dict or type(result.get('schema_version')) is not int
            or result.get('schema_version') != 1 or type(result.get('ok')) is not bool
            or type(result.get('code')) is not str):
        return _inspection_bytes(source, request_id, archive,
            phase='response_validation', status='unknown',
            code='inspection_response_invalid', runtime_status=runtime_status)
    private = result.get('inspection_diagnostic')
    if (type(private) is not dict or set(private) != {'phase', 'status', 'code'}
            or any(type(private.get(field)) is not str for field in ('phase', 'status', 'code'))
            or private.get('phase') not in RESTORE_INSPECTION_PHASES
            or private.get('status') not in RESTORE_INSPECTION_STATUSES
            or private.get('code') not in RESTORE_INSPECTION_CODES
            or not restore_inspection_result_matches_diagnostic(
                result.get('code'), result.get('ok'), private)):
        return _inspection_bytes(source, request_id, archive,
            phase='response_validation', status='unknown',
            code='inspection_response_invalid', runtime_status=runtime_status)
    result['inspection_diagnostic'] = {
        'schema_version': 1,
        'correlation_id': request_id,
        'source_digest': source.source_digest,
        'archive_digest': 'sha256:' + hashlib.sha256(archive).hexdigest(),
        'local_runtime_revision': _runtime_revision(runtime_status.get('local_runtime_revision')),
        'installed_runtime_revision': _runtime_revision(runtime_status.get('installed_runtime_revision')),
        'runtime_revision_state': runtime_status.get('runtime_revision_state')
            if isinstance(runtime_status.get('runtime_revision_state'), str)
            and runtime_status.get('runtime_revision_state') in {'match', 'mismatch', 'unavailable', 'unknown'}
            else 'unknown',
        'phase': private['phase'],
        'status': private['status'],
        'code': private['code'],
    }
    return json.dumps(result, sort_keys=True, separators=(',', ':')).encode()


class RegisteredPostgresRecoveryTransport:
    def __init__(self, *, cfg, project_root, state_root, lookup=None, status=None,
                 resolve_home=None, process=None, broker=None):
        if any(value is None for value in (lookup, status, resolve_home, process)):
            from sandbox.core import _remote
            lookup = lookup or _remote.get_remote
            status = status or _remote.remote_mcp_service_status
            resolve_home = resolve_home or _remote.resolve_sandbox_home
            process = process or _remote.ssh_process
        self.lookup, self.status, self.home, self.process = lookup, status, resolve_home, process
        self.cfg, self.project_root, self.state_root = cfg, Path(project_root), Path(state_root)
        self.broker = broker or self._broker

    def _broker(self, source, consumer, reference=None):
        from sandbox.config.secrets import normalize_secret_config
        from sandbox.secrets.sources import SourceRegistry
        from sandbox.secrets.writer import load_revision_key, opaque_revision
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.core import _secrets as personal_secrets
        reference = reference or source.credential_reference
        sources = normalize_secret_config({"root": str(self.project_root),
            "secrets": self.cfg.get("secrets", {})})["sources"]
        registry = SourceRegistry(str(self.project_root), sources,
            personal_path=personal_secrets.secret_file(), project_scope=str(self.project_root))
        alias = reference.split('/', 1)[0]
        descriptor = sources.get(alias)
        owner = descriptor.get("owner") if isinstance(descriptor, dict) else None
        owner = owner or registry.policy(alias).scope
        resolver = SecretReferenceResolver(registry, owner=owner)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace('+00:00', 'Z')
        key = load_revision_key(self.state_root.parent / 'runtime' / 'secrets' / 'revision.key')
        lease = resolver.issue(reference, binding_id='postgres-recovery-' + source.source_digest[7:31],
            binding_version=1, expires_at=expiry, owner=owner)
        result = lease.consume(lambda material: {"payload": consumer(material, opaque_revision(key, material))})
        return result["payload"]

    def invoke(self, source: PostgresSource, operation: str, request_id: str, *, archive=b'', target_volume=None, resume_capture=False, reopen_plan=None):
        source = recovery_source(source.as_mapping())
        if operation not in {'observe', 'capture', 'restore', 'inspect-restore', 'verify-restore', 'reopen-restore', 'status'} or not re.fullmatch(r'[a-f0-9]{64}', request_id):
            raise RecoveryError('PostgreSQL request is invalid', 'request_invalid')
        if operation in {'inspect-restore', 'verify-restore', 'reopen-restore'} and (source.profile != 'lenzora-dev' or target_volume is not None):
            raise RecoveryError('restore inspection is invalid', 'request_invalid')
        if (operation == 'reopen-restore' and (source.credential_reference is not None
                or type(reopen_plan) is not dict or not reopen_plan)
                or operation != 'reopen-restore' and reopen_plan is not None):
            raise RecoveryError('restore reopen is invalid', 'request_invalid')
        if resume_capture and (operation != 'capture' or source.profile != 'lenzora-dev'):
            raise RecoveryError('capture resume is invalid', 'request_invalid')
        try:
            entry = self.lookup(source.remote)
        except Exception:
            if operation == 'inspect-restore':
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_unavailable')
            raise
        if not isinstance(entry, dict) or entry.get('provisioned') is not True:
            if operation == 'inspect-restore':
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_unavailable')
            raise RecoveryError('registered remote is unavailable', 'remote_unavailable')
        try:
            status = self.status(entry)
        except Exception:
            if operation == 'inspect-restore':
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_status_unavailable')
            raise
        # Spec 061: a different revision that serves this controller's
        # control protocol is admitted; the exact rule applies otherwise.
        from sandbox.remote_runtime.verdict import admitted
        compatible, _ = admitted(status)
        if operation == 'inspect-restore' and isinstance(status, dict):
            revision_state = status.get('runtime_revision_state')
            if (isinstance(revision_state, str) and revision_state in {'match', 'mismatch'}
                    and (_runtime_revision(status.get('local_runtime_revision')) is None
                         or _runtime_revision(status.get('installed_runtime_revision')) is None)):
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_status_unavailable', runtime_status=status)
            if revision_state == 'mismatch' and not compatible:
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='refused',
                    code='remote_revision_mismatch', runtime_status=status)
            if not status.get('active') or not status.get('authenticated'):
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_authentication_unavailable', runtime_status=status)
        if (not isinstance(status, dict)
                or (status.get('runtime_revision_state') != 'match' and not compatible)
                or not status.get('active') or not status.get('authenticated')):
            if operation == 'inspect-restore':
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_status_unavailable', runtime_status=status)
            raise RecoveryError('installed recovery runtime differs', 'remote_revision_mismatch')
        try:
            home = self.home(entry)
        except Exception:
            if operation == 'inspect-restore':
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_unavailable', runtime_status=status)
            raise
        if not isinstance(home, str) or not home.startswith('/') or '\n' in home:
            if operation == 'inspect-restore':
                return _inspection_bytes(source, request_id, archive,
                    phase='runtime_compatibility', status='unavailable',
                    code='remote_unavailable', runtime_status=status)
            raise RecoveryError('remote recovery root is unavailable', 'remote_unavailable')
        helper = (Path(__file__).parents[1] / 'recovery' / 'postgres_helper.py').read_text()
        def invoke(material, revision):
            request = {'operation': operation, 'source': source.as_mapping(), 'request_id': request_id,
                'root': home + '/runtime/postgres-recovery', 'credential_size': len(material),
                'credential_revision': revision, 'archive_size': len(archive),
                'archive_digest': 'sha256:' + hashlib.sha256(archive).hexdigest(), 'target_volume': target_volume}
            if resume_capture: request['resume_capture'] = True
            if reopen_plan is not None: request['reopen_plan'] = reopen_plan
            frame = json.dumps(request, sort_keys=True, separators=(',', ':')).encode() + b'\n' + material + archive
            try:
                result = self.process(entry, 'python3 -c ' + shlex.quote(helper), input_data=frame, timeout=3600)
            except Exception:
                if operation == 'inspect-restore':
                    return _inspection_bytes(source, request_id, archive,
                        phase='transport', status='unknown', code='acceptance_unknown',
                        runtime_status=status)
                raise
            payload = getattr(result, 'stdout', b'')
            if isinstance(payload, str): payload = payload.encode()
            if getattr(result, 'returncode', 1) != 0 or not isinstance(payload, bytes) or not payload or len(payload) > 512 * 1024 * 1024:
                if operation == 'inspect-restore':
                    code = ('inspection_response_oversized' if isinstance(payload, bytes)
                            and len(payload) > 512 * 1024 * 1024 else 'acceptance_unknown')
                    return _inspection_bytes(source, request_id, archive,
                        phase='transport', status='unknown', code=code,
                        runtime_status=status)
                raise RecoveryError('PostgreSQL operation requires retained-request inspection', 'acceptance_unknown')
            if operation == 'inspect-restore':
                return _inspection_response(source, request_id, archive, payload, status)
            return payload
        if operation == 'restore' and target_volume is not None:
            if not source.target_password_reference:
                raise RecoveryError('target password binding is required', 'target_password_unavailable')
            return self.broker(source, invoke, reference=source.target_password_reference)
        if source.credential_reference is not None and operation not in {'restore', 'status'}:
            return self.broker(source, invoke)
        return invoke(b'', None)

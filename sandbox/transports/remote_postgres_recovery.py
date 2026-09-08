"""Registered transport for the closed PostgreSQL recovery helper."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex

from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.postgres_contract import PostgresSource, recovery_source


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

    def invoke(self, source: PostgresSource, operation: str, request_id: str, *, archive=b'', target_volume=None, resume_capture=False):
        source = recovery_source(source.as_mapping())
        if operation not in {'observe', 'capture', 'restore', 'status'} or not re.fullmatch(r'[a-f0-9]{64}', request_id):
            raise RecoveryError('PostgreSQL request is invalid', 'request_invalid')
        if resume_capture and (operation != 'capture' or source.profile != 'lenzora-dev'):
            raise RecoveryError('capture resume is invalid', 'request_invalid')
        entry = self.lookup(source.remote)
        if not isinstance(entry, dict) or entry.get('provisioned') is not True:
            raise RecoveryError('registered remote is unavailable', 'remote_unavailable')
        status = self.status(entry)
        if (not isinstance(status, dict) or status.get('runtime_revision_state') != 'match'
                or not status.get('active') or not status.get('authenticated')):
            raise RecoveryError('installed recovery runtime differs', 'remote_revision_mismatch')
        home = self.home(entry)
        if not isinstance(home, str) or not home.startswith('/') or '\n' in home:
            raise RecoveryError('remote recovery root is unavailable', 'remote_unavailable')
        helper = (Path(__file__).parents[1] / 'recovery' / 'postgres_helper.py').read_text()
        def invoke(material, revision):
            request = {'operation': operation, 'source': source.as_mapping(), 'request_id': request_id,
                'root': home + '/runtime/postgres-recovery', 'credential_size': len(material),
                'credential_revision': revision, 'archive_size': len(archive),
                'archive_digest': 'sha256:' + hashlib.sha256(archive).hexdigest(), 'target_volume': target_volume}
            if resume_capture: request['resume_capture'] = True
            frame = json.dumps(request, sort_keys=True, separators=(',', ':')).encode() + b'\n' + material + archive
            result = self.process(entry, 'python3 -c ' + shlex.quote(helper), input_data=frame, timeout=3600)
            payload = getattr(result, 'stdout', b'')
            if isinstance(payload, str): payload = payload.encode()
            if getattr(result, 'returncode', 1) != 0 or not isinstance(payload, bytes) or not payload or len(payload) > 512 * 1024 * 1024:
                raise RecoveryError('PostgreSQL operation requires retained-request inspection', 'acceptance_unknown')
            return payload
        if operation == 'restore' and target_volume is not None:
            if not source.target_password_reference:
                raise RecoveryError('target password binding is required', 'target_password_unavailable')
            return self.broker(source, invoke, reference=source.target_password_reference)
        if source.credential_reference is not None and operation not in {'restore', 'status'}:
            return self.broker(source, invoke)
        return invoke(b'', None)

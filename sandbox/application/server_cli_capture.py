"""Private byte custody for one genuine installed host-exec job.

Only the trusted host capture handler supplies its bounded ExecOutcome. There
is no caller path, provider-store reader, environment injection or log codec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import stat

from sandbox.jobs.artifacts import (PRIVATE_CAPTURE_KIND, PRIVATE_CAPTURE_MAX_BYTES,
    PRIVATE_CAPTURE_RETENTION_SECONDS, read_private_capture)
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage

CONTRACT = 'job.server-cli.capture-artifact.v1'
OUTPUT_CONTRACT = 'artifact.v1'
RETENTION_SECONDS = PRIVATE_CAPTURE_RETENTION_SECONDS
MAX_MANIFEST_BYTES = 16384
_ISSUER = object()


class CaptureUnavailable(RuntimeError):
    code = 'server_cli_capture_unavailable'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def artifact_id(job_id, stream):
    if stream not in {'stdout', 'stderr', 'manifest'}:
        raise CaptureUnavailable('capture stream is invalid')
    return hashlib.sha256((job_id + ':server-exec-capture-v1:' + stream).encode()).hexdigest()[:24]


def _binding(context, row):
    try:
        proof = json.loads(row['execution_policy_provenance_json'])
        snapshot = json.loads(proof['server_cli.target_snapshot'])
        from sandbox.application.server_cli_targets import validate_target_snapshot
        validate_target_snapshot(snapshot, owner=context.owner, origin_remote_name=context.origin_remote_name)
        if (row['job_id'] != context.job_id or row['request_id'] != context.request_id
                or row['kind'] != 'server-cli' or row['target_kind'] != 'local' or row['lifecycle'] != 'running'
                or any(row[field] != getattr(context, field) for field in
                       ('project_identity', 'source_identity', 'source_commit', 'source_dirty_digest'))
                or proof['server_cli.target_identity'] != context.target_identity
                or proof['server_cli.ownership_marker'] != context.owner.ownership_marker
                or proof['server_cli.runtime_revision'] != context.runtime_revision
                or proof['server_cli.remote_name'] != context.origin_remote_name
                or proof['server_cli.output_contract'] != OUTPUT_CONTRACT
                or proof['server_cli.target_selector_digest'] != context.target_selector_digest
                or not isinstance(context.target_selector_digest, str)
                or re.fullmatch(r'sha256:[0-9a-f]{64}', context.target_selector_digest) is None
                or snapshot['selector_digest'] != context.target_selector_digest
                or snapshot['source_commit'] != context.source_commit
                or snapshot['project'] + '/' + snapshot['environment'] != proof['server_cli.exec_target']
                or not isinstance(proof['server_cli.exec_service'], str)
                or not any(item['name'] == proof['server_cli.exec_service'] and item['index'] == 1 for item in snapshot['services'])):
            raise ValueError()
        return {'job_id': context.job_id, 'request_id': context.request_id,
            'target_identity': context.target_identity, 'ownership_marker': context.owner.ownership_marker,
            'runtime_revision': context.runtime_revision, 'origin_remote_name': context.origin_remote_name,
            'project_identity': context.project_identity, 'source_identity': context.source_identity,
            'source_commit': context.source_commit, 'source_dirty_digest': context.source_dirty_digest,
            'source_origin_commit': snapshot['source_origin_commit'],
            'selector_digest': context.target_selector_digest, 'service': proof['server_cli.exec_service'],
            'target': proof['server_cli.exec_target'], 'deadline_at': proof['server_cli.absolute_deadline_at']}
    except (KeyError, TypeError, ValueError):
        raise CaptureUnavailable('original job does not bind trusted byte capture') from None


def _outcome(outcome, binding):
    from sandbox.hosting.host_exec import ExecOutcome, OK, FAILED
    if (type(outcome) is not ExecOutcome or outcome.code not in {OK, FAILED}
            or type(outcome.exit_code) is not int or not 0 <= outcome.exit_code <= 255
            or (outcome.code == OK) != (outcome.exit_code == 0)
            or type(outcome.duration_ms) is not int or not 0 <= outcome.duration_ms <= 86400000
            or type(outcome.stdout) is not bytes or type(outcome.stderr) is not bytes
            or len(outcome.stdout) > PRIVATE_CAPTURE_MAX_BYTES or len(outcome.stderr) > PRIVATE_CAPTURE_MAX_BYTES
            or type(outcome.stdout_truncated) is not bool or type(outcome.stderr_truncated) is not bool):
        raise CaptureUnavailable('capture completion or bounded bytes are unavailable')
    return {'ok': outcome.ok, 'code': outcome.code, 'exit_code': outcome.exit_code,
        'remote': binding['origin_remote_name'], 'target': binding['target'], 'service': binding['service'],
        'duration_ms': outcome.duration_ms, 'stdout_truncated': outcome.stdout_truncated,
        'stderr_truncated': outcome.stderr_truncated}


def _envelope(manifest, manifest_bytes):
    return {**manifest['outcome'], 'capture': {'schema_version': 1, 'contract': CONTRACT,
        'manifest_artifact_id': artifact_id(manifest['binding']['job_id'], 'manifest'),
        'size_bytes': len(manifest_bytes), 'sha256': hashlib.sha256(manifest_bytes).hexdigest()}}


@dataclass
class CaptureCustody:
    _context: object = field(repr=False)
    _storage: object = field(repr=False)
    _repository: object = field(repr=False)
    _directory: int = field(repr=False)
    _binding: dict = field(repr=False)
    _seal: object = field(repr=False)
    _closed: bool = field(default=False, repr=False)

    def close(self):
        if not self._closed:
            self._closed = True
            os.close(self._directory)
            self._repository.close()

    def __enter__(self):
        self._current()
        return self

    def __exit__(self, *args):
        self.close()

    def _current(self, *, verify_process=True):
        from sandbox.application.server_cli import current_server_cli_context
        if type(self) is not CaptureCustody or self._seal is not _ISSUER or self._closed:
            raise CaptureUnavailable('byte custody is unavailable')
        if verify_process and current_server_cli_context() != self._context:
            raise CaptureUnavailable('original capture process or installed owner changed')
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self._context.deadline_at):
            raise CaptureUnavailable('original capture deadline expired')
        if _binding(self._context, self._repository.get(self._context.job_id)) != self._binding:
            raise CaptureUnavailable('original capture binding changed or was cancelled')
        from sandbox.jobs.server_cli_lineage import check_server_cli_lineage
        check_server_cli_lineage(self._repository.server_cli_ancestry(self._context.job_id))
        held = os.fstat(self._directory)
        descriptors = []
        try:
            descriptors.append(os.open(self._storage.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for name in (self._context.job_id, 'artifacts'):
                info = os.fstat(descriptors[-1])
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise CaptureUnavailable('capture parent directory identity changed')
                descriptors.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptors[-1]))
            info = os.fstat(descriptors[-1])
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
                    or not held.st_nlink or (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino)):
                raise CaptureUnavailable('capture directory identity changed')
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _existing(self, metadata, streams, outcome):
        selected = {row['artifact_id']: row for row in self._repository.snapshot(self._context.job_id)['artifacts']
                    if row.get('kind') == PRIVATE_CAPTURE_KIND}
        if not selected:
            return None
        expected = {artifact_id(self._context.job_id, item) for item in ('stdout', 'stderr', 'manifest')}
        if set(selected) != expected:
            raise CaptureUnavailable('partial byte custody is retained; do not replay execution')
        row = selected[artifact_id(self._context.job_id, 'manifest')]
        body = read_private_capture(self._storage, self._context.job_id, row, offset=0, max_bytes=MAX_MANIFEST_BYTES)
        manifest = json.loads(body)
        if manifest.get('binding') != self._binding or manifest.get('streams') != metadata or manifest.get('outcome') != outcome:
            raise CaptureUnavailable('existing byte custody differs from this original result')
        for name in ('stdout', 'stderr'):
            row = selected[artifact_id(self._context.job_id, name)]
            stored = read_private_capture(self._storage, self._context.job_id, row,
                offset=0, max_bytes=PRIVATE_CAPTURE_MAX_BYTES)
            if stored != streams[name]:
                raise CaptureUnavailable('existing byte custody changed')
        self._current()
        return _envelope(manifest, body)

    def publish(self, outcome):
        """One use, after successful execution-scope cleanup and fresh proof."""
        created, publishing = [], False
        try:
            self._current()
            public = _outcome(outcome, self._binding)
            streams = {'stdout': outcome.stdout, 'stderr': outcome.stderr}
            metadata = {name: {'artifact_id': artifact_id(self._context.job_id, name),
                'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in streams.items()}
            existing = self._existing(metadata, streams, public)
            if existing is not None:
                return existing
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=RETENTION_SECONDS)).isoformat()
            manifest = {'schema_version': 1, 'contract': CONTRACT, 'binding': self._binding,
                'streams': metadata, 'outcome': public, 'expires_at': expiry}
            body = _canonical(manifest)
            if len(body) > MAX_MANIFEST_BYTES:
                raise CaptureUnavailable('capture manifest exceeds its bound')
            payloads = {**streams, 'manifest': body}
            self._storage.require_capacity(sum(len(value) for value in payloads.values()))
            for name, data in payloads.items():
                identifier = artifact_id(self._context.job_id, name)
                fd = os.open(identifier, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._directory)
                created.append(identifier)
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise CaptureUnavailable('private capture write was incomplete')
                        view = view[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.fsync(self._directory)
            self._current()
            # Once registry publication starts, retain files on uncertainty.
            # Existing job cleanup owns later collection; execution is never replayed.
            publishing = True
            with self._repository.transaction(immediate=True):
                # Kernel/unit proof was just checked before this transaction.
                # Re-read the original row and ancestor fence on THIS locked
                # connection; opening a second migrating repository here would
                # contend with our own writer lock. Full proof is checked again
                # after commit before any metadata is acknowledged.
                self._current(verify_process=False)
                for name, data in payloads.items():
                    identifier = artifact_id(self._context.job_id, name)
                    self._repository.add_artifact(self._context.job_id, artifact_id=identifier,
                        display_name='server-exec-' + name, stored_relative_path='artifacts/' + identifier,
                        declared_path=None, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        kind=PRIVATE_CAPTURE_KIND, media_type='application/json' if name == 'manifest' else 'application/octet-stream',
                        expires_at=expiry)
            self._current()
            return _envelope(manifest, body)
        except Exception:
            if not publishing:
                for identifier in created:
                    try:
                        os.unlink(identifier, dir_fd=self._directory)
                    except OSError:
                        pass
            raise CaptureUnavailable('original byte capture is unavailable; retain the job and do not replay execution') from None
        finally:
            self.close()


def prepare_server_cli_capture():
    """Reserve only private output capacity before the trusted handler's effects."""
    from sandbox.application.server_cli import current_server_cli_context
    from sandbox.core._paths import RUNTIME_DIR
    descriptors, directory, repository = [], None, None
    try:
        context = current_server_cli_context()
        storage = JobStorage.open_existing(RUNTIME_DIR)
        storage.require_capacity(2 * PRIVATE_CAPTURE_MAX_BYTES + MAX_MANIFEST_BYTES)
        repository = JobRepository(storage.root / 'registry.sqlite3', busy_timeout_ms=1000)
        binding = _binding(context, repository.get(context.job_id))
        root = os.open(storage.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root)
        job = os.open(context.job_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        descriptors.append(job)
        info = os.fstat(job)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CaptureUnavailable('original job directory is not private')
        try:
            os.mkdir('artifacts', mode=0o700, dir_fd=job)
        except FileExistsError:
            pass
        directory = os.open('artifacts', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=job)
        custody = CaptureCustody(context, storage, repository, directory, binding, _ISSUER)
        custody._current()
        return custody
    except Exception:
        if repository is not None:
            repository.close()
        if directory is not None:
            os.close(directory)
        raise CaptureUnavailable('trusted capture preparation is unavailable before effects') from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def read_server_cli_capture(envelope, *, receipt, state, control):
    """Read existing artifact pages only; dictionaries do not issue custody."""
    import base64
    try:
        job_id = receipt['job_id']
        proof = receipt['execution_policy']['provenance']
        snapshot = json.loads(proof['server_cli.target_snapshot'])
        if (proof['server_cli.output_contract'] != OUTPUT_CONTRACT or state['lifecycle'] not in {'succeeded', 'failed'}
                or state['job_id'] != job_id or state['request_id'] != receipt['request_id']
                or state['kind'] != 'server-cli' or state['target_kind'] != 'local'
                or json.loads(state['execution_policy_provenance_json']) != proof
                or state['source_identity'] != receipt['source']['identity'] or state['source_commit'] != receipt['source']['commit']):
            raise ValueError()
        binding = {'job_id': job_id, 'request_id': receipt['request_id'],
            'target_identity': proof['server_cli.target_identity'], 'ownership_marker': proof['server_cli.ownership_marker'],
            'runtime_revision': proof['server_cli.runtime_revision'], 'origin_remote_name': proof['server_cli.remote_name'],
            'project_identity': state['project_identity'], 'source_identity': state['source_identity'],
            'source_commit': state['source_commit'], 'source_dirty_digest': state.get('source_dirty_digest'),
            'source_origin_commit': snapshot['source_origin_commit'], 'selector_digest': receipt['selector_digest'],
            'service': proof['server_cli.exec_service'], 'target': proof['server_cli.exec_target'],
            'deadline_at': proof['server_cli.absolute_deadline_at']}
        capture = envelope['capture']
        if (not isinstance(capture, dict) or set(capture) != {'schema_version', 'contract', 'manifest_artifact_id', 'size_bytes', 'sha256'}
                or type(capture['schema_version']) is not int or capture['schema_version'] != 1 or capture['contract'] != CONTRACT
                or capture['manifest_artifact_id'] != artifact_id(job_id, 'manifest')
                or type(capture['size_bytes']) is not int or not 1 <= capture['size_bytes'] <= MAX_MANIFEST_BYTES
                or not isinstance(capture['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', capture['sha256']) is None):
            raise ValueError()
        response = control(['job-artifacts', job_id])
        rows = response.get('artifacts') if isinstance(response, dict) else None
        if response.get('ok') is not True or not isinstance(rows, list) or len(rows) > 50:
            raise ValueError()
        selected = {}
        expected_ids = {artifact_id(job_id, name) for name in ('stdout', 'stderr', 'manifest')}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError()
            identifier = row.get('artifact_id')
            if identifier not in expected_ids:
                continue
            if (identifier in selected or row.get('job_id') != job_id or row.get('kind') != PRIVATE_CAPTURE_KIND
                    or row.get('status', 'available') != 'available'
                    or type(row.get('size_bytes')) is not int or not 0 <= row['size_bytes'] <= PRIVATE_CAPTURE_MAX_BYTES
                    or row.get('stored_relative_path') != 'artifacts/' + identifier
                    or not isinstance(row.get('sha256'), str) or re.fullmatch(r'[0-9a-f]{64}', row['sha256']) is None):
                raise ValueError()
            expiry = datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))
            created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
            if (expiry.utcoffset() is None or created.utcoffset() is None or expiry <= datetime.now(timezone.utc)
                    or expiry <= created or (expiry - created).total_seconds() > RETENTION_SECONDS):
                raise ValueError()
            selected[identifier] = row
        if set(selected) != expected_ids:
            raise ValueError()

        def fetch(metadata):
            chunks, offset = [], 0
            # 512 KiB stays below the existing 1 MiB JSON envelope limit after
            # base64 expansion. The complete stream is bounded to 1 MiB.
            while offset < metadata['size_bytes']:
                count = min(524288, metadata['size_bytes'] - offset)
                page = control(['job-artifact-get', job_id, metadata['artifact_id'],
                                '--offset', str(offset), '--max-bytes', str(count)])
                if (not isinstance(page, dict) or page.get('ok') is not True or page.get('job_id') != job_id
                        or page.get('artifact_id') != metadata['artifact_id'] or page.get('encoding') != 'base64'
                        or type(page.get('size_bytes')) is not int or page['size_bytes'] != metadata['size_bytes']
                        or page.get('sha256') != metadata['sha256']
                        or type(page.get('offset')) is not int or page['offset'] != offset
                        or type(page.get('bytes_read')) is not int or not 0 < page['bytes_read'] <= count
                        or not isinstance(page.get('data'), str) or type(page.get('has_more')) is not bool):
                    raise ValueError()
                data = base64.b64decode(page['data'], validate=True)
                if len(data) != page['bytes_read'] or len(data) > count:
                    raise ValueError()
                offset += len(data)
                if type(page.get('next_offset')) is not int or page['next_offset'] != offset or page['has_more'] != (offset < metadata['size_bytes']):
                    raise ValueError()
                chunks.append(data)
            data = b''.join(chunks)
            if hashlib.sha256(data).hexdigest() != metadata['sha256']:
                raise ValueError()
            return data

        meta = selected[capture['manifest_artifact_id']]
        if meta['sha256'] != capture['sha256'] or meta['size_bytes'] != capture['size_bytes']:
            raise ValueError()
        manifest = json.loads(fetch(meta))
        if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'contract', 'binding', 'streams', 'outcome', 'expires_at'}
                or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['contract'] != CONTRACT or manifest['binding'] != binding
                or manifest['outcome'] != {key: value for key, value in envelope.items() if key != 'capture'}
                or not isinstance(manifest['streams'], dict) or set(manifest['streams']) != {'stdout', 'stderr'}):
            raise ValueError()
        decoded = {}
        for name in ('stdout', 'stderr'):
            entry = manifest['streams'][name]
            expected = selected[artifact_id(job_id, name)]
            if (not isinstance(entry, dict) or set(entry) != {'artifact_id', 'size_bytes', 'sha256'}
                    or entry != {key: expected[key] for key in entry}
                    or expected['expires_at'] != manifest['expires_at']):
                raise ValueError()
            decoded[name + '_bytes'] = fetch(expected)
        if meta['expires_at'] != manifest['expires_at']:
            raise ValueError()
        expiry = datetime.fromisoformat(manifest['expires_at'].replace('Z', '+00:00'))
        if expiry.utcoffset() is None or expiry <= datetime.now(timezone.utc):
            raise ValueError()
        return {**envelope, **decoded, 'output_encoding': 'artifact-bytes'}
    except Exception:
        # Public failure never includes file bytes, a provider-shaped canary,
        # host paths or a raw transport exception. Known job custody is retained.
        raise CaptureUnavailable('retained byte custody is missing, changed, expired or unavailable; do not replay execution') from None

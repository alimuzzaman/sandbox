"""Source-bound encrypted PostgreSQL capture and isolated restore drills.

Publication reuses StagingCaptureCoordinator; native formats use DatabaseCapture.
Only the explicitly installed non-secret source descriptor selects a live source.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile

from sandbox.hosting.images.provisioning import install_owner_only_json, _read_owner_only_json, _owned_directory
from sandbox.hosting.images.plan_set import read_stable_file
from .database import DatabaseCapture
from .errors import RecoveryError
from .integrity import sha256_file
from .postgres_contract import recovery_source, digest
from .restore import verify_manifest


def _load_json_bytes(data):
    """Decode bounded recovery evidence independently of image service limits."""
    def invalid():
        raise RecoveryError('PostgreSQL evidence is invalid', 'observation_invalid')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: invalid()
            value[key] = item
        return value
    if type(data) is not bytes or not data or len(data) > 1024 * 1024:
        invalid()
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: invalid())
        remaining = 100000
        def check(item, depth=0):
            nonlocal remaining
            remaining -= 1
            if depth > 32 or remaining < 0: invalid()
            if type(item) in (dict, list):
                if len(item) > 10000: invalid()
                for child in (item.values() if type(item) is dict else item):
                    check(child, depth + 1)
            elif item is not None and type(item) not in (str, int, bool):
                invalid()
        check(value)
        if type(value) is not dict: invalid()
        return value
    except (UnicodeError, ValueError, RecursionError):
        invalid()


class PostgresRecovery:
    def __init__(self, root: Path, transport, capture, catalog):
        self.root, self.transport, self.capture, self.catalog = root, transport, capture, catalog
        _owned_directory(root, create=True)

    def _source_path(self, remote, profile):
        if not isinstance(remote, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', remote):
            raise RecoveryError('remote selector is invalid', 'source_binding_invalid')
        if profile not in {'lenzora-dev', 'lenzora-prod', 'lenzora-prod-legacy', 'lenzora-prod-storage'}:
            raise RecoveryError('PostgreSQL profile is unsupported', 'source_binding_invalid')
        return self.root / 'sources' / f'{remote}-{profile}.json'

    def register(self, source, *, confirm=False):
        source = recovery_source(source)
        if not confirm: return {'code': 'registration_planned', 'source': source.as_mapping(), 'source_digest': source.source_digest}
        disposition = install_owner_only_json(self._source_path(source.remote, source.profile), source.as_mapping())
        return {'code': disposition, 'source_digest': source.source_digest}

    def source(self, remote, profile):
        return recovery_source(_read_owner_only_json(self._source_path(remote, profile)))

    def _request(self, source, operation, request_id, extra=None):
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', request_id):
            raise RecoveryError('replay-safe request ID is required', 'request_invalid')
        body = {'source_digest': source.source_digest, 'operation': operation, 'request_id': request_id, 'extra': extra}
        # Public request identity owns an immutable operation body locally too.
        install_owner_only_json(self.root / 'requests' / (hashlib.sha256(request_id.encode()).hexdigest() + '.json'), body)
        return digest(body)[7:]

    def observe(self, remote, profile, request_id, binding=None):
        source = self.source(remote, profile) if binding is None else recovery_source(binding)
        if source.remote != remote or source.profile != profile:
            raise RecoveryError('source selectors differ', 'source_binding_invalid')
        identity = self._request(source, 'observe', request_id)
        value = _load_json_bytes(self.transport.invoke(source, 'observe', identity))
        if type(value) is not dict or value.get('ok') is not True or value.get('code') != 'observed':
            raise RecoveryError('observation is unavailable', 'observation_invalid')
        return {**value, 'source_digest': source.source_digest}

    def status(self, remote, profile, request_id):
        source = self.source(remote, profile)
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', request_id):
            raise RecoveryError('request ID is invalid', 'request_invalid')
        body = _read_owner_only_json(self.root / 'requests' / (hashlib.sha256(request_id.encode()).hexdigest() + '.json'))
        if body is None or body.get('source_digest') != source.source_digest or body.get('request_id') != request_id:
            raise RecoveryError('retained request does not match source', 'request_invalid')
        value = _load_json_bytes(self.transport.invoke(source, 'status', digest(body)[7:]))
        if value.get('ok') is not True or value.get('source_digest') != source.source_digest:
            raise RecoveryError('retained status is unavailable', 'acceptance_unknown')
        return value

    @staticmethod
    def _unpack_capture(archive, destination, storage=False):
        with tarfile.open(archive, 'r') as bundle:
            members = bundle.getmembers()
            names = ['evidence.json', 'storage.tar'] if storage else ['database.dump', 'evidence.json']
            if sorted(member.name for member in members) != names or any(
                    not member.isfile() or member.size < 1 or member.size > 512 * 1024 * 1024 for member in members):
                raise RecoveryError('PostgreSQL capture is invalid', 'capture_invalid')
            for member in members:
                path = destination / member.name
                with path.open('xb') as output:
                    os.chmod(path, 0o600)
                    handle = bundle.extractfile(member)
                    import shutil
                    shutil.copyfileobj(handle, output)
        if not storage: DatabaseCapture._validate_format('postgresql', destination / 'database.dump')
        evidence = _load_json_bytes(read_stable_file(destination / 'evidence.json', 1024 * 1024, owner_only=True))
        if evidence.get('archive_digest' if storage else 'dump_digest') != 'sha256:' + sha256_file(destination / ('storage.tar' if storage else 'database.dump')):
            raise RecoveryError('PostgreSQL capture digest differs', 'capture_invalid')
        return evidence

    def create(self, remote, profile, request_id, backup_id, *, confirm=False, resume=False):
        if not confirm: raise RecoveryError('capture requires confirmation', 'confirmation_required')
        if self.capture is None: raise RecoveryError('encrypted recovery is not configured', 'recovery_not_configured')
        source = self.source(remote, profile)
        if resume and profile != 'lenzora-dev':
            raise RecoveryError('only local development capture can resume', 'request_invalid')
        identity = self._request(source, 'capture', request_id, backup_id)
        receipt_path = self.root / 'captures' / (identity + '.json')
        retained = _read_owner_only_json(receipt_path)
        if retained is not None:
            verify_manifest(self.capture.drive, backup_id)
            return retained
        if resume:
            status = self.status(remote, profile, request_id)
            if status.get('code') != 'retained_without_result' or status.get('operation') != 'capture':
                raise RecoveryError('capture is not resumable', 'acceptance_unknown')
        material_root = self.capture.materialization_root
        if material_root is None: raise RecoveryError('owned materialization is unavailable', 'recovery_not_configured')
        _owned_directory(material_root, create=True)
        with tempfile.TemporaryDirectory(prefix='postgres-', dir=material_root) as temporary:
            work = Path(temporary)
            archive = work / 'capture.tar'
            options = {'resume_capture': True} if resume else {}
            payload = self.transport.invoke(source, 'capture', identity, **options)
            with archive.open('xb') as handle:
                os.chmod(archive, 0o600); handle.write(payload)
            evidence = self._unpack_capture(archive, work, storage=profile == 'lenzora-prod-storage')
            if evidence.get('source_digest') != source.source_digest:
                raise RecoveryError('capture source changed', 'source_changed')
            manifest = self.capture.publish_files(backup_id, {'native-capture.tar': archive}, profiles=(profile,),
                provenance={'source_digest': source.source_digest, 'observation': evidence},
                profile_bindings={profile: {'dependencies': [], 'restore_target': 'isolated-postgresql-volume',
                    'allowed_roots': ['registered-postgresql-source']}})
            verify_manifest(self.capture.drive, backup_id)
            destination = getattr(self.capture.drive, 'destination', None)
            if destination is not None:
                install_owner_only_json(self.root / 'channels' / f'{remote}-{profile}.json',
                    {'schema_version': 1, 'source_digest': source.source_digest, 'destination': destination})
            result = {'code': 'captured', 'backup_id': backup_id, 'source_digest': source.source_digest,
                'ciphertext_digest': 'sha256:' + manifest['ciphertext_sha256'], 'evidence': evidence}
            install_owner_only_json(receipt_path, result)
            return result

    def readiness(self, remote, profile, target_volume):
        source = self.source(remote, profile)
        if self.capture is not None:
            drive = self.capture.drive
        else:
            # Readiness verifies published ciphertext; it never decrypts and
            # must not require delivering the encryption passphrase again.
            channel = _read_owner_only_json(self.root / 'channels' / f'{remote}-{profile}.json')
            if (type(channel) is not dict or set(channel) != {'schema_version', 'source_digest', 'destination'}
                    or channel['schema_version'] != 1 or channel['source_digest'] != source.source_digest):
                raise RecoveryError('verified recovery channel is unavailable', 'recovery_not_configured')
            from .drive import RcloneDrive
            from sandbox.services.process import BoundedProcessRunner
            drive = RcloneDrive(BoundedProcessRunner(), channel['destination'])
        directory = self.root / 'restores'
        if not directory.exists(): raise RecoveryError('a verified restore drill is required', 'restore_verification_required')
        _owned_directory(directory, create=False)
        paths = list(directory.glob('*.json'))
        if len(paths) > 200: raise RecoveryError('restore history requires review', 'restore_verification_required')
        candidates = []
        for path in paths:
            receipt = _read_owner_only_json(path)
            if receipt.get('source_digest') != source.source_digest: continue
            if profile == 'lenzora-prod-legacy':
                if receipt.get('production_transfer') is not True or receipt.get('volume') != target_volume: continue
            elif source.volume != target_volume: continue
            manifest = verify_manifest(drive, receipt['backup_id'])
            evidence = manifest.get('provenance', {}).get('observation')
            if (not isinstance(evidence, dict) or evidence.get('source_digest') != source.source_digest
                    or receipt.get('dump_digest', receipt.get('archive_digest')) != evidence.get('dump_digest', evidence.get('archive_digest'))):
                raise RecoveryError('restore proof no longer binds its backup', 'restore_verification_required')
            candidates.append((int(evidence.get('captured_at', 0)), receipt, evidence))
        if not candidates: raise RecoveryError('a matching verified restore is required', 'restore_verification_required')
        _, receipt, evidence = max(candidates, key=lambda item: (item[0], item[1]['backup_id']))
        return {'code': 'data_ready', 'profile': profile, 'volume': target_volume,
            'source_digest': source.source_digest, 'backup_id': receipt['backup_id'],
            'restore_plan_digest': receipt['plan_digest'], 'major': evidence.get('major'),
            'production_transfer': receipt.get('production_transfer', False)}

    def restore_plan(self, remote, profile, request_id, backup_id, target_volume=None):
        if self.capture is None: raise RecoveryError('encrypted recovery is not configured', 'recovery_not_configured')
        source = self.source(remote, profile)
        manifest = verify_manifest(self.capture.drive, backup_id)
        if manifest.get('profiles') != [profile] or manifest.get('provenance', {}).get('source_digest') != source.source_digest:
            raise RecoveryError('backup does not match registered source', 'source_changed')
        evidence = manifest.get('provenance', {}).get('observation')
        if not isinstance(evidence, dict): raise RecoveryError('backup observation is unavailable', 'capture_invalid')
        if target_volume is not None and (profile != 'lenzora-prod-legacy' or target_volume != 'sandbox-host-lenzora-production_lenzora-postgres-data'):
            raise RecoveryError('production transfer target is invalid', 'restore_plan_changed')
        identity = self._request(source, 'restore', request_id, {'backup_id': backup_id, 'ciphertext_digest': manifest['ciphertext_sha256'], 'target_volume': target_volume})
        body = {'schema_version': 1, 'remote': remote, 'profile': profile, 'request_id': request_id,
            'native_request_id': identity, 'backup_id': backup_id, 'source_digest': source.source_digest,
            'ciphertext_digest': manifest['ciphertext_sha256'], 'target': 'sandbox-recovery-restore-' + identity[:24],
            'source_major': evidence.get('major'), 'target_volume': target_volume,
            'target_database': 'lenzora' if target_volume is not None else getattr(source, 'database', None),
            'target_role': 'lenzora' if target_volume is not None else getattr(source, 'role', None), 'active_database_overwrite': False, 'published_ports': []}
        return {**body, 'plan_digest': digest(body)}

    def restore(self, plan, *, confirm=False):
        if not confirm: raise RecoveryError('restore drill requires confirmation', 'confirmation_required')
        expected = self.restore_plan(plan['remote'], plan['profile'], plan['request_id'], plan['backup_id'], plan['target_volume'])
        if plan != expected: raise RecoveryError('restore plan changed', 'restore_plan_changed')
        source = self.source(plan['remote'], plan['profile'])
        receipt_path = self.root / 'restores' / (plan['native_request_id'] + '.json')
        retained = _read_owner_only_json(receipt_path)
        if retained is not None: return retained
        manifest = verify_manifest(self.capture.drive, plan['backup_id'])
        with tempfile.TemporaryDirectory(prefix='postgres-restore-', dir=self.root) as temporary:
            work = Path(temporary); ciphertext = work / 'ciphertext'; plaintext = work / 'archive.tar'
            self.capture.drive.get_file(manifest['ciphertext_object'], ciphertext)
            if sha256_file(ciphertext) != manifest['ciphertext_sha256']: raise RecoveryError('ciphertext changed', 'capture_invalid')
            self.capture.crypto.decrypt_file(ciphertext, plaintext)
            if sha256_file(plaintext) != manifest['plaintext_sha256']: raise RecoveryError('plaintext changed', 'capture_invalid')
            with tarfile.open(plaintext, 'r') as bundle:
                members = bundle.getmembers()
                if len(members) != 1 or members[0].name != 'native-capture.tar' or not members[0].isfile() or members[0].size > 512 * 1024 * 1024:
                    raise RecoveryError('recovery archive is invalid', 'capture_invalid')
                archive = bundle.extractfile(members[0]).read()
            result = _load_json_bytes(self.transport.invoke(source, 'restore', plan['native_request_id'], archive=archive, target_volume=plan['target_volume']))
            if not result.get('ok') or result.get('code') not in {'restore_verified', 'storage_restore_verified'} or result.get('target') != plan['target']:
                raise RecoveryError('restore evidence is incomplete', 'restore_verification_failed')
            if plan['profile'] != 'lenzora-prod-storage':
                if result.get('target_database') != plan['target_database'] or result.get('target_role') != plan['target_role']:
                    raise RecoveryError('restored database destination differs', 'restore_verification_failed')
            result.update(plan_digest=plan['plan_digest'], backup_id=plan['backup_id'], source_digest=source.source_digest,
                production_transfer=plan['target_volume'] is not None)
            install_owner_only_json(receipt_path, result)
            return result

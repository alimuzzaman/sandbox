"""Real PostgreSQL schema-reference verification canary.

This canary is intentionally integration-only. It requires an already-installed,
pinned PostgreSQL image and uses real Docker, pg_dump, pg_restore, and catalog
reads. It does not mock SQL or Docker output. It interrupts receipt writes and
commits controlled fixture changes after inspection to check refusal behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from sandbox.recovery import postgres_helper as helper
from tests.subprocess_support import run_test_process, synthetic_environment


class CanaryFailure(ValueError):
    def __init__(self, code, stage='unknown'):
        super().__init__(code)
        self.code = code
        self.stage = stage


def require(condition, code):
    if not condition:
        raise CanaryFailure(code)


def _sha256(value):
    return 'sha256:' + hashlib.sha256(value).hexdigest()


def _pinned_image(image):
    require(bool(re.fullmatch(r'sha256:[a-f0-9]{64}', image)), 'image_not_pinned')


def _schema_fixture_sql():
    """Natural PostgreSQL CHECK patterns with synthetic values only."""
    return (
        "CREATE TABLE public._prisma_migrations (migration_name text, checksum text, "
        "finished_at timestamptz, rolled_back_at timestamptz);"
        "INSERT INTO public._prisma_migrations VALUES ('fixture', 'original', now(), NULL);"
        "CREATE TABLE public.schema_reference_parent ("
        "id integer PRIMARY KEY, marker text NOT NULL"
        ");"
        "CREATE TABLE public.schema_reference_child ("
        "id integer PRIMARY KEY,"
        "status varchar(16) NOT NULL,"
        "revision integer NOT NULL,"
        "parent_id integer NOT NULL,"
        "optional_status varchar(16),"
        "first_col integer,"
        "second_col integer,"
        "CONSTRAINT schema_reference_status_in CHECK (status IN ('x', 'y')),"
        "CONSTRAINT schema_reference_status_not_in CHECK (status NOT IN ('q', 'r')),"
        "CONSTRAINT schema_reference_revision_bounds CHECK (revision BETWEEN 0 AND 10 AND revision >= 0),"
        "CONSTRAINT schema_reference_nested_or CHECK ("
        "optional_status IS NULL OR ((revision BETWEEN 0 AND 10) AND (status ~ '^[a-z]+$'))"
        "),"
        "CONSTRAINT schema_reference_parent_fkey FOREIGN KEY (parent_id) "
        "REFERENCES public.schema_reference_parent(id) ON DELETE CASCADE"
        ");"
        "ALTER TABLE public.schema_reference_child "
        "ADD CONSTRAINT schema_reference_not_valid CHECK (revision >= 0) NOT VALID;"
        "INSERT INTO public.schema_reference_parent (id, marker) "
        "VALUES (1, 'parent-row-sentinel');"
        "INSERT INTO public.schema_reference_child "
        "(id, status, revision, parent_id, optional_status, first_col, second_col) VALUES "
        "(1, 'x', 4, 1, 'x', 11, 21), (2, 'y', 7, 1, 'y', 12, 22);"
    )


def _row_probe_sql():
    return (
        "SELECT id::text || ':' || status || ':' || revision::text || ':' || "
        "parent_id::text || ':' || coalesce(optional_status, '<null>') || ':' || "
        "first_col::text || ':' || second_col::text "
        "FROM public.schema_reference_child ORDER BY id;"
    )


def _parent_probe_sql():
    return "SELECT id::text || ':' || marker FROM public.schema_reference_parent ORDER BY id;"


def _table_counts(observation):
    return {row['name']: row['count'] for row in observation['table_counts']}


def _archive_evidence(archive):
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
        members = tar.getmembers()
        require(sorted(item.name for item in members) == ['database.dump', 'evidence.json'],
                'capture_members_changed')
        evidence = json.loads(tar.extractfile('evidence.json').read())
        dump = tar.extractfile('database.dump').read()
    require(evidence['dump_digest'] == _sha256(dump), 'capture_dump_digest_changed')
    require(evidence.get('schema_fingerprint_version') == 2, 'capture_structure_fingerprint_missing')
    require(bool(re.fullmatch(r'sha256:[a-f0-9]{64}', evidence.get('schema_structure_digest', ''))),
            'capture_structure_fingerprint_invalid')
    return evidence


def _legacy_archive(archive):
    """Build a test-only v1 envelope; the dump and raw fingerprint stay byte-identical."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as source:
        members = {item.name: source.extractfile(item).read() for item in source.getmembers()}
    evidence = json.loads(members['evidence.json'])
    evidence.pop('schema_fingerprint_version', None)
    evidence.pop('schema_structure_digest', None)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as target:
        for name, payload in (('database.dump', members['database.dump']),
                              ('evidence.json', helper.canonical(evidence))):
            info = tarfile.TarInfo(name)
            info.mode = 0o600
            info.size = len(payload)
            target.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def run_canary(image, *, fixture_only=False):
    _pinned_image(image)
    identity = uuid.uuid4().hex + uuid.uuid4().hex
    source_name = 'sandbox-schema-reference-source-' + identity[:24]
    source_volume = source_name + '-data'
    request_root = Path(tempfile.mkdtemp(prefix='sandbox-schema-reference-')).resolve()
    os.chmod(request_root, 0o700)
    environment = synthetic_environment({
        'PATH': '/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin',
        'LANG': 'C.UTF-8',
    })
    calls = []
    containers = {}
    volumes = {}
    reference_commands = []
    reference_names = set()
    reference_creation_calls = {}
    reference_seen = False
    command_records = []
    stage = ['setup']
    last_failure = {}

    def command(argv, *, data=None, timeout=60, output=None):
        nonlocal reference_seen
        calls.append(tuple(argv))
        upper_data = data.upper() if data else b''
        command_records.append((tuple(argv),
            b'CREATE TEMP TABLE RECOVERY_COUNTS' not in upper_data
            and any(keyword in upper_data for keyword in (
                b'CREATE ', b'ALTER ', b'DROP ', b'TRUNCATE ', b'UPDATE ', b'DELETE '))))
        reference_command = any(name in argv for name in reference_names)
        if reference_command and argv[:2] in (['docker', 'run'], ['docker', 'create']):
            reference_seen = True
            reference_commands.append(tuple(argv))
            require('--network' in argv and argv[argv.index('--network') + 1] == 'none',
                    'reference_network_changed')
            require('--read-only' in argv or '--read-only=true' in argv,
                    'reference_not_read_only')
            require(not any(item in {'-p', '--publish', '-P', '--publish-all'} for item in argv),
                    'reference_published')
            require(not any('type=volume' in item or item.startswith('/var/lib/docker') for item in argv),
                    'reference_volume_mount')
            require(all(any(path in item for item in argv) for path in (
                '/var/lib/postgresql/data', '/var/run/postgresql', '/tmp')),
                    'reference_mount_allowlist_changed')
        try:
            result = run_test_process(argv, input=data, stdout=output or subprocess.PIPE,
                stderr=subprocess.PIPE, env=environment, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            last_failure.update(category='command', reason='timeout')
            raise CanaryFailure('command_timeout', stage=stage[0]) from None
        if result.returncode:
            category = argv[1] if argv[:1] == ['docker'] and argv[1] in {
                'run', 'create', 'exec', 'image', 'volume', 'inspect', 'ps', 'stop', 'rm', 'start'} else 'command'
            error = (result.stderr or b'').lower()
            reason = 'unclassified'
            for marker, code in (
                    (b'is not running', 'container_not_running'),
                    (b'cannot connect to the docker daemon', 'daemon_unavailable'),
                    (b'no such image', 'image_unavailable'),
                    (b'exec format error', 'platform_mismatch'),
                    (b'connection refused', 'database_not_ready'),
                    (b'no such file or directory', 'file_unavailable'),
                    (b'password authentication failed', 'authentication_failed')):
                if marker in error:
                    reason = code; break
            last_failure.update(category=category, reason=reason, exit_code=result.returncode)
            raise CanaryFailure('command_failed', stage=stage[0])
        body = result.stdout or b''
        if argv[:2] in (['docker', 'run'], ['docker', 'create']) and '--name' in argv:
            name = argv[argv.index('--name') + 1]
            if name.startswith(('sandbox-schema-reference-', 'sandbox-recovery-restore-',
                                'sandbox-recovery-schema-')):
                container_id = body.decode().strip()
                require(bool(re.fullmatch(r'[a-f0-9]{64}', container_id)),
                        'container_identity_invalid')
                containers[name] = container_id
        if argv[:3] == ['docker', 'volume', 'create']:
            volume = argv[-1]
            if volume.startswith(('sandbox-schema-reference-', 'sandbox-recovery-restore-',
                                  'sandbox-recovery-schema-')):
                labels = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == '--label']
                owners = [value.split('=', 1)[1] for value in labels if value.startswith('sandbox.recovery.owner=')]
                require(len(owners) == 1, 'volume_owner_missing')
                volumes[volume] = owners[0]
        return body

    def docker(*argv):
        return command(['docker', *argv])

    def inspect_container(name):
        require(name in containers, 'container_identity_missing')
        rows = json.loads(docker('inspect', containers[name]))
        require(len(rows) == 1, 'container_identity_invalid')
        row = rows[0]
        require(row.get('Id') == containers[name] and row.get('Name') == '/' + name,
                'container_identity_changed')
        config = row.get('Config') or {}
        host = row.get('HostConfig') or {}
        require(row.get('Image') == image
                and config.get('Labels', {}).get('sandbox.recovery.owner') == name
                and host.get('NetworkMode') == 'none'
                and not host.get('PortBindings'), 'container_ownership_changed')
        mounts = row.get('Mounts') or []
        if name == source_name:
            require(config.get('Labels', {}).get('com.docker.compose.project') == source_name
                    and host.get('ReadonlyRootfs') is not True
                    and not host.get('Tmpfs')
                    and len(mounts) == 1
                    and mounts[0].get('Type') == 'volume'
                    and mounts[0].get('Name') == source_volume
                    and mounts[0].get('Destination') == '/var/lib/postgresql/data',
                    'source_mount_changed')
        elif name.startswith('sandbox-recovery-schema-'):
            expected_tmpfs = {
                '/var/lib/postgresql/data': 'rw,noexec,nosuid,nodev,size=536870912,mode=0700',
                '/var/run/postgresql': 'rw,noexec,nosuid,nodev,size=1048576,mode=0775',
                '/tmp': 'rw,noexec,nosuid,nodev,size=16777216,mode=1777',
            }
            require(host.get('ReadonlyRootfs') is True
                    and host.get('Tmpfs') == expected_tmpfs
                    and all(item.get('Type') == 'tmpfs'
                            and item.get('Destination') in expected_tmpfs for item in mounts),
                    'reference_mount_changed')
        elif name.startswith('sandbox-recovery-restore-'):
            expected_volume = name + '-data'
            require(host.get('ReadonlyRootfs') is not True
                    and host.get('Tmpfs') == {'/run/recovery': 'rw,noexec,nosuid,mode=0700'}
                    and all((item.get('Type') == 'volume'
                             and item.get('Name') == expected_volume
                             and item.get('Destination') == '/var/lib/postgresql/data')
                            or (item.get('Type') == 'tmpfs'
                                and item.get('Destination') == '/run/recovery')
                            for item in mounts)
                    and sum(item.get('Type') == 'volume' for item in mounts) == 1
                    and any(item.get('Type') == 'volume'
                            and item.get('Name') == expected_volume
                            and item.get('Destination') == '/var/lib/postgresql/data'
                            for item in mounts),
                    'target_mount_changed')
        else:
            raise CanaryFailure('unknown_owned_container')
        return row

    def ensure_database(client, database):
        deadline = time.monotonic() + 90
        while True:
            try:
                helper.sql(client, database, 'SELECT 1;')
                return
            except ValueError:
                if time.monotonic() >= deadline:
                    raise CanaryFailure('database_readiness_timeout') from None
                time.sleep(1)

    def make_request(operation, request_id, archive=b''):
        return {
            'operation': operation,
            'source': source,
            'request_id': request_id,
            'root': str(request_root),
            'credential_size': 0,
            'credential_revision': None,
            'archive_size': len(archive),
            'archive_digest': _sha256(archive),
            'target_volume': None,
        }

    def invoke(request, archive=b'', *, fault_receipt=False):
        output = io.BytesIO()
        incoming = helper.canonical(request) + b'\n' + archive
        slot = request_root / request['request_id']
        original_write = helper.private_write

        def interrupt_receipt(path, data):
            if Path(path) == slot / 'result.json':
                raise OSError('canary_receipt_interrupted')
            original_write(path, data)

        writer = patch.object(helper, 'private_write', side_effect=interrupt_receipt) \
            if fault_receipt else patch.object(helper, 'private_write', wraps=original_write)
        with patch.object(helper, 'run', side_effect=command), \
                patch.object(helper, 'ENV', environment), \
                patch.object(helper.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(incoming))), \
                patch.object(helper.sys, 'stdout', SimpleNamespace(buffer=output)), \
                writer:
            helper.main()
        return output.getvalue()

    def invoke_json(request, archive=b'', *, fault_receipt=False):
        return json.loads(invoke(request, archive, fault_receipt=fault_receipt))

    def restore_without_receipt(request_id, archive):
        expected_reference = 'sandbox-recovery-schema-' + request_id[:24]
        reference_names.add(expected_reference)
        request = make_request('restore', request_id, archive)
        slot = request_root / request_id
        restore_start = len(calls)
        try:
            invoke(request, archive, fault_receipt=True)
        except OSError as error:
            require(str(error) == 'canary_receipt_interrupted', 'unexpected_receipt_failure')
        else:
            raise CanaryFailure('receipt_interruption_missing')
        creations = [call for call in calls[restore_start:]
                     if call[:2] in (('docker', 'create'), ('docker', 'run'))
                     and '--name' in call and call[call.index('--name') + 1] == expected_reference]
        require(creations, 'reference_creation_missing_during_restore')
        reference_creation_calls[request_id] = creations
        require(not (slot / 'result.json').exists(), 'premature_verified_receipt')
        inspection_request = {**request, 'operation': 'inspect-restore'}
        inspected = invoke_json(inspection_request, archive)
        if inspected.get('code') == 'restore_target_stopped':
            plan = inspected.get('reopen_plan')
            require(isinstance(plan, dict), 'reopen_plan_missing')
            reopened = invoke_json({**request, 'operation': 'reopen-restore', 'reopen_plan': plan}, archive)
            require(reopened.get('code') == 'restore_reopened', 'reopen_failed')
            inspected = invoke_json(inspection_request, archive)
        require(inspected.get('code') == 'restore_inspected' and inspected.get('database_available') is True,
                'restore_inspection_failed')
        assert_reference_gone(expected_reference)
        return request, slot, inspected

    def expect_verify_refusal(request, archive, slot, code='restore_verification_failed'):
        verify_start = len(calls)
        try:
            invoke_json({**request, 'operation': 'verify-restore'}, archive)
        except ValueError as error:
            require(str(error) in {code, 'schema_evidence_invalid'}, 'unexpected_negative_code')
        else:
            raise CanaryFailure('negative_receipt_accepted')
        expected_reference = 'sandbox-recovery-schema-' + request['request_id'][:24]
        require(not any(call[:2] in (('docker', 'create'), ('docker', 'run'))
                        and '--name' in call and call[call.index('--name') + 1] == expected_reference
                        for call in calls[verify_start:]), 'negative_created_reference')
        require(not (slot / 'result.json').exists(), 'negative_receipt_written')
        found = docker('ps', '-aq', '--filter', 'name=^' + expected_reference + '$').decode().strip()
        require(not found, 'reference_retained_after_refusal')

    def verify_raw_baseline(inspected, captured):
        require(inspected.get('all_match') is False, 'fixture_did_not_trigger_raw_mismatch')
        matches = inspected.get('matches') or {}
        require(matches.get('schema_digest') is False, 'schema_digest_did_not_mismatch')
        require(all(matches.get(key) is True for key in matches if key != 'schema_digest'),
                'non_schema_fixture_drifted')
        require(_table_counts(inspected['observation']) == _table_counts(captured),
                'fixture_table_counts_changed')
        diagnostic = inspected.get('schema_diagnostic') or {}
        require(diagnostic.get('code') == 'schema_compared' and diagnostic.get('difference_count', 0) > 0,
                'schema_difference_not_diagnosed')
        return diagnostic

    def mutate_target(client, sql):
        helper.sql(client, 'lenzora', sql)
        current = helper.observation(client, 'lenzora')
        require(_table_counts(current) == captured_counts, 'negative_table_count_changed')

    def assert_reference_gone(name):
        found = docker('ps', '-aq', '--filter', 'name=^' + name + '$').decode().strip()
        require(not found, 'reference_not_cleaned')
        volume_matches = docker('volume', 'ls', '-q', '--filter', 'name=^' + name + '$').decode().strip()
        require(not volume_matches, 'reference_volume_retained')

    try:
        stage[0] = 'pinned_image_preflight'
        reference_name = None
        with patch.object(helper, 'run', side_effect=command), patch.object(helper, 'ENV', environment):
            images = json.loads(docker('image', 'inspect', image))
            require(len(images) == 1 and images[0].get('Id') == image, 'pinned_image_unavailable')
            stage[0] = 'source_fixture'
            docker('volume', 'create', '--label', 'sandbox.recovery.owner=' + source_name, source_volume)
            docker('run', '-d', '--pull', 'never', '--name', source_name,
                '--label', 'sandbox.recovery.owner=' + source_name,
                '--label', 'com.docker.compose.project=' + source_name,
                '--network', 'none', '--mount',
                'type=volume,source=' + source_volume + ',target=/var/lib/postgresql/data',
                '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_USER=postgres',
                '-e', 'POSTGRES_DB=lenzora', image)
            source = {'schema_version': 1, 'profile': 'lenzora-dev', 'remote': 'canary',
                'compose_project': source_name, 'container_id': containers[source_name],
                'volume': source_volume, 'database': 'lenzora', 'role': 'postgres',
                'image_id': image, 'client_image_id': image,
                'credential_reference': None, 'target_password_reference': None}
            source_client = helper.local_client(source)
            ensure_database(source_client, 'lenzora')
            helper.sql(source_client, 'lenzora', _schema_fixture_sql())
            source_rows = helper.sql(source_client, 'lenzora', _row_probe_sql())
            require(source_rows == '1:x:4:1:x:11:21\n2:y:7:1:y:12:22', 'source_rows_changed')
            source_parent_rows = helper.sql(source_client, 'lenzora', _parent_probe_sql())
            require(source_parent_rows == '1:parent-row-sentinel', 'source_parent_rows_changed')
            if fixture_only:
                stage[0] = 'verification_checkpoint'
                actual, records = helper.verification_checkpoint(source_client, 'lenzora')
                require(actual['schema_structure_digest'] == helper.schema_structure_digest(records),
                        'checkpoint_structure_changed')
                return {'ok': True, 'code': 'schema_reference_fixture_passed',
                        'pinned_image_executed': True, 'readonly_checkpoint_executed': True}

            stage[0] = 'capture'
            capture_id = uuid.uuid4().hex + uuid.uuid4().hex
            capture_request = make_request('capture', capture_id)
            archive = invoke(capture_request)
            require(len(archive) > 0, 'capture_empty')
            captured = _archive_evidence(archive)
            captured_counts = _table_counts(captured)
            require(captured_counts.get('schema_reference_parent') == 1
                    and captured_counts.get('schema_reference_child') == 2,
                    'capture_fixture_counts_missing')

            # One retained target exercises all negative controls before the positive target.
            stage[0] = 'negative_restore'
            negative_id = uuid.uuid4().hex + uuid.uuid4().hex
            negative_request, negative_slot, baseline = restore_without_receipt(negative_id, archive)
            verify_raw_baseline(baseline, captured)
            target_name = 'sandbox-recovery-restore-' + negative_id[:24]
            target_client = ['docker', 'exec', '-i', '--user', 'postgres', containers[target_name]]
            require(helper.sql(target_client, 'lenzora', _row_probe_sql()) == source_rows,
                    'negative_target_rows_changed')
            require(helper.sql(target_client, 'lenzora', _parent_probe_sql()) == source_parent_rows,
                    'negative_parent_rows_changed')

            stage[0] = 'negative_controls'
            # Commit mutations after the real inspect has returned its payload.
            # The acceptance checkpoint must observe them and leave no receipt.
            original_inspect = helper.inspect_restore
            races = (
                ("INSERT INTO public.schema_reference_parent VALUES (2, 'late');",
                 "DELETE FROM public.schema_reference_parent WHERE id=2;"),
                ("ALTER TABLE public.schema_reference_child ADD COLUMN late_column integer;",
                 "ALTER TABLE public.schema_reference_child DROP COLUMN late_column;"),
                ("UPDATE public._prisma_migrations SET checksum='late';",
                 "UPDATE public._prisma_migrations SET checksum='original';"),
            )
            for mutation, undo in races:
                fired = []
                def inspected_then_changed(*args, **kwargs):
                    result = original_inspect(*args, **kwargs)
                    helper.sql(target_client, 'lenzora', mutation)
                    fired.append(True)
                    # A second supported operation cannot enter this request.
                    try:
                        invoke_json({**negative_request, 'operation': 'status'}, archive)
                    except BlockingIOError:
                        pass
                    else:
                        raise CanaryFailure('original_request_lock_not_held')
                    return result
                with patch.object(helper, 'inspect_restore', side_effect=inspected_then_changed):
                    expect_verify_refusal(negative_request, archive, negative_slot)
                require(fired == [True], 'after_inspect_mutation_missing')
                helper.sql(target_client, 'lenzora', undo)
            # Same table and row counts, changed CHECK literal.
            mutate_target(target_client,
                "ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_status_in;"
                "ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_status_in "
                "CHECK (status IN ('x', 'y', 'z'));"
            )
            expect_verify_refusal(negative_request, archive, negative_slot)
            helper.sql(target_client, 'lenzora',
                "ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_status_in;"
                "ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_status_in "
                "CHECK (status IN ('x', 'y'));"
            )

            # Same table and row counts, changed CHECK comparison operator.
            mutate_target(target_client,
                "ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_revision_bounds;"
                "ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_revision_bounds "
                "CHECK (revision BETWEEN 0 AND 10 AND revision > 0);"
            )
            expect_verify_refusal(negative_request, archive, negative_slot)
            helper.sql(target_client, 'lenzora',
                "ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_revision_bounds;"
                "ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_revision_bounds "
                "CHECK (revision BETWEEN 0 AND 10 AND revision >= 0);"
            )

            # Same table and row counts, changed FK action.
            mutate_target(target_client,
                "ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_parent_fkey;"
                "ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_parent_fkey "
                "FOREIGN KEY (parent_id) REFERENCES public.schema_reference_parent(id) ON DELETE RESTRICT;"
            )
            expect_verify_refusal(negative_request, archive, negative_slot)
            helper.sql(target_client, 'lenzora',
                "ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_parent_fkey;"
                "ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_parent_fkey "
                "FOREIGN KEY (parent_id) REFERENCES public.schema_reference_parent(id) ON DELETE CASCADE;"
            )

            # Same table and row counts, changed nullability.
            mutate_target(target_client,
                'ALTER TABLE public.schema_reference_child ALTER COLUMN optional_status SET NOT NULL;'
            )
            expect_verify_refusal(negative_request, archive, negative_slot)
            helper.sql(target_client, 'lenzora',
                'ALTER TABLE public.schema_reference_child ALTER COLUMN optional_status DROP NOT NULL;'
            )

            # Same table and row counts, changed NOT VALID state.
            mutate_target(target_client,
                'ALTER TABLE public.schema_reference_child VALIDATE CONSTRAINT schema_reference_not_valid;'
            )
            expect_verify_refusal(negative_request, archive, negative_slot)
            helper.sql(target_client, 'lenzora',
                'ALTER TABLE public.schema_reference_child DROP CONSTRAINT schema_reference_not_valid;'
                'ALTER TABLE public.schema_reference_child ADD CONSTRAINT schema_reference_not_valid '
                'CHECK (revision >= 0) NOT VALID;'
            )

            # Same table and row counts, reorder columns by dropping/re-adding one column.
            mutate_target(target_client,
                'ALTER TABLE public.schema_reference_child DROP COLUMN first_col;'
                'ALTER TABLE public.schema_reference_child ADD COLUMN first_col integer;'
            )
            expect_verify_refusal(negative_request, archive, negative_slot)
            negative_target = inspect_container(target_name)
            if negative_target['State']['Running']:
                docker('stop', '--time', '30', negative_target['Id'])

            # Fresh positive target: prove reference acceptance after the raw mismatch.
            stage[0] = 'positive_restore'
            positive_id = uuid.uuid4().hex + uuid.uuid4().hex
            legacy_archive = _legacy_archive(archive)
            positive_request, positive_slot, positive_baseline = restore_without_receipt(positive_id, legacy_archive)
            verify_raw_baseline(positive_baseline, captured)
            positive_name = 'sandbox-recovery-restore-' + positive_id[:24]
            positive_client = ['docker', 'exec', '-i', '--user', 'postgres', containers[positive_name]]
            require(helper.sql(positive_client, 'lenzora', _row_probe_sql()) == source_rows,
                    'positive_target_rows_changed')
            require(helper.sql(positive_client, 'lenzora', _parent_probe_sql()) == source_parent_rows,
                    'positive_parent_rows_changed')
            reference_name = 'sandbox-recovery-schema-' + positive_id[:24]
            reference_names.add(reference_name)
            stage[0] = 'positive_verify'
            before_verify = len(calls)
            verified = invoke_json({**positive_request, 'operation': 'verify-restore'}, legacy_archive)
            require(verified.get('code') == 'restore_verified' and verified.get('ok') is True,
                    'reference_verification_failed')
            proof = verified.get('schema_verification') or {}
            require(proof.get('method') == 'archived-schema-reference-v1',
                    'reference_method_missing')
            require(proof.get('captured_schema_digest') == captured.get('schema_digest'),
                    'reference_capture_binding_changed')
            require(proof.get('observed_schema_digest') != proof.get('captured_schema_digest'),
                    'raw_mismatch_not_retained')
            require(proof.get('target_schema_digest') == proof.get('reference', {}).get('reference_schema_digest'),
                    'reference_target_digest_changed')
            reference = proof.get('reference') or {}
            for field in ('reference_schema_digest', 'structure_digest', 'completed_at'):
                require(field in reference, 'reference_binding_incomplete')
            require(positive_slot.joinpath('result.json').exists(), 'verified_receipt_missing')
            receipt_bytes = positive_slot.joinpath('result.json').read_bytes()
            require(b'parent-row-sentinel' not in receipt_bytes, 'sentinel_leaked_to_receipt')
            require(b'1:x:4:1:x:11:21' not in receipt_bytes, 'row_probe_leaked_to_receipt')
            require(b"^[a-z]+$" not in receipt_bytes
                    and b'schema_reference_status_in' not in receipt_bytes,
                    'raw_schema_leaked_to_receipt')
            assert_reference_gone(reference_name)
            require(reference_seen and reference_commands, 'reference_container_not_observed')
            require(reference_creation_calls.get(positive_id), 'reference_creation_missing')
            require(not any(call[:2] in (('docker', 'create'), ('docker', 'run'))
                            and '--name' in call and call[call.index('--name') + 1] == reference_name
                            for call in calls[before_verify:]), 'reference_created_during_verify')
            for argv, mutating_sql in command_records[before_verify:]:
                if mutating_sql and any(container_id in argv for container_id in (
                        source['container_id'], containers[positive_name])):
                    raise CanaryFailure('source_or_target_ddl_during_reference')

            replay_before = len(calls)
            replay = invoke({**positive_request, 'operation': 'verify-restore'}, legacy_archive)
            require(replay == json.dumps(verified, sort_keys=True, separators=(',', ':')).encode(),
                    'receipt_replay_changed')
            require(not any(reference_name in call for call in calls[replay_before:]),
                    'receipt_replay_created_reference')
            positive_target = inspect_container(positive_name)
            require(positive_target['State']['Running'] is False, 'verified_target_still_running')
            source_after = inspect_container(source_name)
            require(source_after['Id'] == source['container_id'] and source_after['State']['Running'] is True,
                    'source_identity_or_state_changed')
            require(helper.sql(source_client, 'lenzora', _row_probe_sql()) == source_rows,
                    'source_rows_changed')
            require(helper.sql(source_client, 'lenzora', _parent_probe_sql()) == source_parent_rows,
                    'source_parent_rows_changed')
            return {
                'ok': True,
                'code': 'schema_reference_canary_passed',
                'real_capture_restore': True,
                'raw_schema_mismatch_proven': True,
                'negative_controls_refused': 9,
                'after_inspect_mutations_refused': 3,
                'original_request_lock_verified': True,
                'reference_method': 'archived-schema-reference-v1',
                'reference_cleaned': True,
                'receipt_replay_without_reference': True,
                'source_identity_preserved': True,
                'sentinel_rows_preserved': True,
            }
    except CanaryFailure as error:
        if error.stage == 'unknown':
            error.stage = stage[0]
        print(json.dumps({'ok': False, 'stage': error.stage, 'code': error.code,
                          'last_command_failure': last_failure}))
        raise
    except Exception:
        raise CanaryFailure('schema_reference_canary_failed', stage=stage[0]) from None
    finally:
        primary_failure = sys.exc_info()[1]
        stage[0] = 'cleanup'
        cleanup_error = None

        def record_cleanup_error(error):
            nonlocal cleanup_error
            if cleanup_error is None:
                cleanup_error = error

        try:
            # Remove only containers and volumes recorded from this canary.
            for name, container_id in list(containers.items()):
                # Reference containers are deliberately removed by the helper;
                # their absence is checked below as the expected terminal state.
                if name in reference_names:
                    continue
                try:
                    found = docker('ps', '-aq', '--no-trunc', '--filter', 'id=' + container_id).decode().split()
                    require(found, 'cleanup_container_missing')
                    row = inspect_container(name)
                    owner = row.get('Config', {}).get('Labels', {}).get('sandbox.recovery.owner')
                    require(owner == name, 'cleanup_owner_changed')
                    if row.get('State', {}).get('Running'):
                        docker('stop', '--time', '30', container_id)
                    docker('rm', '-f', container_id)
                except Exception as error:
                    record_cleanup_error(error)
            for volume in list(volumes):
                try:
                    rows = json.loads(docker('volume', 'inspect', volume))
                    require(len(rows) == 1 and rows[0].get('Name') == volume
                            and rows[0].get('Labels', {}).get('sandbox.recovery.owner') == volumes[volume],
                            'cleanup_volume_owner_changed')
                    require(not docker('ps', '-aq', '--filter', 'volume=' + volume).strip(),
                            'cleanup_volume_in_use')
                    docker('volume', 'rm', volume)
                except Exception as error:
                    record_cleanup_error(error)
            for name in reference_names:
                try:
                    assert_reference_gone(name)
                except Exception as error:
                    record_cleanup_error(error)
        except Exception as error:
            record_cleanup_error(error)
        if cleanup_error is None:
            try:
                shutil.rmtree(request_root)
            except Exception as error:
                record_cleanup_error(error)
        if cleanup_error is not None:
            print(json.dumps({'ok': False, 'stage': 'cleanup', 'code': 'cleanup_refused',
                'primary_stage': getattr(primary_failure, 'stage', None),
                'primary_code': getattr(primary_failure, 'code', None),
                'owned_containers': containers, 'owned_volumes': volumes}))
            raise CanaryFailure('cleanup_refused', stage='cleanup') from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--fixture-only', action='store_true')
    args = parser.parse_args()
    try:
        result = run_canary(args.image, fixture_only=args.fixture_only)
    except CanaryFailure as error:
        print(json.dumps({'ok': False, 'stage': error.stage, 'code': error.code}))
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'stage': 'unknown', 'code': 'schema_reference_canary_failed'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

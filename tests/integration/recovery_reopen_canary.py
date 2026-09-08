"""Real PostgreSQL capture, interrupted receipt, reopen, and verification.

Runs only when explicitly invoked on a Docker host with a pinned PostgreSQL
image. No SQL or Docker result is mocked. The sole fault injection interrupts
writing the first restore receipt, after the real restore has completed.
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
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from sandbox.recovery import postgres_helper as helper
from tests.subprocess_support import run_test_process, synthetic_environment


class CanaryFailure(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CanaryFailure(code)


def run_canary(image):
    require(bool(re.fullmatch(r'sha256:[a-f0-9]{64}', image)), 'image_not_pinned')
    identity = uuid.uuid4().hex + uuid.uuid4().hex
    target = 'sandbox-recovery-restore-' + identity[:24]
    source_name = 'sandbox-reopen-source-' + identity[:24]
    expected = {name: name + '-data' for name in (source_name, target)}
    containers, volumes, calls = {}, set(), []
    environment = synthetic_environment({'PATH': '/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin', 'LANG': 'C.UTF-8'})
    root = Path(tempfile.mkdtemp(prefix='sandbox-reopen-canary-')).resolve()
    os.chmod(root, 0o700)

    def command(argv, *, data=None, timeout=60, output=None):
        calls.append(tuple(argv))
        result = run_test_process(argv, input=data, stdout=output or subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, timeout=timeout, check=False)
        if result.returncode:
            raise ValueError('operation_failed')
        body = result.stdout or b''
        if argv[:2] in (['docker', 'run'], ['docker', 'create']) and '--name' in argv:
            name = argv[argv.index('--name') + 1]
            if name in expected:
                cid = body.decode().strip()
                require(bool(re.fullmatch(r'[a-f0-9]{64}', cid)), 'container_identity_invalid')
                containers[name] = cid
        if argv[:3] == ['docker', 'volume', 'create'] and argv[-1] in expected.values():
            volumes.add(argv[-1])
        return body

    def docker(*argv):
        return command(['docker', *argv])

    def inspect(name):
        rows = json.loads(docker('inspect', containers[name]))
        require(len(rows) == 1, 'container_identity_invalid')
        row = rows[0]
        require(row['Id'] == containers[name] and row['Name'] == '/' + name
            and row['Image'] == image
            and row['Config'].get('Labels', {}).get('sandbox.recovery.owner') == name
            and row['HostConfig']['NetworkMode'] == 'none'
            and not row['HostConfig'].get('PortBindings'), 'container_identity_invalid')
        mounts = row['Mounts']
        require(any(m.get('Type') == 'volume' and m.get('Name') == expected[name]
            and m.get('Destination') == '/var/lib/postgresql/data' for m in mounts)
            and all((m.get('Type') == 'volume' and m.get('Name') == expected[name]
                     and m.get('Destination') == '/var/lib/postgresql/data')
                or (m.get('Type') == 'tmpfs' and m.get('Destination') == '/run/recovery')
                for m in mounts), 'container_mount_changed')
        return row

    def cleanup():
        # Revalidate every owned object before deleting any fixture.
        rows = {name: inspect(name) for name in containers}
        for volume in volumes:
            row = json.loads(docker('volume', 'inspect', volume))[0]
            owner = next(name for name, bound in expected.items() if bound == volume)
            require(row['Name'] == volume and row.get('Labels', {}).get('sandbox.recovery.owner') == owner,
                'cleanup_refused')
        for name, row in rows.items():
            if row['State']['Running']:
                docker('stop', '--time', '30', containers[name])
            docker('rm', '-v', containers[name])
        for volume in volumes:
            docker('volume', 'rm', volume)
        shutil.rmtree(root)

    try:
        with patch.object(helper, 'run', side_effect=command), patch.object(helper, 'ENV', environment):
            docker('volume', 'create', '--label', 'sandbox.recovery.owner=' + source_name, expected[source_name])
            docker('run', '-d', '--pull', 'never', '--name', source_name,
                '--label', 'sandbox.recovery.owner=' + source_name,
                '--label', 'com.docker.compose.project=' + source_name,
                '--network', 'none', '--mount',
                'type=volume,source=' + expected[source_name] + ',target=/var/lib/postgresql/data',
                '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_USER=postgres',
                '-e', 'POSTGRES_DB=lenzora', image)
            source = {'schema_version': 1, 'profile': 'lenzora-dev', 'remote': 'canary',
                'compose_project': source_name, 'container_id': containers[source_name],
                'volume': expected[source_name], 'database': 'lenzora', 'role': 'postgres',
                'image_id': image, 'client_image_id': image,
                'credential_reference': None, 'target_password_reference': None}
            client = helper.local_client(source)
            deadline = time.monotonic() + 90
            while True:
                try:
                    helper.sql(client, 'lenzora', 'SELECT 1;')
                    break
                except ValueError:
                    require(time.monotonic() < deadline, 'database_readiness_timeout')
                    time.sleep(1)
            helper.sql(client, 'lenzora',
                "CREATE TABLE public.reopen_canary (id integer PRIMARY KEY, value text NOT NULL);"
                "INSERT INTO public.reopen_canary VALUES (1, 'retained-row');")
            capture_work = root / 'capture'; capture_work.mkdir(mode=0o700)
            archive = helper.capture(source, client, capture_work).read_bytes()
            request_root = root / 'requests'; slot = request_root / identity
            request = {'source': source, 'request_id': identity, 'root': str(request_root),
                'credential_size': 0, 'credential_revision': None, 'archive_size': len(archive),
                'archive_digest': 'sha256:' + hashlib.sha256(archive).hexdigest(), 'target_volume': None}

            def invoke(operation, **extra):
                output = io.BytesIO()
                incoming = helper.canonical({**request, 'operation': operation, **extra}) + b'\n' + archive
                with patch.object(helper.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(incoming))), \
                        patch.object(helper.sys, 'stdout', SimpleNamespace(buffer=output)):
                    helper.main()
                return json.loads(output.getvalue())

            original_write = helper.private_write
            def interrupt_receipt(path, data):
                if path == slot / 'result.json':
                    raise OSError('canary_receipt_interrupted')
                original_write(path, data)
            with patch.object(helper, 'private_write', side_effect=interrupt_receipt):
                try:
                    invoke('restore')
                except OSError as error:
                    require(str(error) == 'canary_receipt_interrupted', 'unexpected_restore_failure')
                else:
                    raise CanaryFailure('receipt_interruption_missing')
            require(not (slot / 'result.json').exists(), 'premature_verified_receipt')
            saved_request = (slot / 'request.json').read_bytes()
            stopped = inspect(target)['State']
            stopped_state = {key: stopped[key] for key in ('Status', 'Running', 'Pid', 'ExitCode', 'OOMKilled')}
            print(json.dumps({'stopped_fixture': stopped_state}), flush=True)
            inspected = invoke('inspect-restore')
            require(inspected['code'] == 'restore_target_stopped' and not inspected['all_match'], 'stopped_inspection_failed')
            plan = inspected['reopen_plan']; before = len(calls)
            reopened = invoke('reopen-restore', reopen_plan=plan)
            require(reopened['code'] == 'restore_reopened' and reopened['container_id'] == containers[target], 'reopen_failed')
            writes = [call for call in calls[before:] if call[:2] == ('docker', 'start')
                or call[:3] == ('docker', 'exec', '-d')]
            require(writes == [('docker', 'start', containers[target]),
                ('docker', 'exec', '-d', '--user', 'postgres', containers[target],
                 'postgres', '-D', '/var/lib/postgresql/data')], 'unexpected_reopen_effect')
            before = len(calls)
            require(invoke('reopen-restore', reopen_plan=plan) == reopened, 'replay_result_changed')
            require(not any(call[:2] == ('docker', 'start') or call[:3] == ('docker', 'exec', '-d')
                for call in calls[before:]), 'replay_repeated_start')
            restored_client = ['docker', 'exec', '-i', '--user', 'postgres', containers[target]]
            require(helper.sql(restored_client, 'lenzora', 'SELECT value FROM public.reopen_canary WHERE id=1;') == 'retained-row',
                'restored_row_changed')
            require(invoke('inspect-restore')['all_match'] is True, 'restored_evidence_mismatch')
            require(not (slot / 'result.json').exists(), 'premature_verified_receipt')
            require(invoke('verify-restore')['code'] == 'restore_verified', 'verification_failed')
            require(json.loads((slot / 'result.json').read_bytes())['code'] == 'restore_verified', 'verified_receipt_missing')
            require((slot / 'request.json').read_bytes() == saved_request, 'request_identity_changed')
            require(inspect(target)['State']['Running'] is False, 'verified_target_still_running')
            return {'ok': True, 'code': 'reopen_canary_passed', 'real_capture_restore_verified': True,
                'container_id_preserved': True, 'sentinel_row_preserved': True,
                'replay_without_start': True, 'receipt_only_after_verification': True}
    finally:
        try:
            cleanup()
        except Exception:
            raise CanaryFailure('cleanup_refused') from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    try:
        result = run_canary(args.image)
    except CanaryFailure as error:
        print(json.dumps({'ok': False, 'code': str(error)}))
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'code': 'reopen_canary_failed'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

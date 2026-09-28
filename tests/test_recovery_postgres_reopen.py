import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.recovery import postgres_helper as helper
from tests.test_recovery_postgres_helper import capture_archive, source


IDENTITY = 'a' * 64
NAME = 'sandbox-recovery-restore-' + IDENTITY[:24]
CONTAINER = 'c' * 64


def marker_tar(name, content):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo(Path(name).name); member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


class ReopenDocker:
    def __init__(self):
        self.calls = []
        self.database_running = False
        self.markers = {'PG_VERSION': b'16\n', 'global/pg_control': b'private-control-canary' * 64}
        self.row = {'Id': CONTAINER, 'Name': '/' + NAME, 'Image': source()['client_image_id'],
            'Config': {'Labels': {'sandbox.recovery.owner': NAME}, 'Entrypoint': ['sleep'],
                'Cmd': ['3600'], 'User': '', 'Env': ['POSTGRES_HOST_AUTH_METHOD=scram-sha-256',
                    'POSTGRES_USER=postgres', 'POSTGRES_DB=lenzora']},
            'State': {'Status': 'exited', 'Running': False, 'Paused': False, 'Restarting': False,
                'Dead': False, 'OOMKilled': False, 'Pid': 0, 'ExitCode': 0,
                'StartedAt': '2026-09-08T10:00:00Z', 'FinishedAt': '2026-09-08T11:00:00Z'},
            'HostConfig': {'NetworkMode': 'none', 'PortBindings': {}, 'RestartPolicy': {'Name': 'no'},
                'Privileged': False, 'PublishAllPorts': False, 'PidMode': '', 'UTSMode': '',
                'UsernsMode': '', 'IpcMode': 'private',
                'Tmpfs': {'/run/recovery': 'rw,noexec,nosuid,mode=0700'}},
            'Mounts': [{'Type': 'volume', 'Name': NAME + '-data',
                'Destination': '/var/lib/postgresql/data', 'RW': True}]}
        self.volume = {'Name': NAME + '-data', 'Driver': 'local', 'Options': None,
            'Scope': 'local', 'Labels': {'sandbox.recovery.owner': NAME}}
        self.consumers = [CONTAINER]
        self.daemon = 'daemon-a'
        self.after_start_failure = False

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ['docker', 'inspect']:
            return json.dumps([self.row]).encode()
        if argv[:3] == ['docker', 'volume', 'inspect']:
            return json.dumps([self.volume]).encode()
        if argv[:2] == ['docker', 'ps']: return '\n'.join(self.consumers).encode()
        if argv[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': self.row['Image'], 'Config': {'Env': []}}]).encode()
        if argv[:2] == ['docker', 'info']: return self.daemon.encode()
        if argv[:2] == ['docker', 'cp']:
            prefix = CONTAINER + ':/var/lib/postgresql/data/'
            if not argv[2].startswith(prefix): raise AssertionError('wrong marker target')
            name = argv[2][len(prefix):]
            return marker_tar(name, self.markers[name])
        if argv == ['docker', 'start', CONTAINER]:
            self.row['State'].update(Status='running', Running=True, Pid=1,
                StartedAt='2026-09-08T12:00:00Z', FinishedAt='0001-01-01T00:00:00Z')
            if self.after_start_failure: raise ValueError('operation_failed')
            return b''
        if argv == ['docker', 'exec', '-d', '--user', 'postgres', CONTAINER,
                    'postgres', '-D', '/var/lib/postgresql/data']:
            self.database_running = True
            return b''
        if argv[:2] == ['docker', 'exec'] and 'psql' in argv:
            if not self.database_running: raise ValueError('operation_failed')
            return b'16\n'
        raise AssertionError('unexpected command')

    def writes(self):
        return [argv for argv in self.calls if argv[:2] == ['docker', 'start']
                or argv[:3] == ['docker', 'exec', '-d']]

    def expire(self):
        self.row['State'].update(Status='exited', Running=False, Pid=0,
            FinishedAt='2026-09-08T13:00:00Z')
        self.database_running = False


class ReopenHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))
        self.slot = self.root / IDENTITY; self.slot.mkdir(mode=0o700)
        self.archive_bytes, self.evidence = capture_archive()
        self.archive = self.root / 'native.tar'; self.archive.write_bytes(self.archive_bytes)
        self.docker = ReopenDocker()
        self.runner = patch.object(helper, 'run', side_effect=self.docker)
        self.runner.start(); self.addCleanup(self.runner.stop)

    def work(self):
        return Path(tempfile.mkdtemp(dir=self.root))

    def plan(self):
        return helper.prepare_reopen(source(), self.archive, self.evidence, NAME, self.slot)

    def apply(self, plan):
        return helper.reopen_restore(source(), self.archive, self.work(), NAME, self.slot, plan)

    def request(self, operation='restore', **extra):
        return {'operation': operation, 'source': source(), 'request_id': IDENTITY,
            'root': str(self.root), 'credential_size': 0, 'credential_revision': None,
            'archive_size': len(self.archive_bytes),
            'archive_digest': 'sha256:' + hashlib.sha256(self.archive_bytes).hexdigest(),
            'target_volume': None, **extra}

    def invoke_main(self, request):
        output = io.BytesIO()
        with patch.object(helper.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(
                helper.canonical(request) + b'\n' + self.archive_bytes))), \
                patch.object(helper.sys, 'stdout', SimpleNamespace(buffer=output)):
            helper.main()
        return json.loads(output.getvalue())

    def test_stopped_inspection_is_read_only_and_plan_contains_no_private_marker_bytes(self):
        result = helper.inspect_restore(source(), self.archive, self.work(), NAME, slot=self.slot)
        self.assertEqual(result['code'], 'restore_target_stopped')
        self.assertFalse(result['all_match'])
        self.assertFalse(result['database_available'])
        self.assertEqual(result['container_id'], CONTAINER)
        self.assertEqual(result['reopen_plan']['generation'], 0)
        self.assertNotIn('private-control-canary', json.dumps(result))
        self.assertEqual(self.docker.writes(), [])
        self.assertEqual(list(self.slot.iterdir()), [])

    def test_reopen_preserves_original_request_identity_and_does_not_install_restore_receipt(self):
        original = self.request()
        helper.private_write(self.slot / 'request.json', helper.canonical(original))
        saved = (self.slot / 'request.json').read_bytes()
        plan = self.plan()
        result = self.invoke_main(self.request('reopen-restore', reopen_plan=plan))
        self.assertEqual(result['code'], 'restore_reopened')
        self.assertEqual(result['container_id'], CONTAINER)
        self.assertEqual((self.slot / 'request.json').read_bytes(), saved)
        self.assertFalse((self.slot / 'result.json').exists())
        writes = list(self.docker.writes())
        self.assertEqual(len(writes), 2)
        self.assertEqual(self.invoke_main(self.request('reopen-restore', reopen_plan=plan)), result)
        self.assertEqual(self.docker.writes(), writes)

    def test_verified_receipt_refuses_reopen_without_starting_target(self):
        helper.private_write(self.slot / 'request.json', helper.canonical(self.request()))
        receipt = helper.canonical({'ok': True, 'code': 'restore_verified'})
        helper.private_write(self.slot / 'result.json', receipt)
        with self.assertRaisesRegex(ValueError, 'request_invalid'):
            self.invoke_main(self.request('reopen-restore', reopen_plan=self.plan()))
        self.assertEqual(self.docker.writes(), [])
        self.assertEqual((self.slot / 'result.json').read_bytes(), receipt)

    def test_explicit_killed_non_oom_target_can_reopen_with_bound_state(self):
        self.docker.row['State']['ExitCode'] = 137
        result = self.apply(self.plan())
        self.assertEqual(result['code'], 'restore_reopened')
        self.assertFalse((self.slot / 'result.json').exists())

    def test_invalid_target_and_data_guards_refuse_before_intent_or_writes(self):
        changes = [
            lambda d: d.row['Config'].update(Entrypoint=['sh']),
            lambda d: d.row['Config'].update(Cmd=['1']),
            lambda d: d.row['Config'].update(Env=['PRIVATE_CANARY=value']),
            lambda d: d.row.update(Image='sha256:' + 'f' * 64),
            lambda d: d.row['HostConfig'].update(Privileged=True),
            lambda d: d.row['HostConfig'].update(PidMode='host'),
            lambda d: d.row['HostConfig'].update(NetworkMode='host'),
            lambda d: d.row['HostConfig'].update(Devices=[{'PathOnHost': '/dev/private'}]),
            lambda d: d.row['HostConfig'].update(RestartPolicy={'Name': 'always'}),
            lambda d: d.row['Mounts'].append({'Type': 'bind', 'Destination': '/extra', 'RW': True}),
            lambda d: d.volume.update(Options={'device': '/foreign'}),
            lambda d: d.consumers.append('d' * 64),
            lambda d: d.row['Config']['Labels'].update({'sandbox.recovery.owner': 'foreign'}),
            lambda d: d.row['State'].update(ExitCode=1),
            lambda d: d.row['State'].update(Pid=12),
            lambda d: d.markers.update(PG_VERSION=b'15\n'),
            lambda d: d.markers.update(PG_VERSION=b''),
            lambda d: d.markers.update({'global/pg_control': b''}),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                previous = copy.deepcopy(self.docker.__dict__)
                change(self.docker)
                with self.assertRaises(ValueError): self.plan()
                self.assertEqual(self.docker.writes(), [])
                self.assertFalse((self.slot / 'reopens').exists())
                self.docker.__dict__.update(previous)

    def test_changed_plan_target_or_archive_cannot_start(self):
        plan = self.plan()
        for field, value in (('container_id', 'f' * 64), ('daemon_identity', 'other'),
                             ('archive_digest', 'sha256:' + '0' * 64), ('generation', 1)):
            with self.subTest(field=field):
                changed = {**plan, field: value}
                changed['plan_digest'] = helper._schema_digest({k: v for k, v in changed.items() if k != 'plan_digest'})
                with self.assertRaises(ValueError): self.apply(changed)
                self.assertEqual(self.docker.writes(), [])

    def test_last_prewrite_recheck_refuses_a_changed_daemon(self):
        plan = self.plan()
        actual = helper._durable_reopen_record
        def record(path, value):
            actual(path, value)
            if path.name == 'intent.json': self.docker.daemon = 'other-daemon'
        with patch.object(helper, '_durable_reopen_record', side_effect=record):
            with self.assertRaisesRegex(ValueError, 'reopen_plan_changed'): self.apply(plan)
        self.assertEqual(self.docker.writes(), [])
        self.assertIsNone(helper.reopen_history(self.slot)[0][1])

    def test_uncertain_start_is_never_repeated_or_bypassed_by_another_generation(self):
        plan = self.plan(); self.docker.after_start_failure = True
        with self.assertRaisesRegex(ValueError, 'reopen_pending'): self.apply(plan)
        writes = list(self.docker.writes())
        with self.assertRaisesRegex(ValueError, 'reopen_pending'): self.apply(plan)
        self.docker.expire()
        with self.assertRaisesRegex(ValueError, 'reopen_pending'): self.plan()
        self.assertEqual(self.docker.writes(), writes)
        self.assertFalse((self.slot / 'result.json').exists())

    def test_configuration_change_after_container_start_prevents_database_launch(self):
        plan = self.plan()
        def changing(argv, **kwargs):
            result = self.docker(argv, **kwargs)
            if argv[:2] == ['docker', 'start']:
                self.docker.row['HostConfig']['Privileged'] = True
            return result
        with patch.object(helper, 'run', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'reopen_pending'): self.apply(plan)
        self.assertEqual(self.docker.writes(), [['docker', 'start', CONTAINER]])
        self.assertFalse(self.docker.database_running)

    def test_lost_success_response_recovers_only_from_positive_observation(self):
        plan = self.plan(); actual = helper._durable_reopen_record
        def record(path, value):
            if path.name == 'result.json': raise OSError('lost result')
            actual(path, value)
        with patch.object(helper, '_durable_reopen_record', side_effect=record):
            with self.assertRaises(OSError): self.apply(plan)
        writes = list(self.docker.writes())
        self.assertEqual(self.apply(plan)['code'], 'restore_reopened')
        self.assertEqual(self.docker.writes(), writes)
        self.assertIsNotNone(helper.reopen_history(self.slot)[0][1])

    def test_new_expired_generation_requires_completed_predecessor_and_fresh_plan(self):
        first = self.plan(); self.apply(first); self.docker.expire()
        second = self.plan()
        self.assertEqual(second['generation'], 1)
        self.assertEqual(second['previous_plan_digest'], first['plan_digest'])
        writes = list(self.docker.writes())
        with self.assertRaisesRegex(ValueError, 'reopen_pending'): self.apply(first)
        self.assertEqual(self.docker.writes(), writes)
        self.assertEqual(self.apply(second)['reopen_generation'], 1)
        self.assertEqual(len(helper.reopen_history(self.slot)), 2)

    def test_history_gaps_and_symlinks_refuse(self):
        root = self.slot / 'reopens'; root.mkdir(mode=0o700)
        (root / '0001').mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'reopen_history_invalid'): self.plan()
        (root / '0001').rmdir()
        (root / '0000').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'path_unsafe'): self.plan()

    def test_original_archive_request_mismatch_and_lock_contention_prevent_mutation(self):
        plan = self.plan(); original = self.request()
        helper.private_write(self.slot / 'request.json', helper.canonical(original))
        changed = self.request('reopen-restore', reopen_plan=plan)
        changed['source'] = source(database='different')
        with self.assertRaisesRegex(ValueError, 'acceptance_unknown'): self.invoke_main(changed)
        descriptor = os.open(self.slot, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.invoke_main(self.request('reopen-restore', reopen_plan=plan))
        finally: os.close(descriptor)
        self.assertEqual(self.docker.writes(), [])

    def test_safe_entrypoint_keeps_refusals_closed_and_does_not_print_exception_material(self):
        for error, expected_code in ((ValueError('reopen_pending'), 0),
                                     (ValueError('PRIVATE_EXCEPTION_CANARY'), 1)):
            out = io.BytesIO(); err = io.StringIO()
            with patch.object(helper, 'main', side_effect=error), \
                    patch.object(helper.sys, 'stdout', SimpleNamespace(buffer=out)), \
                    patch.object(helper.sys, 'stderr', err):
                self.assertEqual(helper.safe_main(), expected_code)
            self.assertNotIn('PRIVATE_EXCEPTION_CANARY', out.getvalue().decode() + err.getvalue())
            if expected_code == 0: self.assertEqual(json.loads(out.getvalue())['code'], 'reopen_pending')


if __name__ == '__main__':
    unittest.main()

"""Non-mutating control-flow probes for the pinned delivery review.

All runtime/process boundaries are synthetic. No Docker, SSH, network, database,
signal, or Sandbox state mutation is performed by these probes.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import ast
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from sandbox.commands.hosting import _initializer_status_command
from sandbox.application.job_service import JobService
from sandbox.jobs.models import validate_transition
from tests.subprocess_support import run_test_process


def initializer(config_wire: str, image_wire: str, project: str = 'review') -> dict:
    config_digest, image_digest = 'a' * 64, 'b' * 64
    container = {
        'Created': '2026-09-08T00:00:00+00:00', 'Image': 'sha256:' + image_digest,
        'Config': {'Labels': {'com.docker.compose.project': project,
            'com.docker.compose.service': 'migrate',
            'com.docker.compose.config-hash': config_digest}},
        'State': {'Status': 'exited', 'ExitCode': 0},
    }
    values = {'config': config_wire, 'images': image_wire, 'ps': 'c' * 64,
              'inspect': json.dumps(container)}
    stub = (
        'import subprocess\nfrom types import SimpleNamespace\n'
        f'_responses = {values!r}\n'
        'def _fake_run(argv, **kwargs):\n'
        ' for key in ("config", "images", "ps", "inspect"):\n'
        '  if key in argv: return SimpleNamespace(returncode=0, stdout=_responses[key], stderr="")\n'
        ' raise AssertionError("unexpected synthetic subprocess")\n'
        'subprocess.run = _fake_run\n'
    )
    argv = shlex.split(_initializer_status_command('docker compose -p review', 'migrate'))
    result = run_test_process([sys.executable, '-c', stub + argv[2], *argv[3:]],
                              timeout=15, capture_output=True, text=True)
    assert result.returncode == 0, 'synthetic initializer program failed'
    return json.loads(result.stdout)


def cancellation() -> dict:
    row = {'lifecycle': 'running', 'kind': 'exec', 'process': {
        'child_pid': 123, 'child_pgid': 123, 'host_boot_id': 'synthetic-boot',
        'child_start_identity': 'synthetic-start', 'supervisor_nonce_hash': 'a' * 64}}
    signals = []
    def transition(_job_id, lifecycle, **_kwargs):
        validate_transition(row['lifecycle'], lifecycle)
        row['lifecycle'] = lifecycle.value
    service = SimpleNamespace(
        repository=SimpleNamespace(snapshot=lambda _job_id: row, transition=transition),
        _is_aggregate=lambda _snapshot: False,
    )
    with patch('sandbox.application.job_service.verify_owned_process_identity', return_value=True), \
         patch('sandbox.application.job_service.signal_owned_process_group',
               side_effect=lambda _identity, number: signals.append(number)):
        JobService.cancel(service, 'd' * 32)
        first = row['lifecycle']
        try:
            JobService.cancel(service, 'd' * 32, force=True)
        except ValueError as exc:
            error = str(exc)
        else:
            error = None
    return {'first_lifecycle': first, 'force_error': error, 'synthetic_signals': signals}


def wordpress_readiness(multisite: bool = False) -> dict:
    from sandbox.core import _instances
    from sandbox.commands import lifecycle, data

    writes = []
    def registry_put(_root, **fields):
        writes.append(fields)
        return fields
    state = SimpleNamespace(
        ConfigError=RuntimeError,
        load_project_config=lambda *_args, **_kwargs: {'root': '/synthetic/project', 'server': 'apache'},
        project_lock=lambda *_args: nullcontext(),
        registry_get=lambda *_args, **_kwargs: None,
        registry_all=lambda: {}, registry_put=registry_put,
    )
    ports = {'wordpress_port': 8088, 'db_port': 3307, 'mailpit_port': 8025}
    resolved = {'fixture': {'server': 'apache', 'multisite': multisite, **ports}}
    overrides = {
        '_core': state, 'docker_daemon_preflight': {'ok': True},
        '_resolve_port_conflicts': {}, 'resolve_instances': resolved,
        '_derive_instance_name': 'fixture', '_pick_instance_ports': ports,
        '_build_instance_block': {}, 'prepare_php_extension_runtime': None,
        '_local_yaml': {}, '_write_local_yaml': None, 'write_compose_files': None,
        'load_config': {}, '_proxy_sudoers_installed': False,
        '_wait_http': False, '_wait_reachable': False, '_multisite_mode': multisite,
        '_wire_project_plugins': None, '_wire_project_themes': None,
        '_server_config_registry_identity_fields': {},
        '_server_config_attached_identity_fields': {},
        'site_url': 'http://localhost:8088', 'compose': None, 'info': None,
    }
    with ExitStack() as stack:
        mocked = {name: stack.enter_context(patch.object(_instances, name, return_value=value))
                  for name, value in overrides.items()}
        stack.enter_context(patch.object(lifecycle, 'cmd_up'))
        stack.enter_context(patch.object(lifecycle, 'cmd_install'))
        stack.enter_context(patch.object(data, 'capture_install_snapshots'))
        result = _instances.ensure_instance({}, '/synthetic/project')
    return {'multisite': multisite, 'http_wait_result': False,
            'http_wait_calls': mocked['_wait_http'].call_count,
            'multisite_wait_calls': mocked['_wait_reachable'].call_count,
            'registry_statuses': [row['status'] for row in writes],
            'returned_status': result['status']}


def compose_status(stdout: str) -> dict:
    from sandbox.runtimes.base import OperationRequest, RuntimeDependencies
    from sandbox.runtimes.compose import ComposeAdapter

    descriptor = {'root': '/synthetic/project', 'compose_file': '/synthetic/compose.yaml',
                  'service': 'web', 'node_store': False,
                  'instanceLifecycle': {'mode': 'always_on'}}
    record = {'instance': 'fixture', 'root': descriptor['root'], 'label': 'default', 'http_port': 8088}
    registry = SimpleNamespace(registry_get=lambda *_args, **_kwargs: record,
                               registry_all=lambda: {'fixture': record})
    process = SimpleNamespace(run=lambda *_args, **_kwargs:
                              SimpleNamespace(returncode=0, stdout=stdout, stderr=''))
    deps = RuntimeDependencies(process=process, http=object(), ports=object(),
                               paths=object(), proxy=object(), registry=registry)
    adapter = ComposeAdapter(deps, registry, timeout=2)
    with patch.object(adapter, '_descriptor', return_value=descriptor), \
         patch.object(adapter, '_overlay', return_value=Path('/synthetic/overlay.yaml')):
        result = adapter.invoke(OperationRequest(descriptor['root'], 'status'))
    return {'ok': result.ok, 'status': result.data['status'],
            'observation': result.data['observation']}


def preview_selector() -> dict:
    from sandbox.core import _instances, _remote

    commands = []
    rows = [{'instance': 'existing-default', 'label': 'default', 'is_default': True},
            {'instance': 'new-preview', 'label': 'preview-branch', 'is_default': False}]
    state = SimpleNamespace(find_project_root=lambda *_args: Path('/synthetic/project'),
                            registry_list_for_root=lambda *_args: rows)
    with patch.object(_remote, 'remote_sb_path', return_value='/synthetic/sb'), \
         patch.object(_remote, 'ssh_run', side_effect=lambda _remote, command, **_kwargs:
                      (commands.append(command) or SimpleNamespace(returncode=0))), \
         patch.object(_instances, '_core', return_value=state):
        _remote.set_remote_instance_url({}, '/synthetic/project', 'https://preview.example.test')
        implicit = _instances.resolve_registered_instance('/synthetic/project')
        explicit = _instances.resolve_registered_instance('/synthetic/project', label='preview-branch')
    return {'command': commands[0], 'implicit_instance': implicit['instance'],
            'explicit_preview_instance': explicit['instance']}


def failed_ensure_cleanup() -> dict:
    from sandbox.commands import deploy

    deleted = []
    concurrent_instance = {'name': 'created-by-caller-b', 'label': 'default'}
    with patch.object(deploy.sr, 'list_remote_instances', side_effect=[[], [concurrent_instance]]), \
         patch.object(deploy.sr, 'ensure_remote_instance',
                      side_effect=RuntimeError('caller A: remote completion unknown')), \
         patch.object(deploy.sr, 'delete_remote_instance',
                      side_effect=lambda _entry, name: deleted.append(name)):
        try:
            deploy._ensure_remote_instance_transactional({}, '/synthetic/project')
        except RuntimeError as exc:
            error = str(exc)
    return {'synthetic_deleted_names': deleted, 'error': error}


def remote_json_boundary() -> dict:
    from sandbox.transports import remote_jobs

    small = json.dumps({'ok': True, 'jobs': [{'synthetic': 'x' * 16}]})
    large = json.dumps({'ok': True, 'jobs': [{'synthetic': 'x' * remote_jobs._MAX_REMOTE_JSON_BYTES}]})
    return {'limit_bytes': remote_jobs._MAX_REMOTE_JSON_BYTES,
            'small_accepted': remote_jobs._last_json(small) is not None,
            'large_is_valid_json': isinstance(json.loads(large), dict),
            'large_bytes': len(large.encode()),
            'large_accepted': remote_jobs._last_json(large) is not None}


def backup_set_capture_identity() -> dict:
    """Two real coordinator publications; only the remote/source is synthetic.

    Seed a prior synthetic capture, then execute the real generated controller
    program. Its cached-output branch must return without any child process.
    """
    from sandbox.recovery.hosted import HostedRecoveryMaterializer, HostedObservation, HostedCaptureReceipt
    from sandbox.recovery.materialize import ScopedMaterializer, SourceBinding
    from sandbox.transports.remote_recovery import RegisteredRemoteRecoveryController

    artifact = SimpleNamespace(profile_id='fixture', artifact_id='content', source_type='filesystem',
        allowed_roots=('fixture',), sources=('fixture',), capture_mode='copy', consistency='stable',
        excludes=(), restore_target='fixture', verification=(), dependencies=())
    plan = SimpleNamespace(profiles=('fixture',), artifacts=(artifact,))
    binding = SourceBinding('review', 'synthetic-machine', 'a' * 24, 'b' * 64)
    requests, published, process_attempts = [], [], []
    with tempfile.TemporaryDirectory(prefix='sandbox-review-capture-') as temporary:
        root = Path(temporary)
        capture_root = root / 'runtime' / 'recovery-controller'
        capture_root.mkdir(parents=True, mode=0o700)
        request = HostedRecoveryMaterializer._request_id('review', artifact, binding)
        (capture_root / (request + '.tar')).write_bytes(b'synthetic-content-before-edit')

        def ssh_process(_entry, command, **_kwargs):
            argv = shlex.split(command)
            stub = ('import subprocess\n'
                    'def forbidden(*args, **kwargs): raise AssertionError("unexpected process")\n'
                    'subprocess.run = forbidden\n')
            result = run_test_process([sys.executable, '-c', stub + argv[2], *argv[3:]],
                                      timeout=10, capture_output=True)
            process_attempts.append(result.returncode)
            return result

        remote_controller = SimpleNamespace(
            _environment={'SANDBOX_RECOVERY_DB_PASSWORD': 'synthetic-unused-fixture'},
            _resolve_home=lambda _entry: str(root), _ssh_process=ssh_process,
            _write_private=RegisteredRemoteRecoveryController._write_private)
        state = SimpleNamespace(entry={})

        def capture(_remote, selected, destination, observed, request_id):
            assert observed == binding
            requests.append(request_id)
            path = RegisteredRemoteRecoveryController._capture_wordpress(
                remote_controller, state, destination, request_id)
            return HostedCaptureReceipt(selected.profile_id, selected.artifact_id, request_id,
                                        (path,), selected.sources, 'tar')

        controller = SimpleNamespace(
            observe=lambda *_args: HostedObservation(binding, {'fixture': artifact.sources}),
            capture=capture)
        materializer = ScopedMaterializer(root / 'stage', HostedRecoveryMaterializer(controller))

        def publish_files(set_id, files, **_kwargs):
            content = next(iter(files.values())).read_bytes()
            published.append((set_id, content))
            return {'set_id': set_id}

        publisher = SimpleNamespace(publish_files=publish_files)
        materializer.publish('review', plan, publisher, 'set-a', {})
        # A content-only source update leaves the observed topology/source binding unchanged.
        current_content = b'synthetic-content-after-edit'
        materializer.publish('review', plan, publisher, 'set-b', {})
    return {'distinct_set_ids': published[0][0] != published[1][0],
            'same_controller_request': requests[0] == requests[1],
            'same_published_content': published[0][1] == published[1][1],
            'second_matches_current_content': published[1][1] == current_content,
            'generated_cache_program_exit_codes': process_attempts,
            'scope': 'synthetic pre-existing archive; native tar validation and live content update not exercised'}


def ensure_progress_redaction() -> dict:
    """Run cmd_ensure around the exact installation progress expression.

    Runtime invocation and installation side effects are replaced. The progress
    statement is extracted from the reviewed source, using a synthetic marker.
    No credential value is emitted in the probe result.
    """
    from sandbox.commands import instances_cmd, lifecycle
    tree = ast.parse(Path(lifecycle.__file__).read_text())
    install = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == 'cmd_install')
    expression = next(node for node in ast.walk(install) if isinstance(node, ast.Expr)
                      and 'Login:' in ast.unparse(node) and 'autologin_token' in ast.unparse(node))
    program = compile(ast.fix_missing_locations(ast.Module(body=[expression], type_ignores=[])),
                      '<review-install-progress>', 'exec')
    marker = 'synthetic-review-login-marker'
    class SyntheticConfigError(RuntimeError):
        pass
    def invoke(_request):
        exec(program, {'ok': print, 'base': 'https://fixture.example.test',
                       'autologin_token': marker})
        raise SyntheticConfigError('synthetic plugin activation failure')
    stdout, stderr = io.StringIO(), io.StringIO()
    args = SimpleNamespace(local=True, json=True, reveal_login=False, project_dir='/synthetic/project')
    with patch.object(instances_cmd, '_core', return_value=SimpleNamespace(ConfigError=SyntheticConfigError)), \
         patch.object(instances_cmd, 'wordpress_runtime_service', return_value=SimpleNamespace(invoke=invoke)), \
         redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            instances_cmd.cmd_ensure({}, args)
        except SystemExit as exc:
            exit_code = exc.code
        else:
            exit_code = 0
    lines = stdout.getvalue().splitlines()
    final = json.loads(lines[-1])
    return {'exit_code': exit_code, 'reveal_login': False,
            'progress_contains_synthetic_login_marker': marker in '\n'.join(lines[:-1]),
            'final_json_contains_marker': marker in lines[-1], 'final_json_ok': final['ok'],
            'scope': 'real progress expression and cmd_ensure; synthetic runtime failure'}


def main() -> None:
    digest, image_digest = 'a' * 64, 'b' * 64
    output = {
        'scope': 'synthetic adapter boundary; no runtime effects',
        'initializer': {
            'invented_matching_formats': initializer(digest, 'sha256:' + image_digest),
            'compose_formats': initializer('migrate ' + digest, image_digest),
            'config_format_only': initializer('migrate ' + digest, 'sha256:' + image_digest),
            'image_format_only': initializer(digest, image_digest),
            'foreign_project_control': initializer(digest, 'sha256:' + image_digest, 'foreign'),
        },
        'cancellation': cancellation(),
        'wordpress_readiness': [wordpress_readiness(False), wordpress_readiness(True)],
        'compose_status': {label: compose_status(wire) for label, wire in {
            'empty': '', 'empty_array': '[]', 'malformed': 'started',
            'exited_array': '[{"Service":"web","State":"exited"}]',
            'exited_row': '{"Service":"web","State":"exited"}',
            'running_row': '{"Service":"web","State":"running"}',
        }.items()},
        'preview_selector': preview_selector(),
        'failed_ensure_cleanup': failed_ensure_cleanup(),
        'remote_json_boundary': remote_json_boundary(),
        'backup_set_capture_identity': backup_set_capture_identity(),
        'ensure_progress_redaction': ensure_progress_redaction(),
    }
    assert output['initializer']['invented_matching_formats']['status'] == 'succeeded'
    assert output['initializer']['compose_formats']['status'] == 'foreign'
    assert output['cancellation']['synthetic_signals'] == [15]
    assert 'cancelling -> cancelling' in output['cancellation']['force_error']
    assert all(row['returned_status'] == 'ready' for row in output['wordpress_readiness'])
    assert output['compose_status']['empty']['status'] == 'ready'
    assert output['compose_status']['exited_row']['status'] == 'stopped'
    assert output['preview_selector']['implicit_instance'] == 'existing-default'
    assert output['failed_ensure_cleanup']['synthetic_deleted_names'] == ['created-by-caller-b']
    assert output['backup_set_capture_identity']['same_controller_request']
    assert not output['backup_set_capture_identity']['second_matches_current_content']
    assert output['ensure_progress_redaction']['progress_contains_synthetic_login_marker']
    assert not output['ensure_progress_redaction']['final_json_contains_marker']
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()

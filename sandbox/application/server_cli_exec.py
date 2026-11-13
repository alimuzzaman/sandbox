"""Server-selected fixed host-exec jobs with retained original anonymous stdin."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import time
import base64

from sandbox.application.server_cli import (ServerCLIUnavailable, read_current_server_owner,
    validate_target_metadata)
from sandbox.application.server_cli_targets import inspect_current_server_target
from sandbox.jobs.anonymous_input import capture_input
from sandbox.jobs.models import JobSubmission, SourceIdentity, validate_argv

CONTRACT = 'job.server-cli.exec-stdin.v1'


def capture_contract_available():
    try:
        from sandbox.commands.host_exec import SERVER_CLI_EXEC_CAPTURE_VERSION
        from sandbox.application.server_cli_capture import prepare_server_cli_capture
        from sandbox.application.command_selection import selected_invocation
        selected = selected_invocation(['host', 'exec', '--exec-capture-artifacts',
            '--remote', 'capture-probe', '--service', 'capture-probe', '--', 'true'])
        return (exec_contract_available() and type(SERVER_CLI_EXEC_CAPTURE_VERSION) is int
                and SERVER_CLI_EXEC_CAPTURE_VERSION == 1 and callable(prepare_server_cli_capture)
                and selected is not None and selected.command == ('host', 'exec'))
    except (ImportError, ValueError):
        return False


def exec_contract_available():
    """Only assembled receiver handlers; never infer readiness from a snapshot."""
    import argparse
    import inspect
    try:
        from sandbox.commands.jobs_runtime import configure_start_parser
        from sandbox.commands.host_exec import _execution_scope, SERVER_CLI_EXEC_BINDING_VERSION
        from sandbox.hosting.server_cli_target import OwnerHostTarget, require_owner_target
        from sandbox.hosting.server_target_selection import read_owner_exec_selection
        from sandbox.application.server_cli_targets import installed_inspector
        parser = argparse.ArgumentParser(add_help=False)
        configure_start_parser(parser)
        return ('--server-cli-exec-selector' in parser._option_string_actions
            and type(SERVER_CLI_EXEC_BINDING_VERSION) is int and SERVER_CLI_EXEC_BINDING_VERSION == 1
            and callable(_execution_scope) and isinstance(OwnerHostTarget, type)
            and callable(require_owner_target) and callable(installed_inspector())
            and 'expected_selector_digest' in inspect.signature(read_owner_exec_selection).parameters)
    except (ImportError, ValueError, ServerCLIUnavailable):
        return False


def _exec_argv(argv):
    from sandbox.services.redaction import require_safe_argv, RedactionError
    try:
        values = validate_argv(tuple(argv))
        if len(values) > 256 or sum(len(value.encode()) + 1 for value in values) > 65536:
            raise ValueError()
        require_safe_argv(values)
        return values
    except (ValueError, TypeError, RedactionError):
        raise ServerCLIUnavailable('server exec command is invalid or exceeds its bounded argv contract') from None


def remaining_deadline(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError()
        remaining = (parsed - datetime.now(timezone.utc)).total_seconds()
        if not math.isfinite(remaining) or not 0 < remaining <= 3600:
            raise ValueError()
        return remaining
    except (AttributeError, TypeError, ValueError):
        raise ServerCLIUnavailable('original server exec deadline is expired or invalid') from None


def _selector(value):
    if not isinstance(value, str) or len(value.encode()) > 8192:
        raise ServerCLIUnavailable('server exec selector is invalid')
    def object_fields(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError()
        return result
    try:
        result = json.loads(value, object_pairs_hook=object_fields)
        if not isinstance(result, dict):
            raise ValueError()
    except (TypeError, ValueError):
        raise ServerCLIUnavailable('server exec selector is invalid') from None
    fields = {'schema_version', 'origin_remote_name', 'target', 'name', 'environment',
        'selector_digest', 'service', 'target_identity', 'ownership_marker', 'runtime_revision',
        'deadline_at', 'target_metadata', 'stdin_sha256', 'stdin_size'}
    if set(result) - fields == {'output_contract'}:
        output_contract = result.pop('output_contract')
    else:
        output_contract = 'utf8.v1'
    if (set(result) != fields or not isinstance(output_contract, str) or output_contract not in {'utf8.v1', 'artifact.v1'}
            or type(result['schema_version']) is not int or result['schema_version'] != 1
            or not isinstance(result['selector_digest'], str)
            or re.fullmatch(r'sha256:[0-9a-f]{64}', result['selector_digest']) is None
            or not isinstance(result['service'], str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', result['service']) is None
            or not isinstance(result['stdin_sha256'], str)
            or re.fullmatch(r'[0-9a-f]{64}', result['stdin_sha256']) is None
            or type(result['stdin_size']) is not int or not 0 <= result['stdin_size'] <= 1 << 20):
        raise ServerCLIUnavailable('server exec selector fields are invalid')
    result['target_metadata'] = validate_target_metadata(result['target_metadata'])
    result['output_contract'] = output_contract
    remaining_deadline(result['deadline_at'])
    return result


def receive_server_cli_exec(args, *, stdin_fd):
    """Linux owner admission. No controller source path or serialized grant."""
    from sandbox.application.context import durable_job_dependencies
    from sandbox.config.facade import project_identity
    from sandbox.config.runtime import resolve_execution_policy
    from sandbox.remote_runtime.verdict import strict_requested
    if not exec_contract_available():
        raise ServerCLIUnavailable('installed fixed server exec handlers are not assembled')
    if (getattr(args, 'server_cli', False) is not True or not getattr(args, 'local', False)
            or getattr(args, 'remote', None) is not None
            or not isinstance(getattr(args, 'request_id', None), str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', args.request_id) is None
            or getattr(args, 'parent_job_id', None) is not None):
        raise ServerCLIUnavailable('server exec requires the original fixed local job request')
    command = list(getattr(args, 'command', ()) or ())
    if command[:1] == ['--']:
        command = command[1:]
    command = _exec_argv(command)
    selector = _selector(getattr(args, 'server_cli_exec_selector', None))
    if selector['output_contract'] == 'artifact.v1' and not capture_contract_available():
        raise ServerCLIUnavailable('installed binary exec capture is not assembled')
    owner = read_current_server_owner()
    for field in ('target_identity', 'ownership_marker', 'runtime_revision'):
        if selector[field] != getattr(owner, field):
            raise ServerCLIUnavailable('server exec receiver identity changed')
    def observe():
        from sandbox.hosting.target_resolution import TargetRefusal
        try:
            value = inspect_current_server_target(origin_remote_name=selector['origin_remote_name'],
                target=selector['target'], name=selector['name'], environment=selector['environment'])
        except TargetRefusal:
            raise ServerCLIUnavailable('server exec target evidence is unavailable or busy') from None
        if (value['selector_digest'] != selector['selector_digest']
                or any(value[field] != getattr(owner, field) for field in
                       ('target_identity', 'ownership_marker', 'runtime_revision'))
                or not any(item['name'] == selector['service'] and item['index'] == 1 for item in value['services'])):
            raise ServerCLIUnavailable('server exec target changed before acceptance')
        return value
    snapshot = observe()
    with capture_input(stdin_fd, timeout_seconds=min(30, remaining_deadline(selector['deadline_at']))) as original_input:
        if (original_input.binding['sha256'] != selector['stdin_sha256']
                or original_input.binding['size'] != selector['stdin_size']):
            raise ServerCLIUnavailable('original stdin was incomplete or changed in transit')
        if observe() != snapshot or read_current_server_owner() != owner:
            raise ServerCLIUnavailable('server exec source changed during original stdin intake')
        seconds = int(remaining_deadline(selector['deadline_at']))
        if seconds < 1:
            raise ServerCLIUnavailable('original server exec deadline expired before acceptance')
        root = snapshot['source_dir']
        # These identities name the receiver's measured child root. The original
        # source commit and retained target selector remain separate bindings.
        canonical = project_identity({'root': root})
        policy = resolve_execution_policy({}, workspace='server-exec', execution_profile='exec', timeout_seconds=seconds)
        provenance = dict(policy.provenance or {})
        provenance.update({'server_cli.target_identity': owner.target_identity,
            'server_cli.ownership_marker': owner.ownership_marker, 'server_cli.runtime_revision': owner.runtime_revision,
            'server_cli.remote_name': selector['origin_remote_name'],
            'server_cli.target_metadata': json.dumps(selector['target_metadata'], sort_keys=True, separators=(',', ':')),
            'server_cli.target_selector_digest': snapshot['selector_digest'],
            'server_cli.exec_service': selector['service'],
            'server_cli.exec_target': snapshot['project'] + '/' + snapshot['environment'],
            'server_cli.output_contract': selector['output_contract'],
            'server_cli.target_snapshot': json.dumps({key: value for key, value in snapshot.items() if key != 'ok'},
                sort_keys=True, separators=(',', ':')),
            'server_cli.absolute_deadline_at': selector['deadline_at'],
            'server_cli.stdin_sha256': original_input.binding['sha256'],
            'server_cli.stdin_size': str(original_input.binding['size'])})
        argv = ([owner.executable] + (['--strict-runtime'] if strict_requested() else []) +
            ['host', 'exec', '--remote', selector['origin_remote_name'], '--target',
             snapshot['project'] + '/' + snapshot['environment'], '--service', selector['service'],
             '--timeout', str(seconds), '--json'] +
             (['--exec-capture-artifacts'] if selector['output_contract'] == 'artifact.v1' else []) + ['--', *command])
        submission = JobSubmission(kind='server-cli', project_root=root, project_identity=canonical['identity'],
            target_kind='local', workspace_label='server-exec', argv=tuple(argv),
            deadline_seconds=policy.deadline_seconds,
            source=SourceIdentity('sha256:' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest(), snapshot['source_commit']),
            request_id=args.request_id, execution_profile=policy.execution_profile, output_profile='full',
            deadline_source=policy.deadline_source, deadline_reminder=policy.deadline_reminder,
            stall_seconds=policy.stall_seconds, cancel_grace_seconds=policy.cancel_grace_seconds,
            cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
            execution_policy_provenance=provenance)
        receipt = durable_job_dependencies()['job_service'].submit(submission, anonymous_input=original_input)
        return {**receipt, 'request_id': args.request_id,
                'selector_digest': snapshot['selector_digest']}


class ServerCLIExecAcceptanceUnknown(RuntimeError):
    code = 'acceptance_unknown'
    def __init__(self, request_id):
        self.request_id = request_id
        super().__init__('server exec acceptance is unknown; retain this request and inspect the server ledger before any replay')


class ServerCLIExecObservationUnavailable(RuntimeError):
    code = 'server_cli_exec_observation_unavailable'
    def __init__(self, receipt):
        self.job_id = receipt.get('job_id') if isinstance(receipt, dict) else None
        self.request_id = receipt.get('request_id') if isinstance(receipt, dict) else None
        super().__init__('original server exec is retained; its current result could not be validated, so do not replay it')


def submit_server_cli_exec(remote_name, *, snapshot, service, argv, stdin_fd, request_id,
                           timeout_seconds=300, output_mode='artifact'):
    """Send original stdin by FD, without a controller checkout or secret source."""
    from sandbox.core import _remote
    from sandbox.resources.context import authenticated_target_identity
    from sandbox.application.server_cli_targets import validate_target_snapshot
    from sandbox.services.process import BoundedProcessRunner
    from sandbox.remote_runtime.verdict import strict_requested
    from types import SimpleNamespace
    from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError
    if (not isinstance(output_mode, str) or output_mode not in {'artifact', 'utf8'} or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
            or not isinstance(request_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', request_id) is None
            or not isinstance(service, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', service) is None):
        raise ServerCLIUnavailable('server exec request is invalid')
    values = _exec_argv(argv)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    def remaining():
        return remaining_deadline(deadline.isoformat())
    identity = authenticated_target_identity(remote_name, budget_seconds=min(15, remaining()))
    record = _remote.get_remote(remote_name, budget_seconds=min(15, remaining()))
    if (not isinstance(record, dict) or record.get('joined') is not True
            or record.get('control_transport') != 'tailscale'):
        raise ServerCLIUnavailable('server exec requires the admitted joined Tailnet target')
    binding = record.get('mcp_service') or {}
    owner = SimpleNamespace(target_identity=identity['target_identity'],
        ownership_marker=binding.get('ownership_marker'), runtime_revision=binding.get('runtime_revision'))
    validate_target_snapshot(snapshot, owner=owner, origin_remote_name=remote_name)
    transport = RemoteJobTransport(deploy=None, ssh_run=_remote.ssh_run,
        remote_lookup=lambda name: record if name == remote_name else None, remote_sb_path=_remote.remote_sb_path)
    try:
        installed = transport.control(remote_name, ['job-capabilities'], timeout=min(15, remaining()))
    except RemoteJobTransportError:
        raise ServerCLIUnavailable('installed server exec capability could not be observed; no job was submitted') from None
    if (not isinstance(installed, dict) or installed.get('ok') is not True
            or any(installed.get(field) != getattr(owner, field) for field in
                   ('target_identity', 'ownership_marker', 'runtime_revision'))
            or not isinstance(installed.get('server_exec_capabilities'), list)
            or installed['server_exec_capabilities'] != [CONTRACT]):
        raise ServerCLIUnavailable('installed server exec contract is unavailable or changed')
    if output_mode == 'artifact':
        from sandbox.application.server_cli_capture import CONTRACT as CAPTURE_CONTRACT
        if installed.get('server_exec_capture_capabilities') != [CAPTURE_CONTRACT]:
            raise ServerCLIUnavailable('installed binary capture contract is unavailable; no job was submitted')
    if not any(item['name'] == service and item['index'] == 1 for item in snapshot['services']):
        raise ServerCLIUnavailable('selected owner service has no unique running first container')
    with capture_input(stdin_fd, timeout_seconds=min(30, remaining())) as original_input:
        selector = {'schema_version': 1, 'origin_remote_name': remote_name,
            'target': snapshot['project'] + '/' + snapshot['environment'], 'name': None,
            'environment': snapshot['environment'], 'selector_digest': snapshot['selector_digest'],
            'service': service, 'target_identity': owner.target_identity,
            'ownership_marker': owner.ownership_marker, 'runtime_revision': owner.runtime_revision,
            'deadline_at': deadline.isoformat(),
            'output_contract': 'artifact.v1' if output_mode == 'artifact' else 'utf8.v1',
            'target_metadata': validate_target_metadata({key: record[key] for key in
                ('origin_ipv4', 'origin_ipv6', 'front_door', 'mcp_port') if key in record}),
            'stdin_sha256': original_input.binding['sha256'], 'stdin_size': original_input.binding['size']}
        command = (['--strict-runtime'] if strict_requested() else []) + [
            'job-start', '--local', '--server-cli', '--request-id', request_id,
            '--server-cli-exec-selector', json.dumps(selector, sort_keys=True, separators=(',', ':')),
            '--', *values]
        wire = shlex.join([_remote.remote_sb_path(record), *command])
        ssh = _remote.ssh_command_args(record, wire, multiplex=False)
        result = BoundedProcessRunner(max_output=65536, terminate_on_output_limit=True).run(tuple(ssh),
            env={'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C.UTF-8'},
            stdin=original_input.fileno(), timeout=remaining())
    if result.termination_reason is not None or result.stdout_truncated or result.stderr_truncated:
        raise ServerCLIExecAcceptanceUnknown(request_id)
    try:
        receipt = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ServerCLIExecAcceptanceUnknown(request_id) from None
    if (isinstance(receipt, dict) and receipt.get('ok') is False
            and receipt.get('status') == 'refused' and 'job_id' not in receipt):
        raise ServerCLIUnavailable('installed server exec refused before job acceptance')
    if (result.returncode or not isinstance(receipt, dict) or receipt.get('ok') is not True
            or receipt.get('request_id') != request_id or receipt.get('selector_digest') != snapshot['selector_digest']
            or not isinstance(receipt.get('job_id'), str) or re.fullmatch(r'[0-9a-f]{32}', receipt['job_id']) is None):
        raise ServerCLIExecAcceptanceUnknown(request_id)
    retained_policy = receipt.get('execution_policy')
    proof = retained_policy.get('provenance') if isinstance(retained_policy, dict) else None
    expected = {'server_cli.target_identity': owner.target_identity,
        'server_cli.ownership_marker': owner.ownership_marker, 'server_cli.runtime_revision': owner.runtime_revision,
        'server_cli.remote_name': remote_name, 'server_cli.target_selector_digest': snapshot['selector_digest'],
        'server_cli.exec_service': service, 'server_cli.exec_target': snapshot['project'] + '/' + snapshot['environment'],
        'server_cli.output_contract': selector['output_contract'],
        'server_cli.stdin_sha256': original_input.binding['sha256'],
        'server_cli.stdin_size': str(original_input.binding['size']),
        'server_cli.absolute_deadline_at': deadline.isoformat()}
    source = receipt.get('source')
    if (not isinstance(proof, dict) or any(proof.get(key) != value for key, value in expected.items())
            or not isinstance(source, dict) or source.get('commit') != snapshot['source_commit']
            or source.get('identity') != 'sha256:' + hashlib.sha256(snapshot['source_dir'].encode()).hexdigest()):
        raise ServerCLIExecAcceptanceUnknown(request_id)
    return receipt


def read_server_cli_exec_result(remote_name, receipt, *, budget_seconds=15):
    """One bounded retained observation, never child-stdio streaming or replay."""
    from sandbox.core import _remote
    from sandbox.resources.context import authenticated_target_identity
    from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError
    from sandbox.resources.host_memory.remote import RemoteProtocolError
    if (not isinstance(receipt, dict) or not isinstance(receipt.get('job_id'), str)
            or re.fullmatch(r'[0-9a-f]{32}', receipt['job_id']) is None
            or not isinstance(receipt.get('request_id'), str)
            or isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float))
            or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 300):
        raise ServerCLIExecObservationUnavailable(receipt)
    policy = receipt.get('execution_policy')
    proof = policy.get('provenance') if isinstance(policy, dict) else None
    if (not isinstance(proof, dict) or proof.get('server_cli.remote_name') != remote_name
            or proof.get('server_cli.target_selector_digest') != receipt.get('selector_digest')
            or not isinstance(receipt.get('selector_digest'), str)
            or re.fullmatch(r'sha256:[0-9a-f]{64}', receipt['selector_digest']) is None):
        raise ServerCLIExecObservationUnavailable(receipt)
    deadline = time.monotonic() + budget_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ServerCLIExecObservationUnavailable(receipt)
        return value
    try:
        identity = authenticated_target_identity(remote_name, budget_seconds=remaining())
        record = _remote.get_remote(remote_name, budget_seconds=remaining())
        if (not isinstance(record, dict) or record.get('joined') is not True
                or record.get('control_transport') != 'tailscale'
                or identity['target_identity'] != proof.get('server_cli.target_identity')
                or (record.get('mcp_service') or {}).get('ownership_marker') != proof.get('server_cli.ownership_marker')):
            raise ServerCLIExecObservationUnavailable(receipt)
        transport = RemoteJobTransport(deploy=None, ssh_run=_remote.ssh_run,
            remote_lookup=lambda name: record if name == remote_name else None,
            remote_sb_path=_remote.remote_sb_path)
        state = transport.control(remote_name, ['job-status', receipt['job_id']], timeout=remaining())
        if not isinstance(state, dict):
            raise ServerCLIExecObservationUnavailable(receipt)
        retained = json.loads(state.get('execution_policy_provenance_json') or '{}')
        source = receipt.get('source')
        if (state.get('ok') is not True or state.get('job_id') != receipt['job_id']
                or state.get('request_id') != receipt['request_id'] or state.get('kind') != 'server-cli'
                or state.get('target_kind') != 'local' or not isinstance(source, dict)
                or state.get('source_identity') != source.get('identity')
                or state.get('source_commit') != source.get('commit') or retained != proof):
            raise ServerCLIExecObservationUnavailable(receipt)
        lifecycle = state.get('lifecycle')
        if lifecycle not in {'accepted', 'queued', 'running', 'cancelling',
                             'succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted'}:
            raise ServerCLIExecObservationUnavailable(receipt)
        result = {'ok': True, 'job_id': receipt['job_id'], 'request_id': receipt['request_id'],
            'status': lifecycle, 'receipt': receipt, 'result': None}
        if lifecycle in {'accepted', 'queued', 'running', 'cancelling'}:
            return result
        if state.get('termination_reason') == 'anonymous_stdin_capacity_unavailable':
            return {**result, 'result': {'ok': False, 'code': 'anonymous_stdin_capacity_unavailable',
                'exit_code': 1, 'error': 'no job effect was dispatched because the server slot was occupied'}}
        chunks, cursor, seen, total = [], None, set(), 0
        for _page in range(256):
            argv = ['job-output', receipt['job_id'], '--stream', 'stdout', '--encoding', 'base64', '--max-bytes', '65536']
            if cursor is not None:
                argv.extend(['--cursor', cursor])
            page = transport.control(remote_name, argv, timeout=remaining())
            if (not isinstance(page, dict) or page.get('ok') is not True or page.get('job_id') != receipt['job_id']
                    or page.get('stream') != 'stdout' or page.get('encoding') != 'base64'
                    or type(page.get('has_more')) is not bool or not isinstance(page.get('data'), str)):
                raise ServerCLIExecObservationUnavailable(receipt)
            chunk = base64.b64decode(page['data'], validate=True)
            if len(chunk) > 65536 or type(page.get('bytes_read')) is not int or page['bytes_read'] != len(chunk):
                raise ServerCLIExecObservationUnavailable(receipt)
            total += len(chunk)
            if total > 16 * 1024 * 1024:
                raise ServerCLIExecObservationUnavailable(receipt)
            chunks.append(chunk)
            if not page['has_more']:
                break
            cursor = page.get('cursor')
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ServerCLIExecObservationUnavailable(receipt)
            seen.add(cursor)
        else:
            raise ServerCLIExecObservationUnavailable(receipt)
        outcome = json.loads(b''.join(chunks))
        if (not isinstance(outcome, dict) or type(outcome.get('ok')) is not bool
                or not isinstance(outcome.get('code'), str) or type(state.get('exit_code')) is not int):
            raise ServerCLIExecObservationUnavailable(receipt)
        if outcome.get('ok') is False and 'exit_code' not in outcome and isinstance(outcome.get('error'), str):
            if state['exit_code'] == 0:
                raise ServerCLIExecObservationUnavailable(receipt)
            return {**result, 'result': {**outcome, 'exit_code': state['exit_code']}}
        if (type(outcome.get('exit_code')) is not int or outcome['exit_code'] != state['exit_code']
                or outcome['ok'] != (outcome['exit_code'] == 0 and outcome['code'] == 'ok')
                or outcome.get('remote') != remote_name or outcome.get('target') != proof.get('server_cli.exec_target')
                or outcome.get('service') != proof.get('server_cli.exec_service')
                or any(type(outcome.get(key)) is not bool for key in ('stdout_truncated', 'stderr_truncated'))):
            raise ServerCLIExecObservationUnavailable(receipt)
        if proof.get('server_cli.output_contract') == 'artifact.v1':
            from sandbox.application.server_cli_capture import read_server_cli_capture, CaptureUnavailable
            try:
                decoded = read_server_cli_capture(outcome, receipt=receipt, state=state,
                    control=lambda argv: transport.control(remote_name, argv, timeout=remaining()))
            except CaptureUnavailable:
                raise ServerCLIExecObservationUnavailable(receipt) from None
            return {**result, 'result': decoded}
        if ('capture' in outcome or any(not isinstance(outcome.get(key), str) for key in ('stdout', 'stderr'))):
            raise ServerCLIExecObservationUnavailable(receipt)
        return {**result, 'result': outcome}
    except (RemoteJobTransportError, RemoteProtocolError, ServerCLIUnavailable, OSError, ValueError, TypeError, KeyError):
        raise ServerCLIExecObservationUnavailable(receipt) from None

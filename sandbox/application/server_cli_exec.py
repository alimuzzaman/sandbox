"""Server-selected fixed host-exec jobs with retained original anonymous stdin."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shlex

from sandbox.application.server_cli import (ServerCLIUnavailable, read_current_server_owner,
    validate_target_metadata)
from sandbox.application.server_cli_targets import inspect_current_server_target
from sandbox.jobs.anonymous_input import capture_input
from sandbox.jobs.models import JobSubmission, SourceIdentity, validate_argv

CONTRACT = 'job.server-cli.exec-stdin.v1'


def exec_contract_available():
    """Only assembled receiver handlers; never infer readiness from a snapshot."""
    import argparse
    import inspect
    try:
        from sandbox.commands.jobs_runtime import configure_start_parser
        from sandbox.commands.host_exec import _execution_scope
        from sandbox.hosting.server_cli_target import OwnerHostTarget, require_owner_target
        from sandbox.hosting.server_target_selection import read_owner_exec_selection
        from sandbox.application.server_cli_targets import installed_inspector
        parser = argparse.ArgumentParser(add_help=False)
        configure_start_parser(parser)
        return ('--server-cli-exec-selector' in parser._option_string_actions
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
    if (set(result) != fields or type(result['schema_version']) is not int or result['schema_version'] != 1
            or not isinstance(result['selector_digest'], str)
            or re.fullmatch(r'sha256:[0-9a-f]{64}', result['selector_digest']) is None
            or not isinstance(result['service'], str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', result['service']) is None
            or not isinstance(result['stdin_sha256'], str)
            or re.fullmatch(r'[0-9a-f]{64}', result['stdin_sha256']) is None
            or type(result['stdin_size']) is not int or not 0 <= result['stdin_size'] <= 1 << 20):
        raise ServerCLIUnavailable('server exec selector fields are invalid')
    result['target_metadata'] = validate_target_metadata(result['target_metadata'])
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
            'server_cli.target_snapshot': json.dumps({key: value for key, value in snapshot.items() if key != 'ok'},
                sort_keys=True, separators=(',', ':')),
            'server_cli.absolute_deadline_at': selector['deadline_at'],
            'server_cli.stdin_sha256': original_input.binding['sha256'],
            'server_cli.stdin_size': str(original_input.binding['size'])})
        argv = ([owner.executable] + (['--strict-runtime'] if strict_requested() else []) +
            ['host', 'exec', '--remote', selector['origin_remote_name'], '--target',
             snapshot['project'] + '/' + snapshot['environment'], '--service', selector['service'],
             '--timeout', str(seconds), '--json', '--', *command])
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


def submit_server_cli_exec(remote_name, *, snapshot, service, argv, stdin_fd, request_id,
                           timeout_seconds=300):
    """Send original stdin by FD, without a controller checkout or secret source."""
    from sandbox.core import _remote
    from sandbox.resources.context import authenticated_target_identity
    from sandbox.application.server_cli_targets import validate_target_snapshot
    from sandbox.services.process import BoundedProcessRunner
    from sandbox.remote_runtime.verdict import strict_requested
    from types import SimpleNamespace
    from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError
    if (type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
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
    if not any(item['name'] == service and item['index'] == 1 for item in snapshot['services']):
        raise ServerCLIUnavailable('selected owner service has no unique running first container')
    with capture_input(stdin_fd, timeout_seconds=min(30, remaining())) as original_input:
        selector = {'schema_version': 1, 'origin_remote_name': remote_name,
            'target': snapshot['project'] + '/' + snapshot['environment'], 'name': None,
            'environment': snapshot['environment'], 'selector_digest': snapshot['selector_digest'],
            'service': service, 'target_identity': owner.target_identity,
            'ownership_marker': owner.ownership_marker, 'runtime_revision': owner.runtime_revision,
            'deadline_at': deadline.isoformat(),
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
    return receipt

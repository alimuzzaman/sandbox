"""Fixed installed CLI jobs in the admitted Linux owner's existing context.

This is neither a new operator grant nor managed-installation authority. The
normal durable repository/supervisor supplies retention, cancellation and
deadlines; project input never selects the executable.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import pwd
import shlex
import stat
import subprocess
import ipaddress
from types import MappingProxyType
import sys
import time
import math
import tempfile
from contextlib import nullcontext

from sandbox.jobs.models import JobSubmission, SourceIdentity, validate_argv, validate_job_id

CONTRACT = 'job.server-cli.v1'
KIND = 'server-cli'


class ServerCLIUnavailable(RuntimeError):
    code = 'server_cli_capability_unavailable'


class ServerCLIEffectUnknown(RuntimeError):
    code = 'server_cli_effect_unknown'
    completion_unknown = True


class ServerCLIStdinUnavailable(ServerCLIUnavailable):
    code = 'server_cli_stdin_unavailable'


@dataclass(frozen=True)
class ServerCliOwner:
    uid: int
    home: str
    sandbox_home: str
    executable: str
    target_identity: str
    ownership_marker: str
    runtime_revision: str


def _current_owner() -> ServerCliOwner:
    """Read only the selected nonsecret unit metadata, never EnvironmentFile."""
    from sandbox.core._paths import ROOT, RUNTIME_DIR
    from sandbox.core._remote import _remote_mcp_runtime_revision, REMOTE_MCP_SERVICE
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()
    unit = home / '.config/systemd/user' / REMOTE_MCP_SERVICE
    info = unit.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise ServerCLIUnavailable('selected owner service metadata is unavailable')
    fields = {}
    selected = {'ExecStart', 'WorkingDirectory'}
    for line in unit.read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and key in selected:
            if key in fields:
                raise ServerCLIUnavailable('selected owner service metadata is ambiguous')
            fields[key] = value
        for name in ('SANDBOX_REMOTE_MCP_MARKER', 'SANDBOX_REMOTE_MCP_RUNTIME_REVISION'):
            prefix = 'Environment=' + name + '='
            if line.startswith(prefix):
                if name in fields:
                    raise ServerCLIUnavailable('selected service identity is ambiguous')
                fields[name] = line[len(prefix):]
    revision = fields.get('SANDBOX_REMOTE_MCP_RUNTIME_REVISION')
    marker = fields.get('SANDBOX_REMOTE_MCP_MARKER')
    executable = installed_cli()
    command = shlex.split(fields.get('ExecStart', '').replace('%h', str(home)))
    working = fields.get('WorkingDirectory', '').replace('%h', str(home))
    if (not isinstance(marker, str) or not re.fullmatch('[0-9a-f]{24}', marker)
            or revision != _remote_mcp_runtime_revision()
            or not command or Path(command[0]).resolve() != executable
            or Path(working).resolve() != Path(ROOT).resolve()):
        raise ServerCLIUnavailable('installed CLI and selected owner service identity do not agree')
    return ServerCliOwner(os.geteuid(), str(home), str(Path(RUNTIME_DIR).parent.resolve()),
        str(executable), machine_identity(), marker, revision)


def read_current_server_owner() -> ServerCliOwner:
    """Nonsecret installed receiver metadata only, never a job execution grant."""
    try:
        return _current_owner()
    except ServerCLIUnavailable:
        raise
    except (OSError, ValueError, KeyError):
        raise ServerCLIUnavailable('current installed owner binding is unavailable') from None


def installed_cli() -> Path:
    from sandbox.core._paths import ROOT
    path = (Path(ROOT) / 'sb').resolve()
    if not path.is_file() or not os.access(path, os.X_OK) or path.stat().st_uid != os.geteuid():
        raise ServerCLIUnavailable('the current Linux owner installed CLI is unavailable')
    return path


def machine_identity() -> str:
    if not sys.platform.startswith('linux'):
        raise ServerCLIUnavailable('server CLI requires the selected Linux owner context')
    value = Path('/etc/machine-id').read_text().strip().lower()
    if not re.fullmatch('[0-9a-f]{32}', value):
        raise ServerCLIUnavailable('canonical machine identity is unavailable')
    return hashlib.sha256(b'sandbox-host-machine-id-v1\0' + value.encode('ascii')).hexdigest()[:24]


def capabilities() -> dict:
    """Read-only installed component discovery; no job/config/store creation."""
    from sandbox.core._remote import _remote_mcp_runtime_revision
    from sandbox.commands.jobs_runtime import configure_start_parser
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    configure_start_parser(parser)
    # The actual installed parser must assemble the fixed handler contract.
    if '--server-cli' not in parser._option_string_actions:
        raise ServerCLIUnavailable('fixed installed CLI job handler is not assembled')
    owner = _current_owner()
    revision = owner.runtime_revision
    if not re.fullmatch('[0-9a-f]{24}', revision):
        raise ServerCLIUnavailable('installed CLI revision is unavailable')
    from sandbox.application.server_cli_targets import installed_inspector, CONTRACT as INSPECTION_CONTRACT
    try:
        installed_inspector()
        inspection = [INSPECTION_CONTRACT]
    except ServerCLIUnavailable:
        inspection = []
    from sandbox.application.server_cli_exec import exec_contract_available, capture_contract_available, CONTRACT as EXEC_CONTRACT
    from sandbox.application.server_cli_capture import CONTRACT as CAPTURE_CONTRACT
    from sandbox.jobs.acceptance import CONTRACT as ACCEPTANCE_CONTRACT
    return {'schema_version': 1, 'ok': True, 'target_identity': owner.target_identity,
            'ownership_marker': owner.ownership_marker, 'runtime_revision': revision,
            'executable': owner.executable, 'uid': owner.uid,
            'target_metadata': _receiver_routing_metadata(owner),
            'inspection_capabilities': inspection,
            'server_exec_capabilities': [EXEC_CONTRACT] if exec_contract_available() else [],
            'server_exec_capture_capabilities': [CAPTURE_CONTRACT] if capture_contract_available() else [],
            'capabilities': ['job.exec', 'job.execution-policy.v1', ACCEPTANCE_CONTRACT, CONTRACT]}


def _receiver_routing_metadata(owner: ServerCliOwner) -> dict:
    """Unique actual public interface addresses, never an Internet/hostname guess."""
    from sandbox.services.process import BoundedProcessRunner
    try:
        observed = BoundedProcessRunner(max_output=65_536, terminate_on_output_limit=True).run(
            ['ip', '-json', 'address', 'show', 'scope', 'global'], cwd=owner.home,
            env=owner_environment(owner), timeout=3)
        if observed.returncode or observed.termination_reason or observed.stdout_truncated:
            return {}
        rows = json.loads(observed.stdout)
        if not isinstance(rows, list) or len(rows) > 64:
            return {}
        candidates = {4: set(), 6: set()}
        for row in rows:
            addresses = row.get('addr_info') if isinstance(row, dict) else None
            if not isinstance(addresses, list) or len(addresses) > 256:
                return {}
            for item in addresses:
                if not isinstance(item, dict) or item.get('scope') != 'global':
                    continue
                if not isinstance(item.get('local'), str):
                    return {}
                address = ipaddress.ip_address(item['local'])
                if address.is_global:
                    candidates[address.version].add(str(address))
        return {field: next(iter(candidates[version])) for field, version in
                (('origin_ipv4', 4), ('origin_ipv6', 6)) if len(candidates[version]) == 1}
    except (OSError, ValueError, TypeError, KeyError):
        return {}  # Missing/ambiguous routing evidence is not denied owner access.


def _command_head(values: tuple[str, ...]) -> tuple[str, ...]:
    """Inspect documented global options without discarding their semantics."""
    index = 0
    while index < len(values) and values[index].startswith('-'):
        option = values[index]
        if option == '--strict-runtime':
            index += 1
        elif option in {'--instance', '--label'}:
            if index + 1 >= len(values):
                raise ServerCLIUnavailable('global CLI selector needs its value')
            index += 2
        elif option.startswith(('--instance=', '--label=')):
            index += 1
        elif option in {'--help', '-h', '--version'}:
            return ()  # The CLI exits without executing a command.
        else:
            raise ServerCLIUnavailable('global CLI option is unavailable in retained commands')
    return values[index:]


def _command_argv(argv) -> tuple[str, ...]:
    values = validate_argv(tuple(argv))
    if values[0] in {'sb', 'sandbox'} or Path(values[0]).is_absolute():
        raise ServerCLIUnavailable('server CLI expects command argv without an executable')
    # Secret values and free-form secret-printing shells are not a job contract.
    # Provider-specific normal commands consume the server store themselves.
    from sandbox.application.command_selection import selected_invocation, CommandSelectionUnavailable
    from sandbox.services.redaction import require_safe_argv
    require_safe_argv(values)
    try:
        selection = selected_invocation(values)
    except CommandSelectionUnavailable:
        raise ServerCLIUnavailable('retained command does not match the installed CLI grammar') from None
    selected = selection.command if selection is not None else None
    input_flags = ('--stdin', '--token-stdin', '--passphrase-stdin', '--interactive')
    if selected is None:
        # Non-CLI embeddings have no canonical parser. Preserve safe legacy
        # prefixes, but ambiguous sensitive syntax cannot grant execution.
        selected = _command_head(values)
        if (selected[:1] in {('secrets',), ('host',)} and len(selected) > 1
                and selected[1].startswith('-')):
            raise ServerCLIUnavailable('canonical installed CLI selection is unavailable')
        # Embeddings without the installed parser cannot disambiguate option
        # prefixes. Refuse possible input-mode spellings rather than supply EOF.
        stdin_requested = any(value.startswith('--') and value != '--'
                              and any(flag.startswith(value.split('=', 1)[0]) for flag in input_flags)
                              for value in values)
    else:
        stdin_requested = selection.stdin_requested
    if selected[:2] in {('secrets', 'reveal'), ('secrets', 'run')}:
        raise ServerCLIUnavailable('secret value output is unavailable through server CLI jobs')
    if selected[:2] == ('host', 'exec') or stdin_requested:
        raise ServerCLIStdinUnavailable('retained server CLI stdin transport is unavailable; no job was accepted')
    return values


def fixed_argv(argv) -> tuple[str, ...]:
    values = _command_argv(argv)
    return (str(installed_cli()), *values)


@dataclass(frozen=True)
class ServerCLIContext:
    schema_version: int
    job_id: str
    request_id: str
    project_root: str
    project_identity: str
    source_identity: str
    source_commit: str | None
    source_dirty_digest: str | None
    target_identity: str
    runtime_revision: str
    deadline_at: str
    argv: tuple[str, ...]
    origin_remote_name: str
    target_metadata: object
    owner: ServerCliOwner
    target_selector_digest: str | None = None


def validate_target_metadata(value) -> dict:
    """Routing observations only; no key, token, ssh/config or role fields."""
    from sandbox.jobs.server_cli_retained import routing_metadata
    try:
        return routing_metadata(value)
    except (ValueError, TypeError):
        raise ServerCLIUnavailable('original target routing metadata is invalid') from None


def server_cli_context_if_selected() -> ServerCLIContext | None:
    """None only for no selected server-CLI job; invalid selection never falls back."""
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository
    selected = os.environ.get('SANDBOX_DURABLE_EXECUTION_KIND') == KIND
    job_id = os.environ.get('SANDBOX_DURABLE_JOB_ID', '')
    if not job_id:
        if selected:
            raise ServerCLIUnavailable('selected server CLI job identity is missing')
        return None
    try:
        validate_job_id(job_id)
    except ValueError:
        raise ServerCLIUnavailable('selected durable job identity is invalid') from None
    registry = Path(RUNTIME_DIR) / 'jobs' / 'registry.sqlite3'
    if not registry.is_file() or registry.is_symlink() or registry.stat().st_uid != os.geteuid():
        raise ServerCLIUnavailable('selected durable job registry is unavailable')
    repository = JobRepository(registry)
    try:
        try:
            row = repository.get(job_id)
        except Exception:
            raise ServerCLIUnavailable('selected durable job evidence is unavailable') from None
    finally:
        repository.close()
    if selected or row.get('kind') == KIND:
        return current_server_cli_context()
    if not isinstance(row.get('kind'), str) or not row['kind']:
        raise ServerCLIUnavailable('selected durable job kind is unavailable')
    return None  # Existing ordinary durable execution remains ordinary.


def _read_server_cli_context() -> ServerCLIContext:
    """Prove this kernel child against its retained original server job.

    Environment carries only a lookup key. It cannot assert authority: the
    durable running row, exact installed argv and both live process identities
    must agree. Bounded waiting covers the supervisor's child-identity commit.
    """
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository
    from sandbox.jobs.process import capture_process_identity
    job_id = os.environ.get('SANDBOX_DURABLE_JOB_ID', '')
    try:
        validate_job_id(job_id)
    except ValueError:
        raise ServerCLIUnavailable('no retained server CLI job context') from None
    registry = Path(RUNTIME_DIR) / 'jobs' / 'registry.sqlite3'
    if not registry.is_file() or registry.is_symlink() or registry.stat().st_uid != os.geteuid():
        raise ServerCLIUnavailable('owner job registry is unavailable')
    repository = JobRepository(registry)
    try:
        limit = time.monotonic() + 1
        while True:
            row = repository.snapshot(job_id)
            process = row.get('process') or {}
            if process.get('child_pid') is not None or time.monotonic() >= limit:
                break
            time.sleep(0.01)
        argv = tuple(json.loads(row.get('command_json') or '[]'))
        if (row.get('kind') != KIND or row.get('target_kind') != 'local'
                or row.get('lifecycle') != 'running' or not row.get('request_id')
                or not argv or argv[0] != str(installed_cli())
                or process.get('child_pid') != os.getpid()
                or process.get('supervisor_pid') != os.getppid()):
            raise ServerCLIUnavailable('current process is not the retained installed CLI job')
        child = capture_process_identity(os.getpid())
        parent = capture_process_identity(os.getppid())
        if (child is None or parent is None
                or child.host_boot_id != process.get('host_boot_id')
                or parent.host_boot_id != process.get('host_boot_id')
                or child.start_identity != process.get('child_start_identity')
                or parent.start_identity != process.get('supervisor_start_identity')):
            raise ServerCLIUnavailable('retained server CLI process identity changed')
        started = datetime.fromisoformat(row['started_at'].replace('Z', '+00:00'))
        deadline = started + timedelta(seconds=row['deadline_seconds'])
        if datetime.now(timezone.utc) >= deadline:
            raise ServerCLIUnavailable('retained server CLI job deadline expired')
        owner = _current_owner()
        provenance = json.loads(row.get('execution_policy_provenance_json') or '{}')
        if 'server_cli.absolute_deadline_at' in provenance:
            from sandbox.application.server_cli_exec import remaining_deadline
            remaining_deadline(provenance['server_cli.absolute_deadline_at'])
            deadline = min(deadline, datetime.fromisoformat(provenance['server_cli.absolute_deadline_at'].replace('Z', '+00:00')))
        from sandbox.jobs.server_cli_lineage import check_server_cli_lineage, ServerCliLineageError
        try:
            inherited = check_server_cli_lineage(repository.server_cli_ancestry(job_id))
        except ServerCliLineageError:
            raise ServerCLIUnavailable('original ancestor cancellation or deadline refuses execution') from None
        if inherited is not None:
            deadline = min(deadline, inherited)
        origin_remote_name = provenance.get('server_cli.remote_name')
        target_metadata = validate_target_metadata(json.loads(provenance.get('server_cli.target_metadata', '{}')))
        if (provenance.get('server_cli.target_identity') != owner.target_identity
                or provenance.get('server_cli.runtime_revision') != owner.runtime_revision
                or provenance.get('server_cli.ownership_marker') != owner.ownership_marker
                or not isinstance(origin_remote_name, str)
                or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', origin_remote_name)):
            raise ServerCLIUnavailable('original job target or installed service identity changed')
        selector_digest = provenance.get('server_cli.target_selector_digest')
        if selector_digest is not None and (not isinstance(selector_digest, str)
                or re.fullmatch(r'sha256:[0-9a-f]{64}', selector_digest) is None):
            raise ServerCLIUnavailable('retained original target selector is invalid')
        return ServerCLIContext(1, job_id, row['request_id'], row['project_root'],
            row['project_identity'], row['source_identity'], row.get('source_commit'),
            row.get('source_dirty_digest'), owner.target_identity, owner.runtime_revision,
            deadline.isoformat(), argv[1:], origin_remote_name, MappingProxyType(target_metadata), owner,
            target_selector_digest=selector_digest)
    finally:
        repository.close()


def current_server_cli_context() -> ServerCLIContext:
    try:
        return _read_server_cli_context()
    except ServerCLIUnavailable:
        raise
    except Exception:
        raise ServerCLIUnavailable('retained server CLI evidence is unavailable') from None


def validate_owner_current(owner: ServerCliOwner) -> ServerCLIContext:
    """A typed value alone is insufficient: revalidate this actual retained child."""
    if type(owner) is not ServerCliOwner:
        raise ServerCLIUnavailable('typed current server CLI owner is required')
    context = current_server_cli_context()
    if context.owner != owner:
        raise ServerCLIUnavailable('current server CLI target identity changed')
    return context


def local_server_cli_submission_argv(owner: ServerCliOwner, argv, *, project_dir: str,
                                    request_id: str, source_commit: str,
                                    timeout_seconds: int, parent_job_id: str) -> list[str]:
    """Build one nested fixed CLI job from current kernel/retained authority."""
    context = validate_owner_current(owner)
    if parent_job_id != context.job_id:
        raise ServerCLIUnavailable('nested server CLI parent must be this original job')
    if not isinstance(source_commit, str) or not re.fullmatch('[0-9a-f]{40}', source_commit):
        raise ValueError('nested server CLI requires exact source commit')
    from sandbox.config.runtime import resolve_execution_policy
    import base64
    values = fixed_argv(argv)[1:]
    remaining = math.floor((datetime.fromisoformat(context.deadline_at) - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        raise ServerCLIUnavailable('original server CLI deadline expired')
    policy = resolve_execution_policy({}, execution_profile='exec',
                                      timeout_seconds=min(timeout_seconds, remaining))
    key = 'nested-' + hashlib.sha256((parent_job_id + '\0' + request_id).encode()).hexdigest()[:32]
    encoded = base64.b64encode(json.dumps(policy.as_dict(), sort_keys=True, separators=(',', ':')).encode()).decode()
    return [owner.executable, 'job-start', '--local', '--server-cli',
        '--server-cli-target', owner.target_identity, '--server-cli-revision', owner.runtime_revision,
        '--server-cli-marker', owner.ownership_marker, '--server-cli-origin', context.origin_remote_name,
        '--server-cli-target-metadata', json.dumps(dict(context.target_metadata), sort_keys=True, separators=(',', ':')),
        '--parent-job-id', parent_job_id, '--project-dir', str(Path(project_dir).resolve()),
        '--request-id', key, '--source-commit', source_commit,
        '--timeout', str(policy.deadline_seconds), '--execution-policy-json', encoded, '--json', '--', *values]


def validate_nested_parent(parent_job_id: str, *, target_identity: str,
                           marker: str, revision: str, origin_remote_name: str,
                           timeout_seconds: int) -> str:
    """Validate an actual current kernel descendant, not a serialized owner grant."""
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository
    from sandbox.jobs.process import capture_process_identity
    validate_job_id(parent_job_id)
    repository = JobRepository(Path(RUNTIME_DIR) / 'jobs/registry.sqlite3')
    try:
        row = repository.snapshot(parent_job_id)
        from sandbox.jobs.server_cli_lineage import check_server_cli_lineage, ServerCliLineageError
        try:
            inherited = check_server_cli_lineage(repository.server_cli_ancestry(parent_job_id))
        except ServerCliLineageError:
            raise ServerCLIUnavailable('original ancestor cancellation or deadline refuses nested admission') from None
    finally:
        repository.close()
    process = row.get('process') or {}
    proof = json.loads(row.get('execution_policy_provenance_json') or '{}')
    if (row.get('kind') != KIND or row.get('lifecycle') != 'running'
            or proof.get('server_cli.target_identity') != target_identity
            or proof.get('server_cli.ownership_marker') != marker
            or proof.get('server_cli.runtime_revision') != revision
            or proof.get('server_cli.remote_name') != origin_remote_name):
        raise ServerCLIUnavailable('original nested CLI parent binding is unavailable')
    ancestor = capture_process_identity(process.get('child_pid'))
    if (ancestor is None or ancestor.host_boot_id != process.get('host_boot_id')
            or ancestor.start_identity != process.get('child_start_identity')):
        raise ServerCLIUnavailable('original parent process identity changed')
    current = os.getppid()
    for _ in range(64):
        if current == process.get('child_pid'):
            break
        if current <= 1:
            raise ServerCLIUnavailable('nested CLI is not an original job descendant')
        raw = Path('/proc', str(current), 'stat').read_text()
        current = int(raw.rpartition(')')[2].split()[1])
    else:
        raise ServerCLIUnavailable('nested CLI ancestry is unavailable')
    deadline = datetime.fromisoformat(row['started_at'].replace('Z', '+00:00')) + timedelta(seconds=row['deadline_seconds'])
    if proof.get('server_cli.parent_deadline_at'):
        deadline = min(deadline, datetime.fromisoformat(proof['server_cli.parent_deadline_at']))
    if inherited is not None:
        deadline = min(deadline, inherited)
    remaining = math.floor((deadline - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        raise ServerCLIUnavailable('nested CLI exceeds original remaining deadline')
    return deadline.isoformat()


def owner_environment(owner: ServerCliOwner) -> dict[str, str]:
    """Explicit server identity only: never enumerate/copy provider environment."""
    account = pwd.getpwuid(owner.uid)
    runtime = '/run/user/' + str(owner.uid)
    return {'HOME': owner.home, 'SANDBOX_HOME': owner.sandbox_home,
            'USER': account.pw_name, 'LOGNAME': account.pw_name,
            'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1',
            'XDG_RUNTIME_DIR': runtime, 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + runtime + '/bus'}


def owner_command_args(owner: ServerCliOwner, command: str) -> list[str]:
    """Validated local argv for callers retaining their own bounded stdin FD."""
    validate_owner_current(owner)
    if not isinstance(command, str) or not command or '\0' in command:
        raise ValueError('local owner command is invalid')
    return ['/bin/sh', '-c', command]


def owner_process(owner: ServerCliOwner, command: str, *, timeout: float = 30,
                  input_data=None) -> subprocess.CompletedProcess:
    """Same-server local process channel, retaining native command/target guards."""
    result = owner_process_bounded(owner, command, timeout=timeout, input_data=input_data)
    if result.stdout_truncated or result.stderr_truncated:
        raise ServerCLIEffectUnknown('bounded owner output is incomplete; effect completion is unknown')
    stdout, stderr = result.stdout, result.stderr
    if isinstance(input_data, (bytes, bytearray)):
        stdout, stderr = stdout.encode(), stderr.encode()
    return subprocess.CompletedProcess(result.argv, result.returncode, stdout=stdout, stderr=stderr)


def owner_process_bounded(owner: ServerCliOwner, command: str, *, timeout: float = 30,
                          input_data=None, max_output: int = 1_048_576,
                          terminate_on_output_limit: bool = True):
    from sandbox.services.process import BoundedProcessRunner
    context = validate_owner_current(owner)
    if not isinstance(command, str) or not command or '\0' in command:
        raise ValueError('local owner command is invalid')
    deadline = datetime.fromisoformat(context.deadline_at)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise ServerCLIUnavailable('original server CLI deadline expired')
    if input_data is not None and not isinstance(input_data, (str, bytes, bytearray)):
        raise ValueError('owner process input must be bytes or text')
    # Anonymous owner-only input, never a named shared temporary credential file.
    # File stdin avoids pipe-writer deadlock and stays within the runner deadline.
    with (tempfile.TemporaryFile() if input_data is not None else nullcontext(None)) as source:
        if source is not None:
            source.write(input_data.encode() if isinstance(input_data, str) else input_data)
            source.seek(0)
        result = BoundedProcessRunner(max_output=max_output,
            terminate_on_output_limit=terminate_on_output_limit).run(
                ['/bin/sh', '-c', command], cwd=owner.home, env=owner_environment(owner),
                timeout=min(timeout, remaining), stdin=None if source is None else source.fileno(), start_new_session=False)
    # Remain in the original durable process group: its supervisor can still
    # terminate grandchildren after this caller exits. A local timeout does not
    # prove that every effect or escaped descendant stopped, so never retry it.
    if result.termination_reason is not None:
        raise ServerCLIEffectUnknown('owner command completion is unknown; inspect the original retained job')
    try:
        validate_owner_current(owner)
    except ServerCLIUnavailable:
        raise ServerCLIEffectUnknown('original owner job changed after dispatch; effect completion is unknown') from None
    return result


class FixedServerCLI:
    contract = CONTRACT

    def environment(self, submission) -> dict[str, str]:
        owner = _current_owner()
        proof = submission.execution_policy_provenance or {}
        if (submission.kind != KIND or submission.target_kind != 'local'
                or submission.argv[0] != owner.executable
                or not submission.request_id
                or not isinstance(proof.get('server_cli.remote_name'), str)
                or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', proof['server_cli.remote_name'])
                or proof.get('server_cli.target_identity') != owner.target_identity
                or proof.get('server_cli.runtime_revision') != owner.runtime_revision
                or proof.get('server_cli.ownership_marker') != owner.ownership_marker):
            raise ServerCLIUnavailable('fixed CLI descriptor binding changed')
        return owner_environment(owner)


def dispatch_server_cli(remote_name: str, argv, *, project_dir: str,
                        request_id: str, timeout_seconds: int = 900) -> dict:
    """Submit before controller host/CI/provider-secret preflight.

    Returns the original remote durable acceptance receipt; never retries an
    unknown acceptance, streams child stdio, or invokes a self remote alias.
    """
    from sandbox.core import _remote
    from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError
    from sandbox.resources.context import authenticated_target_identity
    from sandbox.services.redaction import require_safe_argv
    from sandbox.config.facade import project_identity
    from sandbox.config.runtime import resolve_execution_policy
    values = _command_argv(argv)
    require_safe_argv(values)
    if not request_id:
        raise ValueError('server CLI requires the original request identity')
    # JobSubmission validates all caller input before lookup or transport.
    root = str(Path(project_dir).resolve())
    canonical = project_identity({'root': root})
    policy = resolve_execution_policy({}, workspace='server-cli', execution_profile='exec',
                                      timeout_seconds=timeout_seconds)
    submission = JobSubmission(kind=KIND, project_root=root,
        project_identity=canonical['identity'], target_kind='remote', remote_name=remote_name,
        workspace_label='server-cli', argv=values, deadline_seconds=policy.deadline_seconds,
        source=SourceIdentity('server-cli-request'), request_id=request_id,
        execution_profile=policy.execution_profile, output_profile='smart',
        deadline_source=policy.deadline_source, deadline_reminder=policy.deadline_reminder,
        stall_seconds=policy.stall_seconds, cancel_grace_seconds=policy.cancel_grace_seconds,
        cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
        execution_policy_provenance=policy.provenance)
    entry = _remote.get_remote(remote_name)
    if not isinstance(entry, dict) or entry.get('joined') is not True or entry.get('control_transport') != 'tailscale':
        raise ServerCLIUnavailable('server CLI requires an admitted joined Tailnet registration')
    identity = authenticated_target_identity(remote_name)
    # A fresh identity read may have safely reconnected the observed revision
    # cache. Use that current registration, preserving the original owner pin.
    current = _remote.get_remote(remote_name)
    if (not isinstance(current, dict) or current.get('joined') is not True
            or current.get('control_transport') != 'tailscale'
            or (current.get('mcp_service') or {}).get('ownership_marker') !=
               (entry.get('mcp_service') or {}).get('ownership_marker')
            or ('control_identity' in current and current['control_identity'] !=
                {'schema_version': 1, 'target_identity': identity['target_identity']})):
        raise ServerCLIUnavailable('joined owner registration changed during identity observation')
    entry = current
    transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
        ssh_run=_remote.ssh_run, remote_lookup=lambda name: entry if name == remote_name else None,
        remote_sb_path=_remote.remote_sb_path)
    try:
        observed = transport.control(remote_name, ['job-capabilities'])
    except RemoteJobTransportError:
        # This constant operation only observes the installed contract. The
        # source/job submission below has not been invoked, even if its reply
        # is unavailable. Do not strand a request as unknown acceptance.
        raise ServerCLIUnavailable('installed server CLI capability could not be observed') from None
    from sandbox.jobs.acceptance import CONTRACT as ACCEPTANCE_CONTRACT
    if (type(observed.get('schema_version')) is not int or observed['schema_version'] != 1
            or observed.get('target_identity') != identity['target_identity']
            or observed.get('runtime_revision') != (entry.get('mcp_service') or {}).get('runtime_revision')
            or observed.get('ownership_marker') != (entry.get('mcp_service') or {}).get('ownership_marker')
            or not isinstance(observed.get('capabilities'), list)
            or any(not isinstance(value, str) for value in observed['capabilities'])
            or not {CONTRACT, ACCEPTANCE_CONTRACT}.issubset(observed['capabilities'])):
        raise ServerCLIUnavailable('installed server CLI contract is unavailable or changed')
    routing = validate_target_metadata(observed.get('target_metadata', {}))
    selected = dict(entry)
    for field, value in routing.items():
        if not selected.get(field):
            selected[field] = value
    return transport.submit_server_cli(submission, selected, observed)

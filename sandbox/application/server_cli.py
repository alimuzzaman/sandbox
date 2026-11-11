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
import sys
import time

from sandbox.jobs.models import JobSubmission, SourceIdentity, validate_argv, validate_job_id

CONTRACT = 'job.server-cli.v1'
KIND = 'server-cli'


class ServerCLIUnavailable(RuntimeError):
    code = 'server_cli_capability_unavailable'


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
    from sandbox.core._remote import _remote_mcp_runtime_revision
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()
    unit = home / '.config/systemd/user/sandbox-remote-mcp.service'
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
    return {'schema_version': 1, 'ok': True, 'target_identity': owner.target_identity,
            'ownership_marker': owner.ownership_marker, 'runtime_revision': revision,
            'executable': owner.executable, 'uid': owner.uid,
            'capabilities': ['job.exec', 'job.execution-policy.v1', CONTRACT]}


def fixed_argv(argv) -> tuple[str, ...]:
    values = validate_argv(tuple(argv))
    if values[0] in {'sb', 'sandbox'} or Path(values[0]).is_absolute():
        raise ServerCLIUnavailable('server CLI expects command argv without an executable')
    # Secret values and free-form secret-printing shells are not a job contract.
    # Provider-specific normal commands consume the server store themselves.
    if values[:2] in {('secrets', 'reveal'), ('secrets', 'run')}:
        raise ServerCLIUnavailable('secret value output is unavailable through server CLI jobs')
    from sandbox.services.redaction import require_safe_argv
    require_safe_argv(values)
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
    owner: ServerCliOwner


def current_server_cli_context() -> ServerCLIContext:
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
        if (provenance.get('server_cli.target_identity') != owner.target_identity
                or provenance.get('server_cli.runtime_revision') != owner.runtime_revision
                or provenance.get('server_cli.ownership_marker') != owner.ownership_marker):
            raise ServerCLIUnavailable('original job target or installed service identity changed')
        return ServerCLIContext(1, job_id, row['request_id'], row['project_root'],
            row['project_identity'], row['source_identity'], row.get('source_commit'),
            row.get('source_dirty_digest'), owner.target_identity, owner.runtime_revision,
            deadline.isoformat(), argv[1:], owner)
    finally:
        repository.close()


def validate_owner_current(owner: ServerCliOwner) -> ServerCLIContext:
    """A typed value alone is insufficient: revalidate this actual retained child."""
    if not isinstance(owner, ServerCliOwner):
        raise ServerCLIUnavailable('typed current server CLI owner is required')
    context = current_server_cli_context()
    if context.owner != owner:
        raise ServerCLIUnavailable('current server CLI target identity changed')
    return context


def owner_environment(owner: ServerCliOwner) -> dict[str, str]:
    """Explicit server identity only: never enumerate/copy provider environment."""
    account = pwd.getpwuid(owner.uid)
    runtime = '/run/user/' + str(owner.uid)
    return {'HOME': owner.home, 'SANDBOX_HOME': owner.sandbox_home,
            'USER': account.pw_name, 'LOGNAME': account.pw_name,
            'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1',
            'XDG_RUNTIME_DIR': runtime, 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + runtime + '/bus'}


def owner_process(owner: ServerCliOwner, command: str, *, timeout: float = 30,
                  input_data=None) -> subprocess.CompletedProcess:
    """Same-server local process channel, retaining native command/target guards."""
    context = validate_owner_current(owner)
    if not isinstance(command, str) or not command or '\0' in command:
        raise ValueError('local owner command is invalid')
    deadline = datetime.fromisoformat(context.deadline_at)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise ServerCLIUnavailable('original server CLI deadline expired')
    kwargs = {'capture_output': True, 'check': False, 'cwd': owner.home,
              'timeout': min(timeout, remaining), 'env': owner_environment(owner),
              'text': not isinstance(input_data, (bytes, bytearray))}
    if input_data is None:
        kwargs['stdin'] = subprocess.DEVNULL
    else:
        kwargs['input'] = input_data
    return subprocess.run(['/bin/sh', '-c', command], **kwargs)


class FixedServerCLI:
    contract = CONTRACT

    def environment(self, submission) -> dict[str, str]:
        owner = _current_owner()
        proof = submission.execution_policy_provenance or {}
        if (submission.kind != KIND or submission.target_kind != 'local'
                or submission.argv[0] != owner.executable
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
    from sandbox.transports.remote_jobs import RemoteJobTransport
    from sandbox.resources.context import authenticated_target_identity
    from sandbox.services.redaction import require_safe_argv
    from sandbox.config.facade import project_identity
    values = validate_argv(tuple(argv))
    require_safe_argv(values)
    if not request_id:
        raise ValueError('server CLI requires the original request identity')
    # JobSubmission validates all caller input before lookup or transport.
    root = str(Path(project_dir).resolve())
    canonical = project_identity({'root': root})
    submission = JobSubmission(kind=KIND, project_root=root,
        project_identity=canonical['identity'], target_kind='remote', remote_name=remote_name,
        workspace_label='server-cli', argv=values, deadline_seconds=timeout_seconds,
        source=SourceIdentity('server-cli-request'), request_id=request_id,
        execution_profile='exec', output_profile='smart')
    entry = _remote.get_remote(remote_name)
    if not isinstance(entry, dict) or entry.get('joined') is not True or entry.get('control_transport') != 'tailscale':
        raise ServerCLIUnavailable('server CLI requires an admitted joined Tailnet registration')
    identity = authenticated_target_identity(remote_name)
    transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
        ssh_run=_remote.ssh_run, remote_lookup=lambda name: entry if name == remote_name else None,
        remote_sb_path=_remote.remote_sb_path)
    observed = transport.control(remote_name, ['job-capabilities'])
    if (type(observed.get('schema_version')) is not int or observed['schema_version'] != 1
            or observed.get('target_identity') != identity['target_identity']
            or observed.get('runtime_revision') != (entry.get('mcp_service') or {}).get('runtime_revision')
            or observed.get('ownership_marker') != (entry.get('mcp_service') or {}).get('ownership_marker')
            or not isinstance(observed.get('capabilities'), list)
            or CONTRACT not in observed['capabilities']):
        raise ServerCLIUnavailable('installed server CLI contract is unavailable or changed')
    return transport.submit_server_cli(submission, entry, observed)

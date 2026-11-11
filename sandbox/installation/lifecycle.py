"""Root-owned managed release lifecycle. Host effects require explicit confirmation."""
from __future__ import annotations

import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

from sandbox.services.runtime_revision import runtime_revision
from .binding import InstallationBindingError, OWNER_POLICY, _read, _write
from .paths import ManagedPaths

SERVICE = 'sandbox-managed.service'


def refuse(code, message):
    raise InstallationBindingError(code, message)


def protected(path: Path, *, directory=False):
    try:
        info = path.lstat()
    except OSError:
        refuse('managed_path_unavailable', 'required managed path is unavailable')
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        refuse('managed_path_authority_invalid', 'managed executable or policy authority is invalid')
    return info


def release_descriptor(revision: str, paths=ManagedPaths()) -> dict:
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{24}', revision):
        refuse('managed_release_invalid', 'release revision must be 24 hexadecimal characters')
    root = paths.releases / revision
    for parent in reversed(root.parents):
        protected(parent, directory=True)
    protected(root, directory=True)
    descriptor = _read(root / 'release.json', 0, private=False)
    if (set(descriptor) != {'schema_version', 'runtime_revision', 'source_revision', 'bundle_digest'}
            or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
            or descriptor['runtime_revision'] != revision
            or not re.fullmatch(r'[0-9a-f]{40}', str(descriptor['source_revision']))
            or not re.fullmatch(r'[0-9a-f]{64}', str(descriptor['bundle_digest']))):
        refuse('managed_release_invalid', 'release descriptor is invalid')
    # Release may be read by all, but no service/job may replace any executable.
    for entry in root.rglob('*'):
        protected(entry, directory=entry.is_dir())
    if not (root / 'sb').is_file() or not (root / '.cli-venv/bin/python').is_file():
        refuse('managed_release_unprepared', 'release must include prepared CLI and dependencies')
    if not (root / 'mcp/wp-server/.venv/bin/python').is_file():
        refuse('managed_release_unprepared', 'release must include prepared control dependencies')
    if runtime_revision(root) != revision:
        refuse('managed_release_invalid', 'actual source differs from release descriptor')
    return descriptor


def render_unit(revision: str, bind: str, port: int, marker: str, paths=ManagedPaths()) -> str:
    import ipaddress
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        refuse('managed_bind_invalid', 'managed bind must be a tailnet IPv4 address')
    if (address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10')
            or type(port) is not int or not 1024 <= port <= 65535
            or not re.fullmatch(r'[0-9a-f]{24}', marker)):
        refuse('managed_bind_invalid', 'managed service endpoint is invalid')
    root = paths.releases / revision
    return '\n'.join(('[Unit]', 'Description=Sandbox managed shared control service',
        'After=network-online.target', 'Wants=network-online.target', '[Service]',
        'Type=simple', 'User=sandbox', 'Group=sandbox', 'UMask=0077',
        f'Environment=SANDBOX_HOME={paths.state}', 'Environment=SANDBOX_MANAGED=1',
        'Environment=SANDBOX_AUTO_UPDATE=0', f'Environment=SANDBOX_REMOTE_MCP_MARKER={marker}',
        f'Environment=SANDBOX_SECRETS_FILE={paths.config}/secrets/personal.env',
        f'Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION={revision}',
        f'EnvironmentFile={paths.config}/control.env', f'WorkingDirectory={root}',
        f'ExecStart={root}/sb mcp --transport streamable-http --bind {bind} --port {port}',
        f'ReadOnlyPaths={paths.releases} {paths.config}',
        f'ReadWritePaths={paths.config}/secrets',
        # Do not use StateDirectory on an unreviewed mixed-owner workload tree.
        'RuntimeDirectory=sandbox', 'RuntimeDirectoryMode=0700',
        'Restart=on-failure', 'RestartSec=3', '[Install]', 'WantedBy=multi-user.target', ''))


def principal(paths=ManagedPaths()):
    try:
        user = pwd.getpwnam('sandbox')
    except KeyError:
        user = None
    try:
        group = grp.getgrnam('sandbox')
    except KeyError:
        group = None
    if user is None and group is None:
        return None
    # Never infer ownership from a matching name or home alone.
    if user is None or group is None:
        refuse('managed_principal_collision', 'service principal needs reviewed ownership proof')
    protected(paths.config, directory=True)
    receipt = _read(paths.config / 'principal.json', 0, private=False)
    if (receipt != {'schema_version': 1, 'name': 'sandbox', 'uid': user.pw_uid, 'gid': group.gr_gid}
            or user.pw_gid != group.gr_gid or user.pw_dir != str(paths.state)
            or user.pw_shell not in {'/usr/sbin/nologin', '/sbin/nologin'}):
        refuse('managed_principal_collision', 'existing service account is not installation-owned')
    return user


def plan(revision, bind, port, marker, *, paths=ManagedPaths()) -> dict:
    descriptor = release_descriptor(revision, paths)
    unit = render_unit(revision, bind, port, marker, paths)
    user = principal(paths)
    if paths.state.exists():
        info = paths.state.lstat()
        if user is None or not stat.S_ISDIR(info.st_mode) or info.st_uid != user.pw_uid:
            refuse('managed_state_collision', 'state destination is not installation-owned')
    if paths.current.is_symlink():
        target = paths.current.resolve(strict=True)
        if target.parent != paths.releases:
            refuse('managed_release_collision', 'current release selector has foreign authority')
        protected(paths.current.parent, directory=True)
    elif os.path.lexists(paths.current):
        refuse('managed_release_collision', 'release selector is occupied')
    value = {'schema_version': 1, 'release': descriptor, 'bind': bind, 'port': port,
             'ownership_marker': marker, 'unit': unit, 'create_principal': user is None,
             'state': str(paths.state), 'data_deletion': False,
             'requires': ['root_approval', 'broker_provisioned_control_credentials',
                          'approved_docker_authority', 'exclusive_installation_maintenance']}
    value['plan_id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


class LinuxHost:
    """Fixed argv only, no inherited controller credentials or arbitrary shell."""
    def observe(self, argv):
        result = subprocess.run(tuple(argv), check=False, capture_output=True, text=True,
            timeout=60, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
        if len(result.stdout) + len(result.stderr) > 1024 * 1024:
            refuse('managed_host_output_unavailable', 'bounded host operation output was exceeded')
        return result

    def run(self, argv):
        result = self.observe(argv)
        if result.returncode:
            refuse('managed_host_operation_failed', 'managed host operation failed; inspect bounded service diagnostics')
        return result


def apply(expected_plan, *, confirm=False, approve_docker=False, maintenance=None, request_id=None,
          paths=ManagedPaths(), host=None) -> dict:
    if not confirm or not approve_docker or os.geteuid() != 0:
        refuse('managed_authority_required', 'root confirmation and approved Docker authority are required')
    if maintenance is None:
        refuse('managed_maintenance_required', 'installation maintenance adapter is required')
    current = plan(expected_plan['release']['runtime_revision'], expected_plan['bind'],
        expected_plan['port'], expected_plan['ownership_marker'], paths=paths)
    if current != expected_plan:
        refuse('managed_plan_stale', 'managed lifecycle observations changed')
    host = host or LinuxHost()
    # Adapter holds the installation guard and proves all old writers fenced.
    with maintenance.exclusive(request_id or expected_plan['plan_id']):
        if maintenance.writers_fenced() is not True:
            refuse('managed_writers_active', 'old installation writers are not proven fenced')
        known_user = principal(paths)
        for root in (paths.config, paths.state, paths.logs, paths.cache, paths.backups):
            if root.exists():
                if root in (paths.state, paths.logs, paths.cache) and known_user is not None:
                    info = root.lstat()
                    if not stat.S_ISDIR(info.st_mode) or info.st_uid != known_user.pw_uid or info.st_mode & 0o077:
                        refuse('managed_state_collision', 'mutable directory authority is invalid')
                elif root != paths.state:
                    protected(root, directory=True)
            else:
                root.mkdir(mode=0o700, parents=False)
        protected(paths.config, directory=True)
        credentials = paths.config / 'control.env'
        info = protected(credentials)
        if stat.S_IMODE(info.st_mode) != 0o600:
            refuse('managed_credentials_unavailable', 'broker must provision private control credentials')
        # The service manager reads root-only EnvironmentFile without exposing it.
        user = principal(paths)
        if user is None:
            host.run(('groupadd', '--system', 'sandbox'))
            host.run(('useradd', '--system', '--gid', 'sandbox', '--home-dir', str(paths.state),
                      '--no-create-home', '--shell', '/usr/sbin/nologin', 'sandbox'))
            user = pwd.getpwnam('sandbox')
            _write(paths.config / 'principal.json', {'schema_version': 1, 'name': 'sandbox',
                                                   'uid': user.pw_uid, 'gid': user.pw_gid})
        for root in (paths.state, paths.logs, paths.cache):
            os.chown(root, user.pw_uid, user.pw_gid)  # only roots, never recursive workload ownership
            os.chmod(root, 0o700)
        host.run(('usermod', '-aG', 'docker', 'sandbox'))
        broker_sources = paths.config / 'secrets'
        if os.path.lexists(broker_sources):
            info = broker_sources.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != user.pw_uid or info.st_mode & 0o077:
                refuse('managed_source_collision', 'broker source directory is not installation-owned')
        else:
            broker_sources.mkdir(mode=0o700)
            os.chown(broker_sources, user.pw_uid, user.pw_gid)
        policy_path = paths.config / 'server-policy.json'
        if not os.path.lexists(policy_path):
            _write(policy_path, OWNER_POLICY)
        else:
            policy = _read(policy_path, 0, private=False)
            if (set(policy) != set(OWNER_POLICY) or policy.get('admitted_operator') != 'owner'
                    or policy.get('approved_sudo') is not True):
                refuse('installation_policy_invalid', 'existing managed policy is invalid')
        # Root-owned policy may be group-readable to the service, never writable.
        os.chown(paths.config, 0, user.pw_gid)
        os.chmod(paths.config, 0o750)
        os.chown(paths.config / 'server-policy.json', 0, user.pw_gid)
        os.chmod(paths.config / 'server-policy.json', 0o640)
        _write(paths.config / 'admission.json', {'schema_version': 1, 'open': False})
        os.chown(paths.config / 'admission.json', 0, user.pw_gid)
        os.chmod(paths.config / 'admission.json', 0o640)
        revision = current['release']['runtime_revision']
        root = paths.releases / revision
        host.run(('runuser', '-u', 'sandbox', '--', str(root / 'sb'), 'installation', 'initialize',
            '--home', str(paths.state), '--layout', 'managed', '--ownership-marker', current['ownership_marker'],
            '--confirm', '--json'))
        unit_path = Path('/etc/systemd/system') / SERVICE
        previous = unit_path.read_text() if unit_path.exists() else None
        previous_selector = os.readlink(paths.current) if paths.current.is_symlink() else None
        try:
            _write_unit(unit_path, current['unit'])
            selector = paths.current.with_name('.current.pending')
            if os.path.lexists(selector):
                refuse('managed_release_collision', 'pending release selector needs reconciliation')
            selector.symlink_to(root)
            os.replace(selector, paths.current)
            host.run(('systemctl', 'daemon-reload'))
            host.run(('systemctl', 'enable', '--now', SERVICE))
            host.run(('systemctl', 'restart', SERVICE))
            host.run(('systemctl', 'is-active', '--quiet', SERVICE))
        except Exception:
            # Keep admission fenced. Never start an unproven old owner automatically.
            if previous is not None:
                _write_unit(unit_path, previous)
            if previous_selector is not None:
                rollback = paths.current.with_name('.current.rollback')
                if not os.path.lexists(rollback):
                    rollback.symlink_to(previous_selector)
                    os.replace(rollback, paths.current)
            raise
        return {'ok': True, 'plan_id': current['plan_id'], 'status': 'active_admission_fenced',
                'release': revision, 'data_deleted': False}


def _write_unit(path, text):
    # Atomic publication using the same private durable primitive as identity files.
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.sandbox-unit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)

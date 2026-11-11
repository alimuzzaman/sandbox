"""Linux kernel writer evidence and explicitly scoped service/source fencing.

No engine is selected here. Bind-mount evidence comes from kernel metadata and
covers existing container namespaces without pulling images or changing Docker.
Deliberately bypassing root administrators remain outside this proof; every
cutover requires an explicit administrator-quiescence acknowledgement.
"""
import os
from pathlib import Path
import pwd
import re
import stat

from .binding import InstallationBindingError

OWNER_UNIT = 'sandbox-mcp-remote.service'
MANAGED_UNIT = 'sandbox-managed.service'


def fail(code, message):
    raise InstallationBindingError(code, message)


def _inside(value, root):
    text = value.removesuffix(' (deleted)')
    path = Path(text)
    return path == root or root in path.parents


def kernel_writers(source, *, proc=Path('/proc'), ignore_pid=None):
    """Read only proc links/maps/mount metadata; never argv, environments or file bytes."""
    source = Path(source).resolve(strict=True)
    identity = source.stat()
    expected = (identity.st_dev, identity.st_ino)
    own_pid = os.getpid() if ignore_pid is None else ignore_pid
    own_namespace = (proc / str(own_pid) / 'ns/mnt').readlink()
    handles, mappings, binds, unknown = set(), set(), set(), 0
    visited_namespaces = set()
    try:
        processes = list(proc.iterdir())
    except OSError:
        fail('installation_writer_evidence_unavailable', 'kernel process inventory is unavailable')
    for process in processes:
        if not process.name.isdigit() or int(process.name) == own_pid:
            continue
        try:
            for name in ('cwd', 'root'):
                try:
                    if _inside(str((process / name).readlink()), source):
                        handles.add(int(process.name))
                except FileNotFoundError:
                    pass
            for descriptor in (process / 'fd').iterdir():
                try:
                    if _inside(str(descriptor.readlink()), source):
                        handles.add(int(process.name))
                except FileNotFoundError:
                    continue
            # Only mapped path/permissions metadata is inspected, not mapped bytes.
            with (process / 'maps').open() as stream:
                for line in stream:
                    fields = line.split(None, 5)
                    if len(fields) == 6 and 'w' in fields[1] and _inside(fields[5].strip(), source):
                        mappings.add(int(process.name))
            namespace = (process / 'ns/mnt').readlink()
            if namespace == own_namespace or str(namespace) in visited_namespaces:
                continue
            visited_namespaces.add(str(namespace))
            with (process / 'mountinfo').open() as stream:
                for line in stream:
                    fields = line.split()
                    if len(fields) < 7 or '-' not in fields:
                        unknown += 1
                        continue
                    mounted_root = Path(fields[3].replace('\\040', ' ').replace('\\134', '\\'))
                    target = Path(fields[4].replace('\\040', ' ').replace('\\134', '\\'))
                    if _inside(str(mounted_root), source):
                        binds.add(str(namespace))
                        continue
                    if mounted_root in source.parents:
                        candidate = process / 'root' / str(target).lstrip('/') / source.relative_to(mounted_root)
                        try:
                            candidate_info = candidate.stat()
                        except FileNotFoundError:
                            continue
                        # A broad host bind at /host also grants access to the
                        # source even when the source prefix is not mountinfo.root.
                        if (candidate_info.st_dev, candidate_info.st_ino) == expected and target != Path('/'):
                            if not candidate.statvfs().f_flag & getattr(os, 'ST_RDONLY', 1):
                                binds.add(str(namespace))
        except FileNotFoundError:
            continue  # A process that exited cannot retain a writer handle.
        except (OSError, ValueError):
            unknown += 1
    return {'open_handle_processes': len(handles), 'writable_mapping_processes': len(mappings),
            'active_bind_namespaces': len(binds), 'unknown_observations': unknown}


def mount_rows(source, *, mountinfo=Path('/proc/self/mountinfo')):
    target = str(Path(source).resolve(strict=True))
    rows = []
    for line in mountinfo.read_text().splitlines():
        fields = line.split()
        if len(fields) < 7 or '-' not in fields:
            fail('installation_mount_evidence_invalid', 'mount metadata is incomplete')
        path = fields[4].replace('\\040', ' ').replace('\\134', '\\')
        if path == target:
            rows.append({'id': int(fields[0]), 'root': fields[3],
                         'read_only': 'ro' in fields[5].split(',')})
    return rows


class LinuxFence:
    """A scoped old-service fence; source trees are never unmounted without owned evidence."""
    def __init__(self, host, *, source=None, source_uid=None, administrator_quiescent=False,
                 managed=False, writer_probe=kernel_writers):
        self.host = host
        self.source = Path(source) if source is not None else None
        if self.source is not None and (not self.source.is_absolute()
                or self.source.is_symlink() or self.source.resolve(strict=True) != self.source):
            fail('installation_source_invalid', 'source must be a canonical non-link directory')
        self.source_uid = source_uid
        self.administrator_quiescent = administrator_quiescent is True
        self.managed = managed
        self.writer_probe = writer_probe
        self.receipt = None

    def _unit_argv(self, *operation):
        if self.managed:
            return ('systemctl', *operation, MANAGED_UNIT)
        if self.source_uid is None:
            fail('installation_source_owner_unavailable', 'source owner must be derived from approved source metadata')
        owner = pwd.getpwuid(self.source_uid).pw_name
        return ('runuser', '-u', owner, '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{self.source_uid}',
                'systemctl', '--user', *operation, OWNER_UNIT)

    def service_state(self):
        value = self.host.observe(self._unit_argv('is-active'))
        text = value.stdout.strip()
        if value.returncode not in (0, 3, 4) or text not in {'active', 'inactive', 'failed', 'unknown'}:
            fail('installation_service_evidence_unavailable', 'selected service state is not proven')
        return text

    def seal(self, *, request_id, journal):
        if not self.administrator_quiescent:
            fail('administrator_quiescence_required', 'operator must acknowledge the exclusive administrator maintenance window')
        if self.receipt is not None:
            if self.receipt.get('request_id') != request_id or not self.proven():
                fail('installation_fence_reconcile_required', 'existing source fence requires matching live reconciliation')
            return self.receipt
        if self.source is not None:
            info = self.source.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != self.source_uid:
                fail('installation_source_changed', 'source directory authority changed')
            if mount_rows(self.source):
                fail('installation_source_mount_conflict', 'source already has a mount requiring explicit reconciliation')
            before = self.writer_probe(self.source)
            if before['active_bind_namespaces'] or before['unknown_observations']:
                fail('installation_workload_bind_active', 'active source binds or unknown kernel observations require an approved workload plan')
        prior_active = self.service_state() == 'active'
        prior_enabled = self.host.observe(self._unit_argv('is-enabled'))
        if prior_enabled.returncode not in (0, 1, 3, 4) or prior_enabled.stdout.strip() not in {
                'enabled', 'disabled', 'static', 'indirect'}:
            fail('installation_service_evidence_unavailable', 'selected unit enablement is not proven')
        prior = {'was_active': prior_active, 'was_enabled': prior_enabled.stdout.strip()}
        self.host.run(self._unit_argv('stop'))
        self.host.run(self._unit_argv('disable'))
        if self.service_state() == 'active':
            fail('installation_old_owner_active', 'old Sandbox service is still active')
        if self.source is None:
            self.receipt = {'request_id': request_id, 'service_stopped': True, 'source': None, **prior}
            return self.receipt
        # The caller publishes this pending record before either mount syscall.
        journal({'request_id': request_id, 'source_device': info.st_dev,
                 'source_inode': info.st_ino, 'mount_id': None, 'pending': 'seal', **prior})
        self.host.run(('mount', '--bind', '--', str(self.source), str(self.source)))
        self.host.run(('mount', '-o', 'remount,bind,ro', '--', str(self.source)))
        rows = mount_rows(self.source)
        if len(rows) != 1 or not rows[0]['read_only']:
            fail('installation_source_seal_unverified', 'read-only source mount is not uniquely proven')
        self.receipt = {'request_id': request_id, 'source_device': info.st_dev,
                        'source_inode': info.st_ino, 'mount_id': rows[0]['id'],
                        'pending': None, 'service_stopped': True, **prior}
        journal(self.receipt)
        if not self.proven():
            fail('installation_writers_active', 'source remains sealed; open handles or mappings need reconciliation')
        return self.receipt

    def proven(self):
        if not self.administrator_quiescent or self.receipt is None or self.service_state() == 'active':
            return False
        if self.source is None:
            return self.receipt.get('service_stopped') is True
        info = self.source.stat()
        if (info.st_dev, info.st_ino) != (self.receipt.get('source_device'), self.receipt.get('source_inode')):
            return False
        rows = mount_rows(self.source)
        if len(rows) != 1 or rows[0]['id'] != self.receipt.get('mount_id') or not rows[0]['read_only']:
            return False
        observed = self.writer_probe(self.source)
        return all(type(value) is int and value == 0 for value in observed.values())

    def unseal(self, receipt):
        if self.source is None or receipt != self.receipt or not self.proven():
            fail('installation_rollback_unverified', 'source seal ownership must be proven before rollback')
        self.host.run(('umount', '--', str(self.source)))
        if mount_rows(self.source):
            fail('installation_rollback_unverified', 'source mount still exists after scoped unseal')

    def restore_service(self):
        if self.receipt is None:
            fail('installation_rollback_unverified', 'original service state is unavailable')
        if self.receipt.get('was_enabled') == 'enabled':
            self.host.run(self._unit_argv('enable'))
        if self.receipt.get('was_active') is True:
            self.host.run(self._unit_argv('start'))


class FreshSlotFence:
    """Fresh installation never stops an existing service to make room."""
    def __init__(self, host, *, bind, port, administrator_quiescent=False):
        self.host, self.bind, self.port = host, bind, port
        self.administrator_quiescent = administrator_quiescent is True

    def proven(self):
        if not self.administrator_quiescent:
            return False
        result = self.host.observe(('ss', '-H', '-ltn', f'( sport = :{self.port} )'))
        if result.returncode != 0:
            fail('installation_listener_evidence_unavailable', 'listener inventory is unavailable')
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 4:
                fail('installation_listener_evidence_unavailable', 'listener inventory is malformed')
            host, separator, port = fields[3].rpartition(':')
            if not separator:
                fail('installation_listener_evidence_unavailable', 'listener address is malformed')
            if port == str(self.port) and host.strip('[]') in {self.bind, '*', '0.0.0.0', '::'}:
                fail('installation_listener_occupied', 'fresh control endpoint is occupied; existing authority needs migration')
        return True

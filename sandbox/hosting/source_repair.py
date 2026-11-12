"""Plan-bound replacement of one proven rendered orphan; no workload effects."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time

from sandbox.application.server_cli import read_current_server_owner
from sandbox.application.server_cli_delivery import Budget
from sandbox.hosting import stack_registry, stack_template
from sandbox.hosting.recovery.models import canonical_digest
from sandbox.services.process import BoundedProcessRunner

CONTRACT = 'source.repair-rendered.v1'
MAX_FILES = 10000
MAX_BYTES = 256 * 1024 * 1024


def refuse():
    raise ValueError('rendered source repair proof is unavailable')


def _owned(path, *, directory=False):
    path = Path(path)
    if path.absolute() != path.resolve() or not path.is_absolute():
        refuse()
    info = path.lstat()
    if (info.st_uid != os.geteuid() or info.st_mode & 0o022
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        refuse()
    return info


def _bytes(path, maximum):
    _owned(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        if os.fstat(handle.fileno()).st_size > maximum:
            refuse()
        value = handle.read(maximum + 1)
    if len(value) > maximum:
        refuse()
    return value


def _git(repository, *argv, budget, maximum=2 * 1024 * 1024):
    if argv and argv[0] in {'ls-tree', 'cat-file', 'show', 'rev-parse'}:
        return _git_bytes(repository, *argv, budget=budget, maximum=maximum).decode('utf-8', errors='strict')
    result = BoundedProcessRunner(max_output=maximum, terminate_on_output_limit=True).run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
         '-c', 'core.attributesFile=/dev/null', '-c', 'protocol.allow=never',
         '-c', 'protocol.file.allow=always', '-C', str(repository), *argv],
        env={'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_TERMINAL_PROMPT': '0'}, timeout=budget.remaining())
    budget.check()
    if (result.returncode or result.termination_reason or result.stdout_truncated or result.stderr_truncated):
        refuse()
    return result.stdout


def _git_bytes(repository, *argv, budget, maximum=2 * 1024 * 1024):
    """Private bounded machine data; raw bytes never enter diagnostics or logs."""
    if not argv or argv[0] not in {'ls-tree', 'cat-file', 'show', 'rev-parse'}:
        refuse()
    budget.check()
    process = subprocess.Popen(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
         '-c', 'core.attributesFile=/dev/null', '-c', 'protocol.allow=never',
         '-c', 'protocol.file.allow=always', '-C', str(repository), *argv],
        env={'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_TERMINAL_PROMPT': '0'}, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        for name, stream in (('stdout', process.stdout), ('stderr', process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            for key, _ in selector.select(min(0.1, budget.remaining())):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = output[key.data]
                if len(target) + len(chunk) > maximum:
                    refuse()
                target.extend(chunk)
        if process.wait(timeout=budget.remaining()) != 0:
            refuse()
        budget.check()
        return bytes(output['stdout'])
    finally:
        selector.close()
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close()
        process.stderr.close()


def _replacement_identity(tree, backing, *, staging, installed, budget):
    """Bind only the created pair, including across the worktree's rename."""
    budget.check()
    root_info = _owned(tree, directory=True)
    backing_info = _owned(backing, directory=True)
    worktrees_info = _owned(backing / 'worktrees', directory=True)
    pointer = _bytes(tree / '.git', 4096)
    pointer_info = _owned(tree / '.git')
    if not pointer.startswith(b'gitdir: '):
        refuse()
    admin = Path(pointer.decode('utf-8', errors='strict').strip()[8:])
    if not admin.is_absolute() or admin.parent != backing / 'worktrees':
        refuse()
    admin_info = _owned(admin, directory=True)
    common = _bytes(admin / 'commondir', 4096).decode('utf-8', errors='strict').strip()
    linked = _bytes(admin / 'gitdir', 4096).decode('utf-8', errors='strict').strip()
    if ((admin / common).resolve() != backing.resolve()
            or linked not in {str(staging / '.git'), str(installed / '.git')}):
        refuse()
    budget.check()
    return {'root': [root_info.st_dev, root_info.st_ino],
            'backing': [backing_info.st_dev, backing_info.st_ino],
            'worktrees': [worktrees_info.st_dev, worktrees_info.st_ino],
            'admin': [admin_info.st_dev, admin_info.st_ino], 'admin_name': admin.name,
            'pointer': [pointer_info.st_dev, pointer_info.st_ino],
            'pointer_digest': 'sha256:' + hashlib.sha256(pointer).hexdigest()}


def _tree(repository, commit, budget):
    raw = _git_bytes(repository, 'ls-tree', '-rz', '--full-tree', commit, budget=budget)
    entries = {}
    for record in raw.split(b'\0'):
        line = record.decode('utf-8', errors='strict')
        if not line:
            continue
        metadata, name = line.split('\t', 1)
        mode, kind, oid = metadata.split()
        path = Path(name)
        if (kind != 'blob' or mode not in {'100644', '100755', '120000'}
                or re.fullmatch('[0-9a-f]{40}', oid) is None or path.is_absolute()
                or any(part in {'.', '..', '.git'} for part in path.parts)
                or name != path.as_posix() or name in entries
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            refuse()
        # A metadata repair cannot read credentials or invoke checkout filters.
        if (path.name.startswith('.env') and path.name not in {'.env.example', '.env.sample', '.env.template'}) or path.suffix in {'.pem', '.key', '.p12'}:
            refuse()
        if path.name == '.gitattributes':
            attributes = _git_bytes(repository, 'cat-file', 'blob', oid, budget=budget, maximum=65536).decode('utf-8', errors='strict')
            if any(word in attributes for word in ('filter', 'working-tree-encoding', 'ident', 'export-subst')):
                refuse()
        entries[name] = (mode, oid)
    if not entries or len(entries) > MAX_FILES:
        refuse()
    return entries


def _content(root, entries, budget):
    """Compare every byte, executable bit and link target; refuse extra files."""
    _owned(root, directory=True)
    seen = set()
    total = 0
    allowed_dirs = {parent.as_posix() for name in entries for parent in Path(name).parents if parent != Path('.')}
    for directory, dirs, files in os.walk(root, followlinks=False):
        budget.check()
        for name in list(dirs):
            item = Path(directory) / name
            relative = item.relative_to(root).as_posix()
            if item.is_symlink():
                dirs.remove(name)
                files.append(name)
            elif relative not in allowed_dirs:
                refuse()
            else:
                _owned(item, directory=True)
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == '.git':
                continue
            if relative not in entries:
                refuse()
            mode, oid = entries[relative]
            info = path.lstat()
            if info.st_uid != os.geteuid():
                refuse()
            if mode == '120000':
                if not stat.S_ISLNK(info.st_mode):
                    refuse()
                data = os.fsencode(os.readlink(path))
                actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                total += len(data)
            else:
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022
                        or bool(info.st_mode & 0o111) != (mode == '100755')):
                    refuse()
                total += info.st_size
                if total > MAX_BYTES:
                    refuse()
                digest = hashlib.sha1(b'blob ' + str(info.st_size).encode() + b'\0')
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as handle:
                    before = os.fstat(handle.fileno())
                    if (before.st_dev, before.st_ino, before.st_size) != (info.st_dev, info.st_ino, info.st_size):
                        refuse()
                    while True:
                        budget.check()
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                    after = os.fstat(handle.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        refuse()
                actual = digest.hexdigest()
            if actual != oid or total > MAX_BYTES:
                refuse()
            seen.add(relative)
    if seen != set(entries):
        refuse()
    return canonical_digest({key: list(value) for key, value in sorted(entries.items())})


def _consumers_absent(roots, custody, budget):
    """Same-owner complete visibility only. Denied cwd/fds never mean no writer."""
    from sandbox.jobs.process import is_boot_session_identity, process_absence_proven
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not is_boot_session_identity(boot):
        refuse()
    needles = tuple(os.fsencode(str(path)) for path in roots)
    for _ in range(2):
        count = 0
        for directory in Path('/proc').iterdir():
            budget.check()
            if not directory.name.isdigit():
                continue
            count += 1
            if count > 65536:
                refuse()
            try:
                with (directory / 'stat').open('rb') as handle:
                    before = handle.read(8193)
                if len(before) > 8192:
                    refuse()
                fields = before.rpartition(b')')[2].split()
                if len(fields) < 20:
                    refuse()
                for item in custody:
                    process = item['process']
                    if process['host_boot_id'] == boot and int(fields[2]) == process['child_pgid']:
                        refuse()
                if directory.stat().st_uid != os.geteuid():
                    continue
                # Evaluate each available field independently; a failed cwd read
                # cannot erase a positive command-line match.
                with (directory / 'cmdline').open('rb') as handle:
                    command = handle.read(65537)
                if len(command) > 65536 or any(needle in command for needle in needles):
                    refuse()
                cwd = os.fsencode(os.readlink(directory / 'cwd'))
                if any(needle in cwd for needle in needles):
                    refuse()
                for count_fd, descriptor in enumerate((directory / 'fd').iterdir(), 1):
                    if count_fd > 4096:
                        refuse()
                    try:
                        destination = os.fsencode(os.readlink(descriptor))
                    except FileNotFoundError:
                        continue
                    if any(needle in destination for needle in needles):
                        refuse()
                after = (directory / 'stat').read_bytes().rpartition(b')')[2].split()
                if len(after) < 20 or fields[19] != after[19]:
                    refuse()
            except FileNotFoundError:
                if directory.exists():
                    refuse()
                continue
        for item in custody:
            process = item['process']
            if not is_boot_session_identity(process['host_boot_id']):
                refuse()
            if process['host_boot_id'] == boot:
                pids = [process['child_pid'], process['supervisor_pid'], *item['descendants']]
                if any(type(pid) is not int or pid <= 1 or not process_absence_proven(pid) for pid in pids):
                    refuse()
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot:
        refuse()
    return boot


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_tree(root, budget):
    """Make the privately staged Git pair durable before publishing intent."""
    count = 0
    for directory, dirs, files in os.walk(root, topdown=False, followlinks=False):
        budget.check()
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            _owned(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            count += 1
            if count > 4 * MAX_FILES:
                refuse()
        _sync(directory)


def _journal(path, value):
    import secrets
    temporary = path.with_name(path.name + '.new-' + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(json.dumps(value, sort_keys=True).encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync(path.parent)


def repair_rendered_source(*, remote_name, name, source, confirm=False, plan_digest=None, expected_owner=None):
    """Only explicitly selected recorded deployed Git objects supply repair bytes.

    The orphan's exact tree must already match those objects. This never chooses
    the deployed HEAD as the attempt commit or substitutes its source path.
    """
    from sandbox.jobs.registry import JobRepository
    from sandbox.workspaces.maintenance import base_maintenance_lock
    from sandbox.hosting.recovery.repository import RecoveryRepository
    from sandbox.hosting.target_resolution import parse_target
    from sandbox.core._config import ensure_pyyaml
    from sandbox.core import _hosting
    if (not isinstance(remote_name, str) or re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', remote_name) is None
            or not isinstance(name, str) or re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', name) is None
            or source != 'deployed' or type(confirm) is not bool
            or (confirm and (not isinstance(plan_digest, str) or re.fullmatch('sha256:[0-9a-f]{64}', plan_digest) is None))
            or (not confirm and plan_digest is not None)):
        refuse()
    owner = read_current_server_owner()
    from sandbox.application.server_cli_delivery import owner_projection
    if expected_owner is None or owner_projection(owner) != expected_owner:
        refuse()
    home = Path(owner.sandbox_home)
    budget = Budget(120)
    _owned(home, directory=True)
    _owned(stack_registry.stacks_dir(), directory=True)
    with ExitStack() as locks:
        # Stack operations already take registry before workspace admission.
        # Keep that order and bound waiting; never hold maintenance while
        # waiting for a stack writer that needs the shared maintenance lock.
        registry = locks.enter_context(stack_registry.locked(timeout_seconds=1))
        locks.enter_context(base_maintenance_lock(home, exclusive=True))
        record = registry.get(remote_name, name)
        if not isinstance(record, dict) or record.get('remote') != remote_name or record.get('slug') != name:
            refuse()
        project, environment = parse_target(str(record.get('project')) + '/' + str(record.get('environment')))
        recovery = RecoveryRepository()
        locks.enter_context(recovery.target_lock(remote_name + '/' + project + '/' + environment, timeout_seconds=1))
        stack = stack_registry.stack_dir(name).absolute()
        _owned(stack, directory=True)
        manifest_path = stack / stack_template.MANIFEST_NAME
        if record.get('manifest') != str(manifest_path):
            refuse()
        manifest_bytes = _bytes(manifest_path, 1024 * 1024)
        ensure_pyyaml()
        import yaml
        manifest = yaml.safe_load(manifest_bytes)
        facts = stack_template.rendered_instance(manifest, manifest_path, stack_registry.stacks_dir())
        caller = Path(record.get('checkout', ''))
        if (not caller.is_absolute() or caller.absolute() != caller.resolve()
                or home / 'deploy-src' not in caller.parents):
            refuse()
        source_root = Path(facts['source_root'])
        selected = [Path(path) for path in record.get('worktrees', [])
                    if source_root == Path(path) or Path(path) in source_root.parents]
        if len(selected) != 1:
            refuse()
        orphan = selected[0]
        if (orphan.parent != stack / 'source' or orphan.name in {'', '.', '..'}
                or facts['ports'] != record.get('ports') or facts['ref'] != record.get('ref')
                or facts['ref_branch'] != record.get('ref_branch') or facts['template'] != record.get('template')
                or facts['commit'] != (record.get('last_apply') or {}).get('commit')):
            refuse()
        # Explicit object source: a recorded target's canonical deployment repository.
        repository = home / 'deploy-src' / 'hosts' / project
        _owned(repository, directory=True)
        _owned(repository / '.git', directory=True)
        alternates = repository / '.git/objects/info/alternates'
        if alternates.exists() or alternates.is_symlink():
            refuse()
        if (repository / _git(repository, 'rev-parse', '--git-common-dir', budget=budget).strip()).resolve() != repository / '.git':
            refuse()
        if _git(repository, 'rev-parse', '--show-toplevel', budget=budget).strip() != str(repository):
            refuse()
        commit = facts['commit']
        if _git(repository, 'rev-parse', '--verify', commit + '^{commit}', budget=budget).strip() != commit:
            refuse()
        template_relative = Path(facts['template']).relative_to(caller).as_posix()
        template_text = _git_bytes(repository, 'show', commit + ':' + template_relative,
                                   budget=budget, maximum=1024 * 1024).decode('utf-8', errors='strict')
        template = stack_template.parse_template(yaml.safe_load(template_text))
        relative = stack_template.template_source_root(template, template_relative)
        if source_root != orphan / relative:
            refuse()
        expected_manifest = stack_template.render(template, slug=name,
            ref=stack_template.StackRef(facts['ref'], commit, facts['ref_branch']), ports=facts['ports'],
            source_root=source_root, template_path=facts['template'])
        if manifest != expected_manifest or sorted(template.uses) != sorted(record.get('uses') or []):
            refuse()
        entries = _tree(repository, commit, budget)
        admission = recovery.read_exact_delivery_admission(remote_name + '/' + project + '/' + environment, budget=budget, require_idle=True)
        if (admission is None or admission['job_id'] != (record.get('last_apply') or {}).get('job_id')
                or admission['request_id'] != (record.get('last_apply') or {}).get('request_id')
                or admission['source']['commit'] != commit
                or admission['evidence']['machine_identity'] != owner.target_identity
                or admission['project_root_digest'] != 'sha256:' + hashlib.sha256(str(source_root).encode()).hexdigest()):
            refuse()
        from sandbox.delivery.repository import DeliveryRepository
        from sandbox.delivery.hosting import recorded_request_scope
        outcome = DeliveryRepository(home, deadline=budget.deadline).lookup_request(
            recorded_request_scope(admission), admission['request_id'])['operation']
        if (outcome is None or outcome['execution_state'] not in {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'}
                or outcome['delivery_state'] == 'unknown'
                or any(effect['state'] == 'unknown' for effect in outcome['effects'])
                or outcome['terminal_snapshot_digest'] is None or outcome['job_id'] != admission['job_id']
                or outcome['target']['project_identity'] != admission['project_identity']
                or outcome['target']['remote_name'] != remote_name or outcome['target']['environment'] != environment
                or outcome['target']['machine_identity'] != owner.target_identity
                or outcome['target']['project_root_digest'] != admission['project_root_digest']
                or outcome['target']['runtime_identity'] != admission['evidence']['runtime_identity']):
            refuse()
        jobs = JobRepository.open_existing(home / 'runtime/jobs/registry.sqlite3', read_only=False)
        locks.callback(jobs.close)
        custody = locks.enter_context(jobs.rendered_source_custody([caller, orphan], admission['job_id']))
        boot = _consumers_absent([caller, orphan], custody, budget)
        # A deterministic journal binds the record and manifest, never a caller path.
        base = {'contract': CONTRACT, 'owner': {'target_identity': owner.target_identity,
            'ownership_marker': owner.ownership_marker, 'runtime_revision': owner.runtime_revision},
            'remote': remote_name, 'name': name, 'source': source, 'commit': commit,
            'record_digest': canonical_digest(record), 'manifest_digest': 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest(),
            'tree_digest': canonical_digest({key: list(value) for key, value in sorted(entries.items())}),
            'delivery_digest': outcome['terminal_snapshot_digest'],
            'custody_digest': canonical_digest(custody), 'boot_id': boot}
        journal = None
        old = orphan
        if confirm:
            supplied_token = plan_digest.split(':')[1]
            saved_path = stack / ('source-repair-' + supplied_token + '.json')
            saved_backup = stack / ('source-orphan-' + supplied_token)
            if saved_path.exists() or saved_path.is_symlink():
                journal = json.loads(_bytes(saved_path, 16384))
                if saved_backup.exists() or saved_backup.is_symlink():
                    old = saved_backup
        if _content(old, entries, budget) != base['tree_digest']:
            refuse()
        pointer = _bytes(old / '.git', 4096)
        try:
            old_git = Path(pointer.decode('utf-8').strip().removeprefix('gitdir: '))
        except UnicodeError:
            refuse()
        if (not pointer.startswith(b'gitdir: ') or not old_git.is_absolute()
                or old_git.parent != caller / '.git/worktrees'
                or old_git.exists() or old_git.is_symlink()):
            refuse()
        pointer_digest = 'sha256:' + hashlib.sha256(pointer).hexdigest()
        root_info, pointer_info = old.lstat(), (old / '.git').lstat()
        base.update(old_git_digest=pointer_digest,
                    orphan_identity=[root_info.st_dev, root_info.st_ino],
                    pointer_identity=[pointer_info.st_dev, pointer_info.st_ino,
                                      pointer_info.st_mtime_ns, pointer_info.st_ctime_ns])
        digest = canonical_digest(base)
        token = digest.split(':')[1]
        journal_path = stack / ('source-repair-' + token + '.json')
        backup = stack / ('source-orphan-' + token)
        staging = stack / 'source' / ('repair-' + token)
        backing = stack / 'source-git' / (orphan.name + '.git')
        if journal is not None and (
                set(journal) != {'binding', 'plan_digest', 'old_git_digest', 'replacement', 'phase'}
                or journal['binding'] != base or journal['plan_digest'] != digest
                or journal['old_git_digest'] != pointer_digest
                or journal['phase'] not in {'prepared', 'orphan_retained', 'installed', 'complete'}):
            refuse()
        if not confirm:
            return {'schema_version': 1, 'ok': True, 'contract': CONTRACT, 'status': 'planned',
                    'plan_digest': digest, 'name': name, 'commit': commit}
        if digest != plan_digest or read_current_server_owner() != owner:
            refuse()
        if journal is None:
            if backup.exists() or staging.exists() or backing.exists() or backing.is_symlink():
                refuse()
            _owned(backing.parent, directory=True) if backing.parent.exists() else backing.parent.mkdir(mode=0o700)
            backing.mkdir(mode=0o700)
            _git(backing, 'init', '--bare', '--quiet', '--template=', budget=budget)
            _git(backing, 'fetch', '--quiet', '--no-tags', '--depth=1', '--update-shallow', '--no-recurse-submodules',
                 str(repository), commit + ':refs/sandbox/source', budget=budget)
            _git(backing, 'worktree', 'add', '--detach', '--quiet', str(staging), commit, budget=budget)
            _content(staging, entries, budget)
            _sync_tree(backing, budget)
            _sync_tree(staging, budget)
            _sync(backing.parent); _sync(staging.parent)
            replacement = _replacement_identity(staging, backing, staging=staging,
                                                 installed=orphan, budget=budget)
            journal = {'binding': base, 'plan_digest': digest, 'old_git_digest': pointer_digest,
                       'replacement': replacement, 'phase': 'prepared'}
            _journal(journal_path, journal)
        # A saved path is not authority to adopt a copied replacement pair.
        # Its root inode survives Git's move, so the same proof works installed.
        replacement_root = orphan if backup.exists() and orphan.exists() else staging
        if (backup.exists() and staging.exists() and orphan.exists()
                or _replacement_identity(replacement_root, backing, staging=staging,
                                         installed=orphan, budget=budget) != journal['replacement']):
            refuse()
        # Re-evaluate custody and the immutable input just before replacement.
        _consumers_absent([caller, orphan], custody, budget)
        if (_bytes(manifest_path, 1024 * 1024) != manifest_bytes
                or canonical_digest(registry.get(remote_name, name)) != base['record_digest']
                or read_current_server_owner() != owner):
            refuse()
        if not backup.exists():
            if _content(orphan, entries, budget) != base['tree_digest'] or _bytes(orphan / '.git', 4096) != pointer:
                refuse()
            os.rename(orphan, backup)
            _sync(orphan.parent); _sync(stack)
        journal['phase'] = 'orphan_retained'
        _journal(journal_path, journal)
        if not orphan.exists():
            _content(staging, entries, budget)
            if _replacement_identity(staging, backing, staging=staging,
                                     installed=orphan, budget=budget) != journal['replacement']:
                refuse()
            _git(backing, 'worktree', 'move', str(staging), str(orphan), budget=budget)
            _sync(orphan.parent)
        if _replacement_identity(orphan, backing, staging=staging,
                                 installed=orphan, budget=budget) != journal['replacement']:
            refuse()
        installed_pointer = _bytes(orphan / '.git', 4096)
        if not installed_pointer.startswith(b'gitdir: '):
            refuse()
        admin = Path(installed_pointer.decode('utf-8').strip()[8:])
        if not admin.is_absolute() or admin.parent != backing / 'worktrees':
            refuse()
        _owned(admin, directory=True)
        # Git may have moved the directory before it updated linked-worktree
        # administration. Its own repair command completes that exact move.
        _git(backing, 'worktree', 'repair', str(orphan), budget=budget)
        if _replacement_identity(orphan, backing, staging=staging,
                                 installed=orphan, budget=budget) != journal['replacement']:
            refuse()
        if (_git(orphan, 'rev-parse', '--verify', 'HEAD', budget=budget).strip() != commit
                or (orphan / _git(orphan, 'rev-parse', '--git-common-dir', budget=budget).strip()).resolve() != backing.resolve()):
            refuse()
        _content(orphan, entries, budget)
        _sync_tree(backing, budget)
        _sync(orphan)
        journal['phase'] = 'installed'
        _journal(journal_path, journal)
        def bounded_git_root(path):
            value = _git_bytes(path, 'rev-parse', '--show-toplevel', budget=budget,
                               maximum=65536).decode('utf-8', errors='strict').strip()
            return Path(value).resolve() if value else None
        _hosting.validate_manifest(manifest_path, environment, git_root_observer=bounded_git_root)
        budget.check()
        if _bytes(manifest_path, 1024 * 1024) != manifest_bytes or read_current_server_owner() != owner:
            refuse()
        journal['phase'] = 'complete'
        budget.check()
        _journal(journal_path, journal)
        budget.check()
        return {'schema_version': 1, 'ok': True, 'contract': CONTRACT, 'status': 'repaired',
                'plan_digest': digest, 'name': name, 'commit': commit,
                'manifest_digest': base['manifest_digest'], 'tree_digest': base['tree_digest'],
                'orphan_retained': True}

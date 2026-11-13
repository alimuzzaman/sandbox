"""Preview-owned preconfiguration routing and genuine retained server services."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import secrets
import shlex
import stat

from sandbox.hosting.server_cli_target import selected_owner_target, is_owner_target


CONTRACT = 'preview.owner-cli.v1'


def preview_contract_available():
    from sandbox.application.command_selection import selected_invocation, CommandSelectionUnavailable
    try:
        preview = selected_invocation(('preview', 'list', '--remote', 'capability'))
        deletion = selected_invocation(('instance', 'delete', 'capability', '--local', '--yes',
            '--creation-context-json={}', '--expected-incarnation=inc_' + '0' * 32))
        return (preview is not None and preview.command == ('preview', 'list')
                and deletion is not None and deletion.command == ('instance', 'delete'))
    except CommandSelectionUnavailable:
        return False


def dispatch_preview_if_selected(args):
    try:
        return _dispatch_preview_if_selected(args)
    except Exception as error:
        from sandbox.hosting.server_cli_dispatch import _failure
        unknown = bool(getattr(error, 'completion_unknown', False)) or (
            bool(getattr(args, '_server_cli_submission_started', False))
            and getattr(error, 'code', None) != 'server_cli_capability_unavailable')
        _failure(args, 'server_cli_acceptance_unknown' if unknown else
                 'server_cli_execution_unavailable', unknown=unknown)


def _dispatch_preview_if_selected(args):
    if not getattr(args, 'remote', None):
        return False
    from sandbox.application.server_cli import server_cli_context_if_selected
    context = server_cli_context_if_selected()
    if context is not None:
        # A selected job never falls back to controller lookup on alias drift.
        if args.remote != context.origin_remote_name:
            raise ValueError('preview_owner_alias_mismatch')
        if args.request_id is not None and args.request_id != context.request_id:
            raise ValueError('preview_request_identity_mismatch')
        args.request_id = context.request_id
        selected_owner_target(args.remote).require_current()
        from sandbox.commands.preview import cmd_preview
        cmd_preview({}, args)
        return True
    from sandbox.core import _remote
    entry = _remote.get_remote(args.remote)
    if not isinstance(entry, dict) or not entry.get('joined') or entry.get('control_transport') != 'tailscale':
        return False
    # Backend refusal is a capability result, before local config/provider lookup
    # and before source staging. Inventory is still available on other backends.
    if args.action == 'create':
        from sandbox.hosting.front_door import require_capability, FrontDoorError
        try:
            require_capability(entry, 'preview_routes')
        except FrontDoorError as error:
            print(json.dumps({'ok': False, 'code': error.code, 'error': str(error),
                              'acceptance': 'not_accepted'}))
            raise SystemExit(1)
    if args.action in {'create', 'destroy', 'cleanup'} and not args.confirm:
        raise ValueError('preview mutation requires --confirm')
    argv = list(getattr(args, '_server_cli_argv', ()) or ())
    if not argv:
        raise ValueError('preview_original_argv_unavailable')
    root = Path(args.project_dir or '.').expanduser().resolve(strict=True)
    index = 0
    while index < len(argv):
        option, equals, value = argv[index].partition('=')
        if option == '--project-dir':
            if not equals:
                index += 1
                value = argv[index]
            if Path(value).expanduser().resolve(strict=True) != root:
                raise ValueError('preview_source_root_mismatch')
            argv[index] = '--project-dir=.' if equals else '.'
        index += 1
    if args.action == 'create' and not args.name:
        # A staged detached checkout has no branch; carry the ordinary public
        # name as argv data, never as instance/owner authority.
        argv.extend(['--name', _remote.current_branch(root)])
    request_id = args.request_id or 'preview-cli-' + secrets.token_hex(12)
    from sandbox.application.server_cli import dispatch_server_cli
    from sandbox.hosting.server_cli_dispatch import _failure
    args._server_cli_request_id = request_id
    args._server_cli_submission_started = True
    try:
        receipt = dispatch_server_cli(args.remote, argv, project_dir=str(root),
            request_id=request_id, timeout_seconds=900, required_capabilities=(CONTRACT,))
    except Exception as error:
        args._server_cli_request_id = request_id
        unknown = getattr(error, 'code', None) != 'server_cli_capability_unavailable'
        _failure(args, 'server_cli_acceptance_unknown' if unknown else
                 'server_cli_execution_unavailable', unknown=unknown)
    if not isinstance(receipt, dict) or receipt.get('ok') is False or receipt.get('status') == 'acceptance_unknown':
        args._server_cli_request_id = request_id
        unknown = not isinstance(receipt, dict) or receipt.get('acceptance') != 'not_accepted'
        _failure(args, 'server_cli_acceptance_unknown' if unknown else 'server_cli_execution_unavailable',
                 unknown=unknown, receipt=receipt)
    print(json.dumps(receipt, sort_keys=True))
    return True


@contextmanager
def preview_registry_lock():
    from sandbox.core._paths import RUNTIME_DIR
    directory = RUNTIME_DIR / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'remote-previews.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def preview_target(remote_name):
    target = selected_owner_target(remote_name)
    if target is not None:
        return target
    from sandbox.application.server_cli import server_cli_context_if_selected
    if server_cli_context_if_selected() is not None:
        raise ValueError('preview_owner_alias_mismatch')
    from sandbox.core import _remote
    return _remote.get_remote(remote_name)


class PreviewProvider:
    """Server-local provider; authority is checked around every provider call."""
    def __init__(self, entry):
        self.entry = entry
        self._current()
        from sandbox.core import _cloudflare
        self.client = _cloudflare.Client()
        self._current()

    def _current(self):
        if is_owner_target(self.entry):
            return self.entry.require_current()

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in {'zone', 'records', 'create_address', 'delete_record'}:
            raise AttributeError(name)
        def call(*args, **kwargs):
            self._current()
            try:
                result = method(*args, **kwargs)
            finally:
                try:
                    self._current()
                except Exception:
                    from sandbox.hosting.server_cli_target import withhold_owner_completion
                    withhold_owner_completion(self.entry)
                    raise
            return result
        return call


def prepare_preview_source(entry, root, commit):
    """Use the existing genuine owner's hosts publication boundary."""
    from sandbox.core import _remote
    if not is_owner_target(entry):
        return _remote.ensure_deploy_repo(entry, root)
    context = entry.require_current()
    if Path(context.project_root).resolve(strict=True) != root or context.source_commit != commit:
        raise ValueError('preview_retained_source_mismatch')
    target = Path(context.owner.sandbox_home) / 'deploy-src' / 'hosts' / ('preview-' + _remote.deploy_target_slug(root))
    for path in (target.parent.parent, target.parent, target):
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != context.owner.uid or info.st_mode & 0o022
                or path.resolve(strict=True) != path):
            raise ValueError('preview_source_destination_unsafe')
    git_dir = target / '.git'
    if git_dir.exists() and (git_dir.is_symlink() or not git_dir.is_dir()):
        raise ValueError('preview_source_destination_unsafe')
    result = entry.process(shlex.join(['git', '-c', 'core.hooksPath=/dev/null',
        '-C', str(target), 'init', '-q']), timeout=30)
    if result.returncode:
        raise ValueError('preview_source_prepare_failed')
    result = entry.process(shlex.join(['git', '-c', 'core.hooksPath=/dev/null',
        '-C', str(target), 'config', 'receive.denyCurrentBranch', 'ignore']), timeout=30)
    if result.returncode:
        raise ValueError('preview_source_prepare_failed')
    entry.require_current()
    return str(target)

"""Captured public CLI main, with traps installed before argument dispatch.

Imports and the outer test child are harness setup. After setup, only the
ordinary synthetic declaration and checked-in CLI version may be opened;
no controller state is allowed.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = (Path('/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use')
           if str(ROOT).startswith('/Users/alim/') else
           Path(tempfile.gettempdir()) / 'sandbox-shared-server-secret-use-tests')
SENTINEL = 'SYNTHETIC_SENTINEL'
MANIFEST = '''environments:
  fixture:
    compose: {service: web}
    secrets:
      required: {TOKEN: PROVIDER_TOKEN}
      generated: {PASSWORD: GENERATED_PASSWORD}
'''
SELECTION = ['--credential-scope', 'server', '--remote', 'fixture-server',
             '--credential-project', 'fixture-project', '--credential-source', 'fixture-source',
             '--credential-source-revision', 'source-r1', '--credential-profile', 'fixture-profile',
             '--credential-profile-revision', 'profile-r1']

# Invoke the public CLI main, rather than a feature helper, in the captured child.
# Preload modules needed by allowed parsing so even reads during main are trapped.
CHILD = r'''
import sys, os, json, io, pathlib, socket, subprocess, yaml
from contextlib import ExitStack
from unittest.mock import patch
import sandbox.cli as cli
import sandbox.hosting.credential_selection
import sandbox.commands.hosting
import sandbox.commands.migrate
import sandbox.controller_update.hook
project = pathlib.Path(sys.argv[1])
argv = json.loads(sys.argv[2])
manifest = str(project / 'sandbox.hosting.yml')
version_file = str(cli.ROOT / 'VERSION')
violations = []
manifest_fds = set()
real_open = os.open
def manifest_open(path, flags, *args, **kwargs):
    descriptor = real_open(path, flags, *args, **kwargs)
    if str(path) == manifest:
        manifest_fds.add(descriptor)
    return descriptor
def forbidden(name):
    def fail(*args, **kwargs):
        violations.append(name)
        raise AssertionError('forbidden CLI boundary: ' + name)
    return fail
# Audit catches low-level operations even if a new helper bypasses these owners.
def audit(event, args):
    if event == 'open':
        path, mode, flags = args
        allowed = (isinstance(path, str) and path in (manifest, version_file)
                   and not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if isinstance(path, int) and path in manifest_fds:
            # os.fdopen wraps the descriptor just returned by the allowed read.
            manifest_fds.remove(path)
            allowed = True
        if not allowed:
            forbidden('open')()
    elif (event.startswith(('subprocess.', 'socket.')) or event in {
            'os.system', 'os.posix_spawn', 'os.fork', 'os.exec', 'os.mkdir', 'os.remove',
            'os.rename', 'os.rmdir', 'os.link', 'os.symlink', 'os.chmod', 'os.truncate',
            'os.listdir', 'os.scandir'}):
        forbidden(event)()
with ExitStack() as stack:
    spies = []
    stack.enter_context(patch.object(os, 'open', side_effect=manifest_open))
    for target in (
        'sandbox.commands.migrate.maybe_auto_migrate',
        'sandbox.controller_update.hook.maybe_auto_update',
        'sandbox.cli.load_config', 'sandbox.cli.resolve_instances',
        'sandbox.cli.write_compose_files', 'sandbox.cli.write_env_for_compose',
        'sandbox.core._remote.get_remote', 'sandbox.core._secrets.resolve_secret',
        'sandbox.core._secrets.read_secret_file',
        'sandbox.core._hosting.load_host_state',
        'sandbox.commands.host_stack._entry', 'sandbox.commands.host_stack._store',
        'sandbox.hosting.stack_registry.read', 'sandbox.hosting.stack_registry.locked',
    ):
        spies.append(stack.enter_context(patch(target, side_effect=forbidden(target))))
    # Trap facade resolution as actually called by main, including registry lookup.
    stack.enter_context(patch.object(cli, '_core', side_effect=forbidden('cli._core')))
    sys.argv = ['sb', *argv]
    sys.addaudithook(audit)
    try:
        cli.main()
    except SystemExit as exc:
        status = exc.code
    else:
        status = 0
    if violations or any(spy.called for spy in spies):
        print('BOUNDARY_VIOLATION', file=sys.stderr)
        status = 77
    raise SystemExit(status)
'''


class ServerCredentialCLITests(unittest.TestCase):
    def invoke(self, action, *, selection=None, flags=(), manifest=MANIFEST, text=False):
        # Every path, including the child temporary directory, stays under shared scratch.
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='regression-cli-', dir=SCRATCH) as directory:
            base = Path(directory)
            project, home, state, temp = (base / part for part in ('project', 'home', 'state', 'tmp'))
            for path in (project, home, state, temp):
                path.mkdir()
            if manifest is not None:
                (project / 'sandbox.hosting.yml').write_text(manifest)
            env = dict(HOME=str(home), USERPROFILE=str(home), SANDBOX_HOME=str(state),
                       SANDBOX_RUNTIME=str(state / 'runtime'), SANDBOX_AUTO_UPDATE='0',
                       PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT),
                       PATH='/usr/bin:/bin', LANG='C', LC_ALL='C', LC_CTYPE='C', TZ='UTC',
                       TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp),
                       SYSTEMROOT='', WINDIR='', COMSPEC='', PATHEXT='')
            argv = ['host', *action, '--project-dir', str(project),
                    *(SELECTION if selection is None else selection), *flags]
            if not text:
                argv.append('--json')
            result = run_test_process([sys.executable, '-c', CHILD, str(project), json.dumps(argv)],
                                      cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(list(home.iterdir()), [])
            self.assertEqual(list(state.iterdir()), [])
            self.assertEqual(list(temp.iterdir()), [])
            if manifest is not None:
                self.assertEqual((project / 'sandbox.hosting.yml').read_text(), manifest)
            self.assertEqual([path.name for path in project.iterdir()],
                             ['sandbox.hosting.yml'] if manifest is not None else [])
            return result

    def check(self, action, code, **kwargs):
        result = self.invoke(action, **kwargs)
        valid = code == 'server_credentials_static_validated'
        self.assertEqual(result.returncode, 0 if valid else 2, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertNotIn(SENTINEL, result.stdout)
        if kwargs.get('text'):
            self.assertEqual(result.stdout, code + ': execution disabled; credential availability unverified.'
                             + (' Inventory unavailable.' if code.endswith('inventory_unavailable') else '') + '\n')
        else:
            payload = dict(ok=valid, code=code, availability='unverified', execution='disabled')
            if valid:
                payload.update(scope='server', declarations='valid')
            if code.endswith('inventory_unavailable'):
                payload.update(scope='server', inventory='unavailable')
            self.assertEqual(json.loads(result.stdout), payload)

    def test_static_and_inventory_json_and_text_before_startup(self):
        for text in (False, True):
            self.check(['validate'], 'server_credentials_static_validated', text=text)
            self.check(['secrets'], 'server_credentials_inventory_unavailable', text=text,
                       manifest='not: a hosting manifest\n')
        for flags in (['--environment', 'fixture'], ['--all']):
            self.check(['validate'], 'server_credentials_static_validated', flags=flags)
        # Syntactically valid unknown server does not establish registration.
        wrong = SELECTION.copy()
        wrong[3] = 'fixture-wrongserver'
        self.check(['validate'], 'server_credentials_static_validated', selection=wrong)

    def test_effectful_actions_never_reach_startup_or_any_effect(self):
        for action in (['apply'], ['stage'], ['sync'], ['plan'], ['image', 'activate'],
                       ['image', 'build-local'], ['image', 'push-local'],
                       ['stack', 'list'], ['stack', 'down']):
            with self.subTest(action=action):
                self.check(action, 'server_credentials_unsupported')
        for mode in ('--plan', '--confirm'):
            self.check(['stack', 'up'], 'server_credentials_unsupported', flags=[mode])
        self.check(['apply'], 'server_credentials_unsupported', text=True)
        for action in (['validate'], ['secrets'], ['apply']):
            self.check(action, 'server_credentials_unsupported', flags=['--generate'])
        self.check(['secrets'], 'server_credentials_unsupported', flags=['--set', SENTINEL])

    def test_malformed_missing_mixed_and_argparse_errors_do_not_echo(self):
        invalid = 'server_credentials_invalid_selection'
        for offset in range(2, len(SELECTION), 2):
            missing = SELECTION.copy()
            del missing[offset:offset + 2]
            with self.subTest(missing=offset):
                self.check(['validate'], invalid, selection=missing)
        for text in (False, True):
            for selection in ([*SELECTION[:1], SENTINEL], ['--credential-scope'],
                              [*SELECTION, '--credential-source'],
                              ['--credential-scope', 'personal-local', *SELECTION[2:]], SELECTION[2:]):
                self.check(['validate'], invalid, selection=selection, text=text)
            malformed = SELECTION.copy()
            malformed[3] = SENTINEL + '/bad'
            self.check(['validate'], invalid, selection=malformed, text=text)
        overlong = SELECTION.copy()
        overlong[7] = 'x' * 129
        self.check(['validate'], invalid, selection=overlong)
        self.check(['validate'], invalid, flags=['--all', '--environment', 'fixture'])

    def test_declaration_errors_are_fixed_without_untrusted_yaml_output(self):
        for body in (MANIFEST.replace('      required:', '      values: {TOKEN: SYNTHETIC_SENTINEL}\n      required:'),
                     MANIFEST.replace('      required:', '      values: {}\n      required:'),
                     MANIFEST.replace('PASSWORD: GENERATED_PASSWORD', 'TOKEN: GENERATED_PASSWORD'),
                     'actor: SYNTHETIC_SENTINEL\n' + MANIFEST,
                     'grant: SYNTHETIC_SENTINEL\n' + MANIFEST,
                     'SYNTHETIC_SENTINEL: [\n',
                     MANIFEST.replace('compose: {service: web}', 'compose: {service: web, service: worker}'),
                     MANIFEST + 'extra: &x [SYNTHETIC_SENTINEL]\ncopy: *x\n'):
            with self.subTest(body=body):
                self.check(['validate'], 'server_credentials_invalid_selection', manifest=body)

    def test_missing_declarations_refuse_without_local_fallback(self):
        self.check(['validate'], 'server_credentials_invalid_selection', manifest=None)
        self.check(['secrets'], 'server_credentials_inventory_unavailable', manifest=None)
        self.check(['validate'], 'server_credentials_invalid_selection', flags=['--environment', 'absent'])

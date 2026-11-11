"""Scrub owner apply diagnostics before retention or public process output.

The exact set is already resolved by hosting. It travels only on the genuine
owner process's anonymous stdin, never argv, environment or a staging file.
This scope supplies a negative output filter and grants no execution authority.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

from sandbox.services.redaction import StreamingRedactor

MAX_INPUT = 2 * 1024 * 1024
TAIL_BYTES = 128 * 1024
_ENV_KEYS = frozenset({'HOME', 'SANDBOX_HOME', 'USER', 'LOGNAME', 'PATH', 'LANG',
    'PYTHONNOUSERSITE', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'})


class OutputUnavailable(RuntimeError):
    code = 'host_apply_output_unavailable'


@dataclass(frozen=True)
class _Scope:
    context: object = field(repr=False)
    values: tuple[str, ...] | None = field(default=None, repr=False)


_CURRENT = ContextVar('hosting_owner_apply_output', default=None)


@contextmanager
def owner_apply_output_scope(entry):
    from sandbox.hosting.server_cli_target import is_owner_target
    if not is_owner_target(entry):
        yield
        return
    token = _CURRENT.set(_Scope(entry.require_current()))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def bind_owner_apply_output(entry, values):
    from sandbox.hosting.server_cli_target import is_owner_target
    if not is_owner_target(entry):
        return
    scope = _CURRENT.get()
    if scope is None or scope.context != entry.require_current() or scope.values is not None:
        raise OutputUnavailable('current owner apply output scope is unavailable')
    selected = tuple(value for value in values if value)
    _validate_values(selected)
    normalized = tuple(sorted(set(selected), key=lambda value: (-len(value), value)))
    _CURRENT.set(replace(scope, values=normalized))


def _validate_values(values):
    if (not isinstance(values, (list, tuple)) or len(values) > 256
            or any(not isinstance(value, str) or not value or len(value.encode()) > 65536 for value in values)
            or sum(len(value.encode()) for value in values) > 1024 * 1024):
        raise OutputUnavailable('resolved output filter exceeds its bounded contract')


def run_owner_logged(entry, command, log_path, phase, *, timeout, progress=None):
    from sandbox.application.server_cli import owner_environment, ServerCLIEffectUnknown
    from sandbox.hosting.server_cli_target import withhold_owner_completion
    context = entry.require_current()
    scope = _CURRENT.get()
    if scope is None or scope.context != context or scope.values is None:
        raise OutputUnavailable('resolved owner apply output filter is required before logged effects')
    if not isinstance(command, str) or not command or '\0' in command or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', phase) is None:
        raise OutputUnavailable('logged owner command is invalid')
    # No reversible credential representation is permitted in the command or
    # other process arguments. Intended-recipient input uses its separate port.
    bootstrap = 'import sys;sys.path.insert(0,sys.argv.pop(1));from sandbox.hosting.apply_output import main;main()'
    argv = [sys.executable, '-I', '-S', '-c', bootstrap,
            str(Path(context.owner.executable).resolve().parent), command, str(log_path), phase]
    for value in scope.values:
        forms = (value, base64.b64encode(value.encode()).decode(),
                 base64.urlsafe_b64encode(value.encode()).decode())
        if any(form in argument for argument in argv for form in forms):
            raise OutputUnavailable('resolved credential is not permitted in logged command arguments')
    payload = json.dumps({'schema_version': 1, 'exact_values': scope.values,
                          'environment': owner_environment(context.owner)}, separators=(',', ':'))
    if len(payload.encode()) > MAX_INPUT:
        raise OutputUnavailable('resolved output filter exceeds its private input bound')
    result = entry.process(shlex.join(argv), timeout=timeout, input_data=payload)
    try:
        if result.returncode != 0 or result.stderr:
            raise ValueError()
        receipt = json.loads(result.stdout)
        if (not isinstance(receipt, dict) or type(receipt.get('schema_version')) is not int
                or receipt['schema_version'] != 1):
            raise ValueError()
        if receipt == {'schema_version': 1, 'status': 'refused'}:
            raise OutputUnavailable('public output preparation refused before command dispatch')
        if (set(receipt) != {'schema_version', 'status', 'exit_code', 'output'}
                or receipt['status'] != 'exited' or type(receipt['exit_code']) is not int
                or not 0 <= receipt['exit_code'] <= 255 or not isinstance(receipt['output'], str)
                or len(receipt['output'].encode()) > TAIL_BYTES + 4):
            raise ValueError()
        entry.require_current()
        if progress is not None:
            for line in receipt['output'].splitlines():
                entry.require_current()
                try:
                    progress(line)
                except Exception:
                    pass  # Preserve the existing observer-only progress contract.
        entry.require_current()
        return subprocess.CompletedProcess(argv, receipt['exit_code'], receipt['output'], '')
    except OutputUnavailable:
        raise
    except Exception:
        withhold_owner_completion(entry)
        raise ServerCLIEffectUnknown('public output completion is unknown; retain the original apply fence') from None


def _open_log(path):
    selected = Path(path)
    if not selected.is_absolute() or selected.resolve() != selected:
        raise OutputUnavailable('apply log path is unsafe')
    parent = os.open(selected.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in selected.parts[1:-1]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = following
        descriptor = os.open(selected.name,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise OutputUnavailable('apply log is not private')
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _write(descriptor, content):
    view = memoryview(content)
    while view:
        count = os.write(descriptor, view)
        if count <= 0:
            raise OSError('public apply log write was incomplete')
        view = view[count:]


def _run(command, phase, log_path, values, environment):
    """One child in the original process group; only scrubbed bytes leave it."""
    descriptor = _open_log(log_path)
    tail = bytearray()
    child = None
    receipt = {'schema_version': 1, 'status': 'unknown'}
    def emit(content):
        _write(descriptor, content)
        tail.extend(content)
        if len(tail) > TAIL_BYTES:
            del tail[:-TAIL_BYTES]
    def marker(event, exit_code=None):
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        suffix = '' if exit_code is None else ' exit=' + str(exit_code)
        emit(f'[Sandbox] apply phase={phase} event={event} at={stamp}{suffix}\n'.encode())
    try:
        scrubber = StreamingRedactor(values)
        marker('started')
        child = subprocess.Popen(['/bin/sh', '-c', command], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, start_new_session=False)
        while True:
            chunk = child.stdout.read(65536)
            if not chunk:
                break
            emit(scrubber.feed(chunk))
        emit(scrubber.finish())
        code = child.wait()
        code = 128 - code if code < 0 else code
        marker('finished', code)
        os.fsync(descriptor)
        receipt = {'schema_version': 1, 'status': 'exited', 'exit_code': code,
                   'output': bytes(tail).decode('utf-8', errors='ignore')}
    except BaseException:
        # Parent/supervisor own cancellation of the original group. A missing
        # completion receipt must never permit effect replay or fence retirement.
        try:
            if child is not None and child.poll() is None:
                child.terminate()
        except Exception:
            pass
    finally:
        try:
            if child is not None and child.stdout is not None:
                child.stdout.close()
        except Exception:
            receipt = {'schema_version': 1, 'status': 'unknown'}
        try:
            os.close(descriptor)
        except OSError:
            receipt = {'schema_version': 1, 'status': 'unknown'}
    return receipt


def main():
    """Fixed internal receiver; no CLI registration, paths or grants from wire."""
    try:
        command, log_path, phase = sys.argv[1:]
        if not command or '\0' in command or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', phase) is None:
            raise ValueError()
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError()
        payload = json.loads(raw)
        if (not isinstance(payload, dict) or set(payload) != {'schema_version', 'exact_values', 'environment'}
                or type(payload['schema_version']) is not int or payload['schema_version'] != 1):
            raise ValueError()
        _validate_values(payload['exact_values'])
        environment = payload['environment']
        if (not isinstance(environment, dict) or set(environment) != _ENV_KEYS
                or any(not isinstance(value, str) or '\0' in value or len(value) > 8192 for value in environment.values())):
            raise ValueError()
        receipt = _run(command, phase, log_path, payload['exact_values'], environment)
    except Exception:
        receipt = {'schema_version': 1, 'status': 'refused'}
    print(json.dumps(receipt, sort_keys=True))

"""Remote source exclusion survives an SSH disconnect.

Only the remote coordinator can attest cessation. Its completion receipt is
written after Linux subreaper wait has consumed every descendant. A missing
receipt leaves the original directory lease in place, including after SIGKILL.
No PID disappearance, SSH exit code, or elapsed timeout permits reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import secrets
import shlex
import subprocess
import inspect
from sandbox.workspaces.content import content_digest


# Standalone trusted program sent over the selected transport. It imports no
# checkout code, inherits stdin only for the immutable archive, and suppresses
# mutation output. The wire carries bounded nonsecret lease metadata only.
COORDINATOR = r'''
import ctypes, errno, fcntl, hashlib, json, os, signal, stat, subprocess, sys
action, parent, name, token = sys.argv[1:5]
if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
    raise SystemExit(70)
parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
if action == "acquire":
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        raise SystemExit(73)
    os.fsync(parent_fd)
directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
identity = os.fstat(directory)
if identity.st_uid != os.geteuid() or stat.S_IMODE(identity.st_mode) != 0o700:
    raise SystemExit(70)
anchor = os.open("control", os.O_RDWR | os.O_NOFOLLOW |
                 (os.O_CREAT | os.O_EXCL if action == "acquire" else 0),
                 0o600, dir_fd=directory)
info = os.fstat(anchor)
if info.st_uid != os.geteuid() or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit(70)
fcntl.flock(anchor, fcntl.LOCK_EX | fcntl.LOCK_NB)
def current_anchor():
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    opened = os.fstat(directory)
    control = os.stat("control", dir_fd=directory, follow_symlinks=False)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino) or (control.st_dev, control.st_ino) != (info.st_dev, info.st_ino):
        raise SystemExit(70)
def write(value):
    current_anchor()
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    os.lseek(anchor, 0, os.SEEK_SET)
    os.write(anchor, data)
    os.ftruncate(anchor, len(data))
    os.fsync(anchor)
    os.fsync(directory)
def read():
    os.lseek(anchor, 0, os.SEEK_SET)
    data = os.read(anchor, 4097)
    if len(data) > 4096:
        raise SystemExit(70)
    value = json.loads(data)
    if value.get("schema_version") != 1 or value.get("operation_id") != token:
        raise SystemExit(70)
    if type(value.get("step")) is not int or value["step"] < 0 or value.get("state") not in ("idle", "active", "complete"):
        raise SystemExit(70)
    if value["state"] == "idle" and value["step"] != 0:
        raise SystemExit(70)
    if value["state"] == "complete" and (type(value.get("exit_code")) is not int or value["step"] < 1):
        raise SystemExit(70)
    current_anchor()
    return value
if action == "acquire":
    value = {"schema_version": 1, "operation_id": token, "state": "idle", "step": 0}
    write(value)
else:
    value = read()
if action in ("run", "measure"):
    if value["state"] not in ("idle", "complete"):
        raise SystemExit(70)
    command, intent = sys.argv[5:7]
    # Set this BEFORE fork. Escaped process groups and double-forks still
    # become this process's children; waiting for ECHILD is the cessation proof.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise SystemExit(70)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    value.update(state="active", step=value["step"] + 1, intent_digest=intent)
    value.pop("exit_code", None)
    value.pop('content_digest', None)
    write(value)
    if action == "measure":
        value['content_digest'] = content_digest(command)
        code = 0
    else:
        child = subprocess.Popen(["/bin/sh", "-c", command], stdin=sys.stdin.buffer,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        code = child.wait()
        while True:
            try:
                os.waitpid(-1, 0)
            except InterruptedError:
                continue
            except ChildProcessError:
                break
    value.update(state="complete", exit_code=code)
    write(value)
elif action == "release":
    if value["state"] not in ("idle", "complete"):
        raise SystemExit(70)
    current_anchor()
    # Keep the anchor locked until the directory name has been removed.
    os.unlink("control", dir_fd=directory)
    os.fsync(directory)
    os.rmdir(name, dir_fd=parent_fd)
    os.fsync(parent_fd)
    value = dict(value, state="released")
elif action not in ("acquire", "inspect"):
    raise SystemExit(70)
print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)
'''
COORDINATOR = inspect.getsource(content_digest) + '\n' + COORDINATOR


class RemoteMaterializationUnknown(RuntimeError):
    """Original exclusion is retained; do not replay or manually remove it."""

    def __init__(self, operation_id: str):
        self.operation_id = operation_id
        super().__init__(
            "remote source completion unknown; materialization lock retained; "
            f"operation_id={operation_id}; inspect/reconcile the original operation"
        )


class RemoteMaterializationBusy(RuntimeError):
    """An existing operation owns the shared source directory lease."""


class RemoteMaterializationLease:
    def __init__(self, remote, target_path: str, transport, *, operation_id=None):
        from sandbox.workspaces.checkout import materialization_lock_name

        self.remote = remote
        self.transport = transport
        self.operation_id = operation_id or secrets.token_hex(16)
        if re.fullmatch(r"[0-9a-f]{32}", self.operation_id) is None:
            raise ValueError("invalid materialization operation identity")
        self.parent = posixpath.dirname(target_path.rstrip("/"))
        self.name = materialization_lock_name(target_path)
        self.unknown = False
        self.step = 0

    def _call(self, action, *, command=None, input_data=None, timeout=10):
        args = ["python3", "-c", COORDINATOR, action, self.parent,
                self.name, self.operation_id]
        intent = None
        if command is not None:
            payload = input_data or b""
            if isinstance(payload, str):
                payload = payload.encode()
            intent = hashlib.sha256(command.encode() + b"\0" +
                                    hashlib.sha256(payload).digest()).hexdigest()
            args.extend((command, intent))
        try:
            result = self.transport(self.remote, shlex.join(args), timeout=timeout,
                                    **({"input_data": input_data} if input_data is not None else {}))
            if action == "acquire" and result.returncode == 73:
                raise RemoteMaterializationBusy("remote source materialization lock is busy")
            if result.returncode != 0:
                raise ValueError("missing remote completion receipt")
            output = result.stdout
            if isinstance(output, bytes):
                output = output.decode("ascii")
            if len(output) > 4096:
                raise ValueError("oversized remote receipt")
            value = json.loads(output)
            if (value.get("schema_version") != 1 or
                    value.get("operation_id") != self.operation_id or
                    type(value.get("step")) is not int or value["step"] < 0):
                raise ValueError("wrong materialization receipt")
            expected = {"acquire": "idle", "run": "complete", "measure": "complete",
                        "release": "released"}.get(action)
            if expected and value.get("state") != expected:
                raise ValueError("incomplete materialization receipt")
            if action in ("run", "measure") and (
                    value["step"] != self.step + 1 or
                    value.get("intent_digest") != intent or
                    type(value.get("exit_code")) is not int):
                raise ValueError("wrong materialization step")
            if action == 'measure' and (value.get('exit_code') != 0 or
                    not isinstance(value.get('content_digest'), str) or
                    re.fullmatch(r'sha256:[0-9a-f]{64}', value['content_digest']) is None):
                raise ValueError('missing measured source content')
            if action == "acquire" and value["step"] != 0:
                raise ValueError("wrong initial materialization step")
            self.step = value["step"]
            return value
        except RemoteMaterializationBusy:
            raise
        except BaseException as exc:
            self.unknown = True
            if not isinstance(exc, Exception):
                raise
            raise RemoteMaterializationUnknown(self.operation_id) from exc

    def acquire(self):
        self._call("acquire")
        return self

    def run(self, command, *, timeout, input_data=None):
        value = self._call("run", command=command, input_data=input_data,
                           timeout=timeout)
        return subprocess.CompletedProcess(command, value["exit_code"], b"", b"")

    def release(self):
        if self.unknown:
            raise RemoteMaterializationUnknown(self.operation_id)
        return self._call("release")

    def measure(self, target_path):
        return self._call('measure', command=target_path, timeout=120)['content_digest']

    def inspect(self):
        """Read original durable evidence; busy/missing/corrupt is unknown."""
        return self._call("inspect")

    def reconcile(self):
        """Explicit release only; never rerun reset, tar, or any mutation."""
        evidence = self.inspect()
        if evidence.get("state") not in ("idle", "complete"):
            raise RemoteMaterializationUnknown(self.operation_id)
        return self.release()

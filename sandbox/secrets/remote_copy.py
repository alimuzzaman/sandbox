"""Copy one secret value to a registered remote's own broker (spec 073).

The value crosses hosts only on the stdin of one SSH session that ``exec``s the
remote's staged ``sb secrets set --stdin --skip-unchanged``. Command words are
validated names, quoted for the remote shell; the value never appears in them.
Only a small allowlist of fields is read back from the remote's JSON answer.
"""
from __future__ import annotations

import json
import posixpath
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Literal, Protocol

from .models import SecretBrokerError
from .policy import validate_key


CopyAction = Literal["created", "updated", "already_current"]
CopyIntent = Literal["create", "replace", "either"]

COPY_ACTIONS: frozenset[str] = frozenset({"created", "updated", "already_current"})
COPY_INTENTS: frozenset[str] = frozenset({"create", "replace", "either"})
REMOTE_TIMEOUT_SECONDS = 60
MAX_REMOTE_STDOUT_BYTES = 65_536

_REMOTE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SOURCE_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_REVISION_RE = re.compile(r"r1_[0-9a-f]{32}")
_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _validate_project_dir(value: str | None) -> str | None:
    if value is None:
        return None
    if (type(value) is not str or not 1 <= len(value.encode("utf-8", "replace")) <= 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or not value.startswith("/") or value == "/" or value.endswith("/")
            or posixpath.normpath(value) != value
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])):
        raise SecretBrokerError("input_invalid", "remote project directory must be a normalized absolute path")
    return value


@dataclass(frozen=True)
class RemoteSetRequest:
    """Non-secret description of one remote ``secrets set``."""

    remote: str
    target_source: str
    target_key: str
    intent: CopyIntent = "either"
    target_project_dir: str | None = None
    target_revision: str | None = None

    def __post_init__(self) -> None:
        if type(self.remote) is not str or not _REMOTE_NAME_RE.fullmatch(self.remote):
            raise SecretBrokerError("input_invalid", "remote name is invalid")
        if type(self.target_source) is not str or not _SOURCE_ALIAS_RE.fullmatch(self.target_source):
            raise SecretBrokerError("input_invalid", "target source alias is invalid")
        validate_key(self.target_key)
        if self.intent not in COPY_INTENTS:
            raise SecretBrokerError("intent_conflict", "secret update intent is invalid")
        _validate_project_dir(self.target_project_dir)
        if self.target_revision is not None and (
                not isinstance(self.target_revision, str)
                or not _REVISION_RE.fullmatch(self.target_revision)):
            raise SecretBrokerError("input_invalid", "target source revision is invalid")

    @property
    def destination(self) -> str:
        return f"remote:{self.remote}/{self.target_source}/{self.target_key}"


@dataclass(frozen=True)
class RemoteSetResult:
    action: CopyAction
    revision: str | None = None


class RemoteSecretSink(Protocol):
    def put(self, request: RemoteSetRequest, value: str) -> RemoteSetResult:
        """Deliver ``value`` to the remote broker; raise a bounded SecretBrokerError."""


def remote_set_command(request: RemoteSetRequest) -> str:
    """The value-free remote shell command for one ``secrets set --stdin``."""
    project = (shlex.quote(request.target_project_dir)
               if request.target_project_dir is not None else '"$home"')
    words = ["secrets", "set", "--project-dir"]
    tail = ["--source", request.target_source, request.target_key,
            "--stdin", "--skip-unchanged", "--json"]
    if request.intent == "create":
        tail.append("--create-only")
    elif request.intent == "replace":
        tail.append("--replace-only")
    if request.target_revision is not None:
        tail.extend(("--if-revision", request.target_revision))
    return ('home="${SANDBOX_HOME:-$HOME/sandbox}"; exec "$home/sb-src/sb" '
            + " ".join(shlex.quote(word) for word in words) + " " + project + " "
            + " ".join(shlex.quote(word) for word in tail))


def parse_remote_answer(returncode: int, stdout: bytes) -> RemoteSetResult:
    """Map one remote exit and stdout to a result or a bounded refusal."""
    if returncode == 255:
        raise SecretBrokerError("remote_unreachable", "the remote could not be reached over SSH",
                                retryable=True)
    if not isinstance(stdout, bytes):
        stdout = b""
    tail = stdout[-MAX_REMOTE_STDOUT_BYTES:].decode("utf-8", "replace")
    lines = [line for line in tail.splitlines() if line.strip()]
    answer = None
    if lines:
        try:
            answer = json.loads(lines[-1])
        except ValueError:
            answer = None
    if not isinstance(answer, dict) or not isinstance(answer.get("ok"), bool):
        if returncode == 2:
            raise SecretBrokerError(
                "remote_unsupported", "the remote Sandbox does not support this copy",
                remedy="Update the remote runtime with `sb remote service migrate REMOTE --confirm`, then retry.",
            )
        raise SecretBrokerError("remote_failed", "the remote secret update failed")
    if answer["ok"] is not True:
        error = answer.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if not isinstance(code, str) or not _ERROR_CODE_RE.fullmatch(code):
            code = "remote_failed"
        raise SecretBrokerError(code, "the remote refused the secret update")
    if returncode != 0:
        raise SecretBrokerError("remote_failed", "the remote secret update failed")
    action = answer.get("action")
    if action not in COPY_ACTIONS:
        raise SecretBrokerError("remote_failed", "the remote secret update answer is invalid")
    revision = answer.get("revision")
    if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
        revision = None
    return RemoteSetResult(action=action, revision=revision)


class SshRemoteSecretSink:
    """Production sink: one SSH session per value, no retry after a timeout."""

    def __init__(self, entry: dict, *, timeout: int = REMOTE_TIMEOUT_SECONDS, runner=None) -> None:
        self._entry = entry
        self._timeout = timeout
        if runner is None:
            from sandbox.core import _remote
            runner = _remote.ssh_run
        self._run = runner

    @classmethod
    def for_remote(cls, name: str, *, lookup=None, **kwargs) -> "SshRemoteSecretSink":
        """Resolve a registered, provisioned remote before any secret is read."""
        if type(name) is not str or not _REMOTE_NAME_RE.fullmatch(name):
            raise SecretBrokerError("input_invalid", "remote name is invalid")
        if lookup is None:
            from sandbox.core import _remote
            lookup = _remote.get_remote
        entry = lookup(name)
        if not isinstance(entry, dict):
            raise SecretBrokerError("remote_unknown", "no remote is registered under that name",
                                    remedy="Register it with `sb remote add`, then retry.")
        if not entry.get("provisioned"):
            raise SecretBrokerError("remote_not_provisioned", "the remote is not provisioned",
                                    remedy="Provision it with `sb remote provision`, then retry.")
        return cls(entry, **kwargs)

    def put(self, request: RemoteSetRequest, value: str) -> RemoteSetResult:
        if not isinstance(request, RemoteSetRequest):
            raise SecretBrokerError("input_invalid", "remote secret request is invalid")
        payload = value.encode("utf-8") + b"\n"
        timed_out = failed = False
        result = None
        try:
            result = self._run(self._entry, remote_set_command(request),
                               timeout=self._timeout, input_data=payload)
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError:
            failed = True
        finally:
            payload = b""
        # Raise outside the handlers so no subprocess exception is chained.
        if timed_out:
            raise SecretBrokerError(
                "remote_timeout", "the remote secret update did not finish in time",
                remedy="The outcome is unknown; rerun the same copy, which converges.",
            )
        if failed or result is None:
            raise SecretBrokerError("remote_unreachable", "ssh could not be started", retryable=True)
        return parse_remote_answer(getattr(result, "returncode", 1), getattr(result, "stdout", b""))


__all__ = [
    "COPY_ACTIONS", "COPY_INTENTS", "CopyAction", "CopyIntent", "RemoteSecretSink",
    "RemoteSetRequest", "RemoteSetResult", "SshRemoteSecretSink", "parse_remote_answer",
    "remote_set_command",
]

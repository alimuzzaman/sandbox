"""Registered-remote transport for server-first recovery capture (spec 058).

Sends ``sandbox/recovery/server_capture_helper.py`` as
``python3 -c <source> <op> <capture_root> <args...>`` through the shared
``ssh_process`` channel, the same reviewed path the hosted recovery
controllers use.  It never uses ``sb remote ssh`` or remote job APIs, never
puts a secret in argv, and maps every helper result to a typed
``RecoveryError`` without reflecting raw remote diagnostics.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shlex
from typing import Callable, Mapping

from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.server_capture import CHUNK_BYTES, refusal
from sandbox.transports.remote_recovery import (
    RemoteSourceState, control_plane_declaration, probe_remote_state,
)


HELPER_PATH = Path(__file__).resolve().parents[1] / "recovery" / "server_capture_helper.py"
_REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SLOT = re.compile(r"^capture-[0-9a-f]{64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_JSON_LIMIT = 2 * 1024 * 1024
_HEADER_LIMIT = 4096
_MESSAGES = {
    "capture_not_found": "no server capture exists for this backup id on this remote",
    "capture_in_progress": "another capture is active on this remote",
    "capture_binding_conflict": "this backup id already has a capture with a different source binding",
    "detach_unsupported": "the remote ends user processes at logout; a detached capture would die",
    "missing_database_credential": "database credential is not available",
    "capture_not_complete": "server capture is not complete",
    "not_retirable": "server capture is not retirable",
    "retire_candidate_changed": "server capture changed since it was reviewed",
    "request_invalid": "server capture request is invalid",
    "record_invalid": "server capture record is invalid",
    "capture_root_invalid": "server capture root is not owner-controlled",
}
# Helper-side refusal data that may reach the result envelope.
_DATA_KEYS = {"active_backup_id", "state"}


def load_sandbox_config() -> Mapping:
    """Merged ``sandbox.yml`` + ``sandbox.local.yml`` for the retention bound, or ``{}``."""
    try:
        from sandbox.core._config import load_config
        config = load_config()
    except (Exception, SystemExit):
        return {}
    return config if isinstance(config, Mapping) else {}


class RegisteredServerCaptureTransport:
    """Invoke the server capture helper on one registered remote."""

    def __init__(self, *, remote_lookup: Callable | None = None, ssh_run: Callable | None = None,
                 ssh_process: Callable | None = None, resolve_home: Callable | None = None,
                 service_status: Callable | None = None, inventory: Callable | None = None,
                 helper_source: str | None = None) -> None:
        if any(value is None for value in (remote_lookup, ssh_run, ssh_process, resolve_home,
                                           service_status, inventory)):
            from sandbox.core import _remote
            from sandbox.recovery.inventory import SandboxRemoteInventory
            remote_lookup = remote_lookup or _remote.get_remote
            ssh_run = ssh_run or _remote.ssh_run
            ssh_process = ssh_process or _remote.ssh_process
            resolve_home = resolve_home or _remote.resolve_sandbox_home
            service_status = service_status or _remote.remote_mcp_service_status
            inventory = inventory or SandboxRemoteInventory().discover
        self._lookup = remote_lookup
        self._ssh_run = ssh_run
        self._ssh_process = ssh_process
        self._resolve_home = resolve_home
        self._service_status = service_status
        self._inventory = inventory
        self._helper = helper_source
        self._homes: dict[str, str] = {}

    # -- source binding (start only) ----------------------------------------

    def observe(self, remote: str) -> RemoteSourceState:
        """Probe the remote; capture start requires runtime revision state ``match``."""
        try:
            state = probe_remote_state(
                remote, lookup=self._lookup, inventory=self._inventory,
                service_status=self._service_status, resolve_home=self._resolve_home,
                ssh_run=self._ssh_run)
        except RecoveryError as exc:
            if exc.code == "remote_revision_mismatch":
                raise RecoveryError("remote runtime revision is stale; run sb remote service migrate",
                                    "remote_runtime_stale") from exc
            raise
        if state.revision_state != "match":
            raise RecoveryError("remote runtime revision is not confirmed current",
                                "remote_runtime_stale")
        return state

    @staticmethod
    def declaration(remote: str, artifact, state: RemoteSourceState, backup_id: str) -> dict:
        return control_plane_declaration(remote, artifact, state, backup_id)

    # -- helper invocation ---------------------------------------------------

    def _entry(self, remote: str) -> dict:
        if not isinstance(remote, str) or not _REMOTE.fullmatch(remote):
            raise RecoveryError("remote name is invalid", "remote_unavailable")
        try:
            entry = self._lookup(remote)
        except Exception as exc:
            raise RecoveryError("remote is unavailable", "remote_unavailable") from exc
        if not isinstance(entry, dict) or entry.get("provisioned") is not True:
            raise RecoveryError("remote is not provisioned", "remote_unavailable")
        return entry

    def _root(self, remote: str, entry: dict) -> str:
        home = self._homes.get(remote)
        if home is None:
            try:
                home = self._resolve_home(entry)
            except Exception as exc:
                raise RecoveryError("remote Sandbox home is unavailable", "remote_unavailable") from exc
            if not isinstance(home, str) or not home.startswith("/") or "\n" in home or "\0" in home:
                raise RecoveryError("remote Sandbox home is invalid", "remote_unavailable")
            self._homes[remote] = home
        return home.rstrip("/") + "/runtime/recovery-captures"

    def _source(self) -> str:
        if self._helper is None:
            self._helper = HELPER_PATH.read_text()
        return self._helper

    def _call(self, remote: str, op: str, *args: str, input_data: bytes = b"",
              timeout: int = 60) -> bytes:
        entry = self._entry(remote)
        root = self._root(remote, entry)
        command = "python3 -c " + shlex.quote(self._source()) + " " + " ".join(
            shlex.quote(str(value)) for value in (op, root, *args))
        try:
            completed = self._ssh_process(entry, command, input_data=input_data, timeout=timeout)
        except Exception as exc:
            # A timeout is ambiguous; the caller never replays automatically.
            raise RecoveryError("remote server capture call failed", "remote_unavailable") from exc
        stdout = getattr(completed, "stdout", b"")
        if isinstance(stdout, str):
            stdout = stdout.encode()
        code = getattr(completed, "returncode", 1)
        if not isinstance(stdout, bytes) or isinstance(code, bool) or code != 0 or not stdout:
            raise RecoveryError("remote server capture call failed", "remote_unavailable")
        return stdout

    @staticmethod
    def _decode(payload: bytes) -> dict:
        if len(payload) > _JSON_LIMIT:
            raise RecoveryError("remote server capture response is oversized", "remote_unavailable")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RecoveryError("remote server capture response is invalid",
                                "remote_unavailable") from exc
        if not isinstance(value, dict):
            raise RecoveryError("remote server capture response is invalid", "remote_unavailable")
        if value.get("ok") is not True:
            code = value.get("code")
            if not isinstance(code, str) or not _CODE.fullmatch(code):
                code = "server_capture_failed"
            data = {key: value[key] for key in _DATA_KEYS
                    if isinstance(value.get(key), (str, type(None))) and key in value}
            raise refusal(_MESSAGES.get(code, "server capture operation was refused"), code, **data)
        return value

    def _json(self, remote: str, op: str, *args: str, input_data: bytes = b"",
              timeout: int = 60) -> dict:
        return self._decode(self._call(remote, op, *args, input_data=input_data, timeout=timeout))

    @staticmethod
    def _slot(slot: str) -> str:
        if not isinstance(slot, str) or not _SLOT.fullmatch(slot):
            raise RecoveryError("server capture slot is invalid", "request_invalid")
        return slot

    # -- operations ----------------------------------------------------------

    def start(self, remote: str, slot: str, request: Mapping, password: str,
              declarations: bytes) -> dict:
        if not isinstance(password, str) or not password or "\n" in password:
            raise RecoveryError("database credential is not available", "missing_database_credential")
        request_text = json.dumps(request, sort_keys=True, separators=(",", ":"))
        # The password is the first stdin line; it never appears in argv.
        stdin = password.encode() + b"\n" + declarations
        return self._json(remote, "start", self._slot(slot), request_text, input_data=stdin,
                          timeout=60)

    def status(self, remote: str, slot: str) -> dict:
        return self._json(remote, "status", self._slot(slot))

    def list(self, remote: str) -> dict:
        return self._json(remote, "list", timeout=120)

    def read_receipt(self, remote: str, slot: str) -> dict:
        receipt = self._json(remote, "read-receipt", self._slot(slot)).get("receipt")
        if not isinstance(receipt, dict):
            raise RecoveryError("server capture receipt is invalid", "record_invalid")
        return receipt

    def read_declaration(self, remote: str, slot: str) -> str:
        text = self._json(remote, "read-declaration", self._slot(slot)).get("text")
        if not isinstance(text, str) or not text:
            raise RecoveryError("server capture declaration is invalid", "record_invalid")
        return text

    def read_chunk(self, remote: str, slot: str, offset: int, length: int) -> bytes:
        if (isinstance(offset, bool) or isinstance(length, bool) or not isinstance(offset, int)
                or not isinstance(length, int) or offset < 0 or not 0 < length <= CHUNK_BYTES):
            raise RecoveryError("chunk request is invalid", "request_invalid")
        payload = self._call(remote, "read-chunk", self._slot(slot), str(offset), str(length),
                             timeout=600)
        newline = payload.find(b"\n", 0, _HEADER_LIMIT)
        if newline < 0:
            raise RecoveryError("transferred chunk header is invalid", "transfer_mismatch")
        header = self._decode(payload[:newline])
        data = payload[newline + 1:]
        if (header.get("offset") != offset or header.get("length") != len(data)
                or len(data) > length or hashlib.sha256(data).hexdigest() != header.get("sha256")):
            raise RecoveryError("transferred chunk does not match its header", "transfer_mismatch")
        return data

    def mark_promoted(self, remote: str, slot: str, marker: Mapping) -> dict:
        return self._json(remote, "mark-promoted", self._slot(slot),
                          json.dumps(marker, sort_keys=True, separators=(",", ":")))

    def check_integrity(self, remote: str, slot: str) -> dict:
        return self._json(remote, "check-integrity", self._slot(slot), timeout=1800)

    @staticmethod
    def _retire_candidate(value: Mapping) -> dict:
        fields = {"state", "receipt_sha256", "archive_sha256", "archive_size"}
        if set(value) != fields:
            raise RecoveryError("server capture retire plan is invalid", "record_invalid")
        state = value.get("state")
        receipt_sha = value.get("receipt_sha256")
        archive_sha = value.get("archive_sha256")
        archive_size = value.get("archive_size")
        if (state not in ("promoted", "failed", "incomplete")
                or (receipt_sha is not None
                    and (not isinstance(receipt_sha, str) or not _HEX64.fullmatch(receipt_sha)))
                or (archive_sha is not None
                    and (not isinstance(archive_sha, str) or not _HEX64.fullmatch(archive_sha)))
                or (archive_size is not None
                    and (isinstance(archive_size, bool) or not isinstance(archive_size, int)
                         or archive_size < 0))
                or ((archive_sha is None) != (archive_size is None))):
            raise RecoveryError("server capture retire plan is invalid", "record_invalid")
        return {"state": state, "receipt_sha256": receipt_sha,
                "archive_sha256": archive_sha, "archive_size": archive_size}

    def retire_plan(self, remote: str, slot: str) -> dict:
        response = self._json(remote, "retire-plan", self._slot(slot), timeout=1800)
        if set(response) != {"ok", "state", "receipt_sha256", "archive_sha256", "archive_size"}:
            raise RecoveryError("server capture retire plan is invalid", "record_invalid")
        return self._retire_candidate({key: response[key] for key in response if key != "ok"})

    def retire(self, remote: str, slot: str, plan: Mapping) -> dict:
        candidate = self._retire_candidate(plan)
        response = self._json(remote, "retire", self._slot(slot),
                              json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                              timeout=1800)
        retired_at = response.get("retired_at")
        removed_bytes = response.get("removed_bytes")
        if (set(response) != {"ok", "retired_at", "removed_bytes"}
                or isinstance(retired_at, bool)
                or not isinstance(retired_at, (int, float))
                or retired_at < 0 or retired_at > 253402300799
                or (isinstance(retired_at, float) and not math.isfinite(retired_at))
                or isinstance(removed_bytes, bool) or not isinstance(removed_bytes, int)
                or removed_bytes < 0):
            raise RecoveryError(
                "server capture retirement result is invalid; inspect status before any retry",
                "record_invalid")
        return {"retired_at": retired_at, "removed_bytes": removed_bytes}


__all__ = ["HELPER_PATH", "RegisteredServerCaptureTransport", "load_sandbox_config"]

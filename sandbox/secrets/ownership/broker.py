"""Parent side of the ownership worker: one process and one login per session.

The parent validates the complete grant authority, spawns the private worker
with the allowlisted environment, speaks the framed protocol, and always ends
with ``close`` plus process-group cleanup. Errors are fixed codes and fixed
messages; provider bodies, exceptions and lengths are never interpolated.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from sandbox.secrets.authority import validate_ownership_grant
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.process_group import terminate_process_group
from sandbox.secrets.providers.base import MAX_PROVIDER_VALUE_BYTES
from sandbox.secrets.providers.broker import BoundedProviderBroker

from .models import CreateOutcome, Existence, Grant, NameState, RevocationOutcome
from .protocol import (
    PROTOCOL_VERSION, FrameChannel, ProtocolError, WorkerError, wipe,
)


# ECMAScript TrimString removes WhiteSpace and LineTerminator code points:
# U+0009-U+000D, U+0020, U+00A0, U+1680, U+2000-U+200A, U+2028, U+2029,
# U+202F, U+205F, U+3000, U+FEFF (25 code points).
TRIM_STRING_CODE_POINTS = frozenset(
    [0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680]
    + list(range(0x2000, 0x200B))
    + [0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF]
)
assert len(TRIM_STRING_CODE_POINTS) == 25

NAME_PATTERN = re.compile(r"\A[A-Z_][A-Z0-9_]*\Z")
GATEWAY_BUNDLE_KEYS = frozenset(("clientSecret", "gatewayClientId", "gatewayClientSecret"))
_MAX_COMMENT = 256
_CLOSE_SECONDS = 5.0

_MESSAGES = {
    "authority_mismatch": "secret ownership grant does not match its authority",
    "credential_backend_unavailable": "approved controller credential store is unavailable",
    "credential_locked": "approved controller credential store is locked",
    "credential_missing": "ownership bootstrap credential is not present in the approved store",
    "credential_store_failed": "approved controller credential store operation failed",
    "credential_scope_invalid": "ownership bootstrap credential does not match the profile gateway shape",
    "authentication_denied": "secret ownership destination refused the bootstrap credential",
    "endpoint_unreachable": "secret ownership destination is unreachable",
    "endpoint_untrusted": "secret ownership destination is not a trusted HTTPS origin",
    "operation_timeout": "secret ownership operation exceeded its deadline",
    "provider_denied": "secret ownership destination denied the request",
    "provider_rate_limited": "secret ownership destination rate limited the request",
    "provider_failed": "secret ownership provider operation failed",
    "response_invalid": "secret ownership destination returned an unrecognized response",
    "response_too_large": "secret ownership destination response exceeded the supported limit",
    "item_unavailable": "secret ownership destination item is unavailable",
    "provider_contract_unconfirmed": "secret ownership provider contract is not confirmed",
    "value_unsupported": "secret ownership value would not be stored losslessly",
}
_STATES = {
    "created": NameState.CREATED,
    "conflict": NameState.CONFLICT,
    "unresolved": NameState.UNRESOLVED,
    "acceptance_unknown": NameState.ACCEPTANCE_UNKNOWN,
    "failed": NameState.FAILED,
}
_REASONS = frozenset((
    "approval_pending", "already_exists", "response_unrecognized", "not_sent",
    "transport_uncertain", "aborted", "worker_lost",
))
_EXISTENCE = {"absent": Existence.ABSENT, "present": Existence.PRESENT, "unknown": Existence.UNKNOWN}
NEXT_ACTION_REVOKED = (
    "derived access token revoked; gateway tokens and native bootstrap entries "
    "are operator-managed and their cleanup is not confirmed"
)
NEXT_ACTION_UNKNOWN = (
    "derived access token revocation not confirmed; it expires at token_expires_at; "
    "gateway tokens and native bootstrap entries are operator-managed and their "
    "cleanup is not confirmed"
)


def _error(code: str) -> SecretBrokerError:
    if code not in _MESSAGES:
        code = "provider_failed"
    return SecretBrokerError(code, _MESSAGES[code])


class ValueUnsupportedError(SecretBrokerError):
    """A create input the provider would transform; carries name and reason only."""

    def __init__(self, name: str, reason: str) -> None:
        safe_name = name if isinstance(name, str) and NAME_PATTERN.match(name) else "<invalid-name>"
        if reason not in ("not_bytes", "too_large", "not_utf8", "nul_character",
                          "trim_boundary", "name_unsupported"):
            reason = "name_unsupported"
        super().__init__(
            "value_unsupported",
            f"secret ownership value for {safe_name} is not stored losslessly ({reason})",
        )
        self.name = safe_name
        self.reason = reason


def check_create_value(name: str, value: bytes | bytearray) -> None:
    """Pure losslessness check for one create value; never normalizes."""
    if not isinstance(value, (bytes, bytearray)):
        raise ValueUnsupportedError(name, "not_bytes")
    if len(value) > MAX_PROVIDER_VALUE_BYTES:
        raise ValueUnsupportedError(name, "too_large")
    text: str | None = None
    try:
        try:
            text = bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueUnsupportedError(name, "not_utf8") from None
        if "\0" in text:
            raise ValueUnsupportedError(name, "nul_character")
        if text and (ord(text[0]) in TRIM_STRING_CODE_POINTS
                     or ord(text[-1]) in TRIM_STRING_CODE_POINTS):
            raise ValueUnsupportedError(name, "trim_boundary")
    finally:
        text = None


def validate_create_inputs(grant: Grant, values: Mapping[str, bytearray]) -> None:
    """Validate an ENTIRE batch before any create; pure, no I/O, no mutation."""
    if not isinstance(grant, Grant) or grant.capability != "create" or not isinstance(values, Mapping):
        raise _error("authority_mismatch")
    if (not isinstance(grant.names, tuple) or not grant.names
            or any(not isinstance(name, str) for name in grant.names)):
        raise _error("authority_mismatch")
    allowed = set(grant.names)
    names = list(values)
    if any(not isinstance(name, str) for name in names) or not set(names) <= allowed:
        raise _error("authority_mismatch")
    for name in sorted(names):
        if not NAME_PATTERN.match(name):
            raise ValueUnsupportedError(name, "name_unsupported")
        check_create_value(name, values[name])


def split_bootstrap_input(
    raw: bytes | bytearray, gateway: Literal["none", "cloudflare-access"],
) -> tuple[bytearray, dict[str, str]]:
    """Return ``(client_secret, gateway_headers)``; pure, fixed-code errors.

    ``none``: the whole entry is the opaque client secret. ``cloudflare-access``:
    exactly ``{clientSecret, gatewayClientId, gatewayClientSecret}``. Any other
    shape is ``credential_scope_invalid``.
    """
    if gateway not in ("none", "cloudflare-access"):
        raise _error("credential_scope_invalid")
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_PROVIDER_VALUE_BYTES:
        raise _error("credential_scope_invalid")
    text: str | None = None
    try:
        try:
            text = bytes(raw).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise _error("credential_scope_invalid") from None
        if gateway == "none":
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
                raise _error("credential_scope_invalid")
            return bytearray(raw), {}
        try:
            bundle = json.loads(
                text, object_pairs_hook=_unique_pairs, parse_constant=_no_constant,
            )
        except (ValueError, RecursionError):
            raise _error("credential_scope_invalid") from None
        if type(bundle) is not dict or set(bundle) != GATEWAY_BUNDLE_KEYS:
            raise _error("credential_scope_invalid")
        secret = bundle["clientSecret"]
        header_id = bundle["gatewayClientId"]
        header_secret = bundle["gatewayClientSecret"]
        if not isinstance(secret, str) or not secret or any(
            ord(char) < 0x20 or ord(char) == 0x7F for char in secret
        ):
            raise _error("credential_scope_invalid")
        for item in (header_id, header_secret):
            if (not isinstance(item, str) or not item or len(item) > 4_096
                    or any(ord(char) < 0x21 or ord(char) > 0x7E for char in item)):
                raise _error("credential_scope_invalid")
        try:
            client_secret = bytearray(secret.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise _error("credential_scope_invalid") from None
        bundle = {}
        secret = ""
        return client_secret, {
            "CF-Access-Client-Id": header_id,
            "CF-Access-Client-Secret": header_secret,
        }
    finally:
        text = None


def validate_bootstrap_input(raw: bytearray,
                             gateway: Literal["none", "cloudflare-access"]) -> None:
    """Pure fixed-code bootstrap shape validation; no I/O, input untouched."""
    client_secret, headers = split_bootstrap_input(raw, gateway)
    wipe(client_secret)
    headers.clear()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _no_constant(_value: str) -> None:
    raise ValueError("constant")


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise _error("authority_mismatch")


def open_frame(grant: Grant, deadline: float) -> dict:
    """The normalized ``open`` message; carries metadata only, never a secret."""
    destination = grant.destination
    return {
        "version": PROTOCOL_VERSION,
        "op": "open",
        "deadline": float(deadline),
        "grant": {
            "profile": grant.profile,
            "capability": grant.capability,
            "endpoint": destination.endpoint,
            "projectId": destination.project_id,
            "environment": destination.environment,
            "path": destination.path,
            "clientId": grant.client_id,
            "authorityDigest": grant.authority_digest,
            "names": list(grant.names),
            "timeoutSeconds": destination.timeout_seconds,
            "gateway": destination.gateway,
            "projectRoot": grant.project_root,
            "ownership": _plain(grant.ownership),
        },
    }


class BrokerOwnershipSession:
    """One worker process, one login; use as a context manager."""

    def __init__(self, adapter: "BrokerOwnershipAdapter", grant: Grant, deadline: float) -> None:
        self._adapter = adapter
        self._grant = grant
        self._deadline_wall = float(deadline)
        self._end = None
        self._process = None
        self._channel: FrameChannel | None = None
        self._broken = False
        self._opened = False
        self._attempted: set[str] = set()
        self._started: set[str] = set()
        self.owners: dict[str, str] = {}
        self._revocation: RevocationOutcome | None = None
        self.token_expires_at: float | None = None

    # -- plumbing ---------------------------------------------------------
    def _remaining(self) -> float:
        return self._end - self._adapter._clock()

    def _require_channel(self) -> FrameChannel:
        if self._channel is None or self._broken or not self._opened:
            raise _error("provider_failed")
        return self._channel

    def _fail(self, exc: BaseException) -> SecretBrokerError:
        self._broken = True
        if isinstance(exc, WorkerError):
            return _error(exc.code)
        if isinstance(exc, ProtocolError) and exc.code == "operation_timeout":
            return _error("operation_timeout")
        return _error("provider_failed")

    def _names(self, names: Sequence[str]) -> list[str]:
        if not isinstance(names, Sequence) or isinstance(names, (str, bytes, bytearray)):
            raise _error("authority_mismatch")
        selected = list(names)
        if (not selected or any(not isinstance(name, str) for name in selected)
                or len(set(selected)) != len(selected)
                or not set(selected) <= set(self._grant.names)):
            raise _error("authority_mismatch")
        return selected

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> "BrokerOwnershipSession":
        if self._process is not None or self._revocation is not None:
            raise _error("provider_failed")
        validate_ownership_grant(self._grant)
        seconds = self._deadline_wall - time.time()
        if not math.isfinite(seconds) or seconds <= 0:
            raise _error("operation_timeout")
        self._end = self._adapter._clock() + seconds
        frame = open_frame(self._grant, self._deadline_wall)
        try:
            self._process = self._adapter._process_factory(
                self._adapter._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=BoundedProviderBroker._environment(),
                start_new_session=True, close_fds=True,
            )
        except (OSError, ValueError):
            self._revocation = RevocationOutcome("not_applicable", None, None)
            raise _error("provider_failed") from None
        try:
            stdin_fd = self._process.stdin.fileno()
            stdout_fd = self._process.stdout.fileno()
            os.set_blocking(stdin_fd, False)
            os.set_blocking(stdout_fd, False)
            self._channel = FrameChannel(stdout_fd, stdin_fd, self._remaining)
            self._channel.send_json(frame)
            opened = self._channel.recv_json("opened")
            expires = opened.get("tokenExpiresAt")
            if (set(opened) != {"op", "tokenExpiresAt"} or isinstance(expires, bool)
                    or not isinstance(expires, (int, float)) or not math.isfinite(expires)
                    or expires <= time.time()):
                raise ProtocolError()
            self.token_expires_at = float(expires)
            self._end = min(self._end, self._adapter._clock() + expires - time.time())
            self._opened = True
        except (ProtocolError, WorkerError, OSError, AttributeError, ValueError) as exc:
            code = exc.code if isinstance(exc, WorkerError) else None
            error = self._fail(exc)
            pre_login = code in (
                "authority_mismatch", "credential_missing", "credential_locked",
                "credential_backend_unavailable", "credential_store_failed",
                "credential_scope_invalid", "authentication_denied",
            )
            self._shutdown(pre_login=pre_login)
            raise error from None
        except BaseException:
            self._shutdown(pre_login=False)
            raise
        return self

    def __exit__(self, *exc) -> None:
        self._shutdown(pre_login=False)

    def _shutdown(self, *, pre_login: bool) -> None:
        if self._revocation is not None and self._process is None:
            return
        status = "cleanup_unknown"
        process, self._process = self._process, None
        try:
            if pre_login:
                status = "not_applicable"
            elif self._channel is not None:
                # Revocation gets a short fixed bound even past the work deadline.
                self._end = self._adapter._clock() + _CLOSE_SECONDS
                self._channel.send_json({"op": "close"})
                # A broken stream may have pending frames. Its reply cannot
                # establish a revoke acknowledgment; SIGTERM still tries revoke.
                if self._opened and not self._broken:
                    closed = self._channel.recv_json("closed")
                    if set(closed) == {"op", "revocation"} and closed["revocation"] == "revoked":
                        status = "revoked"
        except BaseException:
            self._broken = True
        finally:
            if process is not None:
                try:
                    ended = terminate_process_group(process, grace_seconds=0.5, kill_wait_seconds=0.5)
                except Exception:
                    ended = False
                for stream in (process.stdin, process.stdout):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                if not ended:
                    status = "cleanup_unknown"
            self._opened = False
        if status == "revoked":
            next_action = NEXT_ACTION_REVOKED
        elif status == "cleanup_unknown":
            next_action = NEXT_ACTION_UNKNOWN
        else:
            next_action = None
        self._revocation = RevocationOutcome(status, self.token_expires_at, next_action)

    def revocation(self) -> RevocationOutcome:
        if self._revocation is None:
            raise _error("provider_failed")
        return self._revocation

    # -- operations -------------------------------------------------------
    def exists(self, names: Sequence[str]) -> dict[str, Existence]:
        selected = self._names(names)
        channel = self._require_channel()
        try:
            channel.send_json({"op": "exists", "names": selected})
            reply = channel.recv_json("exists")
        except (ProtocolError, WorkerError) as exc:
            raise self._fail(exc) from None
        result = reply.get("result")
        if set(reply) != {"op", "result", "owners"} or type(result) is not dict or set(result) != set(selected):
            raise self._fail(ProtocolError())
        owners = reply["owners"]
        if (type(owners) is not dict or not set(owners) <= set(selected)
                or any(not isinstance(owner, str) or re.fullmatch(r"[0-9a-f]{32}", owner) is None
                       for owner in owners.values())
                or any(result[name] != "present" for name in owners)):
            raise self._fail(ProtocolError())
        self.owners = dict(owners)
        states = {}
        for name in selected:
            state = _EXISTENCE.get(result[name]) if isinstance(result[name], str) else None
            states[name] = state if state is not None else Existence.UNKNOWN
        return states

    def policy_requires_approval(self) -> bool | None:
        channel = self._require_channel()
        try:
            channel.send_json({"op": "policy"})
            reply = channel.recv_json("policy")
        except (ProtocolError, WorkerError) as exc:
            raise self._fail(exc) from None
        value = reply.get("requiresApproval")
        if set(reply) != {"op", "requiresApproval"} or not (value is None or isinstance(value, bool)):
            raise self._fail(ProtocolError())
        return value

    def create(self, name: str, value: bytearray, *, comment: str,
               before_send: Callable[[], None]) -> CreateOutcome:
        if self._grant.capability != "create":
            raise _error("authority_mismatch")
        self._names([name])
        if name in self._started:
            raise _error("authority_mismatch")    # never retry within a session
        if (not isinstance(comment, str) or len(comment) > _MAX_COMMENT
                or any(ord(char) < 0x20 or ord(char) > 0x7E for char in comment)):
            raise _error("authority_mismatch")
        check_create_value(name, value)
        channel = self._require_channel()
        try:
            channel.send_json({"op": "create", "name": name, "comment": comment})
            channel.send_value(value)
            ready = channel.recv_json("ready")
            if set(ready) != {"op", "name"} or ready["name"] != name:
                raise ProtocolError()
        except (ProtocolError, WorkerError) as exc:
            raise self._fail(exc) from None
        self._started.add(name)
        try:
            before_send()
        except BaseException:
            try:
                channel.send_json({"op": "abort"})
                channel.recv_json("created")
            except (ProtocolError, WorkerError):
                self._broken = True
            raise
        self._attempted.add(name)
        try:
            channel.send_json({"op": "go", "name": name})
            reply = channel.recv_json("created")
        except (ProtocolError, WorkerError):
            # 'go' may have been delivered; acceptance is unknowable, never retried.
            self._broken = True
            return CreateOutcome(name, NameState.ACCEPTANCE_UNKNOWN, "worker_lost")
        state = _STATES.get(reply.get("state")) if isinstance(reply.get("state"), str) else None
        reason = reply.get("reason")
        if (set(reply) != {"op", "name", "state", "reason"} or reply.get("name") != name
                or state is None or not (reason is None or isinstance(reason, str) and reason in _REASONS)):
            self._broken = True
            return CreateOutcome(name, NameState.ACCEPTANCE_UNKNOWN, "response_unrecognized")
        return CreateOutcome(name, state, reason)

    def readback_equals(self, name: str, value: bytearray) -> bool | None:
        self._names([name])
        if self._grant.capability == "create" and name not in self._attempted:
            raise _error("authority_mismatch")
        if not isinstance(value, (bytes, bytearray)) or len(value) > MAX_PROVIDER_VALUE_BYTES:
            raise _error("authority_mismatch")
        channel = self._require_channel()
        try:
            channel.send_json({"op": "readback", "name": name})
            channel.send_value(value)
            reply = channel.recv_json("readback")
        except (ProtocolError, WorkerError) as exc:
            raise self._fail(exc) from None
        equal = reply.get("equal")
        if (set(reply) != {"op", "name", "equal"} or reply.get("name") != name
                or not (equal is None or isinstance(equal, bool))):
            raise self._fail(ProtocolError())
        return equal

    def read(self, names: Sequence[str]) -> dict[str, bytearray]:
        if self._grant.capability != "read":
            raise _error("authority_mismatch")
        selected = self._names(names)
        channel = self._require_channel()
        values: dict[str, bytearray] = {}
        try:
            channel.send_json({"op": "read", "names": selected})
            while True:
                message = channel.recv_json()
                if message == {"op": "done"}:
                    break
                name = message.get("name")
                if (set(message) != {"op", "name"} or message.get("op") != "value"
                        or not isinstance(name, str) or name not in selected or name in values):
                    raise ProtocolError()
                values[name] = channel.recv_value()
            if set(values) != set(selected):
                raise ProtocolError()
        except (ProtocolError, WorkerError) as exc:
            for buffer in values.values():
                wipe(buffer)
            values.clear()
            error = self._fail(exc)
            if isinstance(exc, WorkerError):
                self._broken = False    # worker reported a fixed per-read failure and stays usable
            raise error from None
        return values


class BrokerOwnershipAdapter:
    """Production ``OwnershipAdapter`` backed by the private ownership worker."""

    def __init__(self, *, process_factory=subprocess.Popen, clock=time.monotonic) -> None:
        self._process_factory = process_factory
        self._clock = clock

    @staticmethod
    def _command() -> list[str]:
        root = str(Path(__file__).resolve().parents[3])
        code = (
            "import sys;sys.path.insert(0," + repr(root) + ");"
            "from sandbox.secrets.ownership.worker import main;main()"
        )
        return [sys.executable, "-I", "-c", code]

    def validate_create_inputs(self, grant: Grant, values: Mapping[str, bytearray]) -> None:
        validate_create_inputs(grant, values)

    def open_session(self, grant: Grant, *, deadline: float) -> BrokerOwnershipSession:
        validate_ownership_grant(grant)
        if grant.capability == "create":
            from .manifest import OWNERSHIP_MANIFEST
            if OWNERSHIP_MANIFEST["infisical-v4"]["contract_confirmed"] is not True:
                raise _error("provider_contract_unconfirmed")
        if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not math.isfinite(deadline)):
            raise _error("operation_timeout")
        return BrokerOwnershipSession(self, grant, float(deadline))


OwnershipSession = BrokerOwnershipSession

__all__ = [
    "BrokerOwnershipAdapter", "BrokerOwnershipSession", "GATEWAY_BUNDLE_KEYS",
    "NAME_PATTERN", "OwnershipSession", "TRIM_STRING_CODE_POINTS",
    "ValueUnsupportedError", "check_create_value", "open_frame",
    "split_bootstrap_input", "validate_bootstrap_input", "validate_create_inputs",
]

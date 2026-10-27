"""Private ownership worker: one validated grant, one login, framed pipe I/O.

Started by ``BrokerOwnershipAdapter`` as ``python -I`` with an allowlisted
environment and stderr discarded. Standard library plus the minimal
``sandbox.secrets`` helpers needed for authoritative grant validation, the
native credential store, JSON decoding and the frame protocol; it never
imports controller CLI or core modules.

Network surface is fixed: Universal Auth login, exact v4 secret GET/POST for
granted names, and access-token revoke. There is no list, search, export or
administration call. The token, bootstrap secret and values never reach stdout
except value bytes in ``V`` frames answering a ``read`` under a read grant.
"""

from __future__ import annotations

import hmac
import http.client
import json
import math
import os
import re
import signal
import socket
import ssl
import sys
import time
import unicodedata
import urllib.parse

from sandbox.secrets.authority import validate_ownership_grant
from sandbox.secrets.credentials.base import locator_for_ownership
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.provider_identity import canonical_https_origin
from sandbox.secrets.providers.base import MAX_PROVIDER_RESPONSE_BYTES, MAX_PROVIDER_VALUE_BYTES
from sandbox.secrets.providers.infisical import _decode_json, _login_request_body

from .broker import NAME_PATTERN, check_create_value, split_bootstrap_input, validate_bootstrap_input
from .models import Grant, OwnershipConfig
from .protocol import PROTOCOL_VERSION, FrameChannel, ProtocolError, WorkerError, wipe


USER_AGENT = "sandbox-secret-ownership/1"
LOGIN_PATH = "/api/v1/auth/universal-auth/login"
REVOKE_PATH = "/api/v1/auth/token/revoke"
SECRET_PREFIX = "/api/v4/secrets/"
# Infisical v0.166.5 identity-access-token-router.ts:40-65, mounted under /auth
# by routes/v1/index.ts:105-124. This acknowledges derived-token revoke only.
# https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/server/routes/v1/identity-access-token-router.ts#L40-L65
REVOKE_ACKNOWLEDGMENT = "Successfully revoked access token"
_REVOKE_SECONDS = 4.0
_MAX_COMMENT = 256
_GRANT_KEYS = frozenset((
    "profile", "capability", "endpoint", "projectId", "environment", "path",
    "clientId", "authorityDigest", "names", "timeoutSeconds", "gateway",
    "projectRoot", "ownership",
))
_LOGIN_KEYS = frozenset(("accessToken", "expiresIn", "accessTokenMaxTTL", "tokenType"))


class _NotSent(Exception):
    """Failure before any request byte left the process (DNS/connect/TLS)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Uncertain(Exception):
    """Failure after the request may have been sent; acceptance is unknowable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _connect_code(exc: BaseException) -> str:
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
        return "endpoint_untrusted"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "operation_timeout"
    return "endpoint_unreachable"


class _Worker:
    def __init__(self, channel: FrameChannel) -> None:
        self.channel = channel
        self.grant: Grant | None = None
        self.deadline = 0.0
        self._work_end: float | None = None
        self.host = ""
        self.port = 443
        self.gateway_headers: dict[str, str] = {}
        self.token: str | None = None
        self.token_expires_at: float | None = None
        self.attempted: set[str] = set()
        self.started: set[str] = set()
        self.revocation: str | None = None
        self._revoking = False
        self._context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        self._context.check_hostname = True
        self._context.verify_mode = ssl.CERT_REQUIRED

    # -- transport --------------------------------------------------------
    def remaining(self) -> float | None:
        return None if self._work_end is None else self._work_end - time.monotonic()

    def _timeout(self, cap: float | None = None) -> float:
        remaining = self.remaining()
        if remaining is None:
            raise _NotSent("operation_timeout")
        bound = min(float(self.grant.destination.timeout_seconds), remaining)
        if cap is not None:
            bound = min(bound, cap)
        if bound <= 0:
            raise _NotSent("operation_timeout")
        return bound

    def _request(self, method: str, target: str, body: bytearray | None = None, *,
                 auth: bool = True, timeout: float | None = None) -> tuple[int, bytearray]:
        """One HTTPS exchange with the exact origin; no proxy, redirect or retry."""
        seconds = timeout if timeout is not None else self._timeout()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        headers.update(self.gateway_headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            if self.token is None:
                raise _NotSent("provider_failed")
            headers["Authorization"] = "Bearer " + self.token
        connection = http.client.HTTPSConnection(
            self.host, self.port, timeout=seconds, context=self._context,
        )
        buffer = bytearray()
        end = time.monotonic() + seconds

        def request_timeout(_signum, _frame) -> None:
            raise TimeoutError()

        def refresh_timeout() -> None:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(left)

        # Socket idle timeouts alone do not bound slow headers or TLS reads.
        # The worker is single-threaded on the supported native-store platforms.
        previous_handler = signal.signal(signal.SIGALRM, request_timeout)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            try:
                connection.connect()
            except Exception as exc:
                raise _NotSent(_connect_code(exc)) from None
            try:
                refresh_timeout()
                connection.request(method, target, body=body, headers=headers)
                refresh_timeout()
                response = connection.getresponse()
                status = int(response.status)
                while True:
                    refresh_timeout()
                    chunk = response.read(min(65_536, MAX_PROVIDER_RESPONSE_BYTES + 1 - len(buffer)))
                    if not chunk:
                        break
                    buffer += chunk
                    chunk = b""
                    if len(buffer) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise _Uncertain("response_too_large")
            except _Uncertain:
                wipe(buffer)
                raise
            except Exception as exc:
                wipe(buffer)
                code = "operation_timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "provider_failed"
                raise _Uncertain(code) from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
            headers.clear()
            try:
                connection.close()
            except Exception:
                pass
        return status, buffer

    def _secret_target(self, name: str, *, view: bool) -> str:
        destination = self.grant.destination
        quoted = urllib.parse.quote(name, safe="", encoding="utf-8", errors="strict")
        query = urllib.parse.urlencode(
            (
                ("projectId", destination.project_id),
                ("environment", destination.environment),
                ("secretPath", destination.path),
                ("type", "shared"),
                ("viewSecretValue", "true" if view else "false"),
                ("expandSecretReferences", "false"),
                ("includeImports", "false"),
            ),
            safe="", encoding="utf-8", errors="strict", quote_via=urllib.parse.quote,
        )
        return f"{SECRET_PREFIX}{quoted}?{query}"

    def _record(self, body: bytearray, name: str) -> dict | None:
        """The exact secret record for ``name`` in a ``{"secret": …}`` body."""
        try:
            data = _decode_json(body)
        except SecretBrokerError:
            return None
        if type(data) is not dict or set(data) != {"secret"} or type(data["secret"]) is not dict:
            return None
        record = data["secret"]
        destination = self.grant.destination
        if (
            record.get("secretKey") != name
            or record.get("workspace") != destination.project_id
            or record.get("environment") != destination.environment
            or record.get("secretPath") != destination.path
            or record.get("type") != "shared"
        ):
            return None
        return record

    # -- open / login -----------------------------------------------------
    def open(self) -> None:
        message = self.channel.recv_json("open")
        deadline = message.get("deadline")
        frame = message.get("grant")
        if (set(message) != {"version", "op", "grant", "deadline"}
                or message.get("version") != PROTOCOL_VERSION
                or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not math.isfinite(deadline) or type(frame) is not dict
                or set(frame) != _GRANT_KEYS):
            raise ProtocolError()
        self.deadline = float(deadline)
        if self.deadline <= time.time():
            raise SecretBrokerError("operation_timeout", "deadline passed")
        self.grant = _grant_from_frame(frame)
        self._work_end = time.monotonic() + self.deadline - time.time()
        # Authority is recomputed from the complete ownership block and
        # canonical root BEFORE the native store is touched.
        validate_ownership_grant(self.grant)
        if self.grant.capability == "create":
            from .manifest import OWNERSHIP_MANIFEST
            if OWNERSHIP_MANIFEST["infisical-v4"]["contract_confirmed"] is not True:
                raise SecretBrokerError("provider_contract_unconfirmed", "ownership provider contract is not confirmed")
        try:
            origin = canonical_https_origin(self.grant.destination.endpoint)
            parsed = urllib.parse.urlsplit(origin)
            self.host = parsed.hostname or ""
            self.port = parsed.port or 443
        except ValueError:
            raise SecretBrokerError("endpoint_untrusted", "untrusted") from None
        if not self.host or origin != self.grant.destination.endpoint.rstrip("/"):
            raise SecretBrokerError("endpoint_untrusted", "untrusted")
        locator = locator_for_ownership(self.grant)
        try:
            from sandbox.secrets.credentials.manifest import create_credential_store
            store = create_credential_store(bootstrap_dependencies=False)
        except RuntimeError:
            raise SecretBrokerError("credential_backend_unavailable", "unavailable") from None
        text = None
        try:
            text = store._read(locator)
            raw = bytearray(text.encode("utf-8", errors="strict")) if isinstance(text, str) else bytearray()
        except UnicodeError:
            raise SecretBrokerError("credential_scope_invalid", "invalid bootstrap shape") from None
        finally:
            text = None
        client_secret = None
        body = None
        try:
            validate_bootstrap_input(raw, self.grant.destination.gateway)
            client_secret, self.gateway_headers = split_bootstrap_input(
                raw, self.grant.destination.gateway,
            )
            wipe(raw)
            body = _login_request_body(self.grant.client_id, client_secret)
            wipe(client_secret)
            started = time.time()
            try:
                status, response = self._request("POST", LOGIN_PATH, body, auth=False)
            except (_NotSent, _Uncertain) as exc:
                raise SecretBrokerError(exc.code, "login failed") from None
        finally:
            wipe(raw)
            wipe(client_secret)
            wipe(body)
        try:
            if status in (401, 403):
                raise SecretBrokerError("authentication_denied", "denied")
            if status == 429:
                raise SecretBrokerError("provider_rate_limited", "rate limited")
            if status != 200:
                raise SecretBrokerError("provider_failed", "login failed")
            self.token, expires_in = _parse_login(response)
        finally:
            wipe(response)
        self.token_expires_at = started + expires_in
        self._work_end = min(self._work_end, time.monotonic() + self.token_expires_at - time.time())
        self.channel.send_json({"op": "opened", "tokenExpiresAt": self.token_expires_at})

    # -- revoke -----------------------------------------------------------
    def revoke(self, *, signalled: bool = False) -> str:
        """POST revoke once; ``revoked`` only on 200 plus the exact acknowledgment."""
        if self.revocation is not None:
            return self.revocation
        if self._revoking or self.token is None or self.grant is None:
            return "cleanup_unknown"
        self._revoking = True
        status = "cleanup_unknown"
        body = None
        response = None
        try:
            remaining = self.remaining()
            remaining = remaining if remaining is not None else 0.0
            if signalled:
                seconds = min(float(self.grant.destination.timeout_seconds), remaining, _REVOKE_SECONDS)
            else:
                seconds = min(float(self.grant.destination.timeout_seconds), _REVOKE_SECONDS,
                              remaining if remaining > 0 else _REVOKE_SECONDS)
            if seconds > 0:
                body = bytearray(json.dumps(
                    {"accessToken": self.token}, ensure_ascii=True, separators=(",", ":"),
                ).encode("ascii"))
                code, response = self._request("POST", REVOKE_PATH, body, auth=False, timeout=seconds)
                if code == 200:
                    try:
                        data = _decode_json(response)
                    except SecretBrokerError:
                        data = None
                    if (type(data) is dict and data.get("message") == REVOKE_ACKNOWLEDGMENT):
                        status = "revoked"
        except (_NotSent, _Uncertain, SecretBrokerError, OSError, ValueError):
            status = "cleanup_unknown"
        finally:
            wipe(body)
            wipe(response)
            self.token = None
            self.revocation = status
            self._revoking = False
        return status

    def on_sigterm(self, _signum, _frame) -> None:
        if not self._revoking:
            self.revoke(signalled=True)
        self.wipe()
        os._exit(1)

    def wipe(self) -> None:
        self.token = None
        self.gateway_headers.clear()

    # -- operations -------------------------------------------------------
    def _names(self, value: object) -> list[str]:
        if (type(value) is not list or not value
                or any(not isinstance(name, str) or not NAME_PATTERN.match(name) for name in value)
                or len(set(value)) != len(value)
                or not set(value) <= set(self.grant.names)):
            raise ProtocolError()
        return value

    def _name(self, value: object) -> str:
        return self._names([value])[0]

    def serve(self) -> int:
        while True:
            message = self.channel.recv_json()
            op = message.get("op")
            if op == "close" and set(message) == {"op"}:
                status = self.revoke()
                self.channel.send_json({"op": "closed", "revocation": status})
                return 0
            if op == "exists" and set(message) == {"op", "names"}:
                self._exists(self._names(message["names"]))
            elif op == "policy" and set(message) == {"op"}:
                # No value-free read-only policy call exists in verified sources.
                self.channel.send_json({"op": "policy", "requiresApproval": None})
            elif op == "create" and set(message) == {"op", "name", "comment"}:
                self._create(self._name(message["name"]), message["comment"])
            elif op == "readback" and set(message) == {"op", "name"}:
                self._readback(self._name(message["name"]))
            elif op == "read" and set(message) == {"op", "names"}:
                self._read(self._names(message["names"]))
            else:
                raise ProtocolError()

    def _exists(self, names: list[str]) -> None:
        result = {}
        owners = {}
        for name in names:
            state = "unknown"
            body = None
            try:
                status, body = self._request("GET", self._secret_target(name, view=False))
                if status == 200:
                    record = self._record(body, name)
                    # No nonempty hidden placeholder has yet been proven live.
                    # Accept only an absent/null/empty value, never usable bytes.
                    if (record is not None and record.get("secretValueHidden") is True
                            and (record.get("secretValue") is None or record.get("secretValue") == "")):
                        state = "present"
                        comment = record.get("secretComment")
                        match = re.fullmatch(
                            r"sandbox-ownership controller=([0-9a-f]{32}) run=run_([0-9a-f]{32})_[0-9a-f]{16}",
                            comment,
                        ) if isinstance(comment, str) else None
                        if match and match[1] == match[2]:
                            owners[name] = match[1]
                    record = None
                elif status == 404:
                    try:
                        if type(_decode_json(body)) is dict:
                            state = "absent"
                    except SecretBrokerError:
                        pass
            except (_NotSent, _Uncertain):
                state = "unknown"
            finally:
                wipe(body)
            result[name] = state
        self.channel.send_json({"op": "exists", "result": result, "owners": owners})

    def _create(self, name: str, comment: object) -> None:
        value = self.channel.recv_value()
        body = None
        text = None
        try:
            if (self.grant.capability != "create" or name in self.started
                    or not isinstance(comment, str) or len(comment) > _MAX_COMMENT
                    or any(ord(char) < 0x20 or ord(char) > 0x7E for char in comment)):
                raise ProtocolError()
            try:
                check_create_value(name, value)
            except SecretBrokerError:
                raise ProtocolError() from None
            destination = self.grant.destination
            text = bytes(value).decode("utf-8", errors="strict")
            body = bytearray(json.dumps(
                {
                    "projectId": destination.project_id,
                    "environment": destination.environment,
                    "secretPath": destination.path,
                    "secretValue": text,
                    "secretComment": comment,
                    "type": "shared",
                },
                ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8"))
            text = None
            self.started.add(name)
            # Request built; nothing sent until the parent journals 'attempted'.
            self.channel.send_json({"op": "ready", "name": name})
            decision = self.channel.recv_json()
            if decision == {"op": "abort"}:
                self._created(name, "failed", "aborted")
                return
            if decision != {"op": "go", "name": name}:
                raise ProtocolError()
            self.attempted.add(name)
            self._created(name, *self._post_create(name, body, value))
        finally:
            text = None
            wipe(value)
            wipe(body)

    def _post_create(self, name: str, body: bytearray, expected: bytearray) -> tuple[str, str | None]:
        response = None
        try:
            try:
                status, response = self._request(
                    "POST", SECRET_PREFIX + urllib.parse.quote(name, safe=""), body,
                )
            except _NotSent:
                return "failed", "not_sent"
            except _Uncertain:
                return "acceptance_unknown", "transport_uncertain"
            try:
                data = _decode_json(response)
            except SecretBrokerError:
                data = None
            if 200 <= status < 300 and type(data) is dict:
                if set(data) == {"secret"}:
                    record = self._record(response, name)
                    if record is not None:
                        if "secretValue" not in record:
                            return "created", None
                        returned = record["secretValue"]
                        if isinstance(returned, str):
                            encoded = None
                            try:
                                encoded = bytearray(returned.encode("utf-8", errors="strict"))
                                if hmac.compare_digest(expected, encoded):
                                    return "created", None
                            except UnicodeError:
                                pass
                            finally:
                                wipe(encoded)
                        return "acceptance_unknown", "response_unrecognized"
                if set(data) == {"approval"}:
                    return "unresolved", "approval_pending"
                return "acceptance_unknown", "response_unrecognized"
            if status == 400 and type(data) is dict:
                message = data.get("message")
                if isinstance(message, str) and message.startswith(f"Secret '{name}' already exists"):
                    return "conflict", "already_exists"
            return "acceptance_unknown", "response_unrecognized"
        finally:
            data = None
            record = None
            returned = None
            wipe(response)

    def _created(self, name: str, state: str, reason: str | None) -> None:
        self.channel.send_json({"op": "created", "name": name, "state": state, "reason": reason})

    def _fetch_value(self, name: str) -> bytearray | str:
        """Exact value bytes, or a fixed error code string."""
        body = None
        try:
            try:
                status, body = self._request("GET", self._secret_target(name, view=True))
            except (_NotSent, _Uncertain) as exc:
                return exc.code
            if status in (401, 403):
                return "provider_denied"
            if status == 404:
                return "item_unavailable"
            if status == 429:
                return "provider_rate_limited"
            if status != 200:
                return "provider_failed"
            record = self._record(body, name)
            if record is None or record.get("secretValueHidden", False) is not False:
                return "response_invalid"
            value = record.get("secretValue")
            record = None
            if not isinstance(value, str):
                return "response_invalid"
            try:
                encoded = bytearray(value.encode("utf-8", errors="strict"))
            except UnicodeEncodeError:
                return "response_invalid"
            finally:
                value = None
            if len(encoded) > MAX_PROVIDER_VALUE_BYTES:
                wipe(encoded)
                return "response_too_large"
            return encoded
        finally:
            wipe(body)

    def _readback(self, name: str) -> None:
        expected = self.channel.recv_value()
        fetched = None
        try:
            if self.grant.capability == "create" and name not in self.attempted:
                raise ProtocolError()
            fetched = self._fetch_value(name)
            equal = hmac.compare_digest(expected, fetched) if isinstance(fetched, bytearray) else None
            self.channel.send_json({"op": "readback", "name": name, "equal": equal})
        finally:
            if isinstance(fetched, bytearray):
                wipe(fetched)
            wipe(expected)

    def _read(self, names: list[str]) -> None:
        if self.grant.capability != "read":
            raise ProtocolError()
        for name in names:
            fetched = self._fetch_value(name)
            if isinstance(fetched, bytearray):
                text = None
                try:
                    text = fetched.decode("utf-8")
                    # Public output filtering drops these; fail closed like the source read path.
                    unsafe = any(unicodedata.category(char) in {"Cc", "Cf"} and char not in "\t\n\r"
                                 for char in text)
                finally:
                    text = None
                if unsafe:
                    wipe(fetched)
                    fetched = "response_invalid"
            if not isinstance(fetched, bytearray):
                self.channel.send_error(fetched)
                return
            buffer = fetched
            fetched = None
            try:
                self.channel.send_json({"op": "value", "name": name})
                self.channel.send_value(buffer)
            finally:
                wipe(buffer)
        self.channel.send_json({"op": "done"})


def _grant_from_frame(frame: dict) -> Grant:
    """Rebuild the grant; the destination comes from the ownership authority."""
    def mismatch() -> SecretBrokerError:
        return SecretBrokerError("authority_mismatch", "secret ownership grant does not match its authority")

    ownership = frame["ownership"]
    names = frame["names"]
    if type(ownership) is not dict or type(names) is not list:
        raise mismatch()
    config = OwnershipConfig.from_normalized(ownership)
    profile = config.profiles.get(frame["profile"]) if isinstance(frame["profile"], str) else None
    if profile is None:
        raise mismatch()
    destination = profile.destination
    if (
        frame["endpoint"] != destination.endpoint
        or frame["projectId"] != destination.project_id
        or frame["environment"] != destination.environment
        or frame["path"] != destination.path
        or frame["timeoutSeconds"] != destination.timeout_seconds
        or isinstance(frame["timeoutSeconds"], bool)
        or type(frame["timeoutSeconds"]) is not int
        or not 1 <= frame["timeoutSeconds"] <= 120
        or frame["gateway"] != destination.gateway
        or frame["gateway"] not in ("none", "cloudflare-access")
    ):
        raise mismatch()
    return Grant(
        profile=frame["profile"], capability=frame["capability"], destination=destination,
        client_id=frame["clientId"], authority_digest=frame["authorityDigest"],
        names=tuple(names), project_root=frame["projectRoot"], ownership=ownership,
    )


def _parse_login(response: bytearray) -> tuple[str, float]:
    data = _decode_json(response)
    if type(data) is not dict or set(data) != _LOGIN_KEYS:
        raise SecretBrokerError("response_invalid", "invalid")
    token = data["accessToken"]
    expires = data["expiresIn"]
    max_ttl = data["accessTokenMaxTTL"]
    if (
        not isinstance(token, str) or not token or len(token) > 8_192
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in token)
        or isinstance(expires, bool) or not isinstance(expires, (int, float))
        or not math.isfinite(expires) or expires <= 0
        or isinstance(max_ttl, bool) or not isinstance(max_ttl, (int, float))
        or not math.isfinite(max_ttl) or max_ttl <= 0
        or data["tokenType"] != "Bearer"
    ):
        raise SecretBrokerError("response_invalid", "invalid")
    return token, float(expires)


def main() -> None:
    read_fd = sys.stdin.fileno()
    write_fd = os.dup(sys.stdout.fileno())
    # Nothing but frames may reach the protocol pipe.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.close(devnull)
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    channel = FrameChannel(read_fd, write_fd, remaining=lambda: worker.remaining())
    worker = _Worker(channel)
    exit_code = 1
    try:
        signal.signal(signal.SIGTERM, worker.on_sigterm)
        worker.open()
        exit_code = worker.serve()
    except (ProtocolError, WorkerError):
        try:
            channel.send_error("provider_failed")
        except Exception:
            pass
    except SecretBrokerError as exc:
        try:
            channel.send_error(exc.code)
        except Exception:
            pass
    except BaseException:
        try:
            channel.send_error("provider_failed")
        except Exception:
            pass
    finally:
        if worker.revocation is None and worker.token is not None:
            worker.revoke()
        worker.wipe()
    os._exit(exit_code)


if __name__ == "__main__":
    main()

"""Exact-item Infisical adapter using Universal Auth for one broker operation."""

from __future__ import annotations

import json
import math
import socket
import ssl
import urllib.error
import urllib.parse
import unicodedata
from collections.abc import Mapping
from typing import Any

from ..models import SecretBrokerError
from ..provider_identity import canonical_https_origin
from .base import (
    CapabilityStatus,
    CredentialHandle,
    Deadline,
    HttpResponse,
    MAX_PROVIDER_VALUE_BYTES,
    ProviderError,
    ProviderPolicy,
    ProviderTransport,
)
from .http import BoundedHTTPTransport


_STATUS_PATH = "/api/status"
_LOGIN_PATH = "/api/v1/auth/universal-auth/login"
_SECRET_PREFIX = "/api/v4/secrets/"


def _failure_for_exception(exc: BaseException, *, stage: str) -> str:
    code = getattr(exc, "code", None)
    stable_codes = {
        "provider_unsupported", "credential_backend_unavailable", "credential_locked",
        "credential_missing", "credential_store_failed", "endpoint_unreachable",
        "endpoint_untrusted", "authentication_denied", "provider_denied",
        "item_unavailable", "provider_rate_limited", "provider_failed",
        "response_invalid", "response_too_large", "operation_timeout",
        "capacity_exhausted",
    }
    if code in stable_codes:
        return code
    observed: BaseException | None = exc
    for _ in range(4):
        if observed is None:
            break
        if isinstance(observed, (ssl.SSLError, ssl.CertificateError)):
            return "endpoint_untrusted"
        if isinstance(observed, (TimeoutError, socket.timeout)):
            return "operation_timeout"
        if isinstance(observed, urllib.error.URLError):
            observed = observed.reason if isinstance(observed.reason, BaseException) else None
            continue
        if isinstance(observed, (socket.gaierror, ConnectionError, OSError)):
            return "endpoint_unreachable"
        observed = observed.__cause__ or observed.__context__
    return "credential_store_failed" if stage == "credential" else "provider_failed"


def _raise_provider_error(code: str) -> None:
    raise ProviderError(code)


def _safe_call(callback, *, stage: str):
    result = None
    failure: str | None = None
    try:
        result = callback()
    except Exception as exc:
        failure = _failure_for_exception(exc, stage=stage)
    if failure is not None:
        _raise_provider_error(failure)
    return result


def _wipe(buffer: bytearray | None) -> None:
    if buffer is None:
        return
    for index in range(len(buffer)):
        buffer[index] = 0
    buffer.clear()


def _string_field(value: object, *, max_bytes: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ProviderError("provider_unsupported")
    encoded: bytes | None = None
    invalid_encoding = False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        invalid_encoding = True
    if invalid_encoding or encoded is None:
        raise ProviderError("provider_unsupported")
    if len(encoded) > max_bytes or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ProviderError("provider_unsupported")
    return value


def _origin(endpoint: object) -> str:
    endpoint = _string_field(endpoint, max_bytes=2_048)
    try:
        return canonical_https_origin(endpoint)
    except ValueError:
        raise ProviderError("provider_unsupported")


def _normalized_policy(descriptor: object) -> tuple[ProviderPolicy, str]:
    policy = ProviderPolicy.from_descriptor(descriptor)
    origin = _origin(policy.endpoint)
    for value in (policy.project_id, policy.environment, policy.key, policy.client_id):
        _string_field(value, max_bytes=512)
    path = _string_field(policy.path, max_bytes=1_024)
    parts = path.split("/")
    if (
        not path.startswith("/")
        or "\\" in path
        or "//" in path
        or (len(parts) > 1 and parts[-1] == "" and path != "/")
        or any(part in {".", ".."} for part in parts)
    ):
        raise ProviderError("provider_unsupported")
    if (
        isinstance(policy.timeout_seconds, bool)
        or not isinstance(policy.timeout_seconds, (int, float))
        or not math.isfinite(policy.timeout_seconds)
        or not 1 <= policy.timeout_seconds <= 1_800
    ):
        raise ProviderError("provider_unsupported")
    return policy, origin


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _decode_json(body: bytes | bytearray) -> object:
    parsed: object = None
    invalid = False
    text: str | None = None
    try:
        text = bytes(body).decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        invalid = True
    finally:
        text = None
    if invalid:
        raise ProviderError("response_invalid")
    return parsed


def _response_object(response: HttpResponse) -> dict[str, object]:
    parsed = _decode_json(response.body)
    if type(parsed) is not dict:
        raise ProviderError("response_invalid")
    return parsed


def _status_code(status: int, *, stage: str) -> str | None:
    if status == 200:
        return None
    if status in {401, 403}:
        return "authentication_denied" if stage == "login" else "provider_denied"
    if status == 404:
        return "item_unavailable" if stage == "secret" else "provider_unsupported"
    if status == 429:
        return "provider_rate_limited"
    if 500 <= status <= 599:
        return "provider_failed"
    if 300 <= status <= 399:
        return "endpoint_untrusted"
    if 400 <= status <= 499:
        return "response_invalid" if stage == "secret" else "provider_unsupported"
    if 200 <= status <= 299:
        return "provider_unsupported"
    return "provider_failed"


def _response_error(response: HttpResponse, *, stage: str) -> None:
    code = _status_code(response.status, stage=stage)
    if code is not None:
        raise ProviderError(code)


def _login_request_body(client_id: str, client_secret: bytearray) -> bytearray:
    secret_text: str | None = None
    serialized: str | None = None
    result: bytearray | None = None
    invalid_secret = False
    try:
        secret_text = bytes(client_secret).decode("utf-8", errors="strict")
        if not secret_text:
            raise ProviderError("credential_missing")
        serialized = json.dumps(
            {"clientId": client_id, "clientSecret": secret_text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = bytearray(serialized.encode("utf-8", errors="strict"))
    except ProviderError:
        raise
    except (UnicodeDecodeError, UnicodeEncodeError, ValueError):
        invalid_secret = True
    finally:
        secret_text = None
        serialized = None
    if invalid_secret or result is None:
        raise ProviderError("credential_store_failed")
    return result


def _parse_access_token(response: HttpResponse) -> str:
    data = _response_object(response)
    allowed = {"accessToken", "expiresIn", "accessTokenMaxTTL", "tokenType"}
    if set(data) != allowed:
        raise ProviderError("provider_unsupported")
    token = data["accessToken"]
    expires = data["expiresIn"]
    max_ttl = data["accessTokenMaxTTL"]
    token_type = data.get("tokenType")
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 8_192
        or not token.isascii()
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in token)
        or isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not math.isfinite(expires)
        or expires <= 0
        or isinstance(max_ttl, bool)
        or not isinstance(max_ttl, (int, float))
        or not math.isfinite(max_ttl)
        or max_ttl <= 0
        or token_type != "Bearer"
    ):
        raise ProviderError("response_invalid")
    return token


def _parse_exact_item(response: HttpResponse, policy: ProviderPolicy) -> bytes:
    data = _response_object(response)
    if set(data) != {"secret"}:
        raise ProviderError("provider_unsupported")
    record = data["secret"]
    if type(record) is not dict:
        raise ProviderError("response_invalid")
    if (
        record.get("workspace") != policy.project_id
        or record.get("environment") != policy.environment
        or record.get("secretPath") != policy.path
        or record.get("secretKey") != policy.key
        or record.get("type") != "shared"
    ):
        raise ProviderError("response_invalid")
    if record.get("secretValueHidden", False) is not False:
        raise ProviderError("provider_denied")
    value = record.get("secretValue")
    if not isinstance(value, str):
        raise ProviderError("response_invalid")
    if any(unicodedata.category(char) in {"Cc", "Cf"} and char not in "\t\n\r"
           for char in value):
        # Public output filtering removes these code points. Accepting them in
        # an exact redaction needle could therefore expose the transformed
        # value. Fail closed at the provider boundary.
        raise ProviderError("response_invalid")
    encoded: bytes | None = None
    invalid_encoding = False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        invalid_encoding = True
    if invalid_encoding or encoded is None:
        value = ""
        record = {}
        data = {}
        raise ProviderError("response_invalid")
    if len(encoded) > MAX_PROVIDER_VALUE_BYTES:
        value = ""
        encoded = b""
        record = {}
        data = {}
        raise ProviderError("response_too_large")
    return encoded


def _secret_url(origin: str, policy: ProviderPolicy) -> str:
    # A secret key is one route segment. Slash, percent, and UTF-8 bytes are
    # encoded so the registered name cannot alter the route structure.
    name = urllib.parse.quote(policy.key, safe="", encoding="utf-8", errors="strict")
    query = urllib.parse.urlencode(
        (
            ("projectId", policy.project_id),
            ("environment", policy.environment),
            ("secretPath", policy.path),
            ("type", "shared"),
            ("expandSecretReferences", "false"),
            ("includeImports", "false"),
        ),
        doseq=False,
        safe="",
        encoding="utf-8",
        errors="strict",
        quote_via=urllib.parse.quote,
    )
    return f"{origin}{_SECRET_PREFIX}{name}?{query}"


class InfisicalProvider:
    """One Universal Auth login and one exact v4 read per call."""

    def __init__(self, transport: ProviderTransport | None = None) -> None:
        self._transport = transport or BoundedHTTPTransport()

    def status(self, descriptor: object, *, timeout_seconds: float | None = None) -> CapabilityStatus:
        """Check unauthenticated availability only; never inspect credentials or values."""

        try:
            policy, origin = _normalized_policy(descriptor)
            deadline = Deadline(
                policy.timeout_seconds if timeout_seconds is None else timeout_seconds,
            )
            response = _safe_call(
                lambda: self._transport.request(
                    "GET",
                    f"{origin}{_STATUS_PATH}",
                    headers={},
                    body=None,
                    deadline=deadline,
                    capture_body=False,
                ),
                stage="transport",
            )
            try:
                _response_error(response, stage="status")
                deadline.check()
                return CapabilityStatus(state="available", endpoint_reachable=True)
            finally:
                response.discard()
        except ProviderError as exc:
            code = _failure_for_exception(exc, stage="transport")
        except Exception:
            code = "provider_failed"
        state = {
            "operation_timeout": "timed_out",
            "endpoint_unreachable": "unreachable",
            "endpoint_untrusted": "untrusted",
            "provider_unsupported": "unsupported",
        }.get(code, "unavailable")
        return CapabilityStatus(
            state=state,
            endpoint_reachable=False if code != "provider_unsupported" else None,
            error_code=code,
        )

    def resolve_exact(self, descriptor: object, credential: CredentialHandle) -> bytes:
        """Resolve the sole registered item with memory-only per-operation authority."""

        policy: ProviderPolicy | None = None
        deadline: Deadline | None = None
        client_secret: bytearray | None = None
        login_body: bytearray | None = None
        login_response: HttpResponse | None = None
        read_response: HttpResponse | None = None
        access_token: str | None = None
        authorization: str | None = None
        result: bytes | None = None
        failure_code: str | None = None
        raw_secret: object | None = None
        reader = None
        try:
            # Start before descriptor validation and credential access so every
            # provider step consumes the same finite operation budget.
            raw_timeout = _descriptor_timeout(descriptor)
            deadline = Deadline(raw_timeout)
            policy, origin = _normalized_policy(descriptor)
            reader = getattr(credential, "get_secret", None)
            if not callable(reader):
                raise ProviderError("credential_store_failed")
            raw_secret = _safe_call(lambda: reader(deadline), stage="credential")
            if not isinstance(raw_secret, (bytes, bytearray, memoryview)):
                raise ProviderError("credential_store_failed")
            client_secret = bytearray(raw_secret)
            if not client_secret:
                raise ProviderError("credential_missing")
            if len(client_secret) > MAX_PROVIDER_VALUE_BYTES:
                raise ProviderError("credential_store_failed")
            deadline.check()

            login_body = _login_request_body(policy.client_id, client_secret)
            login_response = _safe_call(
                lambda: self._transport.request(
                    "POST",
                    f"{origin}{_LOGIN_PATH}",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    body=login_body,
                    deadline=deadline,
                ),
                stage="transport",
            )
            _response_error(login_response, stage="login")
            access_token = _parse_access_token(login_response)
            login_response.discard()
            login_response = None
            _wipe(login_body)
            login_body = None
            _wipe(client_secret)
            client_secret = None

            deadline.check()
            authorization = f"Bearer {access_token}"
            read_response = _safe_call(
                lambda: self._transport.request(
                    "GET",
                    _secret_url(origin, policy),
                    headers={"Accept": "application/json", "Authorization": authorization},
                    body=None,
                    deadline=deadline,
                ),
                stage="transport",
            )
            _response_error(read_response, stage="secret")
            result = _parse_exact_item(read_response, policy)
            deadline.check()
        except SecretBrokerError as exc:
            failure_code = _failure_for_exception(exc, stage="credential")
        except Exception as exc:
            failure_code = _failure_for_exception(exc, stage="transport")
        finally:
            _wipe(client_secret)
            _wipe(login_body)
            if login_response is not None:
                login_response.discard()
            if read_response is not None:
                read_response.discard()
            raw_secret = None
            reader = None
            access_token = None
            authorization = None
        if deadline is not None:
            try:
                deadline.check()
            except ProviderError:
                failure_code = "operation_timeout"
                result = None
        if failure_code is not None:
            result = None
            raise ProviderError(failure_code)
        return result if result is not None else b""


def _descriptor_timeout(descriptor: object) -> int | float:
    if isinstance(descriptor, Mapping):
        value = descriptor.get("timeout_seconds", descriptor.get("timeoutSeconds", 10))
    else:
        value = getattr(descriptor, "timeout_seconds", getattr(descriptor, "timeoutSeconds", 10))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError("provider_unsupported")
    return value

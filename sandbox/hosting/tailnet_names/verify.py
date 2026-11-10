"""TLS probes for tailnet names (spec 078), run on the controller.

A probe connects with the name as SNI and verification off (the controller's
CA bundle is not trusted to exist), records why it stopped in one typed
category, and decodes the presented leaf: its SHA-256 and the names it
covers.

The public check passes only on a conclusive refusal (connection refused,
reset or closed, or a TLS alert) or on a completed handshake whose leaf is
not ours and does not cover the name. A timeout, an unreachable route, a DNS
failure, any other error, or a leaf that cannot be decoded is inconclusive
and fails the check (FR-018).
"""
from __future__ import annotations

import errno
import hashlib
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Literal

from sandbox.hosting.tailnet_names import x509
from sandbox.hosting.tailnet_names.models import (
    PUBLIC_EXPOSED,
    PUBLIC_PROBE_INCONCLUSIVE,
    TAILNET_PROBE_FAILED,
    StepOutcome,
)

TIMEOUT_SECONDS = 10.0
_STATUS = re.compile(rb"HTTP/\d(?:\.\d)? (\d{3})")
_UNREACHABLE = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN,
                getattr(errno, "EHOSTDOWN", errno.EHOSTUNREACH)}

Category = Literal["handshake", "refused", "reset", "closed", "tls_alert",
                   "timeout", "unreachable", "dns", "tls_error", "error"]
REFUSALS = frozenset({"refused", "reset", "closed", "tls_alert"})


@dataclass(frozen=True)
class ProbeResult:
    category: Category
    leaf_sha256: str | None = None
    leaf_names: tuple[str, ...] | None = None  # None: the leaf could not be decoded
    http_status: int | None = None
    detail: str | None = None

    @property
    def handshake(self) -> bool:
        return self.category == "handshake"


def _connect_category(exc: BaseException) -> Category:
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "reset"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, OSError) and exc.errno in _UNREACHABLE:
        return "unreachable"
    return "error"


def _tls_category(exc: BaseException) -> Category:
    if isinstance(exc, (ssl.SSLZeroReturnError, ssl.SSLEOFError)):
        return "closed"
    if isinstance(exc, ssl.SSLError):
        reason = str(getattr(exc, "reason", "") or exc).upper()
        if "ALERT" in reason or "UNRECOGNIZED_NAME" in reason:
            return "tls_alert"
        return "tls_error"
    return _connect_category(exc)


def probe(address: str, fqdn: str, *, port: int = 443, timeout: float = TIMEOUT_SECONDS,
          send_request: bool = True) -> ProbeResult:
    try:
        raw = socket.create_connection((address, port), timeout=timeout)
    except OSError as exc:
        return ProbeResult(_connect_category(exc), detail=f"connect: {type(exc).__name__}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["http/1.1"])
    try:
        with context.wrap_socket(raw, server_hostname=fqdn) as tls:
            der = tls.getpeercert(binary_form=True) or b""
            leaf = hashlib.sha256(der).hexdigest() if der else None
            try:
                names = x509.parse_leaf(der).names if der else None
            except ValueError:
                names = None
            status = None
            if send_request:
                tls.sendall((f"GET / HTTP/1.1\r\nHost: {fqdn}\r\n"
                             "User-Agent: sandbox-tailnet-name\r\nConnection: close\r\n\r\n").encode())
                head = b""
                while b"\r\n" not in head and len(head) < 1024:
                    chunk = tls.recv(256)
                    if not chunk:
                        break
                    head += chunk
                match = _STATUS.match(head)
                status = int(match.group(1)) if match else None
            return ProbeResult("handshake", leaf, names, status)
    except (ssl.SSLError, OSError) as exc:
        return ProbeResult(_tls_category(exc), detail=f"tls: {type(exc).__name__}")
    finally:
        raw.close()


def tailnet_outcome(result: ProbeResult, expected_sha256: str | None) -> StepOutcome:
    if not result.handshake:
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED,
                           f"{result.category}: {result.detail}")
    if not expected_sha256 or result.leaf_sha256 != expected_sha256:
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED,
                           "the tailnet address presented a different certificate")
    if result.http_status is None:
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED, "no HTTP response")
    if result.http_status in (502, 504):
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED,
                           f"HTTP {result.http_status}: the upstream did not answer")
    return StepOutcome("verify_tailnet", "done", detail=f"HTTP {result.http_status}")


@dataclass(frozen=True)
class PublicVerdict:
    outcome: StepOutcome
    exposed: bool          # a certificate valid for the name (or ours) is served publicly
    ours: bool             # ... and it is the installed leaf


def public_outcome(result: ProbeResult, expected_sha256: str | None, fqdn: str) -> PublicVerdict:
    if result.category in REFUSALS:
        return PublicVerdict(StepOutcome("verify_public", "done",
                                         detail=f"public address refused ({result.category})"),
                             False, False)
    if not result.handshake:
        return PublicVerdict(StepOutcome(
            "verify_public", "failed", PUBLIC_PROBE_INCONCLUSIVE,
            f"the public check was inconclusive ({result.category}); a refusal could not be "
            "established from this controller"), False, False)
    ours = bool(expected_sha256) and result.leaf_sha256 == expected_sha256
    if ours or (result.leaf_names is not None and x509.covers(result.leaf_names, fqdn)):
        what = "this name's installed certificate" if ours else \
            "another certificate valid for this name"
        return PublicVerdict(StepOutcome("verify_public", "failed", PUBLIC_EXPOSED,
                                         f"the public address presented {what}"), True, ours)
    if result.leaf_names is None:
        return PublicVerdict(StepOutcome(
            "verify_public", "failed", PUBLIC_PROBE_INCONCLUSIVE,
            "the public address presented a certificate that could not be decoded"), False, False)
    return PublicVerdict(StepOutcome("verify_public", "done",
                                     detail="the public address presented a certificate "
                                            "not valid for this name"), False, False)

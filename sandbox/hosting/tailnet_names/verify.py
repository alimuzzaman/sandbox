"""TLS probes for tailnet names (spec 078), run on the controller.

A probe connects with the name as SNI and verification off, and fingerprints
the presented leaf. Comparing that with the installed leaf proves "our
certificate on this socket" without relying on the controller's CA bundle.
"""
from __future__ import annotations

import hashlib
import re
import socket
import ssl
from dataclasses import dataclass

from sandbox.hosting.tailnet_names.models import (
    PUBLIC_EXPOSED,
    TAILNET_PROBE_FAILED,
    StepOutcome,
)

TIMEOUT_SECONDS = 10.0
_STATUS = re.compile(rb"HTTP/\d(?:\.\d)? (\d{3})")


@dataclass(frozen=True)
class ProbeResult:
    connected: bool
    handshake: bool
    leaf_sha256: str | None
    http_status: int | None
    error: str | None


def probe(address: str, fqdn: str, *, port: int = 443, timeout: float = TIMEOUT_SECONDS,
          send_request: bool = True) -> ProbeResult:
    try:
        raw = socket.create_connection((address, port), timeout=timeout)
    except OSError as exc:
        return ProbeResult(False, False, None, None, f"connect failed: {type(exc).__name__}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["http/1.1"])
    try:
        with context.wrap_socket(raw, server_hostname=fqdn) as tls:
            der = tls.getpeercert(binary_form=True)
            leaf = hashlib.sha256(der).hexdigest() if der else None
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
            return ProbeResult(True, True, leaf, status, None)
    except (ssl.SSLError, OSError) as exc:
        return ProbeResult(True, False, None, None, f"handshake failed: {type(exc).__name__}")
    finally:
        raw.close()


def tailnet_outcome(result: ProbeResult, expected_sha256: str | None) -> StepOutcome:
    if not result.handshake:
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED, result.error)
    if not expected_sha256 or result.leaf_sha256 != expected_sha256:
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED,
                           "the tailnet address presented a different certificate")
    if result.http_status is None:
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED, "no HTTP response")
    if result.http_status in (502, 504):
        return StepOutcome("verify_tailnet", "failed", TAILNET_PROBE_FAILED,
                           f"HTTP {result.http_status}: the upstream did not answer")
    return StepOutcome("verify_tailnet", "done", detail=f"HTTP {result.http_status}")


def public_outcome(result: ProbeResult, expected_sha256: str | None) -> tuple[StepOutcome, bool]:
    """(outcome, exposed): exposed only when the public address shows our leaf."""
    if not result.connected:
        return StepOutcome("verify_public", "done", detail="connection refused"), False
    if not result.handshake:
        return StepOutcome("verify_public", "done", detail="handshake refused"), False
    if expected_sha256 and result.leaf_sha256 == expected_sha256:
        return StepOutcome("verify_public", "failed", PUBLIC_EXPOSED,
                           "the public address presented this name's certificate"), True
    return StepOutcome("verify_public", "done", detail="another certificate was presented"), False

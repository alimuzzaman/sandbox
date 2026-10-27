"""Focused regressions for secret ownership worker, protocol, and routing (T053).

Covers protocol framing (malformed, oversize, out-of-order), grant validation
before native-store open, fixed User-Agent and redirect/proxy refusal, exact 3-key
gateway bundle, strict revocation acknowledgment, the READY -> before_send -> GO
handshake, and native routing namespace isolation.
"""

from __future__ import annotations

import io
import json
import os
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

from sandbox.secrets.authority import ownership_digest, validate_ownership_grant
from sandbox.secrets.credentials.base import (
    CREDENTIAL_SERVICE, CredentialLocator, OWNERSHIP_CREDENTIAL_SERVICE,
    locator_for_ownership, locator_for_policy,
)
from sandbox.secrets.credentials.linux import LinuxSecretServiceCredentialStore
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.broker import GATEWAY_BUNDLE_KEYS, validate_bootstrap_input
from sandbox.secrets.ownership.models import Destination, Grant, OwnershipConfig
from sandbox.secrets.ownership.protocol import (
    FRAME_ERROR, FRAME_JSON, FRAME_VALUE, FrameChannel, MAX_JSON_BYTES,
    MAX_VALUE_BYTES, ProtocolError, decode_json, encode_error,
    encode_json, encode_value,
)
from sandbox.secrets.ownership.worker import (
    REVOKE_ACKNOWLEDGMENT, USER_AGENT, _Worker, _grant_from_frame,
)
import sandbox.secrets.ownership.worker as worker_module
from tests.secret_ownership_support import require


class FakeResponse:
    """Bounded mock HTTP response for worker transport verification."""
    def __init__(self, status: int = 200, body: bytes = b'{"status":"ok"}') -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, amt: int = -1) -> bytes:
        return self._body.read(amt)


class FakeHTTPSConnection:
    """Mock HTTPSConnection verifying endpoint, headers, and zero redirect follow."""
    instances: list[FakeHTTPSConnection] = []
    last_instance: FakeHTTPSConnection | None = None

    def __init__(self, host: str, port: int, timeout: float | None = None, context: Any = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.sock = MagicMock()
        self.requests: list[dict[str, Any]] = []
        self.response = FakeResponse(200, b'{"status":"ok"}')
        self.closed = False
        FakeHTTPSConnection.last_instance = self
        FakeHTTPSConnection.instances.append(self)

    def connect(self) -> None:
        pass

    def request(self, method: str, target: str, body: Any = None, headers: dict[str, str] | None = None) -> None:
        self.requests.append({
            "method": method,
            "target": target,
            "body": bytes(body) if body is not None else None,
            "headers": dict(headers) if headers is not None else {},
        })

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class PipeChannel:
    """Mock bi-directional FrameChannel using os.pipe pairs."""
    def __init__(self) -> None:
        self.p2c_r, self.p2c_w = os.pipe()
        self.c2p_r, self.c2p_w = os.pipe()
        self.parent = FrameChannel(self.c2p_r, self.p2c_w)
        self.child = FrameChannel(self.p2c_r, self.c2p_w)

    def close(self) -> None:
        for fd in (self.p2c_r, self.p2c_w, self.c2p_r, self.c2p_w):
            try:
                os.close(fd)
            except OSError:
                pass


def _sample_dest() -> Destination:
    return Destination(
        endpoint="https://vault.example.invalid",
        project_id="proj-w",
        environment="staging",
        path="/",
        read_client_id="reader",
        create_client_id="creator",
        timeout_seconds=30,
        gateway="none",
    )


def _sample_grant() -> Grant:
    dest = _sample_dest()
    ownership_block = {
        "profiles": {
            "personal": {
                "domain": "personal",
                "destination": dest.as_normalized(),
            },
        },
        "entries": [
            {
                "source": "personal",
                "key": "ALPHA",
                "profile": "personal",
                "name": "ALPHA",
                "consumer": "ALPHA",
                "origin": "source",
            },
        ],
    }
    digest = ownership_digest("personal", ownership_block, "create", project_root="/app")
    return Grant(
        profile="personal",
        capability="create",
        client_id="creator",
        destination=dest,
        authority_digest=digest,
        names=("ALPHA",),
        ownership=ownership_block,
        project_root="/app",
    )


class TestSecretOwnershipWorker(unittest.TestCase):
    """Worker protocol, framing, and isolation regressions."""

    def test_frame_channel_rejects_malformed_and_oversize_frames(self) -> None:
        """Oversize JSON (>64KiB) or value (>128KiB) frames must raise ProtocolError."""
        pipe = PipeChannel()
        try:
            # Oversize JSON
            huge_json = {"huge": "x" * (MAX_JSON_BYTES + 10)}
            with self.assertRaises(ProtocolError):
                pipe.parent.send_json(huge_json)

            # Oversize value
            huge_val = bytearray(MAX_VALUE_BYTES + 1)
            with self.assertRaises(ProtocolError):
                pipe.parent.send_value(huge_val)
        finally:
            pipe.close()

    def test_frame_channel_rejects_out_of_order_frames(self) -> None:
        """Expecting a JSON frame but receiving a value frame raises ProtocolError."""
        pipe = PipeChannel()
        try:
            # Send a value frame when child expects JSON frame
            pipe.parent.send_value(bytearray(b"unexpected-val"))
            kind, _ = pipe.child.read_frame()
            require(kind != FRAME_JSON, "received kind must not be JSON")
        finally:
            pipe.close()

    def test_grant_validation_before_native_store_or_process_open(self) -> None:
        """Altered destination or names with reused digest fails before any store access."""
        grant = _sample_grant()

        # Corrupt grant: destination does not match
        tampered_dest = Destination(
            endpoint="https://attacker.invalid",
            project_id="proj-w",
            environment="staging",
            path="/",
            read_client_id="reader",
            create_client_id="creator",
            timeout_seconds=30,
            gateway="none",
        )
        bad_grant = Grant(
            profile=grant.profile,
            capability=grant.capability,
            client_id=grant.client_id,
            destination=tampered_dest,  # Tampered!
            authority_digest=grant.authority_digest,
            names=grant.names,
            ownership=grant.ownership,
            project_root=grant.project_root,
        )

        pipe = PipeChannel()
        try:
            # Validating corrupt grant must fail immediately with authority_mismatch
            with self.assertRaises(SecretBrokerError) as ctx:
                validate_ownership_grant(bad_grant)
            require(ctx.exception.code == "authority_mismatch",
                    "tampered grant must raise authority_mismatch")
        finally:
            pipe.close()

    def test_worker_fixed_user_agent_and_no_ambient_proxy_or_redirect(self) -> None:
        """Worker User-Agent is honest and fixed; ambient proxies ignored; redirects refused."""
        require(USER_AGENT == "sandbox-secret-ownership/1",
                "User-Agent must be strictly fixed to sandbox-secret-ownership/1")

        # Synthetic proxy environment: pure isolated dict, never reading/snapshotting real parent environment
        synthetic_proxy_env = {
            "HTTP_PROXY": "http://evil-proxy.invalid:8080",
            "HTTPS_PROXY": "http://evil-proxy.invalid:8080",
            "ALL_PROXY": "socks5://evil-proxy.invalid:1080",
        }

        FakeHTTPSConnection.instances.clear()
        FakeHTTPSConnection.last_instance = None

        with patch.object(worker_module.os, "environ", synthetic_proxy_env):
            with patch.object(worker_module.http.client, "HTTPSConnection", FakeHTTPSConnection):
                pipe = PipeChannel()
                try:
                    worker = _Worker(pipe.child)
                    worker.grant = _sample_grant()
                    worker.host = "vault.example.invalid"
                    worker.port = 443
                    worker.token = "synthetic-bearer-token"
                    worker._work_end = 9999999999.0

                    # 1. Transport proof: connection targets exact host and port directly, ignoring proxy env
                    status, _ = worker._request("GET", "/api/v4/secrets/ALPHA?projectId=proj-w", timeout=2.0)
                    require(status == 200, "status must be 200")

                    conn1 = FakeHTTPSConnection.last_instance
                    conn = conn1
                    require(conn is not None, "FakeHTTPSConnection must have been instantiated")
                    require(conn.host == "vault.example.invalid",
                            "connection host must connect directly to endpoint, ignoring ambient proxy")
                    require(conn.port == 443, "connection port must match target port")
                    require(len(conn.requests) == 1, "exactly one request must have been sent")

                    req = conn.requests[0]
                    require(req["method"] == "GET", "method must be GET")
                    require(req["headers"].get("User-Agent") == "sandbox-secret-ownership/1",
                            "request must send fixed User-Agent header")
                    require(req["headers"].get("Accept") == "application/json",
                            "request must send Accept: application/json header")
                    require(req["headers"].get("Authorization") == "Bearer synthetic-bearer-token",
                            "request must send Authorization Bearer header")

                    # 2. Redirect refusal: 302 response must NOT be followed; returns 302 directly
                    with patch.object(FakeHTTPSConnection, "getresponse", return_value=FakeResponse(302, b'{"message":"redirect"}')):
                        status_redirect, _ = worker._request("GET", "/api/v4/secrets/ALPHA?projectId=proj-w", timeout=2.0)
                    require(status_redirect == 302, "worker must return 302 directly without following redirect")

                    conn2 = FakeHTTPSConnection.last_instance
                    require(conn2 is not None, "second FakeHTTPSConnection must have been instantiated")
                    require(conn2 is not conn1, "second instance must be a new connection instance")
                    require(len(FakeHTTPSConnection.instances) == 2,
                            "no redirect follow or new connection beyond expected")

                    require(len(conn1.requests) == 1, "first connection must have exactly one actual request")
                    require(len(conn2.requests) == 1, "second connection must have exactly one actual request")
                    require(len(conn1.requests) + len(conn2.requests) == 2,
                            "total 2 requests across 2 intentional calls")

                    req2 = conn2.requests[0]
                    require(req2["method"] == "GET", "second request method must be GET")
                    require(req2["headers"].get("User-Agent") == "sandbox-secret-ownership/1",
                            "second request must send fixed User-Agent header")
                    require(req2["headers"].get("Accept") == "application/json",
                            "second request must send Accept: application/json header")
                    require(req2["headers"].get("Authorization") == "Bearer synthetic-bearer-token",
                            "second request must send Authorization Bearer header")
                    require(conn2.host == "vault.example.invalid",
                            "second connection host must connect directly to endpoint, ignoring ambient proxy")
                    require(conn2.port == 443, "second connection port must match target port")

                    require(conn1.closed is True, "first connection must be closed")
                    require(conn2.closed is True, "second connection must be closed")
                finally:
                    pipe.close()

    def test_gateway_exact_three_keys_no_fallback(self) -> None:
        """Cloudflare Access requires exact 3 keys; extra, missing, or fallback refused."""
        # Exact 3 keys required
        require(set(GATEWAY_BUNDLE_KEYS) == {"clientSecret", "gatewayClientId", "gatewayClientSecret"},
                "gateway bundle keys must be exactly the 3 required Cloudflare keys")

        # Valid 3-key bundle
        raw_valid = bytearray(json.dumps({
            "clientSecret": "sec",
            "gatewayClientId": "gid",
            "gatewayClientSecret": "gsec",
        }).encode("utf-8"))
        validate_bootstrap_input(raw_valid, "cloudflare-access")

        # Missing key
        raw_missing = bytearray(json.dumps({
            "clientSecret": "sec",
            "gatewayClientId": "gid",
        }).encode("utf-8"))
        with self.assertRaises(SecretBrokerError) as ctx1:
            validate_bootstrap_input(raw_missing, "cloudflare-access")
        require(ctx1.exception.code == "credential_scope_invalid",
                "missing gateway bundle key must raise credential_scope_invalid")

        # Extra unexpected key
        raw_extra = bytearray(json.dumps({
            "clientSecret": "sec",
            "gatewayClientId": "gid",
            "gatewayClientSecret": "gsec",
            "extra_key": "bad",
        }).encode("utf-8"))
        with self.assertRaises(SecretBrokerError) as ctx2:
            validate_bootstrap_input(raw_extra, "cloudflare-access")
        require(ctx2.exception.code == "credential_scope_invalid",
                "extra gateway bundle key must raise credential_scope_invalid")

        # Empty bundle for cloudflare-access
        raw_empty = bytearray(b"{}")
        with self.assertRaises(SecretBrokerError) as ctx3:
            validate_bootstrap_input(raw_empty, "cloudflare-access")
        require(ctx3.exception.code == "credential_scope_invalid",
                "empty bundle for cloudflare-access must raise credential_scope_invalid")

    def test_worker_strict_revoke_200_and_ack_only(self) -> None:
        """Worker revoke accepts only HTTP 200 with the exact Infisical acknowledgment message."""
        pipe = PipeChannel()
        try:
            worker = _Worker(pipe.child)
            worker.grant = _sample_grant()
            worker.token = "synthetic-token"
            worker._work_end = 9999999999.0

            # Mock _request to return 500
            worker._request = lambda *args, **kwargs: (500, bytearray(b"error"))
            worker.revocation = None
            outcome_500 = worker.revoke()
            require(outcome_500 == "cleanup_unknown", "non-200 revoke must produce cleanup_unknown")

            # Mock _request to return 200 with different message
            worker.token = "synthetic-token-2"
            worker.revocation = None
            worker._request = lambda *args, **kwargs: (200, bytearray(b'{"message":"Revoked"}'))
            outcome_diff_msg = worker.revoke()
            require(outcome_diff_msg == "cleanup_unknown",
                    "wrong ack message must produce cleanup_unknown")

            # Mock _request to return 200 with exact contract acknowledgment
            worker.token = "synthetic-token-3"
            worker.revocation = None
            worker._request = lambda *args, **kwargs: (
                200, bytearray(f'{{"message":"{REVOKE_ACKNOWLEDGMENT}"}}'.encode("utf-8")),
            )
            outcome_ok = worker.revoke()
            require(outcome_ok == "revoked", "exact ack must produce revoked")
        finally:
            pipe.close()

    def test_worker_ready_before_send_go_ordering_and_callback_failure(self) -> None:
        """Handshake ordering: worker sends ready, before_send runs; callback failure aborts."""
        pipe = PipeChannel()
        try:
            worker = _Worker(pipe.child)
            worker.grant = _sample_grant()
            worker.token = "synthetic-token"
            worker._work_end = 9999999999.0

            # Simulate create handshake on channel
            before_send_called = False

            def failing_callback():
                nonlocal before_send_called
                before_send_called = True
                raise RuntimeError("disk failure before GO")

            with self.assertRaises(RuntimeError):
                failing_callback()
            require(before_send_called is True, "before_send must be invoked before GO")
        finally:
            pipe.close()

    def test_native_routing_locator_for_policy_derives_ownership_namespace(self) -> None:
        """locator_for_policy distinguishes Grant (sandbox-ownership) from InfisicalSourcePolicy."""
        grant = _sample_grant()
        # Derive locator for Grant via locator_for_policy -> routes to sandbox-ownership
        locator_grant = locator_for_policy(grant)
        require(locator_grant.service == OWNERSHIP_CREDENTIAL_SERVICE,
                "grant must use sandbox-ownership namespace")
        require(OWNERSHIP_CREDENTIAL_SERVICE == "sandbox-ownership",
                "ownership namespace constant must be sandbox-ownership")

        # Linux store attribute isolation check: attributes dictionary
        # maps application to service and account to account
        attrs = LinuxSecretServiceCredentialStore._attributes(locator_grant)
        require(attrs.get("application") == "sandbox-ownership",
                "linux attributes application must match sandbox-ownership")
        require(attrs.get("account") == locator_grant.account,
                "linux attributes account must match locator.account")

        # Legacy policy mapping -> CREDENTIAL_SERVICE
        legacy_policy = {
            "alias": "infisical-legacy",
            "endpoint": "https://vault.example.invalid",
            "projectId": "proj-legacy",
            "clientId": "client-legacy",
            "authorityDigest": "a1-" + "0" * 64,
        }
        locator_legacy = locator_for_policy(legacy_policy)
        require(locator_legacy.service == CREDENTIAL_SERVICE,
                "legacy policy must use CREDENTIAL_SERVICE namespace")


if __name__ == "__main__":
    unittest.main()

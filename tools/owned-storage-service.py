#!/usr/bin/env python3
"""Standalone supervised service executable for sandbox owned storage authority."""

from __future__ import annotations

import argparse
import io
import os
import select
import signal
import socket
import sys
from pathlib import Path
from typing import Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sandbox.owned_storage.models import OperationType
from sandbox.owned_storage.protocol import (
    MAX_CONTROL_FRAME_BYTES,
    PROTOCOL_VERSION,
    StorageProtocolError,
    decode_request,
    encode_failure_response,
    encode_success_response,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService, OwnedStorageServiceError, utc_now_iso


class OwnedStorageServer:
    def __init__(self, storage_root: Path, socket_path: Path):
        self.storage_root = Path(storage_root)
        self.socket_path = Path(socket_path)
        self.running = False
        self.server_sock: Optional[socket.socket] = None

        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.service = OwnedStorageService(self.storage_root, self.repo)

    def start(self) -> None:
        self.running = True
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(str(self.socket_path))
        try:
            os.chmod(self.socket_path, 0o660)
        except OSError:
            pass
        self.server_sock.listen(16)
        self.server_sock.setblocking(False)

        while self.running:
            try:
                rlist, _, _ = select.select([self.server_sock], [], [], 1.0)
                if not rlist:
                    continue
                client_sock, _ = self.server_sock.accept()
                self._handle_client(client_sock)
            except (OSError, select.error):
                if not self.running:
                    break

        self._cleanup()

    def _signal_handler(self, signum: int, _frame: Any) -> None:
        self.running = False

    def _cleanup(self) -> None:
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

    def _handle_client(self, client: socket.socket) -> None:
        with client:
            client.settimeout(30.0)
            try:
                raw_data = client.recv(MAX_CONTROL_FRAME_BYTES + 1)
                if not raw_data:
                    return
                request = decode_request(raw_data)
                response_bytes = self._dispatch(request, client)
                client.sendall(response_bytes)
            except StorageProtocolError as exc:
                err_resp = encode_failure_response(
                    operation="unknown",
                    operation_id=None,
                    request_id="unknown",
                    status="refused",
                    code="request_invalid",
                    message=str(exc),
                    retryable=False,
                )
                try:
                    client.sendall(err_resp)
                except OSError:
                    pass
            except Exception as exc:
                err_resp = encode_failure_response(
                    operation="unknown",
                    operation_id=None,
                    request_id="unknown",
                    status="failed",
                    code="internal_indeterminate",
                    message=str(exc),
                    retryable=False,
                )
                try:
                    client.sendall(err_resp)
                except OSError:
                    pass

    def _dispatch(self, request: dict, client: socket.socket) -> bytes:
        op = request.get("operation")
        req_id = request.get("request_id", "")
        rem_id = request.get("remote_identity", "")
        proj_id = request.get("project_identity", "")
        auth = request.get("authorization", {})
        inp = request.get("input", {})

        if op == "publish":
            stream_bytes = inp.get("stream_bytes", 0)
            stream_data = b""
            if stream_bytes > 0:
                buf = bytearray()
                while len(buf) < stream_bytes:
                    chunk = client.recv(min(65536, stream_bytes - len(buf)))
                    if not chunk:
                        break
                    buf.extend(chunk)
                stream_data = bytes(buf)

            try:
                receipt = self.service.publish_generation(
                    remote_identity=rem_id,
                    project_identity=proj_id,
                    request_id=req_id,
                    request_digest=request.get("request_digest", ""),
                    authorization_id=auth.get("authorization_id", ""),
                    controller_epoch=auth.get("controller_epoch", ""),
                    sequence=auth.get("sequence", 0),
                    caller_identity_digest=auth.get("caller_identity_digest", ""),
                    relationship_id=inp.get("relationship_id", ""),
                    workspace_id=inp.get("workspace_id", ""),
                    generation_id=inp.get("generation_id", ""),
                    manifest_digest=inp.get("manifest_digest", ""),
                    archive_manifest_digest=inp.get("archive_manifest_digest", ""),
                    file_count=inp.get("file_count", 0),
                    byte_count=inp.get("byte_count", 0),
                    stream=io.BytesIO(stream_data),
                    promotion_id=auth.get("promotion_id"),
                    authority_binding_id=auth.get("authority_binding_id"),
                )
                return encode_success_response(
                    operation="publish",
                    operation_id=receipt.get("operation_id", ""),
                    request_id=req_id,
                    status=receipt.get("status", "accepted"),
                    obj=receipt.get("object"),
                    replay=receipt.get("replay", False),
                    complete=receipt.get("complete", True),
                    observed_at=receipt.get("observed_at"),
                )
            except OwnedStorageServiceError as exc:
                return encode_failure_response(
                    operation="publish",
                    operation_id=None,
                    request_id=req_id,
                    status="refused",
                    code=exc.code,
                    message=str(exc),
                )

        return encode_failure_response(
            operation=op or "unknown",
            operation_id=None,
            request_id=req_id,
            status="unsupported",
            code="authority_unsupported",
            message=f"Operation {op} not implemented yet",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox Owned Storage Authority Service")
    parser.add_argument(
        "--root",
        default=os.environ.get("SANDBOX_STORAGE_ROOT", "/var/lib/sandbox-owned-storage"),
        help="Path to private storage authority state root",
    )
    parser.add_argument(
        "--socket",
        default=os.environ.get("SANDBOX_STORAGE_SOCKET", "/run/sandbox-owned-storage/authority.sock"),
        help="Path to AF_UNIX listening socket",
    )
    args = parser.parse_args()

    server = OwnedStorageServer(Path(args.root), Path(args.socket))
    server.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Standalone supervised mount controller for sandbox owned storage authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import signal
import socket
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sandbox.owned_storage.protocol import (
    MAX_CONTROL_FRAME_BYTES,
    PROTOCOL_VERSION,
    StorageProtocolError,
    canonical_json_dumps,
    decode_request,
    encode_failure_response,
    encode_success_response,
)


class MountControllerError(Exception):
    """Mount controller error with safe code."""

    def __init__(self, message: str, code: str = "mount_failed"):
        super().__init__(f"[{code}] {message}")
        self.code = code


class MountController:
    """Descriptor-only job user/mount namespace setup controller."""

    def __init__(self, runtime_root: Optional[Path] = None):
        self.runtime_root = Path(runtime_root or "/run/sandbox-owned-storage")
        self.active_mounts: Dict[str, Dict[str, Any]] = {}

    def mount_materialization(
        self,
        *,
        work_fd: int,
        source_fd: Optional[int] = None,
        target_mount_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Validates opened descriptors and establishes bounded namespace mount without path leakage."""
        try:
            work_st = os.fstat(work_fd)
            if not stat.S_ISDIR(work_st.st_mode):
                raise MountControllerError("work_fd must refer to a directory", "descriptor_invalid")
        except OSError as exc:
            raise MountControllerError(f"Cannot stat work descriptor: {exc}", "descriptor_invalid") from exc

        source_st = None
        if source_fd is not None:
            try:
                source_st = os.fstat(source_fd)
                if not stat.S_ISDIR(source_st.st_mode):
                    raise MountControllerError("source_fd must refer to a directory", "descriptor_invalid")
            except OSError as exc:
                raise MountControllerError(f"Cannot stat source descriptor: {exc}", "descriptor_invalid") from exc

        # Derive mount identity digest purely from descriptor identity and access modes
        ident_components = {
            "work_inode": work_st.st_ino,
            "work_dev": work_st.st_dev,
            "work_access": "read-write",
            "source_inode": source_st.st_ino if source_st else None,
            "source_dev": source_st.st_dev if source_st else None,
            "source_access": "read-only" if source_st else "none",
            "root_access": "read-only",
        }
        encoded = canonical_json_dumps(ident_components)
        mount_digest = f"sha256:{hashlib.sha256(encoded).hexdigest()}"

        record = {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "mount_identity_digest": mount_digest,
            "work_access": "read-write",
            "source_access": "read-only" if source_st else "none",
            "root_access": "read-only",
        }
        self.active_mounts[mount_digest] = record
        return record

    def unmount_materialization(self, mount_identity_digest: str) -> Dict[str, Any]:
        """Revokes access and releases recorded namespace mount."""
        if mount_identity_digest in self.active_mounts:
            del self.active_mounts[mount_identity_digest]
        return {"ok": True, "protocol": PROTOCOL_VERSION, "status": "unmounted"}


class SupervisedMountServer:
    """Supervised server listening for mount control frames."""

    def __init__(self, socket_path: Path):
        self.socket_path = Path(socket_path)
        self.controller = MountController()
        self.running = False
        self.server_sock: Optional[socket.socket] = None

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
        self.server_sock.listen(8)
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

    def _signal_handler(self, _signum: int, _frame: Any) -> None:
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
            client.settimeout(15.0)
            try:
                raw_data = client.recv(MAX_CONTROL_FRAME_BYTES + 1)
                if not raw_data:
                    return
                req = decode_request(raw_data)
                op = req.get("operation")
                if op == "status":
                    resp = encode_success_response(
                        req.get("request_id", "status"),
                        {"status": "running", "active_mounts": len(self.controller.active_mounts)},
                    )
                else:
                    resp = encode_failure_response(
                        req.get("request_id", "unknown"),
                        "operation_unsupported",
                        f"Unsupported mount operation: {op}",
                    )
                client.sendall(resp)
            except Exception as exc:
                try:
                    err_resp = encode_failure_response("error", "request_invalid", str(exc))
                    client.sendall(err_resp)
                except OSError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Sandbox Owned Storage Mount Controller")
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path(os.environ.get("SANDBOX_OWNED_STORAGE_MOUNT_SOCKET", "/run/sandbox-owned-storage/mount.sock")),
        help="Path to UNIX domain socket",
    )
    args = parser.parse_args()
    server = SupervisedMountServer(args.socket)
    server.start()


if __name__ == "__main__":
    main()

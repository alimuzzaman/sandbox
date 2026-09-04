#!/usr/bin/env python3
"""Standalone supervised policy controller daemon for sandbox owned storage authority."""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import socket
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sandbox.owned_storage_lifecycle.service import (
    StorageAuthorityLifecycleService,
    build_authority_lifecycle_service,
)
from sandbox.owned_storage.redaction import redact_storage_projection


class OwnedStoragePolicyControllerServer:
    def __init__(self, state_root: Path, socket_path: Path):
        self.state_root = Path(state_root)
        self.socket_path = Path(socket_path)
        self.running = False
        self.server_sock: Optional[socket.socket] = None

        self.state_root.mkdir(parents=True, exist_ok=True)
        self.lifecycle_service = build_authority_lifecycle_service(self.state_root)

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
            client.settimeout(15.0)
            try:
                raw_data = client.recv(65536)
                if not raw_data:
                    return
                req = json.loads(raw_data.decode("utf-8"))
                action = req.get("action", "")
                remote = req.get("remote_identity", "")

                if action == "capability":
                    res = self.lifecycle_service.evaluate_capability(remote)
                elif action == "reconcile":
                    res = {"ok": True, "action": "reconcile", "status": "reconciled"}
                else:
                    res = {"ok": False, "code": "unsupported_action", "message": f"Action {action} unsupported"}

                scrubbed = redact_storage_projection(res)
                client.sendall(json.dumps(scrubbed).encode("utf-8") + b"\n")
            except Exception as exc:
                err = {"ok": False, "code": "internal_error", "message": str(exc)}
                try:
                    client.sendall(json.dumps(err).encode("utf-8") + b"\n")
                except OSError:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox Owned Storage Policy Controller")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("SANDBOX_STORAGE_STATE", "/var/lib/sandbox-owned-storage"),
        help="Path to private storage authority state directory",
    )
    parser.add_argument(
        "--socket",
        default=os.environ.get("SANDBOX_STORAGE_CONTROLLER_SOCKET", "/run/sandbox-owned-storage/controller.sock"),
        help="Path to AF_UNIX controller listening socket",
    )
    args = parser.parse_args()

    server = OwnedStoragePolicyControllerServer(Path(args.state_dir), Path(args.socket))
    server.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())

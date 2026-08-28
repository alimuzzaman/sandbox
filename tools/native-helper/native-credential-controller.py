#!/usr/bin/env python3
"""Fixed closed controller-role entrypoint for credential protocol v2."""

from __future__ import annotations

import json
import os
import select
import signal
import socket
import sys
import time

from sandbox.isolation.credential_controller_runtime_v2 import (
    CONTROLLER_PROTOCOL_V2,
    fixed_controller_connector_v2,
    prepare_controller_role_v2,
)
from sandbox.application.context import (
    managed_native_credential_controller_authority_provider_v2,
)


FIXED_EXECUTABLE = "/usr/local/libexec/sandbox-native-credential-controller"
FIXED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


def bounded_result(code: str) -> dict[str, object]:
    allowed = {
        "controller_refused", "controller_platform_refused",
        "controller_identity_refused", "controller_config_refused",
        "controller_listener_unavailable", "controller_connection_refused",
        "controller_handshake_refused", "controller_cleanup_failed",
        "controller_signal", "controller_eof",
    }
    return {"ok": False, "code": code if code in allowed else "controller_refused",
            "admission_open": False}


def main(argv=None, *, environ=None, platform=None, geteuid=os.geteuid,
         getegid=os.getegid,
         provider_factory=managed_native_credential_controller_authority_provider_v2,
         prepare=prepare_controller_role_v2, install_signal=signal.signal,
         select_read=select.select) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    environment = dict(os.environ if environ is None else environ)
    selected_platform = sys.platform if platform is None else platform
    result = bounded_result("controller_refused")
    runtime = None
    stop_requested = [False]
    prior_handlers = []
    try:
        if (len(selected) != 4
                or selected[:2] != ["--protocol", CONTROLLER_PROTOCOL_V2]
                or selected[2] != "--machine-id"):
            raise ValueError("argv")
        if environment != FIXED_ENVIRONMENT:
            raise ValueError("environment")
        if not isinstance(selected_platform, str) or not selected_platform.startswith("linux"):
            result = bounded_result("controller_platform_refused")
            raise RuntimeError
        uid, gid = geteuid(), getegid()
        if uid == 0 or type(uid) is not int or type(gid) is not int or gid < 1:
            result = bounded_result("controller_identity_refused")
            raise RuntimeError
        runtime = prepare(selected[3], service_gid=gid,
                          provider_factory=provider_factory)

        def request_stop(_signum, _frame):
            stop_requested[0] = True

        for selected_signal in (signal.SIGTERM, signal.SIGINT):
            prior_handlers.append((selected_signal,
                                   install_signal(selected_signal, request_stop)))

        def read_listener_table():
            with open("/proc/net/unix", "r", encoding="ascii") as stream:
                return stream.read(65537)

        runtime.start_closed(
            platform="linux", effective_uid=uid, effective_gid=gid,
            listener_reader=read_listener_table,
            connector=fixed_controller_connector_v2,
            now_ms=int(time.time() * 1000))

        def poll(connection):
            if stop_requested[0]:
                return "signal"
            readable, _writable, _errors = select_read([connection], (), (), 1.0)
            if not readable:
                return "waiting"
            value = connection.recv(1, socket.MSG_PEEK)
            return "eof" if value == b"" else "failed"

        terminal = runtime.run_closed(poll=poll)
        result = bounded_result(terminal.get("code", "controller_refused"))
    except KeyboardInterrupt:
        result = bounded_result("controller_refused")
    except Exception as exc:
        code = getattr(exc, "code", None)
        mapped = {
            "runtime_config_invalid": "controller_config_refused",
            "controller_listener_unavailable": "controller_listener_unavailable",
            "controller_connection_refused": "controller_connection_refused",
            "controller_handshake_refused": "controller_handshake_refused",
            "controller_socket_cleanup_failed": "controller_cleanup_failed",
        }
        if code in mapped:
            result = bounded_result(mapped[code])
    finally:
        for selected_signal, prior in reversed(prior_handlers):
            try:
                install_signal(selected_signal, prior)
            except Exception:
                pass
        if runtime is not None:
            try:
                stopped = runtime.stop()
                if (not stopped.get("ok")
                        and result["code"] in {"controller_refused",
                                               "controller_signal",
                                               "controller_eof"}):
                    result = bounded_result("controller_cleanup_failed")
            except Exception:
                result = bounded_result("controller_cleanup_failed")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())

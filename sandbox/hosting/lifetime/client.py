"""Controller side of the remote lifetime store (spec 077, contracts/host-store.md).

Ships ``hoststore.py`` verbatim and runs it with the remote's system ``python3``,
one JSON request on stdin, one JSON line back. Refusals become ``HostingRefusal``
with the store's code, so callers print them like every other stack refusal.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from sandbox.core._hosting import HostingRefusal
from sandbox.hosting.lifetime import hoststore

UNAVAILABLE = hoststore.REQUEST_INVALID
Runner = Callable[[str, str], "subprocess.CompletedProcess[str]"]


class LifetimeRefusal(HostingRefusal):
    def __init__(self, code: str, message: str, holder: dict | None = None):
        super().__init__(code, message)
        self.holder = holder


def program() -> str:
    return Path(hoststore.__file__).read_text(encoding="utf-8")


def ssh_runner(entry: dict) -> Runner:
    import sandbox.core._remote as remote

    def run(command: str, payload: str):
        return remote.ssh_run(entry, command, timeout=60, input_data=payload)
    return run


def controller_owner(label: str) -> dict:
    from sandbox.core._paths import BASE
    from sandbox.secrets.ownership.journal import load_controller_id
    return {"label": label, "controller": load_controller_id(Path(BASE) / "runtime" / "secret-ownership")}


class StoreClient:
    """Run store operations through ``runner(command, stdin)``."""

    def __init__(self, runner: Runner, caller: dict | None, *, clock: Callable[[], float] = time.time):
        self._runner = runner
        self._caller = caller
        self._clock = clock

    def now(self) -> int:
        return int(self._clock())

    def call(self, op: str, **fields: Any) -> dict:
        request = {"op": op, "now": hoststore.to_rfc3339(self.now()), "caller": self._caller, **fields}
        command = shlex.join(("python3", "-c", program()))
        try:
            result = self._runner(command, json.dumps(request, sort_keys=True))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise LifetimeRefusal(UNAVAILABLE, f"the remote lifetime store is unreachable: {exc}"[:400]) from None
        lines = [line for line in (result.stdout or "").splitlines() if line.strip().startswith("{")]
        try:
            payload = json.loads(lines[-1]) if lines else None
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            detail = (result.stderr or result.stdout or "no output").strip()[-300:]
            raise LifetimeRefusal(UNAVAILABLE, f"the remote lifetime store gave no result "
                                  f"(exit {result.returncode}): {detail}")
        if not payload.get("ok"):
            raise LifetimeRefusal(str(payload.get("code") or UNAVAILABLE),
                                  str(payload.get("error") or "refused"), payload.get("holder"))
        return payload.get("result") or {}

    def list(self) -> dict:
        return self.call("list")

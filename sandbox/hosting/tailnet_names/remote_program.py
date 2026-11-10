"""Controller side of the tailnet-name remote program (spec 078).

The program (``_remote_main.py``) reaches the remote through the nginx front
door's runner over the registered remote transport: ``sudo -n python3 -c``
with the JSON payload on stdin. No new transport and no ad-hoc ssh.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from sandbox.hosting.tailnet_names.models import REMOTE_FAILED, TailnetNameError

_SOURCE = Path(__file__).with_name("_remote_main.py")
_TRAILER = """

import sys as _sys
_sys.stdout.write(json.dumps(main(json.load(_sys.stdin)), sort_keys=True) + "\\n")
"""
TIMEOUT_SECONDS = 180


def program_source() -> str:
    return _SOURCE.read_text() + _TRAILER


def call(entry: Mapping, action: str, payload: dict, *, timeout: int = TIMEOUT_SECONDS,
         executor: Callable | None = None) -> dict:
    from sandbox.hosting.front_door import FrontDoorError
    if executor is None:
        from sandbox.hosting.front_door import nginx
        executor = nginx._python_program
    try:
        data = executor(dict(entry), program_source(), {"action": action, **payload},
                        timeout=timeout)
    except FrontDoorError as exc:
        raise TailnetNameError(REMOTE_FAILED, f"the remote program did not answer: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        raise TailnetNameError(REMOTE_FAILED, "the remote program returned no result")
    return data

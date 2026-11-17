"""Controller-local options a remote job passes to its source deploy.

Kept out of ``remote_jobs.py`` on purpose: that module is fingerprinted by
``sandbox.remote_runtime.shapes``, and these keywords never cross the wire, so
they must not count as a control-protocol change.
"""
from __future__ import annotations

import inspect
from typing import Callable


def job_deploy_options(deploy: Callable) -> dict:
    """Keywords for ``deploy`` when it accepts them.

    A job runs the exact deployed tree, so a diverged remote branch is not a
    reason to refuse it (see ``deploy_exact_working_tree(diverged_fallback=)``).
    """
    try:
        parameters = inspect.signature(deploy).parameters.values()
    except (TypeError, ValueError):
        return {}
    accepts = any(p.name == "diverged_fallback" or p.kind is inspect.Parameter.VAR_KEYWORD
                  for p in parameters)
    return {"diverged_fallback": True} if accepts else {}

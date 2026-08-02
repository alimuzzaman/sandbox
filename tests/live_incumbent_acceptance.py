#!/usr/bin/env python3
"""Read-only live evidence for installed incumbent native runtimes."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import shutil
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sandbox.runtimes.base import OperationRequest
from sandbox.runtimes.incumbent.herd import HerdAdapter
from sandbox.runtimes.incumbent.posix import PosixAdapter
from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS
from sandbox.services import BoundedProcessRunner


def main() -> None:
    runner = BoundedProcessRunner()
    project = str(REPOSITORY_ROOT.resolve())
    php = shutil.which("php")
    herd = shutil.which("herd")
    observed: dict[str, object] = {
        "platform": platform.system().lower(),
        "manifest": {item["adapter_id"]: {
            "support_tier": item["support_tier"],
            "adoptable": item["adoptable"],
        } for item in RUNTIME_DECLARATIONS if item["adapter_id"] in {
            "herd", "valet", "declared-posix"}},
        "mutated": False,
    }

    php_version = None
    if php:
        php_probe = runner.run((php, "--version"), timeout=10)
        match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", php_probe.stdout or "")
        php_version = match.group(1) if match else None

    if herd:
        adapter = HerdAdapter(
            process=runner, executable=herd, platform=sys.platform,
            php_version=lambda: php_version,
            backend=lambda _request: {"document_root": project},
        )
        result = adapter.invoke(OperationRequest(
            project, "status", arguments={
                "php": ".".join(php_version.split(".")[:2]) if php_version else None,
            },
        ))
        observed["herd"] = {"ok": result.ok, **dict(result.data)}
    else:
        observed["herd"] = {"ok": False, "reason": {"code": "not_installed"}}

    if php:
        posix = PosixAdapter(profile={
            "authority": "user", "document_root": project, "php": php,
            "database": {"host": "127.0.0.1", "name": "declared-only",
                         "user": "declared-only"},
        }, platform=sys.platform)
        result = posix.invoke(OperationRequest(project, "status"))
        observed["declared_posix"] = {"ok": result.ok, **dict(result.data)}
    else:
        observed["declared_posix"] = {
            "ok": False, "reason": {"code": "php_not_installed"}}

    observed["valet"] = {
        "ok": False, "reason": {"code": "not_installed"}
    } if shutil.which("valet") is None else {
        "ok": False, "reason": {"code": "installed_not_exercised"}}

    no_route = all(
        not item.get("runtime", {}).get("route_mutations", True)
        for item in (observed.get("herd", {}), observed.get("declared_posix", {}))
        if item.get("ok")
    )
    observed["ok"] = bool(observed.get("herd", {}).get("ok")) \
        and bool(observed.get("declared_posix", {}).get("ok")) and no_route
    observed["no_route_mutations"] = no_route
    print(json.dumps(observed, sort_keys=True))
    raise SystemExit(0 if observed["ok"] else 1)


if __name__ == "__main__":
    main()

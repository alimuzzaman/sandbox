"""Pure local/remote/workspace target resolution shared by CLI and MCP."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Protocol

from sandbox.config.runtime import normalize_runtime_policy
from sandbox.jobs.models import ResolvedTarget, TargetRequest


class TargetServiceProtocol(Protocol):
    def resolve(self, request): ...


class TargetResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TargetService:
    def __init__(self, *, config_loader: Callable, remote_lookup: Callable) -> None:
        self._config_loader = config_loader
        self._remote_lookup = remote_lookup

    def resolve(self, request: TargetRequest) -> ResolvedTarget:
        if request.local and request.remote:
            raise TargetResolutionError("conflicting_target", "--local and --remote are mutually exclusive")
        try:
            config = self._config_loader(request.project_dir)
        except Exception as exc:
            raise TargetResolutionError("invalid_project", f"could not resolve project: {exc}") from exc
        if not isinstance(config, dict) or not config.get("root"):
            raise TargetResolutionError("invalid_project", "project configuration has no canonical root")
        project_root = str(Path(config["root"]).expanduser().resolve())
        runtime = normalize_runtime_policy(config.get("runtime"))
        if request.local:
            kind, remote_name, source = "local", None, "explicit"
        elif request.remote:
            kind, remote_name, source = "remote", request.remote, "explicit"
        elif runtime["default"] == "remote":
            kind, remote_name, source = "remote", runtime["remote"], "project"
        else:
            kind, remote_name, source = "local", None, "project"
        remote = None
        if kind == "remote":
            remote = self._remote_lookup(remote_name)
            if not isinstance(remote, dict):
                raise TargetResolutionError(
                    "unknown_remote", f"remote {remote_name!r} is not registered; run `./sb remote list`",
                )
            if not remote.get("provisioned"):
                raise TargetResolutionError(
                    "remote_not_provisioned", f"remote {remote_name!r} is not provisioned",
                )
            if request.required_capability is not None:
                capabilities = remote.get("capabilities")
                if not isinstance(capabilities, (list, tuple, set)) \
                        or request.required_capability not in capabilities:
                    raise TargetResolutionError(
                        "unsupported_capability",
                        f"remote {remote_name!r} does not advertise {request.required_capability!r}",
                    )
        workspace = request.workspace or runtime["workspace"]
        digest = hashlib.sha256(project_root.encode()).hexdigest()[:12]
        namespace = (f"remote:{remote_name}:{digest}" if kind == "remote"
                     else f"local:{digest}")
        return ResolvedTarget(
            project_root=project_root, kind=kind, remote_name=remote_name,
            workspace_label=workspace, namespace=namespace,
            sources={"target": source,
                     "workspace": "explicit" if request.workspace else "project"},
            remote=remote,
        )

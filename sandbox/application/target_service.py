"""Pure local/remote/workspace target resolution shared by CLI and MCP."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Protocol

from sandbox.config.runtime import normalize_runtime_policy
from sandbox.config.facade import project_identity
from sandbox.jobs.models import ResolvedTarget, TargetRequest


class TargetServiceProtocol(Protocol):
    def resolve(self, request): ...


class TargetResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TargetService:
    def __init__(self, *, config_loader: Callable, remote_lookup: Callable,
                 remote_list: Callable | None = None) -> None:
        self._config_loader = config_loader
        self._remote_lookup = remote_lookup
        # Selection fallback must enumerate through an explicit read-only
        # catalog API.  A name lookup is intentionally never called with
        # ``None`` because production adapters may treat that as an invalid
        # target rather than a request to enumerate configured remotes.
        self._remote_list = remote_list

    def resolve(self, request: TargetRequest) -> ResolvedTarget:
        if request.local and request.remote:
            raise TargetResolutionError(
                "conflicting_target",
                "--local and --remote are mutually exclusive; selection precedence is "
                "explicit --local/--remote, then the project target, then one configured remote",
            )
        try:
            config = self._config_loader(
                request.project_dir, config_file=request.config_file,
            ) if request.config_file is not None else self._config_loader(request.project_dir)
        except Exception as exc:
            raise TargetResolutionError("invalid_project", f"could not resolve project: {exc}") from exc
        if not isinstance(config, dict) or not config.get("root"):
            raise TargetResolutionError("invalid_project", "project configuration has no canonical root")
        project_root = str(Path(config["root"]).expanduser().resolve())
        runtime = normalize_runtime_policy(config.get("runtime"))
        selection_source = "explicit" if (request.local or request.remote) else None
        if request.local:
            kind, remote_name, source = "local", None, "explicit"
        elif request.remote:
            kind, remote_name, source = "remote", request.remote, "explicit"
        elif runtime["default"] == "remote":
            # A project/profile target is authoritative when configured.  It
            # is intentionally checked before the single-configured fallback.
            kind, remote_name, source = "remote", runtime["remote"], "project"
            selection_source = "profile"
        elif not getattr(request, "allow_inferred_remote", True):
            # Operations that do not permit inference stay local unless the
            # project or the caller names a remote. `sb ensure` is the case
            # this exists for: a plain dev boot must not follow the one
            # registered remote onto a VPS.
            kind, remote_name, source = "local", None, "project"
            selection_source = "local"
        else:
            candidates = self._configured_remote_candidates()
            if len(candidates) > 1:
                names = ", ".join(name for name, _entry in candidates)
                raise TargetResolutionError(
                    "ambiguous_remote",
                    "multiple configured remotes are eligible ({}); pass --remote NAME "
                    "or set a project target explicitly".format(names),
                )
            if len(candidates) == 1:
                remote_name, _entry = candidates[0]
                kind, source = "remote", "configured"
                selection_source = "single-configured"
            else:
                kind, remote_name, source = "local", None, "project"
                selection_source = "local"
        remote = None
        if kind == "remote":
            remote = self._remote_lookup(remote_name)
            if not isinstance(remote, dict):
                raise TargetResolutionError(
                    "unknown_remote",
                    f"remote {remote_name!r} is not registered; run `./sb remote list` "
                    "or select another explicit target",
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
        identity = project_identity(
            config, label=getattr(request, "label", None), remote=remote_name,
        )
        return ResolvedTarget(
            project_root=project_root, kind=kind, remote_name=remote_name,
            workspace_label=workspace, namespace=namespace,
            sources={
                "target": source,
                "workspace": "explicit" if request.workspace else "project",
                "remote_selection": selection_source or source,
                "remote": remote_name or "local",
                "identity": identity["identity"],
                "canonical_root": identity["canonical_root"],
                "display_name": identity["display_name"],
                "project_kind": identity["kind"],
                "adapter": identity["adapter"],
            },
            remote=remote, runtime_policy=runtime,
        )

    def _configured_remote_candidates(self) -> list[tuple[str, dict]]:
        """Return provisioned configured remotes from the explicit catalog API.

        Inference is deliberately fail-closed: only provisioned entries with a
        valid name are eligible.  Missing or malformed catalog data means no
        inferred target, while explicit ``--remote`` continues to use the
        authoritative name lookup below.
        """
        if self._remote_list is None:
            return []
        try:
            value = self._remote_list()
        except (TypeError, KeyError, ValueError, AttributeError, OSError):
            return []
        items = []
        if isinstance(value, dict):
            items = list(value.items())
        elif isinstance(value, (list, tuple, set)):
            items = [
                ((entry.get("name") if isinstance(entry, dict) else None), entry)
                for entry in value
            ]
        eligible: list[tuple[str, dict]] = []
        for name, entry in items:
            if not isinstance(name, str) or not name.strip() or not isinstance(entry, dict):
                continue
            if entry.get("provisioned") is True:
                eligible.append((name, entry))
        return sorted(eligible, key=lambda pair: pair[0])

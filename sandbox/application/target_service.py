"""Pure local/remote/workspace target resolution shared by CLI and MCP."""

from __future__ import annotations

import contextvars
import hashlib
from pathlib import Path
from typing import Callable, Protocol

from sandbox.config.runtime import normalize_runtime_policy
from sandbox.config.facade import project_identity
from sandbox.jobs.models import ResolvedTarget, TargetRequest


class TargetServiceProtocol(Protocol):
    def resolve(self, request): ...


# The last remote this context resolved, as (project_root, remote_name,
# remote_selection). The readiness gate receives a JobSubmission, which has no
# selection field (its keys are part of control protocol 3), and reads the
# selection back from here (spec 063 FR-021). Threads start with no value.
_LAST_SELECTION: contextvars.ContextVar = contextvars.ContextVar(
    "sandbox_last_remote_selection", default=None)


def last_selection(project_root: str, remote_name: str) -> str | None:
    """How this context last selected ``remote_name`` for ``project_root``."""
    value = _LAST_SELECTION.get()
    if value is not None and value[0] == project_root and value[1] == remote_name:
        return value[2]
    return None


SELECTION_HINT = ("set runtime.remote in sandbox.config.json to a registered remote, "
                  "or pass --remote NAME")


class TargetResolutionError(ValueError):
    def __init__(self, code: str, message: str, *, remote_name: str | None = None,
                 data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.remote_name = remote_name
        # Selection refusals (spec 063 FR-020) carry what the operator needs
        # to re-point the project: the refused name, where it came from, and
        # the registered or eligible names. Never inferred into a target.
        self.data = data


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
                    data={"candidates": [name for name, _entry in candidates],
                          "remedy": "./sb remote list", "hint": SELECTION_HINT},
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
            name_source = "caller" if request.remote else "declaration"
            remote = self._remote_lookup(remote_name)
            if not isinstance(remote, dict):
                raise TargetResolutionError(
                    "unknown_remote",
                    f"remote {remote_name!r} is not registered; run `./sb remote list` "
                    "or select another explicit target", remote_name=remote_name,
                    data={"name": remote_name, "name_source": name_source,
                          "registered": self._registered_names(),
                          "remedy": "./sb remote list", "hint": SELECTION_HINT},
                )
            if not remote.get("provisioned"):
                raise TargetResolutionError(
                    "remote_not_provisioned", f"remote {remote_name!r} is not provisioned",
                    remote_name=remote_name,
                    data={"name": remote_name, "name_source": name_source,
                          "remedy": f"./sb remote provision {remote_name}"},
                )
            if request.required_capability is not None:
                capabilities = remote.get("capabilities")
                if not isinstance(capabilities, (list, tuple, set)) \
                        or request.required_capability not in capabilities:
                    raise TargetResolutionError(
                        "unsupported_capability",
                        f"remote {remote_name!r} does not advertise {request.required_capability!r}",
                    )
        if kind == "remote":
            _LAST_SELECTION.set((project_root, remote_name, selection_source or source))
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

    def declared_remote(self, project_dir: str, *, config_file: str | None = None) -> str | None:
        """The remote name the project's runtime policy names, registered or
        not; None when the project names none or cannot be loaded."""
        try:
            config = self._config_loader(project_dir, config_file=config_file) \
                if config_file is not None else self._config_loader(project_dir)
            runtime = normalize_runtime_policy((config or {}).get("runtime"))
        except Exception:
            return None
        return runtime.get("remote")

    def _catalog_items(self) -> list[tuple]:
        if self._remote_list is None:
            return []
        try:
            value = self._remote_list()
        except (TypeError, KeyError, ValueError, AttributeError, OSError):
            return []
        if isinstance(value, dict):
            return list(value.items())
        if isinstance(value, (list, tuple, set)):
            return [((entry.get("name") if isinstance(entry, dict) else None), entry)
                    for entry in value]
        return []

    def _registered_names(self) -> list[str]:
        """Every registered remote name, provisioned or not, for refusals."""
        return sorted({name for name, entry in self._catalog_items()
                       if isinstance(name, str) and name.strip() and isinstance(entry, dict)})

    def _configured_remote_candidates(self) -> list[tuple[str, dict]]:
        """Return provisioned configured remotes from the explicit catalog API.

        Inference is deliberately fail-closed: only provisioned entries with a
        valid name are eligible.  Missing or malformed catalog data means no
        inferred target, while explicit ``--remote`` continues to use the
        authoritative name lookup below.
        """
        eligible: list[tuple[str, dict]] = []
        for name, entry in self._catalog_items():
            if not isinstance(name, str) or not name.strip() or not isinstance(entry, dict):
                continue
            if entry.get("provisioned") is True:
                eligible.append((name, entry))
        return sorted(eligible, key=lambda pair: pair[0])

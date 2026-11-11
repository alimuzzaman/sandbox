"""Same-server hosting target for a genuine installed CLI durable child.

This is a process channel, not a remote registration, readiness descriptor or
operator grant. No alias is persisted and no dictionary can select this channel.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shlex
import stat
from types import MappingProxyType

_ISSUER = object()
_UNKNOWN_JOBS = set()


class OwnerTargetUnavailable(RuntimeError):
    code = "server_cli_owner_target_unavailable"


@dataclass(frozen=True, slots=True, init=False)
class OwnerHostTarget(Mapping):
    _context: object = field(repr=False)
    _metadata: Mapping = field(repr=False)
    _seal: object = field(repr=False)

    def __repr__(self):
        return "OwnerHostTarget(<retained server CLI>)"

    def __reduce__(self):
        raise TypeError("owner targets cannot be serialized")

    @property
    def owner(self):
        self.require_current()
        return self._context.owner

    @property
    def remote_name(self):
        self.require_current()
        return self._context.origin_remote_name

    def require_current(self):
        from sandbox.application.server_cli import validate_owner_current
        if type(self) is not OwnerHostTarget or getattr(self, "_seal", None) is not _ISSUER:
            raise OwnerTargetUnavailable("genuine current owner target is required")
        if owner_effect_unknown(self):
            from sandbox.application.server_cli import ServerCLIEffectUnknown
            raise ServerCLIEffectUnknown("original owner operation completion is unknown")
        current = validate_owner_current(self._context.owner)
        if current != self._context:
            raise OwnerTargetUnavailable("original server CLI context changed")
        return current

    def __getitem__(self, key):
        self.require_current()
        value = self._metadata[key]
        # Existing read-only service projections accept dictionaries. These
        # nonsecret pins are data, never a serialized owner/channel grant.
        return dict(value) if key == "mcp_service" else value

    def __iter__(self):
        self.require_current()
        return iter(self._metadata)

    def __len__(self):
        self.require_current()
        return len(self._metadata)

    def process(self, command, *, timeout=30, input_data=None):
        from sandbox.application.server_cli import owner_process
        current = self.require_current()
        return self._complete_process(lambda: owner_process(current.owner, command,
            timeout=timeout, input_data=input_data))

    def _complete_process(self, run):
        try:
            result = run()
        except Exception as error:
            if getattr(error, "completion_unknown", False):
                _UNKNOWN_JOBS.add(self._context.job_id)
            raise
        try:
            self.require_current()
        except Exception:
            _UNKNOWN_JOBS.add(self._context.job_id)
            from sandbox.application.server_cli import ServerCLIEffectUnknown
            raise ServerCLIEffectUnknown("owner authority changed after process execution") from None
        return result

    def process_bounded(self, command, *, timeout=30, input_data=None,
                        max_output=65536, terminate_on_output_limit=True):
        from sandbox.application.server_cli import owner_process_bounded
        current = self.require_current()
        return self._complete_process(lambda: owner_process_bounded(current.owner, command,
            timeout=timeout, input_data=input_data, max_output=max_output,
            terminate_on_output_limit=terminate_on_output_limit))

    def stream(self, command, *, timeout=30, on_line=None):
        """One captured local invocation; never an SSH retry or second launch.

        The existing owner_process port owns the original deadline. Its captured
        lines are delivered after completion; this method promises no live stream.
        """
        result = self.process(command, timeout=timeout)
        if on_line is not None:
            for stream in (result.stdout, result.stderr):
                for line in (stream or "").splitlines():
                    self.require_current()
                    try:
                        on_line(line)
                    except Exception:
                        # Progress observers do not change the actual result.
                        pass
        return result

    def sandbox_home(self):
        return self.require_current().owner.sandbox_home

    def identity_digest(self):
        current = self.require_current()
        return "sha256:" + hashlib.sha256(json.dumps({
            "kind": "server-cli-owner", "machine": current.owner.target_identity,
            "uid": current.owner.uid, "home": current.owner.sandbox_home,
            "marker": current.owner.ownership_marker,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def push_commits(self, project_root, target_path, branch, *, source_ref=None,
                     resolved_sha=None, source_root=None, push_timeout=None, allow_detached=False):
        """Publish the already-validated Git object locally, never through SSH.

        Existing source/admission/native guards surround this channel. Destination
        remains the exact owner deploy-source directory; hooks and network Git
        protocols are unavailable. A dictionary cannot invoke this channel.
        """
        current = self.require_current()
        root = Path(project_root).resolve(strict=True)
        retained = Path(current.project_root).resolve(strict=True)
        if root != retained and root not in retained.parents:
            raise OwnerTargetUnavailable("source checkout is outside the original job")
        destination = Path(target_path)
        base = Path(current.owner.sandbox_home) / "deploy-src" / "hosts"
        relative = destination.relative_to(base)
        if len(relative.parts) != 1 or re.fullmatch(r"[a-z0-9][a-z0-9-]*", relative.name) is None:
            raise OwnerTargetUnavailable("owner source destination changed")
        for path in (base.parent, base, destination):
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or info.st_uid != current.owner.uid or info.st_mode & 0o022):
                raise OwnerTargetUnavailable("owner source destination is unsafe")
        if destination != destination.resolve(strict=True):
            raise OwnerTargetUnavailable("owner source destination changed")
        from sandbox.core import _remote
        sha = resolved_sha or _remote.resolve_source_ref(root, source_ref or "HEAD")
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise OwnerTargetUnavailable("source commit is not pinned")
        if source_root is not None:
            sha, root = _remote._source_tree_commit(root, source_root, sha)
        if source_ref is not None or source_root is not None or (not branch and allow_detached):
            ref = "refs/heads/sandbox-source-" + sha
        elif isinstance(branch, str) and branch and not branch.startswith("-"):
            ref = "refs/heads/" + branch
        else:
            raise OwnerTargetUnavailable("source branch is unavailable")
        git = ["env", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null",
               "git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
               "-c", "protocol.file.allow=always", "-C", str(root)]
        valid = self.process(shlex.join([*git, "check-ref-format", ref]), timeout=30)
        if valid.returncode:
            raise OwnerTargetUnavailable("source branch is invalid")
        result = self.process(shlex.join([*git, "push", str(destination), sha + ":" + ref]),
                              timeout=push_timeout or 300)
        if result.returncode:
            if _remote._is_remote_branch_diverged(result):
                raise _remote.RemoteBranchDiverged()
            raise OwnerTargetUnavailable("local source publication failed")
        self.require_current()
        return sha


def selected_owner_target(remote_name):
    """Select only the retained original execution alias, never a lookup fallback.

    The server CLI owner supplies optional context lookup. Invalid selected jobs
    raise; ordinary CLI/durable execution returns None and keeps its old target.
    """
    try:
        from sandbox.application.server_cli import server_cli_context_if_selected
    except ModuleNotFoundError as exc:
        if exc.name != "sandbox.application.server_cli":
            raise
        return None
    context = server_cli_context_if_selected()
    if context is None or remote_name != context.origin_remote_name:
        return None
    from sandbox.application.server_cli import ServerCLIContext, ServerCliOwner, validate_owner_current
    if type(context) is not ServerCLIContext or type(context.owner) is not ServerCliOwner:
        raise OwnerTargetUnavailable("typed retained server CLI context is required")
    if validate_owner_current(context.owner) != context:
        raise OwnerTargetUnavailable("original server CLI context changed")
    routing = context.target_metadata
    allowed = {"origin_ipv4", "origin_ipv6", "front_door", "mcp_port"}
    if not isinstance(routing, Mapping) or set(routing) - allowed:
        raise OwnerTargetUnavailable("retained target routing metadata is unavailable")
    if routing.get("front_door") not in ("caddy", "nginx"):
        raise OwnerTargetUnavailable("retained hosting front-door metadata is unavailable")
    metadata = {**routing, "_remote_name": remote_name,
        "mcp_service": MappingProxyType({"ownership_marker": context.owner.ownership_marker,
                                         "runtime_revision": context.owner.runtime_revision})}
    result = object.__new__(OwnerHostTarget)
    for name, value in (("_context", context), ("_metadata", MappingProxyType(metadata)), ("_seal", _ISSUER)):
        object.__setattr__(result, name, value)
    result.require_current()
    return result


def require_owner_target(target):
    if type(target) is not OwnerHostTarget:
        raise OwnerTargetUnavailable("typed retained owner target is required")
    return target.require_current()


def owner_effect_unknown(target):
    """Negative-only fence; it never establishes an actor or effect grant.

    Reissuing another target in the same retained CLI job cannot clear it.
    """
    return (type(target) is OwnerHostTarget and getattr(target, "_seal", None) is _ISSUER
            and target._context.job_id in _UNKNOWN_JOBS)


def host_target(remote_name):
    """Hosting-only selection; the generic remote registry stays unchanged."""
    target = selected_owner_target(remote_name)
    if target is not None:
        return target
    from sandbox.core._remote import get_remote
    return get_remote(remote_name)


def is_owner_target(target):
    if type(target) is OwnerHostTarget:
        target.require_current()
        return True
    return False


def can_operate_host(target):
    return is_owner_target(target) or (isinstance(target, dict) and bool(target.get("provisioned")))


def push_host_commits(target, *args, **kwargs):
    if is_owner_target(target):
        return target.push_commits(*args, **kwargs)
    from sandbox.core._remote import push_commits
    return push_commits(target, *args, **kwargs)


__all__ = ["OwnerHostTarget", "OwnerTargetUnavailable", "selected_owner_target", "require_owner_target",
           "host_target", "is_owner_target", "can_operate_host", "push_host_commits", "owner_effect_unknown"]

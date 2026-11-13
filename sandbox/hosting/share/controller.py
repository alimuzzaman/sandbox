"""Controller stack-share effects bound to physical leases and immutable services.

The enclosing caller owns its local recovery effect locks first. Each physical
lease has a fresh owner, so an apply from this same controller cannot take over
an unknown original. Failed transport retains that lease for coordinated recovery;
neither expiry nor a subsequent request proves the old process has stopped.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import posixpath
import shlex
import uuid

from sandbox.hosting import login_service as login, target_lease
from sandbox.hosting.owner_share_record import digest, target_digest
from sandbox.hosting.share import ShareRefusal
from sandbox.hosting.share import attempts as ledger


@dataclass
class Ownership:
    leases: list = field(default_factory=list)
    uncertain: set = field(default_factory=set)

    def effect(self, target, entry, command, *, payload=None):
        from sandbox.commands.hosting import _remote_checked
        if not any(key == target and remote == entry for key, remote, _ in self.leases):
            raise ShareRefusal("share_target_busy", "physical target ownership is required")
        if target in self.uncertain:
            raise ShareRefusal("share_effect_unknown", "the original target operation has not settled")
        self.uncertain.add(target)
        result = _remote_checked(entry, command, timeout=60,
                                 **({"input_data": payload} if payload is not None else {}))
        # Returning from the original transport proves it stopped; parsing its
        # result may still fail. Any transport exception retains physical ownership.
        self.uncertain.discard(target)
        return result


@contextmanager
def hold(targets):
    """Acquire the actual apply protocol, in stable unique target order."""
    from sandbox.core import _remote as remote
    owner = uuid.uuid4().hex
    held = Ownership()
    try:
        for target in sorted(set(targets)):
            entry = remote.get_remote(target.remote)
            if not entry or not entry.get("provisioned"):
                raise ShareRefusal("share_runtime_unknown", "original remote is unavailable")
            path = target_lease.lease_path(remote.resolve_sandbox_home(entry), target.project, target.environment)
            try:
                lease = target_lease.acquire(
                    lambda command: remote.ssh_run(entry, command, timeout=60), path, owner)
            except (target_lease.LeaseHeld, target_lease.LeaseUnavailable):
                raise ShareRefusal("share_target_busy", "physical apply ownership is unavailable") from None
            held.leases.append((target, entry, lease))
        yield held
    finally:
        for target, entry, lease in reversed(held.leases):
            if target not in held.uncertain:
                target_lease.release(lambda command: remote.ssh_run(entry, command, timeout=60), lease)


@dataclass(frozen=True)
class Services:
    identity: ledger.RuntimeIdentity
    gate: str
    wordpress: str | None


@dataclass(frozen=True)
class WordPressService:
    service: str
    container_path: str


@dataclass(frozen=True)
class BoundRuntime:
    target: ledger.Target
    entry: dict
    cleanup: ledger.CleanupDescriptor
    services: Services
    ownership: Ownership
    wordpress: ledger.WordPressCleanup | WordPressService | None = None

    def require_current(self):
        from sandbox.hosting.share.fanout import inspect_services
        if inspect_services(self.entry, self.cleanup.compose, self.wordpress) != self.services:
            raise ShareRefusal("share_target_changed", "original services changed during sharing")

    def manage(self, verb, name, *extra, payload=None, on_completed=None):
        # Reuse the public validator for the fixed verb/id contract; never run
        # its Compose service-name command after binding immutable services.
        login.site_command("unused", verb, name, *extra)
        self.require_current()
        command = shlex.join(["docker", "exec", "-i", self.services.gate, "python3", "-c",
                              login._LOADER, login._runtime_source(), "site", verb, name, *extra])
        if verb in ("mint", "revoke"):
            output = self.ownership.effect(self.target, self.entry, command, payload=payload)
        else:
            from sandbox.commands.hosting import _remote_checked
            output = _remote_checked(self.entry, command, timeout=60)
        if on_completed is not None:
            on_completed()
        return login.parse_site_reply(output)

    def require_applied(self, found, name):
        """Local declarations select intent; the running gate must prove parity."""
        from sandbox.commands.hosting import _remote_checked
        from sandbox.hosting.share.fanout import CAPABILITIES
        declaration = found.validated["login_service"]
        if declaration.get("share", {}).get("agent_mint") is not True:
            raise ShareRefusal("agent_mint_not_enabled", "current applied sharing opt-in is required")
        raw = _remote_checked(self.entry, shlex.join([
            "docker", "inspect", "--format", "{{json .Config.Cmd}}", self.services.gate]), timeout=15)
        if (not isinstance(raw, str) or len(raw) > 1048576
                or json.loads(raw) != ["python3", "-c", login._LOADER, login._runtime_source()]):
            raise ShareRefusal("share_protocol_unsupported", "the running gate source must match this controller")
        reply = self.manage("capabilities", name)
        expected = login.runtime_config(declaration, target_digest=target_digest(found.validated))
        if (not (CAPABILITIES | {"owner-panel-gate-v1"}).issubset(set(reply.get("capabilities", [])))
                or reply.get("config_digest") != digest(expected)
                or reply.get("panel_access_digest") != expected["panel_access_digest"]
                or reply.get("panel_target_digest") != expected["panel_target_digest"]):
            raise ShareRefusal("share_applied_policy_changed", "apply the selected sharing policy before issuing links")
        self.require_current()

    def require_enabled(self, name):
        """Recovery may lack a manifest; its exact original gate still owns opt-in."""
        from sandbox.commands.hosting import _remote_checked
        raw = _remote_checked(self.entry, shlex.join([
            "docker", "inspect", "--format", "{{json .Config.Cmd}}", self.services.gate]), timeout=15)
        if (not isinstance(raw, str) or len(raw) > 1048576
                or json.loads(raw) != ["python3", "-c", login._LOADER, login._runtime_source()]):
            raise ShareRefusal("share_protocol_unsupported", "the running gate source must match this controller")
        reply = self.manage("capabilities", name)
        if reply.get("panel_agent_mint") is not True:
            raise ShareRefusal("agent_mint_not_enabled", "current applied sharing opt-in is required")

    def install_wordpress(self, wordpress, expires_at):
        from sandbox.hosting.share import stack_page
        if self.cleanup.wordpress is None or self.services.wordpress is None:
            raise ShareRefusal("share_runtime_unknown", "original WordPress service is unavailable")
        expected = self.cleanup.wordpress
        if (wordpress["kid"] != expected.key_id or expires_at != expected.expires_at
                or wordpress["container_path"] != expected.container_path
                or wordpress["service"] != expected.service):
            raise ShareRefusal("share_target_changed", "WordPress cleanup identity changed")
        script, plugin = stack_page.wordpress_key_install(wordpress, expires_at)
        self.require_current()
        self.ownership.effect(self.target, self.entry, shlex.join([
            "docker", "exec", "-i", self.services.wordpress, "sh", "-c", script]), payload=plugin)

    def remove_wordpress(self):
        from sandbox.hosting.share import stack_page
        wp = self.cleanup.wordpress
        if wp is None or self.services.wordpress is None:
            raise ShareRefusal("share_runtime_unknown", "original WordPress service is unavailable")
        path = posixpath.join(stack_page.key_directory(wp.container_path),
                             f"{stack_page.KEY_FILE_PREFIX}{wp.expires_at}-{wp.key_id}.php")
        script = f"rm -f -- {shlex.quote(path)} && test ! -e {shlex.quote(path)} && echo sandbox-key-absent"
        self.require_current()
        return self.ownership.effect(self.target, self.entry, shlex.join([
            "docker", "exec", "-i", self.services.wordpress, "sh", "-c", script]))

    def remove_legacy_wordpress(self, link_id):
        """Remove only this selected legacy link's key, on the bound WP service."""
        from sandbox.hosting.share import stack_page, LINK_ID_RE
        if self.wordpress is None or link_id.startswith("s-"):
            return 0
        if not LINK_ID_RE.fullmatch(link_id) or self.services.wordpress is None:
            raise ShareRefusal("site_link_unknown", "an exact link id is required")
        directory = stack_page.key_directory(self.wordpress.container_path)
        pattern = shlex.quote(directory + "/" + stack_page.KEY_FILE_PREFIX) + "*-" + link_id[2:] + ".php"
        script = ("set -eu; n=0; for f in " + pattern + "; do [ -e \"$f\" ] || continue; "
                  "rm -f -- \"$f\"; test ! -e \"$f\"; n=$((n+1)); done; printf '%s\\n' \"$n\"")
        self.require_current()
        output = self.ownership.effect(self.target, self.entry, shlex.join([
            "docker", "exec", "-i", self.services.wordpress, "sh", "-c", script]))
        if not isinstance(output, str) or not output.strip().isdigit() or len(output.strip()) > 6:
            raise ShareRefusal("share_cleanup_pending", "WordPress removal was not confirmed")
        return int(output.strip())


def bind(target, entry, cleanup, ownership):
    from sandbox.core import _remote as remote, _hosting as hosting
    from sandbox.hosting.share.fanout import inspect_services
    if not any(key == target and registered == entry for key, registered, _ in ownership.leases):
        raise ShareRefusal("share_target_busy", "physical target ownership is required")
    home = remote.resolve_sandbox_home(entry)
    runtime = f"{home}/runtime/hosts/{target.project}/{target.environment}"
    if (cleanup.compose.project_name != hosting.compose_project_name(
            {"project": target.project, "environment": target.environment})
            or cleanup.compose.override_path != runtime + "/compose.override.yml"
            or cleanup.compose.env_path != runtime + "/environment.env"
            or any(not path.startswith(f"{home}/deploy-src/hosts/{target.project}/")
                   for path in cleanup.compose.files)):
        raise ShareRefusal("share_runtime_unknown", "saved cleanup paths do not match the registered target")
    services = inspect_services(entry, cleanup.compose, cleanup.wordpress)
    if services.identity != cleanup.identity:
        raise ShareRefusal("share_runtime_unknown", "original service identity is unproven")
    return BoundRuntime(target, entry, cleanup, services, ownership, cleanup.wordpress)


def bind_current(found, ownership):
    """Bind explicitly selected current legacy grants, never recover an old id by guess."""
    from sandbox.core import _remote as remote, _hosting as hosting
    from sandbox.hosting.share.fanout import inspect_services
    target = ledger.Target(found.remote, found.validated["project"], found.validated["environment"])
    if not any(key == target and entry == found.entry for key, entry, _ in ownership.leases):
        raise ShareRefusal("share_target_busy", "physical target ownership is required")
    home = remote.resolve_sandbox_home(found.entry)
    runtime = f"{home}/runtime/hosts/{target.project}/{target.environment}"
    compose = ledger.ComposeCleanup(hosting.compose_project_name(found.validated), tuple(
        f"{home}/deploy-src/hosts/{target.project}/{path}" for path in found.validated["compose"]["files"]),
        runtime + "/compose.override.yml", runtime + "/environment.env")
    wp = found.validated.get("autologin")
    wordpress = WordPressService(wp.get("service") or found.validated["compose"]["service"], wp["container_path"]) if wp else None
    services = inspect_services(found.entry, compose, wordpress)
    cleanup = ledger.CleanupDescriptor(found.validated["login_service"]["share"]["hostname"], services.identity, compose)
    return BoundRuntime(target, found.entry, cleanup, services, ownership, wordpress)

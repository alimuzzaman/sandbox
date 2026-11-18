"""Tailnet-panel admission and fixed owner-local gate operations.

No remote record, original job, process callback or serialized actor is accepted.
The only issuer repeats the panel HTTP checks using the handler's actual peer.
Metadata selects the target; admission, applied opt-in and current gate proof
together issue the non-serializable action-specific grant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from sandbox.hosting import login_service as login
from sandbox.hosting import panel_records
from sandbox.hosting.owner_share_record import digest
from sandbox.hosting.share import ShareRefusal
from sandbox.jobs import process as _process

_SEAL = object()
_LIVE = {}  # Actual Popen handles only. Never reconstruct one from a pid/body.
# After the original docker CLI is proven gone, wait this long before cleanup
# so an in-container effect still finishing cannot land after the revoke.
EFFECT_DRAIN_SECONDS = 30
_LOADER = ("import base64,sys,zlib; "
           "exec(compile(zlib.decompress(base64.b64decode(sys.argv[1])),'login_service','exec')) "
           "if sys.stdin.buffer.read(1) == b'\\x01' else sys.exit(73)")


def _refuse(code="owner_share_unavailable"):
    return ShareRefusal(code, "Owner-local sharing evidence is missing, changed or uncertain")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PanelRequest:
    action: str
    remote: str
    stack: str
    link_id: str | None
    _seal: object = field(repr=False)

    def __reduce__(self):
        raise TypeError("panel admission cannot be serialized")


def admit(peer, bound_host, headers, allowed_names, payload) -> PanelRequest:
    from sandbox.commands.ui_dash import _share_request_allowed, _share_request
    if not _share_request_allowed(peer, bound_host, headers, allowed_names):
        raise _refuse("panel_request_refused")
    action, remote, stack, link_id = _share_request(payload)
    value = object.__new__(PanelRequest)
    for key, item in zip(("action", "remote", "stack", "link_id", "_seal"),
                         (action, remote, stack, link_id, _SEAL)):
        object.__setattr__(value, key, item)
    return value


def current_owner():
    # This reader is metadata only. It does not itself issue a gate grant.
    from sandbox.application.server_cli import read_current_server_owner
    owner = read_current_server_owner()
    if owner.uid != os.geteuid():
        raise _refuse()
    return owner


def _environment(owner):
    from sandbox.application.server_cli import owner_environment
    return owner_environment(owner)


# One projection per bound container: identity, liveness, the Compose labels
# the full snapshot selected it by, and its command (the gate's loader proof).
_BOUND = ('[{{json .Id}},{{json .State.Running}},'
          '{{json (index .Config.Labels "com.docker.compose.project")}},'
          '{{json (index .Config.Labels "com.docker.compose.service")}},'
          '{{json (index .Config.Labels "com.docker.compose.container-number")}},'
          '{{json .Config.Cmd}},'
          '{{json (index .Config.Labels "com.docker.compose.oneoff")}}]')


def _observe_bound(owner, snapshot):
    """One `docker inspect` of every container the full snapshot bound.

    Returns a comparable tuple of (service, index, full id, command, oneoff
    label). A missing,
    stopped, relabelled or replaced container refuses; the caller compares the
    tuple to the one taken at bind, so any restart into a new id also refuses.
    """
    from sandbox.hosting.lifetime.hoststore import compose_project_for
    rows = [(r["name"], r["index"], r["container_id"]) for r in snapshot["services"]]
    if not rows or len(rows) > 64 or any(not re.fullmatch(r"[0-9a-f]{12,64}", ident) for _, _, ident in rows):
        raise _refuse()
    result = subprocess.run(["docker", "inspect", "--format", _BOUND, *(ident for _, _, ident in rows)],
        cwd=owner.home, env=_environment(owner), capture_output=True, text=True, timeout=10)
    if result.returncode or result.stderr or len(result.stdout) > 1048576:
        raise _refuse("owner_share_target_changed")
    lines = result.stdout.splitlines()
    if len(lines) != len(rows):
        raise _refuse("owner_share_target_changed")
    project = compose_project_for(snapshot["project"], snapshot["environment"])
    observed = []
    for (service, index, ident), line in zip(rows, lines):
        try:
            full_id, running, label_project, label_service, number, command, oneoff = json.loads(line)
        except (ValueError, TypeError):
            raise _refuse() from None
        if (not isinstance(full_id, str) or not re.fullmatch(r"[0-9a-f]{64}", full_id)
                or not full_id.startswith(ident) or running is not True
                or label_project != project or label_service != service or number != str(index)):
            raise _refuse("owner_share_target_changed")
        observed.append((service, index, full_id, json.dumps(command), oneoff))
    return tuple(observed)


def _running_services(owner, compose_project):
    """Full ids of the project's running service containers (not one-off runs).

    A replacement or extra service container that leaves the bound ones running
    changes this set. `docker run` / `compose run` containers carry no
    `oneoff=False` label, so they neither satisfy nor break it.
    """
    result = subprocess.run(["docker", "ps", "--no-trunc", "--quiet",
        "--filter", "label=com.docker.compose.project=" + compose_project,
        "--filter", "label=com.docker.compose.oneoff=False"],
        cwd=owner.home, env=_environment(owner), capture_output=True, text=True, timeout=10)
    ids = result.stdout.split()
    if (result.returncode or result.stderr or len(ids) > 64
            or any(not re.fullmatch(r"[0-9a-f]{64}", ident) for ident in ids)):
        raise _refuse("owner_share_target_changed")
    return set(ids)


def _first(observed, service):
    matches = [row for row in observed if row[0] == service and row[1] == 1]
    if len(matches) != 1:
        raise _refuse()
    return matches[0][2], json.loads(matches[0][3])


@dataclass(frozen=True, slots=True, init=False, repr=False)
class GateGrant:
    request: PanelRequest
    owner: object
    snapshot: dict = field(repr=False)
    summary: dict = field(repr=False)
    gate_id: str
    wordpress_id: str | None
    containers: tuple = field(repr=False)
    gate_verbs: bool
    _seal: object = field(repr=False)

    def __reduce__(self):
        raise TypeError("owner gate grants cannot be serialized")

    @property
    def validated(self):
        return self.summary["owner_share"]["validated"]

    @property
    def entry(self):
        return "share"

    def require_current(self):
        """Prove the bound target and containers are unchanged since bind.

        bind() took the one full server-target snapshot. Every later re-check
        repeats the cheap facts that snapshot rested on: the same installed
        owner, the same applied server summary, an active lifetime record for
        the same copy, and one inspect of every bound container returning the
        same ids, labels and commands, no other running service container in
        the project, and no persisted recovery fence. _execute calls it once, right before an
        effect; apply, destroy and recovery change the summary or the
        containers, and cannot run meanwhile because the effect holds the
        target lease and mutation lock (owner_panel_share._held).
        """
        if self._seal is not _SEAL or self.request._seal is not _SEAL or current_owner() != self.owner:
            raise _refuse()
        from sandbox.hosting.lifetime.hoststore import Store, compose_project_for
        home = Path(self.owner.sandbox_home)
        project, environment = self.snapshot["project"], self.snapshot["environment"]
        try:
            summary = panel_records.parse_target_summary(panel_records._read_json(
                Path(panel_records.summary_path(str(home), project, environment))))
        except (OSError, ValueError):
            raise _refuse("owner_share_target_changed") from None
        if summary != self.summary:
            raise _refuse("owner_share_target_changed")
        compose_project = compose_project_for(project, environment)
        inventory = Store(home).peek(int(time.time()))
        lifetimes = [item for item in inventory["records"] if item.get("compose_project") == compose_project]
        if (inventory.get("truncated") or len(lifetimes) != 1
                or any(item.get("compose_project") == compose_project for item in inventory.get("invalid", ()))
                or lifetimes[0].get("state") != "active" or lifetimes[0].get("reap_run") is not None
                or lifetimes[0].get("copy_id") != self.snapshot["copy_id"]
                or lifetimes[0].get("commit") != self.snapshot["source_origin_commit"]):
            raise _refuse("owner_share_target_changed")
        # A persisted recovery/apply fence left after bind survives the process
        # lock _held takes, so read it again; the locked key set must not grow.
        from sandbox.hosting.host_exec import busy_reason
        from sandbox.hosting.recovery.repository import RecoveryRepository
        try:
            state = RecoveryRepository(home / "runtime" / "hosts.json").load()
        except (OSError, ValueError):
            raise _refuse("owner_share_target_changed") from None
        if getattr(state, "unreadable", ()):
            raise _refuse("owner_share_target_changed")
        keys = sorted(key for key in state["hosts"] if
                      isinstance(key, str) and key.split("/")[1:] == [project, environment])
        if sorted(set(keys + [f"{self.request.remote}/{project}/{environment}"])) != sorted(self.snapshot["native_keys"]):
            raise _refuse("owner_share_target_changed")
        for key in keys:
            if not isinstance(state["hosts"][key], dict) or busy_reason(state["hosts"][key], None):
                raise _refuse("owner_share_target_changed")
        if _running_services(self.owner, compose_project) != {row[2] for row in self.containers if row[4] == "False"}:
            raise _refuse("owner_share_target_changed")
        if _observe_bound(self.owner, self.snapshot) != self.containers:
            raise _refuse("owner_share_gate_changed")


def bind(request: PanelRequest, slug: str | None = None, *, gate_verbs: bool = True) -> GateGrant:
    """Issue the grant from one full target snapshot and one container binding.

    gate_verbs=False (the WordPress admin click) skips the gate's running-config
    query: that effect runs only in the WordPress container. Such a grant can
    never run a gate verb (_execute refuses list/mint/revoke for it).
    """
    if type(request) is not PanelRequest or request._seal is not _SEAL:
        raise _refuse("panel_request_refused")
    from sandbox.application.server_cli_targets import inspect_current_server_target
    owner = current_owner()
    snapshot = inspect_current_server_target(origin_remote_name=request.remote, name=slug or request.stack)
    records = panel_records.read_server_records(Path(owner.sandbox_home) / "runtime")
    matches = [row for row in records["targets"] if row["project"] == snapshot["project"]
               and row["environment"] == snapshot["environment"] and (row.get("stack") or {}).get("slug") == (slug or request.stack)]
    if len(matches) != 1:
        raise _refuse()
    summary = matches[0]
    access = summary.get("owner_share")
    if (not access or summary["last_apply"]["status"] != "succeeded"
            or summary["commit"] != snapshot["source_commit"] or access["copy_id"] != snapshot["copy_id"]
            or access["validated"]["project"] != snapshot["project"]
            or access["validated"]["environment"] != snapshot["environment"]):
        raise _refuse()
    declaration = access["validated"]["login_service"]
    if declaration["share"].get("agent_mint") is not True:
        raise _refuse("share_agent_mint_disabled")
    login.assert_applicable(declaration, now=time.time())
    gate = [row for row in snapshot["services"] if row["name"] == "sandbox-login" and row["index"] == 1]
    if len(gate) != 1 or declaration["entries"].get("share", {}).get("mode") != "site_gate":
        raise _refuse()
    containers = _observe_bound(owner, snapshot)
    gate_id, command = _first(containers, "sandbox-login")
    if command != ["python3", "-c", login._LOADER, login._runtime_source()]:
        raise _refuse("owner_share_upgrade_required")
    wordpress_id = None
    wp = access["validated"].get("autologin")
    if wp:
        wordpress_id = _first(containers, wp.get("service") or access["validated"]["compose"]["service"])[0]
    result = object.__new__(GateGrant)
    for key, value in (("request", request), ("owner", owner), ("snapshot", snapshot), ("summary", summary),
                       ("gate_id", gate_id), ("wordpress_id", wordpress_id), ("containers", containers),
                       ("gate_verbs", gate_verbs is True), ("_seal", _SEAL)):
        object.__setattr__(result, key, value)
    if not result.gate_verbs:
        return result
    reply = _capabilities(result)
    if (reply.get("config_digest") != access["gate_config_digest"]
            or reply.get("panel_access_digest") != digest(declaration)
            or reply.get("panel_target_digest") != access["target_digest"]
            or "owner-panel-gate-v1" not in reply.get("capabilities", [])):
        raise _refuse("owner_share_upgrade_required")
    return result


def _capabilities(grant):
    # Only bind() calls this, directly after observing the bound containers.
    # It is a read-only query; every effect re-checks in _execute.
    result = subprocess.run(["docker", "exec", grant.gate_id, "python3", "-c", login._LOADER,
        login._runtime_source(), "site", "capabilities", grant.entry], cwd=grant.owner.home,
        env=_environment(grant.owner), capture_output=True, text=True, timeout=15)
    if result.returncode or result.stderr or len(result.stdout) > 4096:
        raise _refuse()
    return login.parse_site_reply(result.stdout)


def _execute(grant: GateGrant, verb: str, row, store, *, payload=None, grant_id=None, wordpress=None):
    """Closed effect adapter; stdout contains ids/counts only, stdin holds private bytes.

    The actual child waits for a one-byte release before any effect. Persist its
    real pid and identity first. Timeout retains that exact Popen and fences new
    effects until observe_original settles the operation; never launch a retry.
    """
    if type(grant) is not GateGrant or grant._seal is not _SEAL:
        raise _refuse()
    if verb in ("list", "mint", "revoke") and grant.gate_verbs is not True:
        raise _refuse()
    if verb == "list":
        if grant.request.action not in ("list", "revoke"):
            raise _refuse()
    elif verb in ("mint", "wp_install", "wp_admin"):
        if grant.request.action != "create":
            raise _refuse()
    elif verb in ("revoke", "wp_remove"):
        if grant.request.action not in ("create", "revoke"):
            raise _refuse()
    else:
        raise _refuse()
    # The one re-check per effect: a target or container swapped since bind
    # refuses here, before the child starts.
    grant.require_current()
    data = json.dumps(payload, sort_keys=True).encode() if payload is not None else b""
    if len(data) > 786432:
        raise _refuse()
    if verb in ("wp_install", "wp_remove", "wp_admin"):
        from sandbox.hosting.share import stack_page
        wp = grant.validated["autologin"]
        if not grant.wordpress_id or not re.fullmatch(r"l-(?:[0-9a-f]{12}|[0-9a-f]{32})", str(grant_id)):
            raise _refuse()
        directory = stack_page.key_directory(wp["container_path"])
        if verb == "wp_admin":
            from sandbox.hosting import autologin
            target = wp["container_path"]
            literal = stack_page._php
            program = ("if (fread(STDIN,1)!==chr(1)) exit(73); umask(0022); "
                f"$t={literal(target)}; $d=dirname($t); "
                "if (!is_dir($d) && !mkdir($d,0755,true)) exit(74); "
                "$tmp=$t.'.tmp.'.getmypid(); $bytes=stream_get_contents(STDIN); "
                "if (file_put_contents($tmp,$bytes)!==strlen($bytes) || !chmod($tmp,0644) || !rename($tmp,$t)) exit(75); "
                + autologin.register_script(target, wordpress["token_hash"], wp["user"],
                    wordpress["issued_at"], wordpress["expires_at"]))
            data = autologin.render_plugin().encode()
        elif verb == "wp_install":
            plugin = stack_page.render_key_plugin(wordpress["kid"], wordpress["key"], wp["user"],
                wordpress["expires_at"], claim_origin=wordpress["claim_origin"])
            target = str(Path(directory) / f"{stack_page.KEY_FILE_PREFIX}{wordpress['expires_at']}-{wordpress['kid']}.php")
            literal = stack_page._php
            program = ("if (fread(STDIN,1)!==chr(1)) exit(73); umask(0022); "
                f"$d={literal(directory)}; $t={literal(target)}; "
                "if (!is_dir($d) && !mkdir($d,0755,true)) exit(74); "
                "$tmp=$t.'.tmp.'.getmypid(); $bytes=stream_get_contents(STDIN); "
                "if (file_put_contents($tmp,$bytes)!==strlen($bytes) || !chmod($tmp,0644) || !rename($tmp,$t)) exit(75);")
            data = plugin.encode()
        else:
            pattern = str(Path(directory) / f"{stack_page.KEY_FILE_PREFIX}*-{grant_id[2:]}.php")
            program = ("if (fread(STDIN,1)!==chr(1)) exit(73); "
                f"foreach (glob({stack_page._php(pattern)}) as $f) {{ if (!unlink($f)) exit(75); }}")
        argv = ["docker", "exec", "-i", grant.wordpress_id, "php", "-r", program]
    else:
        extra = [grant_id] if verb == "revoke" else []
        if extra and not re.fullmatch(r"[ls]-(?:[0-9a-f]{12}|[0-9a-f]{32})", str(grant_id)):
            raise _refuse()
        argv = ["docker", "exec", "-i", grant.gate_id, "python3", "-c", _LOADER,
                login._runtime_source(), "site", verb, grant.entry, *extra]
    child = subprocess.Popen(argv, cwd=grant.owner.home, env=_environment(grant.owner),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    operation = "o-" + uuid.uuid4().hex
    _LIVE[operation] = child
    identity = _process.capture_process_identity(child.pid)
    row["operation"] = {"id": operation, "verb": verb, "pid": child.pid, "state": "accepted",
                        "grant_id": grant_id,
                        "boot_id": identity.host_boot_id if identity else None,
                        "start": identity.start_identity if identity else None}
    try:
        store.save(row)
    except BaseException:
        child.communicate(b"", timeout=5)  # never release the barrier
        raise
    try:
        stdout, stderr = child.communicate(b"\x01" + data, timeout=30)
        if child.returncode or stderr or len(stdout) > 1048576:
            raise _refuse("owner_share_effect_unknown")
        # No re-check after the effect: it ran by full container id, which only
        # the bound container answers, and proving it afterwards changes nothing.
        if verb.startswith("wp_"):
            reply = {"ok": True}
        else:
            decoded = json.loads(stdout.decode())
            if verb == "revoke" and decoded == {"ok": False, "code": "site_link_unknown"}:
                # Original identity was re-proved. Gate restart/previous revoke
                # losing this exact id is absence, not a new cleanup target.
                reply = {"ok": True, "revoked_links": 0, "revoked_sessions": 0}
            else:
                reply = login.parse_site_reply(stdout.decode())
        if verb == "mint":
            expected = payload["link"]
            if (reply.get("id") != grant_id or type(reply.get("expires_at")) is not int
                    or reply["expires_at"] != expected["expires_at"]
                    or type(reply.get("uses")) is not type(expected["uses"])
                    or reply.get("uses") != expected["uses"]):
                raise _refuse("owner_share_effect_unknown")
        if reply.get("ok") is not True:
            raise _refuse("owner_share_effect_unknown")
        row["operation"]["state"] = "completed"
        store.save(row)
        _LIVE.pop(operation, None)
        return reply
    except BaseException:
        row["operation"]["state"] = "unknown"
        if child.poll() is not None:
            row["operation"]["exited_at"] = time.time()  # a completed failure, not a live one
        row["phase"] = "cleanup_pending"
        store.save(row)
        raise _refuse("owner_share_effect_unknown") from None


def _original_gone(operation):
    """True only when the recorded docker CLI process provably no longer exists."""
    pid, boot, start = operation.get("pid"), operation.get("boot_id"), operation.get("start")
    if _process.process_absence_proven(pid):
        return True  # No process holds the pid at all, so the original is gone.
    if not _process.is_boot_session_identity(boot) or not isinstance(start, str) or not start:
        return False  # A live pid and no recorded identity: it may be the original.
    observed = _process.capture_process_identity(pid)
    return observed is not None and (observed.host_boot_id != boot or observed.start_identity != start)


def observe_original(grant: GateGrant, row, store):
    """Settle an operation whose original docker CLI is finished. Never re-launch it.

    A clean zero exit settles at once; an invalid reply still needs cleanup,
    never a new mint. A failed exit, or after a panel restart a recorded process
    proven gone (other boot, absent pid, or a reused pid), settles once
    EFFECT_DRAIN_SECONDS have passed. A process that may still run stays unknown.
    """
    operation = row.get("operation") or {}
    if operation.get("state") not in ("accepted", "unknown"):
        return
    child = _LIVE.get(operation.get("id"))
    try:
        if operation.get("exited_at") is None:
            if child is not None:
                # Resume draining the same pipes too; a blocked stdout writer must not
                # require another process or guessed pid-based completion.
                stdout, stderr = child.communicate(timeout=0.05)
                clean = child.returncode == 0 and not stderr and len(stdout) <= 1048576
            elif _original_gone(operation):
                clean = False
            else:
                return
            if not clean:
                operation["exited_at"] = time.time()
                store.save(row)
        if (operation.get("exited_at") is not None
                and time.time() - operation["exited_at"] < EFFECT_DRAIN_SECONDS):
            return
        grant.require_current()
        operation["state"] = "settled"
        row["phase"] = "cleanup_pending"
        store.save(row)
        _LIVE.pop(operation["id"], None)
    except Exception:
        return

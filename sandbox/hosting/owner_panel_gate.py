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

_SEAL = object()
_LIVE = {}  # Actual Popen handles only. Never reconstruct one from a pid/body.
_LOADER = "import sys; exec(sys.argv[1]) if sys.stdin.buffer.read(1) == b'\\x01' else sys.exit(73)"


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


def _inspect(owner, container):
    if not re.fullmatch(r"[0-9a-f]{12,64}", container):
        raise _refuse()
    result = subprocess.run(["docker", "inspect", "--format", "{{json .Id}} {{json .Config.Cmd}}", container],
        cwd=owner.home, env=_environment(owner), capture_output=True, text=True, timeout=10)
    if result.returncode or result.stderr or len(result.stdout) > 262144:
        raise _refuse()
    identifier, command = result.stdout.strip().split(" ", 1)
    identifier, command = json.loads(identifier), json.loads(command)
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise _refuse()
    return identifier, command


@dataclass(frozen=True, slots=True, init=False, repr=False)
class GateGrant:
    request: PanelRequest
    owner: object
    snapshot: dict = field(repr=False)
    summary: dict = field(repr=False)
    gate_id: str
    wordpress_id: str | None
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
        if self._seal is not _SEAL or self.request._seal is not _SEAL or current_owner() != self.owner:
            raise _refuse()
        from sandbox.application.server_cli_targets import inspect_current_server_target
        observed = inspect_current_server_target(origin_remote_name=self.request.remote,
            name=self.summary["stack"]["slug"])
        if observed != self.snapshot:
            raise _refuse("owner_share_target_changed")
        for service, index, ident in ((r["name"], r["index"], r["container_id"]) for r in observed["services"]):
            if index == 1 and service == "sandbox-login":
                full_id, command = _inspect(self.owner, ident)
                if full_id != self.gate_id or command != ["python3", "-c", login._LOADER, login._runtime_source()]:
                    raise _refuse("owner_share_gate_changed")
            wp = self.validated.get("autologin")
            if wp and index == 1 and service == (wp.get("service") or self.validated["compose"]["service"]):
                if _inspect(self.owner, ident)[0] != self.wordpress_id:
                    raise _refuse("owner_share_gate_changed")


def bind(request: PanelRequest, slug: str | None = None) -> GateGrant:
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
    gate_id, command = _inspect(owner, gate[0]["container_id"])
    if command != ["python3", "-c", login._LOADER, login._runtime_source()]:
        raise _refuse("owner_share_upgrade_required")
    wordpress_id = None
    wp = access["validated"].get("autologin")
    if wp:
        services = [row for row in snapshot["services"] if row["index"] == 1
                    and row["name"] == (wp.get("service") or access["validated"]["compose"]["service"])]
        if len(services) != 1:
            raise _refuse()
        wordpress_id = _inspect(owner, services[0]["container_id"])[0]
    result = object.__new__(GateGrant)
    for key, value in (("request", request), ("owner", owner), ("snapshot", snapshot), ("summary", summary),
                       ("gate_id", gate_id), ("wordpress_id", wordpress_id), ("_seal", _SEAL)):
        object.__setattr__(result, key, value)
    result.require_current()
    reply = _capabilities(result)
    if (reply.get("config_digest") != access["gate_config_digest"]
            or reply.get("panel_access_digest") != digest(declaration)
            or reply.get("panel_target_digest") != access["target_digest"]
            or "owner-panel-gate-v1" not in reply.get("capabilities", [])):
        raise _refuse("owner_share_upgrade_required")
    result.require_current()
    return result


def _capabilities(grant):
    grant.require_current()
    result = subprocess.run(["docker", "exec", grant.gate_id, "python3", "-c", login._LOADER,
        login._runtime_source(), "site", "capabilities", grant.entry], cwd=grant.owner.home,
        env=_environment(grant.owner), capture_output=True, text=True, timeout=15)
    if result.returncode or result.stderr or len(result.stdout) > 4096:
        raise _refuse()
    return login.parse_site_reply(result.stdout)


def _execute(grant: GateGrant, verb: str, row, store, *, payload=None, grant_id=None, wordpress=None):
    """Closed effect adapter; stdout contains ids/counts only, stdin holds private bytes.

    The actual child waits for a one-byte release before any effect. Persist its
    real pid first. Timeout retains that exact Popen and permanently fences new
    effects until original-handle observation/recovery; never launch a retry.
    """
    if type(grant) is not GateGrant or grant._seal is not _SEAL:
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
    row["operation"] = {"id": operation, "verb": verb, "pid": child.pid, "state": "accepted",
                        "grant_id": grant_id}
    try:
        store.save(row)
    except BaseException:
        child.communicate(b"", timeout=5)  # never release the barrier
        raise
    try:
        stdout, stderr = child.communicate(b"\x01" + data, timeout=30)
        if child.returncode or stderr or len(stdout) > 1048576:
            raise _refuse("owner_share_effect_unknown")
        grant.require_current()
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
        row["phase"] = "cleanup_pending"
        store.save(row)
        raise _refuse("owner_share_effect_unknown") from None


def observe_original(grant: GateGrant, row, store):
    """Observe only a retained original handle. Never infer completion from a pid.

    Docker CLI zero exit confirms the original exec finished; an invalid reply
    still needs cleanup, never a new mint. Nonzero/transport loss remains unknown.
    After process restart there is deliberately no guessed replacement handle.
    """
    operation = row.get("operation") or {}
    if operation.get("state") not in ("accepted", "unknown"):
        return
    child = _LIVE.get(operation.get("id"))
    if child is None:
        return
    try:
        # Resume draining the same pipes too; a blocked stdout writer must not
        # require another process or guessed pid-based completion.
        stdout, stderr = child.communicate(timeout=0.05)
        if child.returncode != 0 or stderr or len(stdout) > 1048576:
            return
        grant.require_current()
        operation["state"] = "settled"
        row["phase"] = "cleanup_pending"
        store.save(row)
        _LIVE.pop(operation["id"], None)
    except Exception:
        return

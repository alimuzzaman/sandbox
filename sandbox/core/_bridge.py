from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr


def save_local_bridge_token(token: str, instance: str) -> None:
    """Persist the per-instance snapshot-bridge token in sandbox.local.yml.
    The sb web bridge authenticates dashboard snapshot calls against it."""
    ensure_pyyaml()
    import yaml
    local = {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
    local.setdefault("instances", {}).setdefault(instance, {})["bridge_token"] = token
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)


def _write_snapshot_muplugin(instance: str, token: str) -> None:
    """Drop 00-sandbox-snapshots.php into the instance — a sandbox-only wp-admin
    screen (Tools → Sandbox Snapshots) that takes/restores/lists/deletes snapshots
    by calling the host `sb web` bridge. PHP admin-ajax handlers enforce nonce +
    manage_options, then call the bridge over host.docker.internal with the
    per-instance Bearer token (the bridge runs the host-level sb snapshot/restore
    out-of-band). Regenerated on every install/recreate."""
    mu_dir = wp_dir(instance) / "wp-content" / "mu-plugins"
    mu_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://host.docker.internal:{BRIDGE_PORT}"
    php = _SNAPSHOT_MU_TEMPLATE.replace("%URL%", url) \
                               .replace("%TOKEN%", token) \
                               .replace("%INSTANCE%", instance)
    (mu_dir / "00-sandbox-snapshots.php").write_text(php)


def _bridge_port_up() -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", BRIDGE_PORT)) == 0
    finally:
        s.close()


def _ensure_bridge_server() -> None:
    """Start the `sb web` snapshot bridge on BRIDGE_PORT if it isn't already
    running (idempotent, FR-014). Verifies any existing listener is actually our
    bridge (not a foreign service squatting the port — in which case we leave it
    alone rather than spawn over it / trust it), and serializes startup with a
    stale-aware lock so two concurrent `sb up`s don't double-spawn."""
    import time, urllib.request, json as _json
    if _bridge_port_up():
        try:  # confirm it's OUR bridge (dashboard route returns {"instances":…})
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{BRIDGE_PORT}/api/instances", timeout=0.5) as r:
                _json.loads(r.read() or b"{}")
        except Exception:
            pass
        return  # something is serving the port — don't spawn a competing one
    lock = RUNTIME_DIR / "locks" / "bridge-web.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and (time.time() - lock.stat().st_mtime) > 30:
            lock.unlink(missing_ok=True)  # stale (a previous start crashed)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return  # another start is already in flight
    try:
        subprocess.Popen(
            [str(ENTRY), "web", "--port", str(BRIDGE_PORT), "--exact-port"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        for _ in range(20):           # hold the lock until it's accepting (≤~6s)
            time.sleep(0.3)
            if _bridge_port_up():
                break
    finally:
        lock.unlink(missing_ok=True)


def _valid_snapshot_name(name: str) -> bool:
    """A safe snapshot name: alphanumeric-led token of [A-Za-z0-9_.-], with no
    `..` segment — so `snapshots_dir(inst) / name` can never escape the tree.
    The bridge (not the mu-plugin) is the trust boundary, so it validates every
    name before any filesystem use."""
    return bool(re.fullmatch(r"[A-Za-z0-9][\w.-]*", name or "")) and ".." not in (name or "")


def _slug_snapshot_name(raw: str) -> str:
    """Turn free-form user input into a safe snapshot slug (post-slug style):
    lowercased, every run of non-alphanumerics collapsed to a single hyphen,
    ends trimmed — so `snapshot 2` becomes `snapshot-2`. Returns '' when nothing
    usable survives (e.g. '..' or '/'). The result always satisfies
    _valid_snapshot_name, so a slugified name can never escape the tree."""
    return re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower()).strip("-")


def _bridge_token_for(instance: str) -> str | None:
    return ((_local_yaml().get("instances") or {}).get(instance) or {}).get("bridge_token")


def _bridge_handle(method: str, instance: str, subpath: str,
                   body: dict, auth: str) -> tuple[int, dict]:
    """Token-authed snapshot bridge for the wp-admin mu-plugin (spec 002).

    Only these verbs, only for `instance`, only with the matching Bearer token:
      GET /snapshots · POST /snapshot {name,force} · POST /restore {name}
      DELETE /snapshot/<name> · GET /job/<id>
    Snapshot/restore run out-of-band via the existing job machinery so a restore
    never severs the caller's request. NO arbitrary host commands (FR-010)."""
    import time, shutil as _sh, types as _types, hmac
    tok = _bridge_token_for(instance)
    if not tok:
        return 404, {"ok": False, "error": "unknown instance"}
    presented = (auth or "").removeprefix("Bearer ").strip()
    if not presented or not hmac.compare_digest(presented, tok):
        return 403, {"ok": False, "error": "unauthorized"}
    if _is_herd_instance(instance):
        return 409, {"ok": False, "error": "unsupported", "reason": "herd"}
    _ok_name = _valid_snapshot_name  # applied to EVERY name the bridge turns into
    from sandbox.commands.data import cmd_snapshot, cmd_restore  # late: avoid cycle
    cfg = load_config()

    if method == "GET" and subpath == "/snapshots":
        root = snapshots_dir(instance)
        snaps = []
        if root.exists():
            for e in sorted(root.iterdir()):
                # Skip reserved internal baselines (e.g. __install__): leading-
                # underscore names aren't user snapshots and fail _valid_snapshot_name,
                # so listing them only yields an "invalid snapshot name" on any action.
                if not e.is_dir() or e.name.startswith("_"):
                    continue
                meta = ((e / "META").read_text().strip().replace("\n", " ")
                        if (e / "META").exists() else "")
                size = sum(f.stat().st_size for f in e.rglob("*") if f.is_file())
                snaps.append({"name": e.name, "size_kb": size // 1024, "meta": meta})
        return 200, {"ok": True, "snapshots": snaps}

    if method == "POST" and subpath == "/snapshot":
        raw = (body.get("name") or "").strip()
        # Slugify free-form input instead of rejecting it: "snapshot 2" → the
        # snapshot "snapshot-2". Empty input → a timestamped default.
        name = _slug_snapshot_name(raw) if raw else time.strftime("snap-%Y%m%d-%H%M%S")
        if not name or not _ok_name(name):
            return 400, {"ok": False, "error":
                         f"could not derive a snapshot name from '{raw}' — use "
                         "letters, numbers, spaces or hyphens"}
        ns = _types.SimpleNamespace(resolved_instance=instance, name=name,
                                    force=bool(body.get("force")))
        jid = _start_job(f"snapshot {name}", lambda: cmd_snapshot(cfg, ns))
        return 202, {"ok": True, "job_id": jid, "name": name}

    if method == "POST" and subpath == "/restore":
        name = (body.get("name") or "").strip()
        if not _ok_name(name):
            return 400, {"ok": False, "error": "invalid snapshot name"}
        if not (snapshots_dir(instance) / name).exists():
            return 404, {"ok": False, "error": "no such snapshot"}
        ns = _types.SimpleNamespace(resolved_instance=instance, name=name)
        jid = _start_job(f"restore {name}", lambda: cmd_restore(cfg, ns))
        return 202, {"ok": True, "job_id": jid, "name": name}

    if method == "DELETE" and subpath.startswith("/snapshot/"):
        from urllib.parse import unquote
        name = unquote(subpath[len("/snapshot/"):])
        if not _ok_name(name):
            return 400, {"ok": False, "error": "invalid snapshot name"}
        d = snapshots_dir(instance) / name
        if not d.exists():
            return 404, {"ok": False, "error": "no such snapshot"}
        _sh.rmtree(d)
        return 200, {"ok": True}

    if method == "GET" and subpath.startswith("/job/"):
        snap = _job_snapshot(subpath[len("/job/"):])
        if snap is None:
            return 404, {"ok": False, "error": "no such job"}
        status = ("running" if not snap["done"]
                  else ("succeeded" if snap["ok"] else "failed"))
        return 200, {"ok": True, "status": status, "detail": snap["status"]}

    return 404, {"ok": False, "error": "not found"}

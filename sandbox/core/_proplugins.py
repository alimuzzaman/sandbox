"""Pro-plugin store mirroring (local catalog -> remote host).

Pro plugins live as local checkouts/extracted zips in ONE directory on the
developer machine (`defaults.pro_plugins_home`, e.g. ~/Sites/plugins-pro) and
are registered slug -> path in the user-global catalog, which makes every LOCAL
instance offer them on the wp-admin *Plugins -> Sandbox On-Demand* page without
installing them (spec 010: a catalog-only path resolves to on-demand).

A remote VPS has no such directory, so that same page is empty there. This
module pushes the whole store to `<remote $SANDBOX_HOME>/plugins-pro` once and
merges the mirrored slugs into the REMOTE user-global catalog, so every remote
instance on that host resolves the same slugs on demand — no per-instance
config, no per-plugin flag, no wp.org download fallback.

One-way and idempotent, like `./sb deploy`: the mirror is a copy of the local
store as of the last push (`rsync --delete`), never a continuous sync. A content
fingerprint receipt makes an unchanged re-push a no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from sandbox.core._paths import RUNTIME_DIR
import sandbox.core._remote as sr


DEFAULT_STORE = "~/Sites/plugins-pro"
STORE_DIRNAME = "plugins-pro"

# Build junk and VCS metadata: never needed by the on-demand installer (which
# zips the directory as-is) and they dominate transfer time when present.
EXCLUDES = (".git/", ".DS_Store", "node_modules/", ".idea/", ".vscode/")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HEADER_BYTES = 8192
_RSYNC_TIMEOUT = 1800


def local_store(cfg: dict | None = None) -> Path | None:
    """Resolve the local pro-plugin store, or None when the machine has none.

    Order: `SANDBOX_PRO_PLUGINS` env, `defaults.pro_plugins_home` in
    sandbox.yml / sandbox.local.yml, then the conventional default. A configured
    path that does not exist is an error (a typo must not silently no-op); the
    unconfigured default simply resolves to None."""
    raw = os.environ.get("SANDBOX_PRO_PLUGINS") or ""
    configured = bool(raw)
    if not raw:
        raw = str(((cfg or {}).get("defaults") or {}).get("pro_plugins_home") or "")
        configured = bool(raw)
    if not raw:
        raw = DEFAULT_STORE
    path = Path(raw).expanduser()
    if not path.is_dir():
        if configured:
            raise ValueError(f"pro-plugin store not found at {path}")
        return None
    return path.resolve()


def remote_store(remote: dict) -> str:
    """`<resolved remote $SANDBOX_HOME>/plugins-pro` — one store per host."""
    return f"{sr.resolve_sandbox_home(remote)}/{STORE_DIRNAME}"


def _is_excluded(rel: Path) -> bool:
    names = set(rel.parts)
    return any(part.rstrip("/") in names for part in EXCLUDES)


def store_plugins(store: Path) -> dict[str, dict]:
    """Map slug -> {"dir", "files", "bytes"} for every WordPress plugin directory
    directly under the store. A directory qualifies when a top-level PHP file
    carries a `Plugin Name:` header — the same thing WordPress looks for, so the
    mirrored catalog never advertises a slug WordPress would refuse to install."""
    found: dict[str, dict] = {}
    for child in sorted(store.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        if not _SLUG_RE.fullmatch(child.name):
            continue
        if not _has_plugin_header(child):
            continue
        files = size = 0
        for path in child.rglob("*"):
            if path.is_file() and not path.is_symlink():
                files += 1
                try:
                    size += path.stat().st_size
                except OSError:
                    pass
        found[child.name] = {"dir": str(child), "files": files, "bytes": size}
    return found


def _has_plugin_header(directory: Path) -> bool:
    for php in sorted(directory.glob("*.php")):
        try:
            with php.open("rb") as handle:
                head = handle.read(_HEADER_BYTES)
        except OSError:
            continue
        if b"Plugin Name:" in head:
            return True
    return False


def fingerprint(store: Path) -> str:
    """Content fingerprint of the store: relative path + size + mtime for every
    file that would be transferred. Cheap enough to run before every deploy and
    strict enough that an edited plugin file forces a re-push."""
    digest = hashlib.sha256()
    for path in sorted(store.rglob("*")):
        rel = path.relative_to(store)
        if _is_excluded(rel):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{rel}\0{stat.st_size}\0{int(stat.st_mtime)}\0".encode())
    return digest.hexdigest()


def _receipt_path(remote_name: str) -> Path:
    return Path(RUNTIME_DIR) / "pro-plugins" / f"{sr.validate_remote_name(remote_name)}.json"


def read_receipt(remote_name: str) -> dict:
    try:
        value = json.loads(_receipt_path(remote_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_receipt(remote_name: str, payload: dict) -> None:
    target = _receipt_path(remote_name)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)


# Runs on the remote with the payload on stdin: no interpolation of local
# values into the program text, and the catalog is replaced atomically.
_CATALOG_PROGRAM = r'''
import json, os, sys, tempfile
payload = json.load(sys.stdin)
store = payload["store"]
home = payload["home"]
entries = payload["plugins"]
path = os.path.join(home, "config.json")
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle) or {}
if not isinstance(data, dict):
    raise SystemExit("remote catalog is not a JSON object")
plugins = data.get("plugins")
if not isinstance(plugins, dict):
    plugins = {}
prefix = store.rstrip("/") + "/"
added, kept, removed, conflicts = [], [], [], []
for slug, value in sorted(plugins.items()):
    # Only entries this mirror owns are reclaimed; anything the host configured
    # by hand stays exactly as it is.
    if isinstance(value, str) and value.startswith(prefix) and slug not in entries:
        del plugins[slug]
        removed.append(slug)
for slug, plugin_path in sorted(entries.items()):
    current = plugins.get(slug)
    if current == plugin_path:
        kept.append(slug)
    elif current is None or (isinstance(current, str) and current.startswith(prefix)):
        plugins[slug] = plugin_path
        added.append(slug)
    else:
        conflicts.append(slug)
data["plugins"] = plugins
descriptor, temporary = tempfile.mkstemp(prefix=".config.json.", dir=home)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print(json.dumps({"ok": True, "catalog": path, "added": added, "kept": kept,
                  "removed": removed, "conflicts": conflicts}))
'''


def _update_remote_catalog(remote: dict, home: str, store: str,
                           slugs: list[str]) -> dict:
    payload = json.dumps({
        "home": home,
        "store": store,
        "plugins": {slug: f"{store}/{slug}" for slug in slugs},
    })
    program = f"python3 -c {shlex.quote(_CATALOG_PROGRAM)}"
    result = sr.ssh_run(remote, program, timeout=60, input_data=payload)
    if result.returncode != 0:
        raise RuntimeError(
            "remote catalog update failed: "
            f"{sr._safe_remote_diagnostic(result, remote, limit=500)}"
        )
    try:
        return json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("remote catalog update returned no result") from exc


def _rsync_argv(remote: dict, store: Path, destination: str) -> list[str]:
    argv = ["rsync", "--archive", "--delete", "--compress", "--quiet"]
    for pattern in EXCLUDES:
        argv.extend(["--exclude", pattern])
    argv.extend(["-e", sr.git_ssh_command(remote)])
    target = sr.remote_ssh_parts(remote)["target"]
    argv.extend([f"{store}/", f"{target}:{destination}/"])
    return argv


def sync(remote: dict, remote_name: str, *, cfg: dict | None = None,
         force: bool = False, dry_run: bool = False) -> dict:
    """Mirror the local pro-plugin store onto `remote` and register its slugs in
    the remote user-global catalog. Returns a JSON-safe summary; a machine with
    no store is reported as skipped rather than treated as a failure, so this can
    be called unconditionally from `./sb deploy`."""
    store = local_store(cfg)
    if store is None:
        return {"ok": True, "skipped": "no_local_store", "slugs": [],
                "store": None, "remote_store": None}
    plugins = store_plugins(store)
    if not plugins:
        return {"ok": True, "skipped": "empty_store", "slugs": [],
                "store": str(store), "remote_store": None}
    slugs = sorted(plugins)
    digest = fingerprint(store)
    receipt = read_receipt(remote_name)
    destination = remote_store(remote)
    summary = {
        "ok": True, "store": str(store), "remote_store": destination,
        "slugs": slugs, "fingerprint": digest,
        "bytes": sum(entry["bytes"] for entry in plugins.values()),
    }
    if dry_run:
        summary["skipped"] = "dry_run"
        summary["would_push"] = force or receipt.get("fingerprint") != digest
        return summary
    if not force and receipt.get("fingerprint") == digest \
            and receipt.get("remote_store") == destination:
        summary["skipped"] = "unchanged"
        summary["catalog"] = receipt.get("catalog")
        return summary

    prepare = sr.ssh_run(remote, f"mkdir -p {shlex.quote(destination)}", timeout=30)
    if prepare.returncode != 0:
        raise RuntimeError(
            "could not create the remote pro-plugin store: "
            f"{sr._safe_remote_diagnostic(prepare, remote, limit=500)}"
        )
    transfer = subprocess.run(_rsync_argv(remote, store, destination),
                              capture_output=True, text=True,
                              timeout=_RSYNC_TIMEOUT, check=False)
    if transfer.returncode != 0:
        raise RuntimeError(
            "pro-plugin transfer failed: "
            f"{sr._safe_remote_diagnostic(transfer, remote, limit=500)}"
        )
    home = destination.rsplit("/", 1)[0]
    catalog = _update_remote_catalog(remote, home, destination, slugs)
    summary["catalog"] = catalog.get("catalog")
    summary["registered"] = sorted(set(catalog.get("added", []))
                                   | set(catalog.get("kept", [])))
    summary["unregistered"] = catalog.get("removed", [])
    summary["conflicts"] = catalog.get("conflicts", [])
    _write_receipt(remote_name, {
        "fingerprint": digest, "remote_store": destination,
        "catalog": summary["catalog"], "slugs": slugs,
        "pushed_at": int(time.time()),
    })
    return summary

"""sandbox_core — shared core for the Sandbox CLI (`sb`) and MCP server.

Single source of truth so `sb` and `mcp/wp-server/server.py` resolve a plugin
project's config identically. Currently provides per-project config loading
(T0.1); the instance registry / ensure_instance / test-harness helpers land in
later tasks and belong here too.

A "project" is a plugin checkout that carries its own config. Resolution order
for a directory:

    sandbox.config.json | sandbox.config.yml   (canonical, native)
      + sandbox.config.override.{json,yml}      (gitignored, deep-merged on top)
    .wp-env.json                                (import/fallback only)
    built-in defaults

No central catalog is consulted — the project file is authoritative.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

# Native config is canonical; .wp-env.json is import-only; .git marks a repo root.
CONFIG_BASENAMES = ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml")
OVERRIDE_BASENAMES = (
    "sandbox.config.override.json",
    "sandbox.config.override.yml",
    "sandbox.config.override.yaml",
)
WPENV_BASENAMES = (".wp-env.json",)
ROOT_MARKERS = CONFIG_BASENAMES + WPENV_BASENAMES + (".git",)

# Normalised schema returned by load_project_config(). `null` version fields mean
# "use the wordpress:latest default" — no implicit pinning.
DEFAULTS: dict = {
    "plugins": ["."],          # "." = this repo; others are slugs/paths/zip URLs
    "themes": [],
    "mappings": {},            # wp-path -> host path, bind-mounted (not activated)
    "phpVersion": None,
    "wpVersion": None,
    "multisite": False,
    "server": "apache",        # apache | nginx | litespeed  (herd: backlog)
    "config": {},              # wp-config constants -> WORDPRESS_CONFIG_EXTRA
    "port": None,              # preferred port; None = auto-assign
    "tests": {"suite": "auto"},  # auto | unit | integration
}


class ConfigError(Exception):
    """Raised for an unreadable, malformed, or disallowed project config."""


# --------------------------------------------------------------------------- #
# Path safety + project-root discovery
# --------------------------------------------------------------------------- #

def _allowed_roots() -> list[Path]:
    """Directories a project may live under. Home covers ~/Sites, ~/dev, and
    git worktrees; extra roots (repos outside home) via SANDBOX_PROJECT_ROOTS
    (colon-separated)."""
    roots = [Path.home().resolve()]
    for r in filter(None, os.environ.get("SANDBOX_PROJECT_ROOTS", "").split(":")):
        try:
            roots.append(Path(r).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    return roots


def _is_allowed(path: Path) -> bool:
    p = path.resolve()
    for root in _allowed_roots():
        try:
            p.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def find_project_root(start) -> Path:
    """Walk up from `start` to the nearest dir holding a project marker
    (sandbox.config.* / .wp-env.json / .git). Falls back to `start` itself if
    none is found. Rejects paths outside the allowlist (no projectDir=/etc)."""
    start = Path(start).expanduser().resolve()
    if start.is_file():
        start = start.parent
    if not start.exists():
        raise ConfigError(f"project dir does not exist: {start}")
    if not _is_allowed(start):
        raise ConfigError(
            f"path not allowed: {start} "
            f"(must be under $HOME or a SANDBOX_PROJECT_ROOTS entry)"
        )
    cur = start
    while True:
        if any((cur / m).exists() for m in ROOT_MARKERS):
            return cur
        if cur.parent == cur:
            return start  # nothing found — treat the start dir as the root
        cur = cur.parent


# --------------------------------------------------------------------------- #
# Loading + merging
# --------------------------------------------------------------------------- #

def _load_doc(path: Path) -> dict:
    text = path.read_text()
    if path.suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as e:  # pragma: no cover - env dependent
            raise ConfigError(f"{path.name} needs PyYAML installed") from e
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected an object at the top level")
    return data


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _first_existing(root: Path, names) -> Path | None:
    return next((root / n for n in names if (root / n).exists()), None)


def _wp_version_from_core(core) -> str | None:
    """Map a .wp-env.json `core` to a WP version: a wordpress-X.Y.Z.zip URL or a
    bare version becomes that version; branches/other URLs -> None (= latest)."""
    if not core:
        return None
    m = re.search(r"wordpress-([\d.]+)\.zip", str(core), re.I)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\d.]+", str(core)):
        return str(core)
    return None


def _from_wp_env(raw: dict) -> dict:
    """Map a .wp-env.json document onto the native schema. testsPort /
    testsEnvironment / autoPort are intentionally dropped (single-site)."""
    return {
        "plugins": raw.get("plugins", []),
        "themes": raw.get("themes", []),
        "mappings": raw.get("mappings", {}) or {},
        "phpVersion": raw.get("phpVersion"),
        "wpVersion": _wp_version_from_core(raw.get("core")),
        # Pass through as-is: false | true | "subdirectory" | "subdomain"
        # (a bool() coercion would flatten "subdomain" to plain true).
        "multisite": raw.get("multisite", False),
        "config": raw.get("config", {}) or {},
        "port": raw.get("port"),
        "_imported_from": ".wp-env.json",
        "_ignored": [k for k in ("testsPort", "testsEnvironment", "autoPort") if k in raw],
    }


def load_project_config(project_dir) -> dict:
    """Resolve the effective config for a project directory.

    Returns the normalised schema (DEFAULTS keys) plus:
      root:    absolute project root
      source:  which file(s) the config came from
    """
    root = find_project_root(project_dir)

    native = _first_existing(root, CONFIG_BASENAMES)
    if native:
        data = _load_doc(native)
        source = native.name
    else:
        wpenv = _first_existing(root, WPENV_BASENAMES)
        if wpenv:
            data = _from_wp_env(_load_doc(wpenv))
            source = wpenv.name
        else:
            data, source = {}, "defaults"

    override = _first_existing(root, OVERRIDE_BASENAMES)
    if override:
        data = _deep_merge(data, _load_doc(override))
        source = f"{source}+{override.name}"

    merged = _deep_merge(DEFAULTS, data)
    merged["root"] = str(root)
    merged["source"] = source
    return merged


# --------------------------------------------------------------------------- #
# Instance registry + create-lock (T0.2)
#
# Maps a canonical project-root path -> the instance that serves it, so the CLI
# and MCP server can answer "is there an instance for this project?" across
# processes and restarts. This file is the single source of truth for
# project->instance mapping (there is no sandbox.yml projects catalog). The
# runtime dir is overridable via SANDBOX_RUNTIME (used by tests).
# --------------------------------------------------------------------------- #

_ROOT = Path(__file__).resolve().parent


def _runtime_dir() -> Path:
    return Path(os.environ.get("SANDBOX_RUNTIME", str(_ROOT / "runtime")))


def _registry_path() -> Path:
    return _runtime_dir() / "registry.json"


def _ensure_runtime() -> Path:
    rt = _runtime_dir()
    (rt / "locks").mkdir(parents=True, exist_ok=True)
    return rt


def _canonical(root) -> str:
    return str(Path(root).expanduser().resolve())


@contextmanager
def _registry_lock():
    """Exclusive lock around the registry file for read-modify-write."""
    rt = _ensure_runtime()
    fh = open(rt / "registry.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _registry_read() -> dict:
    try:
        data = json.loads(_registry_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "instances": {}}
    data.setdefault("version", 1)
    data.setdefault("instances", {})
    return data


def _registry_write(data: dict) -> None:
    _ensure_runtime()
    path = _registry_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)  # atomic


def registry_all() -> dict:
    """All registered projects, keyed by canonical project root."""
    return _registry_read()["instances"]


def registry_get(root) -> dict | None:
    return registry_all().get(_canonical(root))


def registry_put(root, **fields) -> dict:
    """Create/update the entry for `root` (shallow-merged with existing) under
    lock. Returns the stored entry."""
    key = _canonical(root)
    with _registry_lock():
        data = _registry_read()
        entry = {**data["instances"].get(key, {}), **fields, "root": key}
        data["instances"][key] = entry
        _registry_write(data)
    return entry


def registry_remove(root) -> bool:
    key = _canonical(root)
    with _registry_lock():
        data = _registry_read()
        existed = data["instances"].pop(key, None) is not None
        if existed:
            _registry_write(data)
    return existed


def registry_find_instance(instance_name: str) -> dict | None:
    """Reverse lookup: which project (if any) owns a given instance name."""
    return next(
        (e for e in registry_all().values() if e.get("instance") == instance_name),
        None,
    )


def instance_name_taken(name: str) -> bool:
    return registry_find_instance(name) is not None


@contextmanager
def project_lock(root):
    """Per-project create lock: two concurrent ensure_instance() calls for the
    same project serialize, so the second sees the first's result instead of
    racing to create a duplicate instance. Hold this around the
    'check registry -> create -> record' critical section."""
    rt = _ensure_runtime()
    h = hashlib.sha1(_canonical(root).encode()).hexdigest()[:16]
    fh = open(rt / "locks" / f"{h}.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


# --------------------------------------------------------------------------- #
# Self-test entrypoints
# --------------------------------------------------------------------------- #

def _selftest_registry() -> None:
    """Exercise registry CRUD + prove the per-project lock prevents lost updates
    under concurrent read-modify-write. Uses SANDBOX_RUNTIME (set by caller)."""
    import threading

    root = str(Path.home() / "proj-A")

    # CRUD
    registry_put(root, instance="proj-a", wordpress_port=8200, status="ready")
    assert registry_get(root)["instance"] == "proj-a", "get after put"
    assert registry_find_instance("proj-a")["wordpress_port"] == 8200, "reverse lookup"
    assert instance_name_taken("proj-a") and not instance_name_taken("nope")
    registry_put(root, status="stopped")  # merge keeps port
    assert registry_get(root)["wordpress_port"] == 8200 and registry_get(root)["status"] == "stopped"

    # Concurrency: 8 threads x 50 increments under project_lock → no lost updates.
    registry_put(root, count=0)
    THREADS, ITERS = 8, 50

    def bump():
        for _ in range(ITERS):
            with project_lock(root):
                cur = registry_get(root)["count"]
                registry_put(root, count=cur + 1)

    ts = [threading.Thread(target=bump) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    final = registry_get(root)["count"]
    assert final == THREADS * ITERS, f"lost updates: {final} != {THREADS * ITERS}"

    assert registry_remove(root) and registry_get(root) is None, "remove"
    print(f"registry self-test OK (count={final}, no lost updates; CRUD + lock verified)")


if __name__ == "__main__":  # pragma: no cover
    import sys

    args = sys.argv[1:]
    try:
        if args and args[0] == "--selftest-registry":
            _selftest_registry()
        else:
            target = args[0] if args else "."
            print(json.dumps(load_project_config(target), indent=2))
    except (ConfigError, AssertionError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

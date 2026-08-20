"""sandbox_core — shared core for the Sandbox CLI (`sb`) and MCP server.

Single source of truth so `sb` and `mcp/wp-server/server.py` resolve a plugin
project's config identically. Currently provides per-project config loading
(T0.1); the instance registry / ensure_instance / test-harness helpers land in
later tasks and belong here too.

A "project" is a plugin checkout that carries its own config. Resolution order
for a directory (highest priority last):

    built-in defaults
    ~/.config/sandbox/config.json               (user-global, machine-wide)
    sandbox.config.json | sandbox.config.yml   (canonical, native)
      + sandbox.config.override.{json,yml}      (gitignored, deep-merged on top)
    .wp-env.json                                (import/fallback only)

The user-global layer sits UNDER the project: the project wins scalar
conflicts, while list fields (`plugins`, `themes`) and dict fields (`mappings`,
`mappings_inactive`, `config`) are UNIONED — so a Pro plugin declared once in
~/.config/sandbox/config.json (typically as `mappings_inactive`) becomes
available to every workspace without editing each project's config. Host paths
in the user-global file must be absolute or `~`-anchored (relative paths there
would resolve against each project's root, which is meaningless globally).

No central catalog is consulted — the project file is authoritative.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
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
# The conventional project-local config home is also a root marker so nested
# invocations discover the same project even when the repository has no .git
# checkout metadata (for example a source archive or copied fixture).
CONFIG_SUBDIRECTORY = (".config", "sandbox")
ROOT_MARKERS = CONFIG_BASENAMES + WPENV_BASENAMES + (".git", ".config/sandbox")

# Per-label config layer (multi-instance-per-root, docs/multi-instance-spec.md):
# sandbox.config.<label>.json optionally sits ABOVE sandbox.config.override.json
# in precedence, letting one project root's separate labelled instances diverge
# in plugin set/config — not just the php/wp version override ensure_instance
# already supports. Same validation as instance labels themselves (a label is
# only ever derived from this pattern, never taken raw from user input without
# checking) — reused here so a malicious/malformed label can't be used to
# construct a path that escapes the project root.
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,20}$")


def _label_config_basenames(label: str) -> tuple[str, ...]:
    return (f"sandbox.config.{label}.json",
            f"sandbox.config.{label}.yml",
            f"sandbox.config.{label}.yaml")


def _project_config_home(root: Path) -> Path:
    """Select the one authoritative project-local config home."""
    from sandbox.config.descriptors import config_home
    return config_home(root)

# User-global config: applies to every project on this machine. Honors
# XDG_CONFIG_HOME; overridable for tests via SANDBOX_USER_CONFIG (a file path).
USER_CONFIG_BASENAMES = ("config.json", "config.yml", "config.yaml")

# Normalised schema returned by load_project_config(). `null` version fields mean
# "use the wordpress:latest default" — no implicit pinning.
DEFAULTS: dict = {
    "slug": None,              # project plugin slug; used for legacy plugins:["."]
    "plugins": {
        # `sandbox init` adds the current project's slug -> "." entry when
        # it writes this default map. Query Monitor is deliberately provisioned
        # installed-but-inactive and qm_capture activates it on first capture.
        "query-monitor": False,
        "plugin-check": True,  # wp.org "Plugin Check" — automated compliance/lint testing
        "mcp-adapter": "https://github.com/WordPress/mcp-adapter/releases/download/v0.5.0/mcp-adapter.zip",
    },
    "themes": [],
    "mappings": {},            # wp-path -> host path, bind-mounted AND activated
    "mappings_inactive": {},   # wp-path -> host path, bind-mounted but NOT activated
    "phpVersion": None,
    "wpVersion": None,
    "multisite": False,
    "server": "nginx",         # apache | nginx | litespeed  (herd: backlog)
    "tld": "tst",              # local domain TLD for the proxy: <name>.<tld>
    "config": {},              # wp-config constants -> WORDPRESS_CONFIG_EXTRA
    "port": None,              # preferred port; None = auto-assign
    "tests": {"suite": "auto"},  # auto | unit | integration
    "pluginCheck": {           # ./sb plugin-check (spec 013) — opt-in
        # No slug key: always checks THIS project's own resolved plugin slug
        # (the top-level `slug` above, or the project dir name) via
        # _project_slug — the same resolution legacy plugins:["."] self-
        # entries already use. Self-check only, no override — see
        # sandbox/commands/plugin_check.py's _resolve_plugin_check_config.
        "excludeDirectories": [],  # dirs to skip, relative to project root (mirrors .distignore)
        "versionFile": None,       # None -> resolved at run time to "<slug>.php"
        "baselineFile": "plugin-check-baseline.json",  # git-tracked by convention
    },
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
        # A descriptor below the conventional project-local home belongs to
        # its containing project root, not to ``.config/sandbox`` itself.
        # Resolve this before the generic marker check so nested invocations
        # use the same root as invocations from the checkout root.
        if (cur.name == CONFIG_SUBDIRECTORY[-1]
                and cur.parent.name == CONFIG_SUBDIRECTORY[-2]
                and any((cur / m).exists() for m in CONFIG_BASENAMES)):
            return cur.parent.parent
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


# --------------------------------------------------------------------------- #
# Plugin config map (spec 010): canonical slug-keyed plugins + per-field merge.
#
# A plugin entry decouples SOURCE (org | zip | local path) from STATE (active |
# inactive | on-demand). Fields are explicitly UNSET until a layer sets them, so
# layers field-merge without clobbering. Legacy `plugins`(list)/`mappings`/
# `mappings_inactive` are folded in, preserving their exact current behavior.
# --------------------------------------------------------------------------- #

_UNSET = object()  # "this field was not specified by any layer (yet)"

_PLUGIN_PREFIX = "wp-content/plugins/"
_PLUGIN_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _canonical_plugin_slug(slug) -> str:
    """Validate a canonical map key before it can become a plugin path.

    Canonical entries use their key as the WordPress install directory. Keep
    path separators and traversal syntax out at config-load time; legacy list
    entries retain their established compatibility behaviour.
    """
    if not isinstance(slug, str) or not _PLUGIN_SLUG_RE.fullmatch(slug):
        raise ConfigError(
            f"invalid plugin slug {slug!r}; use lowercase letters, numbers, "
            "hyphen, and underscore (no path separators)"
        )
    return slug


def _is_zip_url(s: str) -> bool:
    return isinstance(s, str) and s.startswith(("http://", "https://")) and s.endswith(".zip")


def _looks_like_path(s: str) -> bool:
    return isinstance(s, str) and (("/" in s) or s.startswith((".", "~")))


def _blank_entry() -> dict:
    return {"source": _UNSET, "active": _UNSET, "on_demand": _UNSET}


def _normalize_plugin_value(value, slug: str) -> dict:
    """One map value (shorthand or object) -> entry with UNSET-aware fields.

    Shorthands set ONE axis only: bool -> state (source UNSET); string -> source
    (state UNSET). 'org' is never stamped here — it is the final fallback.
    """
    entry = _blank_entry()
    if value is True:
        entry["active"] = True
    elif value is False:
        entry["active"] = False
    elif isinstance(value, str):
        if _is_zip_url(value):
            entry["source"] = {"kind": "zip", "value": value}
        elif _looks_like_path(value):
            entry["source"] = {"kind": "path", "value": value}
        else:
            raise ConfigError(
                f"plugin '{slug}': unsupported string shorthand {value!r}; "
                "use true/false, a local path, or a .zip URL"
            )
    elif isinstance(value, dict):
        allowed = {"path", "zip", "source", "active", "onDemand"}
        unknown = sorted(str(k) for k in set(value) - allowed)
        if unknown:
            raise ConfigError(
                f"plugin '{slug}': unknown field(s) {', '.join(unknown)}; "
                "allowed fields are path, zip, source, active, onDemand"
            )
        srcs = [k for k in ("path", "zip", "source") if k in value]
        if len(srcs) > 1:
            raise ConfigError(
                f"plugin '{slug}': more than one source ({', '.join(srcs)}) — "
                f"use exactly one of path / zip / source")
        if "path" in value:
            if not isinstance(value["path"], str) or not value["path"].strip():
                raise ConfigError(f"plugin '{slug}': path must be a non-empty string")
            entry["source"] = {"kind": "path", "value": value["path"]}
        elif "zip" in value:
            if not isinstance(value["zip"], str) or not _is_zip_url(value["zip"]):
                raise ConfigError(f"plugin '{slug}': zip must be an http(s) .zip URL")
            entry["source"] = {"kind": "zip", "value": value["zip"]}
        elif "source" in value:
            sv = value["source"]
            if sv != "org":
                raise ConfigError(f"plugin '{slug}': source must be exactly 'org'")
            entry["source"] = {"kind": "org", "value": None}
        if "active" in value:
            if not isinstance(value["active"], bool):
                raise ConfigError(f"plugin '{slug}': active must be true or false")
            entry["active"] = value["active"]
        if "onDemand" in value:
            if not isinstance(value["onDemand"], bool):
                raise ConfigError(f"plugin '{slug}': onDemand must be true or false")
            entry["on_demand"] = value["onDemand"]
        if entry["active"] is True and entry["on_demand"] is True:
            raise ConfigError(f"plugin '{slug}': active and onDemand cannot both be true")
    else:
        raise ConfigError(f"plugin '{slug}': unsupported value {value!r}")
    return entry


def _legacy_list_entry(item):
    """A legacy `plugins` list element -> (slug, entry) preserving today's
    behavior (install+activate; local-path slug = dir name, for compat)."""
    if not item:
        return None, None
    if item == ".":
        return None, ("self", {"source": {"kind": "path", "value": "."},
                               "active": True, "on_demand": False})
    if _is_zip_url(item):
        # slug derived later by the installer; key by the zip basename sans version
        base = item.rstrip("/").rsplit("/", 1)[-1]
        base = base[:-4] if base.endswith(".zip") else base
        slug = re.sub(r"\.\d[\d.]*$", "", base)
        return slug, {"source": {"kind": "zip", "value": item},
                      "active": True, "on_demand": False}
    if _looks_like_path(item):
        slug = Path(str(item)).expanduser().name
        return slug, {"source": {"kind": "path", "value": item},
                      "active": True, "on_demand": False}
    # bare wp.org slug
    return item, {"source": _UNSET, "active": True, "on_demand": False}


def _project_slug(raw, fallback: str) -> str:
    """Return the project plugin slug for legacy self entries.

    The canonical plugins map is still slug-keyed and does not need this. The
    top-level slug keeps legacy `plugins: ["."]` stable in git worktrees whose
    directory name is not the plugin's install slug.
    """
    slug = str(raw or "").strip() or fallback
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise ConfigError(
            f"invalid project slug {slug!r}; use a WordPress plugin slug "
            "(lowercase letters, numbers, hyphen, underscore)"
        )
    return slug


def _normalize_plugins(doc: dict):
    """Raw config doc -> ({slug: entry}, used_legacy: bool, self_entry).

    Handles the object (canonical) and array (legacy) `plugins` forms and folds
    legacy `mappings`/`mappings_inactive` plugin entries in. Non-plugin mappings
    (other wp-paths) are NOT touched here. `self_entry` carries a legacy "."
    element (slug resolved against the project root by the consumer).
    """
    out: dict[str, dict] = {}
    canonical: set[str] = set()  # slugs declared via the new map (win over legacy)
    self_entry = None
    used_legacy = False
    plugins = doc.get("plugins")
    if isinstance(plugins, dict):
        for slug, val in plugins.items():
            slug = _canonical_plugin_slug(slug)
            out[slug] = _normalize_plugin_value(val, slug)
            canonical.add(slug)
    elif isinstance(plugins, list):
        used_legacy = True
        for item in plugins:
            slug, entry = _legacy_list_entry(item)
            if entry is None:
                continue
            if slug is None and isinstance(entry, tuple) and entry[0] == "self":
                self_entry = entry[1]
            else:
                out[slug] = entry
    elif "plugins" in doc and plugins is not None:
        raise ConfigError("plugins must be either a legacy array or a slug-keyed object")
    # Fold legacy plugin mappings (wp-content/plugins/<slug>) into the map. A slug
    # already declared via the canonical map WINS — skip + warn (FR-012).
    for key, active in (("mappings", True), ("mappings_inactive", False)):
        m = doc.get(key)
        if isinstance(m, dict) and m:
            for wp_path, src in m.items():
                wp = str(wp_path).strip("/")
                if not (wp.startswith(_PLUGIN_PREFIX.strip("/")) and wp.count("/") == 2):
                    continue  # non-plugin mapping — leave for the old path
                used_legacy = True
                slug = wp.split("/")[-1]
                if slug in canonical:
                    print(f"sandbox: plugin '{slug}' is in both the `plugins` map and "
                          f"`{key}` — the map wins.", file=sys.stderr)
                    continue
                out[slug] = {"source": {"kind": "path", "value": src},
                             "active": active, "on_demand": False}
    return out, used_legacy, self_entry


def _merge_plugin_entry(base: dict, top: dict) -> dict:
    """Field-merge two entries — a field SET in `top` wins; UNSET never clobbers."""
    out = dict(base)
    for f in ("source", "active", "on_demand"):
        if top.get(f, _UNSET) is not _UNSET:
            out[f] = top[f]
    return out


def _merge_plugin_maps(*layers) -> dict:
    """Field-merge plugin maps low->high precedence (later layers win per field)."""
    out: dict[str, dict] = {}
    for layer in layers:
        for slug, entry in (layer or {}).items():
            out[slug] = _merge_plugin_entry(out.get(slug, _blank_entry()), entry)
    return out


def _resolve_plugin_entry(entry: dict, project_declared: bool = False) -> dict:
    """Apply final defaults to UNSET fields. `source` -> org. State default is
    LAYER-AWARE: a slug the project/override declared (you opted in) defaults to
    ACTIVE; a slug present ONLY in the user-global catalog defaults to ON-DEMAND
    (never auto-enable). An explicit active/onDemand from any layer always wins."""
    source = entry.get("source", _UNSET)
    if source is _UNSET:
        source = {"kind": "org", "value": None}
    active = entry.get("active", _UNSET)
    on_demand = entry.get("on_demand", _UNSET)
    if on_demand is True:
        active = False
    elif active is not _UNSET:
        on_demand = False
    elif project_declared:        # opted in by project/override → install + activate
        active, on_demand = True, False
    else:                         # catalog-only → available, not enabled
        active, on_demand = False, True
    return {"source": source, "active": bool(active), "on_demand": bool(on_demand)}


_DEPRECATION_WARNED = [False]


def _warn_legacy_once() -> None:
    if _DEPRECATION_WARNED[0]:
        return
    _DEPRECATION_WARNED[0] = True
    print("sandbox: `plugins` list / `mappings` / `mappings_inactive` are "
          "deprecated — use the slug-keyed `plugins` map (see "
          "docs/sandbox-config-reference.md).", file=sys.stderr)


def _merge_layers(base: dict, top: dict) -> dict:
    """Stack `top` over `base`, additively. `top` wins scalar conflicts; dicts
    deep-merge; lists UNION (base entries first, then top's new entries, order
    preserved, de-duplicated). Used to fold the user-global layer (`base`)
    under a project's config (`top`) so the project keeps priority while the
    user layer only ADDS plugins/mappings."""
    out = dict(base)
    for k, v in (top or {}).items():
        bv = out.get(k)
        if isinstance(v, dict) and isinstance(bv, dict):
            out[k] = _merge_layers(bv, v)
        elif isinstance(v, list) and isinstance(bv, list):
            merged = list(bv)
            for item in v:
                if item not in merged:
                    merged.append(item)
            out[k] = merged
        else:
            out[k] = v
    return out


def _user_config_path() -> Path | None:
    """The user-global config file, if present. Explicit override via
    SANDBOX_USER_CONFIG (a full path) wins; else the consolidated base location
    ($SANDBOX_HOME/config.{json,yml}, spec 009); else the legacy
    $XDG_CONFIG_HOME/sandbox/config.{json,yml} (default ~/.config/sandbox/) as a
    backward-compat fallback until migration runs."""
    explicit = os.environ.get("SANDBOX_USER_CONFIG")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    in_base = _first_existing(sandbox_base(), USER_CONFIG_BASENAMES)
    if in_base:
        return in_base
    legacy_base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    legacy_dir = Path(legacy_base).expanduser() / "sandbox"
    return _first_existing(legacy_dir, USER_CONFIG_BASENAMES)


def _load_user_global() -> dict:
    """Load the user-global config doc (raw, un-defaulted), or {} if none."""
    path = _user_config_path()
    if not path:
        return {}
    data = _load_doc(path)
    data.pop("root", None)  # never let the global file forge a project root
    return data


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
        "mappings_inactive": raw.get("mappings_inactive", {}) or {},
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


def _load_project_config_legacy(project_dir, label: str | None = None) -> dict:
    """Resolve the effective config for a project directory.

    `label`: when given (and not "default"), also layers
    `sandbox.config.<label>.json` (optional; falls back silently if absent) at
    the HIGHEST precedence — above `sandbox.config.override.json` — so a
    project root's separate labelled instances (multi-instance-per-root) can
    diverge in plugin set/config, not just the php/wp version `ensure_instance`
    already overrides directly. Malformed labels are ignored (never raise here
    — config loading must not fail because of a bad label; callers that mint
    instances validate labels themselves).

    Returns the normalised schema (DEFAULTS keys) plus:
      root:    absolute project root
      source:  which file(s) the config came from
    """
    root = find_project_root(project_dir)

    config_home = _project_config_home(root)
    native = _first_existing(config_home, CONFIG_BASENAMES)
    if native:
        native_doc = _load_doc(native)
        source = native.name
    else:
        wpenv = _first_existing(root, WPENV_BASENAMES)
        if wpenv:
            native_doc = _from_wp_env(_load_doc(wpenv))
            source = wpenv.name
        else:
            native_doc, source = {}, "defaults"

    override_path = _first_existing(config_home, OVERRIDE_BASENAMES)
    override_doc = _load_doc(override_path) if override_path else {}

    label_path = None
    label_doc: dict = {}
    if label and label != "default" and _LABEL_RE.match(label):
        label_path = _first_existing(config_home, _label_config_basenames(label))
        if label_path:
            label_doc = _load_doc(label_path)

    user_doc = _load_user_global()

    # Generic merge for all NON-plugin keys (themes, mappings for non-plugin
    # wp-paths, scalars, …) — unchanged behavior: override over native, then
    # the OPTIONAL per-label layer over that, then the user-global layer
    # folded under, then DEFAULTS.
    data = _deep_merge(native_doc, override_doc)
    if override_path:
        source = f"{source}+{override_path.name}"
    if label_doc:
        data = _deep_merge(data, label_doc)
        source = f"{source}+{label_path.name}"
    if user_doc:
        data = _merge_layers(user_doc, data)
        source = f"user+{source}"
    merged = _deep_merge(DEFAULTS, data)
    merged["root"] = str(root)
    merged["source"] = source

    # Spec 010: resolve the canonical slug-keyed plugin map SEPARATELY via
    # normalize-then-field-merge (the generic merge would clobber per-slug).
    # Layer precedence low->high: user-global (source catalog) < project <
    # override < per-label (highest — a label's own plugin set wins last).
    layers = []
    used_legacy = False
    self_slug = _project_slug(merged.get("slug"), root.name)
    for doc in (user_doc, native_doc, override_doc, label_doc):
        m, legacy, self_entry = _normalize_plugins(doc or {})
        if self_entry is not None:
            m = dict(m)
            m[self_slug] = self_entry
        layers.append(m)
        used_legacy = used_legacy or legacy
    if used_legacy:
        _warn_legacy_once()
    user_map, native_map, override_map, label_map = layers
    # "Opted in" = declared by the project, its machine override, or its label
    # config (NOT the user-global catalog). These default to active;
    # catalog-only slugs default to on-demand (FR-004b — the catalog never
    # auto-enables).
    opted_in = set(native_map) | set(override_map) | set(label_map)
    merged_map = _merge_plugin_maps(*layers)
    merged["plugins_resolved"] = {
        slug: _resolve_plugin_entry(e, slug in opted_in)
        for slug, e in merged_map.items()
    }
    return merged


def load_project_config(project_dir, label: str | None = None) -> dict:
    """Resolve through the kind-first schema facade while preserving WordPress output."""
    from sandbox.config.facade import resolve_project_config

    return resolve_project_config(
        project_dir,
        label=label,
        legacy_loader=_load_project_config_legacy,
        root_finder=find_project_root,
    )


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


def sandbox_base() -> Path:
    """The single per-user base for ALL machine-state (spec 009).

    A non-empty ``SANDBOX_HOME`` is the explicit override.  When a new process
    is launched without that environment variable, honour the owner-only
    bootstrap selector written by ``sb home`` at
    ``~/.config/sandbox/home``.  Empty, missing, or unreadable selectors fall
    back to ``~/sandbox``.  Both the ``sb`` CLI and the MCP server resolve this
    identically so they never disagree about where state lives.

    ``expanduser()+resolve()`` collapses ``~``, relatives, and symlinks to one
    absolute path.  The selector is deliberately only a path hint: it is not
    consulted for registry migration, merging, or target discovery.
    """
    raw = os.environ.get("SANDBOX_HOME")
    if not raw:
        hint = Path.home() / ".config" / "sandbox" / "home"
        try:
            candidate = hint.read_text().strip()
        except OSError:
            candidate = ""
        # The bootstrap hint is an owner-written absolute path.  Rejecting
        # relative values prevents a separate launch from resolving the base
        # against whichever directory happened to be its current directory.
        raw = candidate if candidate and Path(candidate).is_absolute() else None
    return Path(raw or "~/sandbox").expanduser().resolve()


def _legacy_runtime_dir() -> Path:
    """The pre-009 in-repo runtime dir (state lived in <repo>/runtime)."""
    return _ROOT / "runtime"


def _runtime_dir() -> Path:
    """Runtime state dir. SANDBOX_RUNTIME (tests) wins; else $SANDBOX_HOME/runtime.

    Backward-compat (spec 009 FR-015): until the one-time migration runs, prefer
    the in-repo runtime when it still holds the registry and the new base does
    not — so existing instances keep resolving before `sb migrate`.
    """
    explicit = os.environ.get("SANDBOX_RUNTIME")
    if explicit:
        return Path(explicit)
    new = sandbox_base() / "runtime"
    if not (new / "registry.json").exists() and \
            (_legacy_runtime_dir() / "registry.json").exists():
        return _legacy_runtime_dir()
    return new


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


def _migrate_registry_v1_to_v2(data: dict) -> dict:
    """v1 keyed `{root: entry}` (one instance per root) -> v2 keyed
    `{f"{root}::{label}": entry}` (one-or-more instances per root, spec:
    multi-instance-per-root). Every pre-existing entry becomes that root's
    "default" label. Idempotent — a v2 file is returned unchanged. Preserves
    every field (ports/status/secrets) so a currently-running instance keeps
    resolving with zero downtime; only the registry KEY gains a `::default`
    suffix. See docs/multi-instance-spec.md §1."""
    if data.get("version", 1) >= 2:
        data.setdefault("instances", {})
        return data
    new_instances = {}
    for root, entry in data.get("instances", {}).items():
        label = entry.get("label", "default")
        new_instances[f"{root}::{label}"] = {
            **entry, "root": root, "label": label, "is_default": True,
        }
    return {"version": 2, "instances": new_instances}


def _registry_read_raw() -> dict:
    """Read the registry file with NO locking and NO migration. Callers that
    already hold `_registry_lock()` (registry_put/registry_remove) MUST use
    this, not `_registry_read()` — `fcntl.flock()` is not reentrant even
    within the same process (each `_registry_lock()` call opens a fresh file
    handle), so a lock-holding caller calling back into `_registry_read()`'s
    own internal `with _registry_lock():` migration path deadlocks forever.
    Confirmed live: `registry_put()` against a pre-existing v1 registry.json
    hung indefinitely before this split existed."""
    try:
        data = json.loads(_registry_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 2, "instances": {}}
    data.setdefault("version", 1)
    data.setdefault("instances", {})
    return data


def _registry_read() -> dict:
    """Read + auto-migrate if needed. Safe to call WITHOUT already holding
    `_registry_lock()` — acquires it itself, only when migration is actually
    needed. Do NOT call this from inside a `with _registry_lock():` block
    already held by the caller (see `_registry_read_raw`'s docstring)."""
    data = _registry_read_raw()
    if data["version"] < 2:
        with _registry_lock():
            data = _registry_read_raw()  # re-read: another process may have migrated already
            if data["version"] < 2:
                data = _migrate_registry_v1_to_v2(data)
                _registry_write(data)
    return data


def _registry_write(data: dict) -> None:
    _ensure_runtime()
    path = _registry_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)  # atomic


def _registry_repository():
    """Build the repository at the current environment-resolved registry path."""
    from sandbox.project_registry.json import JsonRegistryRepository

    return JsonRegistryRepository(_registry_path())


def registry_all() -> dict:
    """All registered instances, keyed by `<canonical-project-root>::<label>`.
    Every value carries its own `root` and `label` fields — read those, never
    the dict key, when you need the root or label (spec: multi-instance-per-root)."""
    from sandbox.project_registry.base import RegistryError

    try:
        return _registry_repository().all()
    except RegistryError as exc:
        raise ConfigError(str(exc)) from exc


def registry_list_for_root(root) -> list[dict]:
    """Every instance entry owned by `root`, default-labelled one first. Powers
    label-disambiguation errors and `sb instances --project-dir` listing."""
    key_root = _canonical(root)
    entries = [e for e in registry_all().values() if e.get("root") == key_root]
    entries.sort(key=lambda e: (not e.get("is_default"), e.get("label", "")))
    return entries


def registry_default_label(root) -> str | None:
    """The `is_default` label for `root`, or None if it has no instances."""
    entries = registry_list_for_root(root)
    if not entries:
        return None
    default = next((e for e in entries if e.get("is_default")), None)
    return (default or entries[0]).get("label")


def registry_get(root, label=None) -> dict | None:
    """Resolve one instance entry for `root`.

    label=None: back-compat fast path — a root with exactly one instance
    returns it unconditionally (identical to the pre-multi-instance behavior);
    a root with several returns the `is_default` one, or None if none is
    marked default. label given: returns that exact labelled entry, or None.
    """
    entries = registry_list_for_root(root)
    if not entries:
        return None
    if label is not None:
        return next((e for e in entries if e.get("label") == label), None)
    if len(entries) == 1:
        return entries[0]
    return next((e for e in entries if e.get("is_default")), None)


def registry_put(root, label="default", **fields) -> dict:
    """Create/update the entry for `root`+`label` (shallow-merged with existing)
    under lock. First entry ever written for a root is marked `is_default`.
    Returns the stored entry."""
    from sandbox.project_registry.base import RegistryError

    try:
        return _registry_repository().put(root, label=label, **fields)
    except RegistryError as exc:
        raise ConfigError(str(exc)) from exc


def registry_remove(root, label=None) -> bool:
    """Remove one instance entry for `root`. label=None + exactly one entry ->
    remove it (back-compat). label=None + several entries -> raises (ambiguous
    — caller must pass label). label given -> remove that entry."""
    from sandbox.project_registry.base import RegistryError

    try:
        return _registry_repository().remove(root, label=label)
    except RegistryError as exc:
        raise ConfigError(str(exc)) from exc


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

    # Regression: a second registry_put on the SAME (root, label) — e.g.
    # ensure_instance's pending -> ready transition — must NOT flip a true
    # is_default to False (the entry itself must not count as "already
    # existing for this root" when recomputing).
    root1b = str(Path.home() / "proj-A1")
    registry_put(root1b, label="default", instance="proj-a1", status="pending")
    assert registry_get(root1b)["is_default"], "first write is default"
    registry_put(root1b, label="default", instance="proj-a1", status="ready")
    assert registry_get(root1b)["is_default"], \
        "second write to the SAME (root, label) must stay default"
    registry_remove(root1b, label="default")

    # Multi-instance-per-root (spec: multi-instance-per-root).
    root2 = str(Path.home() / "proj-B")
    registry_put(root2, label="default", instance="proj-b", wordpress_port=8300)
    registry_put(root2, label="qa", instance="proj-b-qa", wordpress_port=8301)
    entries = registry_list_for_root(root2)
    assert len(entries) == 2, "two labelled instances for one root"
    assert entries[0]["is_default"] and entries[0]["label"] == "default", \
        "default label sorts first"
    assert registry_get(root2)["instance"] == "proj-b", \
        "no-label get on multi-instance root resolves to default"
    assert registry_get(root2, label="qa")["instance"] == "proj-b-qa", \
        "labelled get resolves the requested label"
    assert registry_default_label(root2) == "default"
    try:
        registry_remove(root2)
        raise AssertionError("remove without label on multi-instance root should raise")
    except ConfigError:
        pass
    assert registry_remove(root2, label="qa") and \
        registry_get(root2, label="qa") is None, "labelled remove"
    assert registry_get(root2)["instance"] == "proj-b", \
        "removing one label leaves the other intact"
    registry_remove(root2, label="default")

    # v1 -> v2 migration: a raw pre-migration file loads, migrates in place, and
    # every prior instance keeps resolving via registry_get(root) with no label.
    root3 = str(Path.home() / "proj-C")
    key3 = _canonical(root3)
    _registry_write({"version": 1, "instances": {
        key3: {"instance": "proj-c", "wordpress_port": 8400, "status": "ready"},
    }})
    migrated = registry_get(root3)
    assert migrated is not None and migrated["instance"] == "proj-c", \
        "v1 entry resolves after auto-migration"
    assert migrated["label"] == "default" and migrated["is_default"], \
        "migrated entry becomes the default label"
    raw = _registry_read()
    assert raw["version"] == 2 and f"{key3}::default" in raw["instances"], \
        "on-disk file rewritten to v2 composite-key shape"
    registry_remove(root3, label="default")

    # Regression: registry_put/registry_remove call _registry_read_raw() (NOT
    # _registry_read()) while already holding _registry_lock() — calling
    # _registry_read() there instead deadlocks forever the FIRST time either
    # is invoked against a pre-existing v1 file, because fcntl.flock() is not
    # reentrant even within one process (each _registry_lock() call opens a
    # fresh file handle, so a second acquire attempt blocks on the first).
    # This must be the FIRST touch of the v1 file — no registry_get/
    # _registry_read() call in between — to actually exercise the deadlock
    # path (those warm the file to v2 via a lock-free read first, masking
    # the bug). Guarded by a hard wall-clock deadline so a regression FAILS
    # instead of hanging the whole test suite forever.
    import threading
    root4 = str(Path.home() / "proj-D")
    key4 = _canonical(root4)
    _registry_write({"version": 1, "instances": {
        key4: {"instance": "proj-d", "wordpress_port": 8500, "status": "ready"},
    }})
    done = threading.Event()

    def _put_first_touch():
        registry_put(root4, label="default", instance="proj-d", status="ready")
        done.set()

    t = threading.Thread(target=_put_first_touch, daemon=True)
    t.start()
    if not done.wait(timeout=5):
        raise AssertionError(
            "registry_put deadlocked on first touch of a v1 registry file "
            "(fcntl.flock() re-acquired while already held — see "
            "_registry_read_raw vs _registry_read)")
    assert registry_get(root4)["instance"] == "proj-d", \
        "v1 entry survives a direct registry_put (no prior read) without deadlocking"
    registry_remove(root4, label="default")

    print(f"registry self-test OK (count={final}, no lost updates; CRUD + lock + "
          f"multi-instance-per-root + v1->v2 migration verified)")


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

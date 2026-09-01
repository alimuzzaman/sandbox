from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import stat
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

CONFIG_BASENAMES = ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml")
# A project may keep the complete Sandbox descriptor family in the repository
# root (the historical layout) or in this one conventional project-local
# directory.  The home is selected once per resolution; related override and
# label layers must never be mixed across homes.
CONFIG_SUBDIRECTORY = (".config", "sandbox")
COMPOSE_KIND_ALIASES = frozenset({
    "compose", "generic", "docker", "php", "javascript", "js", "node",
    "laravel", "laravel-sail", "astro",
})
_REPO_KEY_CHARS = re.compile(r"[^a-z0-9._-]+")


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if path.suffix in {".yml", ".yaml"}:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise ValueError(f"{path.name} requires PyYAML") from exc
        value = yaml.safe_load(text) or {}
    else:
        value = json.loads(text) if text.strip() else {}
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected an object at the top level")
    return value


def _first_config(home: Path) -> Path | None:
    """Return the first primary descriptor in a config home."""
    return next((home / name for name in CONFIG_BASENAMES
                 if (home / name).exists()), None)


def explicit_primary_config(root: str | Path, config_file: str | Path) -> Path:
    """Validate and return one explicitly selected project descriptor.

    Relative values are project-root-relative.  The selected file must use a
    canonical primary basename and be a real regular file inside the canonical
    project root.  Its parent owns the complete override/label family.
    """
    root = Path(root).expanduser().resolve()
    raw = Path(config_file).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    if candidate.name not in CONFIG_BASENAMES:
        raise ValueError(
            "Sandbox --config-file basename must be sandbox.config.json, "
            "sandbox.config.yml, or sandbox.config.yaml"
        )
    if candidate.parent.is_symlink():
        raise ValueError("Sandbox --config-file directory must not be a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        mode = candidate.lstat().st_mode
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "Sandbox --config-file must be an existing file inside the project root"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError("Sandbox --config-file must be a regular non-symbolic-link file")
    selected_home = resolved.parent
    for family_path in candidate.parent.glob("sandbox.config.*"):
        try:
            family_mode = family_path.lstat().st_mode
            family_resolved = family_path.resolve(strict=True)
            family_resolved.relative_to(selected_home)
            family_resolved.relative_to(root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "Sandbox explicit config family must stay inside its selected directory"
            ) from exc
        if stat.S_ISLNK(family_mode) or not stat.S_ISREG(family_mode):
            raise ValueError(
                f"Sandbox explicit config family file must be a regular "
                f"non-symbolic-link file: {family_path.name}"
            )
    return resolved


def _inside(root: Path, path: Path, *, label: str) -> Path:
    """Resolve *path* and reject a config home that escapes its project root."""
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            f"Sandbox {label} must stay within the project root ({root})"
        ) from exc
    return resolved


def _sandbox_home() -> Path:
    """Resolve the selected per-user Sandbox base without creating it."""
    raw = os.environ.get("SANDBOX_HOME")
    if not raw:
        hint = Path.home() / ".config" / "sandbox" / "home"
        try:
            candidate = hint.read_text().strip()
        except OSError:
            candidate = ""
        raw = candidate if candidate and Path(candidate).is_absolute() else None
    return Path(raw or "~/sandbox").expanduser().resolve()


def _git_output(root: Path, *args: str) -> str | None:
    """Return one bounded Git value, or ``None`` outside a usable checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def project_config_key(root: str | Path) -> str | None:
    """Return the stable key shared by worktrees of one Git repository.

    An origin URL is preferred so a relocated clone keeps the same key.  A
    canonical Git common directory is the local fallback and naturally joins
    all linked worktrees.  Non-Git directories have no external config home.
    """
    root = Path(root).expanduser().resolve()
    origin = _git_output(root, "config", "--get", "remote.origin.url")
    # Relative filesystem remotes are contextual: the same text in unrelated
    # repositories can name different targets.  Use the Git common directory
    # for those instead of creating a cross-repository key collision.
    if origin and "://" not in origin and not re.match(r"^[^/]+:[^/]", origin):
        origin_path = Path(origin).expanduser()
        origin = str(origin_path.resolve()) if origin_path.is_absolute() else None
    elif origin and origin.lower().startswith("file://"):
        parsed = urlparse(origin)
        if parsed.netloc not in ("", "localhost"):
            origin = origin
        else:
            origin = str(Path(unquote(parsed.path)).expanduser().resolve())
    if origin:
        identity = f"origin:{origin}"
        display = origin.rstrip("/").rsplit("/", 1)[-1]
        if ":" in display and "/" not in display:
            display = display.rsplit(":", 1)[-1]
        display = display.removesuffix(".git")
    else:
        common = _git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if not common:
            return None
        common_path = Path(common).expanduser().resolve()
        identity = f"git-common-dir:{common_path}"
        display = common_path.parent.name if common_path.name == ".git" else common_path.name
    slug = _REPO_KEY_CHARS.sub("-", display.lower()).strip("-._") or "repo"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f"{slug[:48]}-{digest}"


def shared_config_home(root: str | Path) -> Path | None:
    """Return this Git repository's fallback config home under SANDBOX_HOME."""
    key = project_config_key(root)
    if key is None:
        return None
    projects = _sandbox_home() / "projects"
    return projects / key


def _validate_shared_home(projects: Path, shared_home: Path) -> None:
    """Keep one repository key from aliasing another config family."""
    if projects.is_symlink() or shared_home.is_symlink():
        raise ValueError("Sandbox shared config directories must not be symbolic links")
    _inside(projects, shared_home, label="shared config directory")
    for path in shared_home.glob("sandbox.config.*"):
        if path.is_symlink():
            raise ValueError(
                f"Sandbox shared config file must not be a symbolic link: {path.name}"
            )
        _inside(shared_home, path, label="shared config file")


def config_home(root: str | Path, config_file: str | Path | None = None) -> Path:
    """Select the authoritative project-local Sandbox config home.

    Root-level configuration remains the compatibility default.  When the
    conventional ``.config/sandbox`` home contains a primary descriptor it
    owns the whole descriptor family.  Defining primary descriptors in both
    homes is ambiguous and fails closed before any schema-specific work.  If
    neither in-tree home has a primary descriptor, a Git-identity-keyed home
    under ``$SANDBOX_HOME/projects`` may own the family for every worktree.
    """
    root = Path(root).expanduser().resolve()
    if config_file is not None:
        return explicit_primary_config(root, config_file).parent
    root_home = root
    nested_home = root.joinpath(*CONFIG_SUBDIRECTORY)

    # A symlinked conventional directory is allowed only when it resolves
    # inside the project.  Do this check before looking for its descriptor so a
    # malicious external target cannot be selected by discovery.
    if nested_home.exists() or nested_home.is_symlink():
        _inside(root, nested_home, label="config directory")

    root_primary = _first_config(root_home)
    nested_primary = _first_config(nested_home) if nested_home.exists() else None
    if root_primary is not None and nested_primary is not None:
        raise ValueError(
            "ambiguous Sandbox project configuration: primary descriptors "
            f"exist in {root_home} and {nested_home}; keep exactly one "
            "config home (project root or .config/sandbox)"
        )
    if nested_primary is not None:
        return nested_home
    if root_primary is not None:
        return root_home

    shared_home = shared_config_home(root)
    if shared_home is not None:
        projects = _sandbox_home() / "projects"
        if shared_home.exists() or shared_home.is_symlink():
            _validate_shared_home(projects, shared_home)
        if _first_config(shared_home) is not None:
            return shared_home
    return root_home


def primary_config(root: str | Path, config_file: str | Path | None = None) -> Path | None:
    """Return the selected home's primary descriptor, if one exists."""
    if config_file is not None:
        return explicit_primary_config(root, config_file)
    return _first_config(config_home(root))


def config_layer(root: str | Path, names: tuple[str, ...], *, home: Path | None = None) -> Path | None:
    """Find one optional layer in the selected config home only."""
    selected = home if home is not None else config_home(root)
    return next((selected / name for name in names
                 if (selected / name).exists()), None)


def discover_project_kind(root: str | Path, config_file: str | Path | None = None) -> str:
    """Read only the committed native descriptor needed to select its schema."""
    root = Path(root).expanduser().resolve()
    path = primary_config(root, config_file)
    if path is not None:
        kind = _load_mapping(path).get("kind", "wordpress")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("project kind must be a non-empty string")
        kind = kind.strip().lower()
        return "compose" if kind in COMPOSE_KIND_ALIASES else kind
    return "wordpress"

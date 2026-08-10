"""Safe relocation for the spec-009 per-user Sandbox base.

Pure data is copied to a same-filesystem staging path, verified, promoted, and
only then removed from its source.  Generated artifacts are deliberately never
moved: Compose, Herd shims, proxy routing files, and the tooling venv are
rebuilt after the transfer.  The small journal makes an interrupted transfer
resumable without ever treating a different destination as disposable.
"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


_REGENERATED = {"compose", "herd-shims", ".venv-tools"}
_PROXY_REGENERATED = {"Caddyfile", "proxy.yml"}
_JOURNAL = ".migration-state.json"
_LOCK = ".migration.lock"
_AUTO_FINALIZE_ENV = "SANDBOX_AUTO_MIGRATION_FINALIZE"
_PERSIST_PENDING_ENV = "SANDBOX_HOME_SELECTION_PENDING"


class MigrationConflict(RuntimeError):
    """A destination differs from the source and must be resolved by a human."""


def _legacy_runtime() -> Path:
    return ROOT / "runtime"


def _new_base() -> Path:
    return BASE


def _state_present(path: Path) -> bool:
    return path.exists() and (not path.is_dir() or any(path.iterdir()))


def _legacy_config_secrets() -> list[tuple[Path, Path]]:
    base = _new_base()
    return [
        (ROOT / "sandbox.local.yml", base / "sandbox.local.yml"),
        (ROOT / ".env.local", base / ".env.local"),
        (Path.home() / ".config" / "sandbox" / "config.json", base / "config.json"),
    ]


def _runtime_moves(source_runtime: Path, destination_runtime: Path) -> list[tuple[Path, Path]]:
    """Return pure-data moves, excluding every baked-path artifact."""
    moves: list[tuple[Path, Path]] = []
    if not source_runtime.exists():
        return moves
    for item in sorted(source_runtime.iterdir()):
        if item.name in _REGENERATED:
            continue
        if item.name == "proxy" and item.is_dir():
            for child in sorted(item.iterdir()):
                if child.name not in _PROXY_REGENERATED:
                    moves.append((child, destination_runtime / "proxy" / child.name))
            continue
        moves.append((item, destination_runtime / item.name))
    return moves


def _plan(source_runtime: Path, destination_runtime: Path,
          config_pairs: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    return _runtime_moves(source_runtime, destination_runtime) + [
        (source, destination) for source, destination in config_pairs if source.exists()
    ]


def _same_content(left: Path, right: Path) -> bool:
    """Compare files/trees deeply before a retry can remove a source copy."""
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and os.readlink(left) == os.readlink(right)
    if left.is_file() or right.is_file():
        return left.is_file() and right.is_file() and filecmp.cmp(left, right, shallow=False)
    if not left.is_dir() or not right.is_dir():
        return False
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    for name in comparison.common_files:
        if not filecmp.cmp(left / name, right / name, shallow=False):
            return False
    return all(_same_content(left / name, right / name) for name in comparison.common_dirs)


def _remove_source(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _stage_copy(source: Path, destination: Path) -> None:
    """Copy source next to destination, verify it, then atomically promote it.

    A failed copy never changes ``source``.  A pre-existing destination is only
    accepted when it is an exact retry copy; anything else is a conflict.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if not _same_content(source, destination):
            raise MigrationConflict(
                f"Conflict at {destination}: destination differs from {source}. "
                "No source was removed and no merge was performed."
            )
        return
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.migrate-", dir=destination.parent))
    staged = stage_dir / "payload"
    try:
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, staged, symlinks=True, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, staged, follow_symlinks=False)
        if not _same_content(source, staged):
            raise MigrationConflict(f"Staged copy verification failed for {source}; source retained.")
        os.replace(staged, destination)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


@contextmanager
def _migration_lock(*bases: Path):
    """Fail closed when another Sandbox process is already relocating state."""
    try:
        import fcntl
    except ImportError as exc:  # Spec 009 supports macOS/Linux only.
        raise MigrationConflict("Migration locking requires a POSIX filesystem.") from exc
    handles = []
    try:
        for base in sorted({path.resolve() for path in bases}, key=str):
            base.mkdir(parents=True, exist_ok=True)
            handle = (base / _LOCK).open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise MigrationConflict(
                    f"Migration is already running for {base}; wait for it to finish and retry."
                ) from exc
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _journal_path(base: Path) -> Path:
    return base / _JOURNAL


def _load_journal(base: Path) -> dict | None:
    try:
        data = json.loads(_journal_path(base).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_journal(base: Path, source_base: Path, moves: list[tuple[Path, Path]]) -> None:
    path = _journal_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": str(source_base.resolve()), "moves": [str(dst) for _, dst in moves]}
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _clear_journal(base: Path) -> None:
    try:
        _journal_path(base).unlink()
    except FileNotFoundError:
        pass


def _base_has_state(base: Path) -> bool:
    if not base.exists():
        return False
    return any(child.name not in {_LOCK, _JOURNAL} for child in base.iterdir())


def _legacy_registry_conflict(source_runtime: Path, destination_runtime: Path,
                              base: Path, source_base: Path) -> None:
    """Distinguish an interrupted transfer from a separately restored legacy tree."""
    if not ((source_runtime / "registry.json").exists() and
            (destination_runtime / "registry.json").exists()):
        return
    journal = _load_journal(base)
    if not journal or journal.get("source") != str(source_base.resolve()):
        raise MigrationConflict(
            f"Conflict: state exists both in {source_runtime} and {destination_runtime}. "
            "The destination base is authoritative; resolve or archive the legacy tree manually."
        )


def _drop_baked_artifacts(source_runtime: Path) -> None:
    """Remove only Sandbox-generated artifacts after every pure-data copy verifies."""
    for name in _REGENERATED:
        path = source_runtime / name
        if path.exists() or path.is_symlink():
            _remove_source(path)
    proxy = source_runtime / "proxy"
    for name in _PROXY_REGENERATED:
        path = proxy / name
        if path.exists() or path.is_symlink():
            _remove_source(path)
    try:
        if proxy.exists() and not any(proxy.iterdir()):
            proxy.rmdir()
        if source_runtime.exists() and not any(source_runtime.iterdir()):
            source_runtime.rmdir()
    except OSError:
        pass


def _transfer(source_runtime: Path, destination_runtime: Path, source_base: Path,
              destination_base: Path, config_pairs: list[tuple[Path, Path]]) -> int:
    """Run a resumable transfer. Sources survive until all targets verify."""
    moves = _plan(source_runtime, destination_runtime, config_pairs)
    if not moves:
        return 0
    _legacy_registry_conflict(source_runtime, destination_runtime, destination_base, source_base)
    _write_journal(destination_base, source_base, moves)
    # First promote/verify every destination. An interruption here leaves all
    # originals intact and the journal authorizes a verified retry.
    for source, destination in moves:
        _stage_copy(source, destination)
    # Only after every target exists and compares exactly may any source go away.
    for source, destination in moves:
        if not _same_content(source, destination):
            raise MigrationConflict(f"Destination changed while migrating {source}; source retained.")
    for source, _ in moves:
        _remove_source(source)
    _drop_baked_artifacts(source_runtime)
    env = destination_base / ".env.local"
    if env.exists():
        try:
            env.chmod(0o600)
        except OSError:
            pass
    return len(moves)


def _reexec_finalize(*, original_command: bool = False, persist_home: Path | None = None) -> None:
    if persist_home is not None:
        os.environ[_PERSIST_PENDING_ENV] = str(persist_home)
    if original_command:
        os.environ[_AUTO_FINALIZE_ENV] = "1"
        os.execv(sys.executable, [sys.executable, str(ENTRY), *sys.argv[1:]])
    os.execv(sys.executable, [sys.executable, str(ENTRY), "migrate", "--finalize"])


def _regenerate_baked_artifacts(cfg) -> None:
    """Rebuild every artifact whose contents can bake the old base path."""
    write_compose_files(cfg)
    regen_caddyfile(cfg)
    # Herd shims are recreated by regular reconcile.  Remove stale paths first
    # so a later ensure cannot accidentally reuse a moved shim.
    shutil.rmtree(RUNTIME_DIR / "herd-shims", ignore_errors=True)
    ensure_tools_venv()


def _finalize(cfg) -> None:
    info(f"Finalizing migration. RUNTIME_DIR = {RUNTIME_DIR}")
    try:
        _regenerate_baked_artifacts(cfg)
    except Exception as exc:
        die(f"Migration retained its verified data but could not regenerate baked artifacts: {exc}")

    failed = []
    for name, inst_cfg in sorted(resolve_instances(cfg).items()):
        if _is_herd_instance(name) or not _instance_running(name):
            continue
        info(f"  recreating web tier for '{name}' (new absolute mounts)…")
        try:
            compose("up", "-d", "--force-recreate", instance=name, check=True)
            if not _wait_reachable(inst_cfg, timeout=40):
                raise RuntimeError(f"not reachable at {site_url(inst_cfg)}")
        except Exception as exc:
            failed.append((name, str(exc)))
    if failed:
        for name, reason in failed:
            info(f"  ! {name}: {reason}")
        die(f"Migration finished but {len(failed)} instance(s) did not verify. "
            "Investigate with `./sb status --instance <name>` / `./sb doctor`.")

    pending = os.environ.pop(_PERSIST_PENDING_ENV, None)
    if pending:
        _persist_home_selection(Path(pending))
    _clear_journal(BASE)
    ok(f"Migration complete. All state under {BASE}; instances verified.")


def _persist_home_selection(base: Path) -> None:
    """Persist the non-secret selector only after relocation succeeds."""
    hint = Path.home() / ".config" / "sandbox" / "home"
    hint.parent.mkdir(parents=True, exist_ok=True)
    temporary = hint.with_name(f".{hint.name}.tmp")
    temporary.write_text(str(base.expanduser().resolve()) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, hint)


def cmd_migrate(cfg, args) -> None:
    if getattr(args, "finalize", False):
        with _migration_lock(BASE):
            _finalize(cfg)
        return

    if getattr(args, "force", False):
        if not getattr(args, "apply", False):
            die("`--force` is a re-verification action; use it with `--apply`.")
        with _migration_lock(BASE):
            _finalize(cfg)
        return

    source_runtime = _legacy_runtime()
    destination_base = _new_base()
    destination_runtime = destination_base / "runtime"
    pairs = _legacy_config_secrets()
    moves = _plan(source_runtime, destination_runtime, pairs)
    if not moves:
        journal = _load_journal(destination_base)
        if journal and journal.get("source") == str(ROOT.resolve()):
            info("Resuming verified migration finalization…")
            _reexec_finalize()
        ok(f"No legacy state to migrate; base is {destination_base}. Nothing to do.")
        return
    if getattr(args, "dry_run", False) or not getattr(args, "apply", False):
        _legacy_registry_conflict(source_runtime, destination_runtime, destination_base, ROOT)
        info(f"Migration plan → base {destination_base} (dry-run; pass --apply to execute):")
        for source, destination in moves:
            print(f"  move  {source}  →  {destination}")
        print("  regenerate compose, Herd shims, proxy routing, and .venv-tools")
        return
    try:
        with _migration_lock(destination_base):
            moved = _transfer(source_runtime, destination_runtime, ROOT, destination_base, pairs)
    except MigrationConflict as exc:
        die(str(exc))
    info(f"Moved {moved} verified pure-data artifact(s). Re-running to regenerate baked artifacts…")
    _reexec_finalize()


def maybe_auto_migrate() -> bool:
    """Apply exactly the safe first-run upgrade path before normal dispatch.

    It runs only when the destination has no user state.  A populated base is
    never merged by an ordinary command; the user receives the explicit
    ``sb migrate --apply`` conflict handling instead.
    """
    if os.environ.get(_AUTO_FINALIZE_ENV):
        return False
    source_runtime = _legacy_runtime()
    destination_base = _new_base()
    moves = _plan(source_runtime, destination_base / "runtime", _legacy_config_secrets())
    if not moves:
        journal = _load_journal(destination_base)
        if journal and journal.get("source") == str(ROOT.resolve()):
            info("Resuming verified automatic migration finalization…")
            _reexec_finalize(original_command=True)
        return False
    if _base_has_state(destination_base):
        _legacy_registry_conflict(source_runtime, destination_base / "runtime", destination_base, ROOT)
        return False
    try:
        with _migration_lock(destination_base):
            moved = _transfer(source_runtime, destination_base / "runtime", ROOT,
                              destination_base, _legacy_config_secrets())
    except MigrationConflict as exc:
        die(str(exc))
    info(f"Automatically migrated {moved} legacy artifact(s) under {destination_base}.")
    _reexec_finalize(original_command=True)
    return True  # pragma: no cover - os.execv never returns


def finalize_auto_migration(cfg) -> None:
    if not os.environ.pop(_AUTO_FINALIZE_ENV, None):
        return
    with _migration_lock(BASE):
        _finalize(cfg)


def cmd_home(cfg, args) -> None:
    target = getattr(args, "dir", None)
    if not target:
        info(f"SANDBOX_HOME base: {BASE}")
        info(f"  runtime: {RUNTIME_DIR}  (present: {_state_present(RUNTIME_DIR)})")
        info(f"  config : {CONFIG_FILE}  (exists: {CONFIG_FILE.exists()})")
        return
    destination_base = Path(target).expanduser().resolve()
    source_base = BASE
    if source_base == destination_base:
        ok("Base already at that location. Nothing to do.")
        return
    source_runtime = RUNTIME_DIR
    pairs = [(source_base / name, destination_base / name)
             for name in ("sandbox.local.yml", ".env.local", "config.json")]
    try:
        with _migration_lock(source_base, destination_base):
            moved = _transfer(source_runtime, destination_base / "runtime", source_base,
                              destination_base, pairs)
    except MigrationConflict as exc:
        die(str(exc))
    os.environ["SANDBOX_HOME"] = str(destination_base)
    info(f"Moved {moved} verified artifact(s). Re-running under {destination_base}…")
    _reexec_finalize(persist_home=destination_base)


register({"migrate": cmd_migrate, "home": cmd_home})

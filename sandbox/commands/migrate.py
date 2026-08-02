"""`sb migrate` / `sb home` — relocate ALL machine-state under the single
per-user base ($SANDBOX_HOME, default ~/sandbox) — spec 009.

Pure-data artifacts (wp-<inst>, snapshots, dl-cache, seeds, registry, test
harness, proxy certs, sandbox.local.yml, .env.local, config.json) MOVE as-is.
Baked artifacts (compose files, herd shims, Caddyfile, the tools venv) are
REGENERATED/RECREATED for the new base, never moved. The move is idempotent and
re-runnable; on conflict the base is authoritative (no merge).
"""
from __future__ import annotations
import os
import shutil
import sys
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


# Items directly under the legacy runtime dir that are REGENERATED (skip the move;
# they're rebuilt from config for the new base). Everything else is pure data.
_REGENERATED = {"compose", "herd-shims", ".venv-tools"}


def _legacy_runtime() -> Path:
    return ROOT / "runtime"


def _new_base() -> Path:
    return Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()


def _legacy_config_secrets() -> list[tuple[Path, Path]]:
    """(src, dst) pairs for per-machine config/secrets consolidating under base."""
    base = _new_base()
    return [
        (ROOT / "sandbox.local.yml", base / "sandbox.local.yml"),
        (ROOT / ".env.local", base / ".env.local"),
        (Path.home() / ".config" / "sandbox" / "config.json", base / "config.json"),
    ]


def _state_present(p: Path) -> bool:
    return p.exists() and (not p.is_dir() or any(p.iterdir()))


def _plan(legacy_rt: Path, new_rt: Path) -> tuple[list, list]:
    """Return (runtime_moves, config_moves) as (src, dst) lists."""
    runtime_moves = []
    if legacy_rt.exists():
        for item in sorted(legacy_rt.iterdir()):
            if item.name in _REGENERATED:
                continue
            runtime_moves.append((item, new_rt / item.name))
    config_moves = [(s, d) for s, d in _legacy_config_secrets() if s.exists()]
    return runtime_moves, config_moves


def _move(src: Path, dst: Path) -> None:
    """Move src→dst preserving mode (shutil.move keeps it). Re-runnable: if dst
    already exists (interrupted prior run) and src still does, prefer the dst that
    is already in place and drop the stale src copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # Already moved in a prior run; remove the leftover source.
        if src.is_dir():
            shutil.rmtree(src, ignore_errors=True)
        else:
            try:
                src.unlink()
            except OSError:
                pass
        return
    shutil.move(str(src), str(dst))


def cmd_migrate(cfg, args) -> None:
    legacy_rt = _legacy_runtime()
    base = _new_base()
    new_rt = base / "runtime"

    finalize = getattr(args, "finalize", False)
    do_apply = getattr(args, "apply", False) or finalize

    # --finalize: second pass after re-exec, with module constants recomputed
    # against the now-populated base. Regenerate baked artifacts + verify.
    if finalize:
        _finalize(cfg)
        return

    legacy_has = _state_present(legacy_rt) and (legacy_rt / "registry.json").exists()
    new_has = (new_rt / "registry.json").exists()

    # Idempotent no-op: already migrated.
    if new_has and not legacy_has:
        ok(f"Already migrated — state lives under {base}. Nothing to do.")
        return

    # Conflict: both populated. Base is authoritative; never merge/overwrite.
    if new_has and legacy_has:
        die(f"Conflict: state exists BOTH under the base ({new_rt}) and in the "
            f"repo ({legacy_rt}). The base is authoritative. Resolve manually "
            f"(remove or archive {legacy_rt}) then re-run. No merge performed.")

    if not legacy_has:
        ok(f"No in-repo state to migrate; base is {base}. Nothing to do.")
        return

    runtime_moves, config_moves = _plan(legacy_rt, new_rt)

    # Dry-run (default): show the plan, change nothing.
    if not do_apply:
        info(f"Migration plan → base {base} (dry-run; pass --apply to execute):")
        print(f"\n  runtime → {new_rt}")
        for s, d in runtime_moves:
            print(f"    move  {s.name}")
        print(f"\n  config/secrets → {base}")
        for s, d in config_moves:
            print(f"    move  {s.name}  →  {d}")
        print("\n  regenerate (not moved): compose files, herd shims, "
              "Caddyfile, .venv-tools")
        print("\n  Then: recreate each running instance's web tier (absolute "
              "mounts) and verify it serves.")
        info("Re-run with `./sb migrate --apply` to perform the migration.")
        return

    # --- Execute -----------------------------------------------------------
    info(f"Migrating machine-state under {base} …")
    new_rt.mkdir(parents=True, exist_ok=True)
    for s, d in runtime_moves:
        _move(s, d)
    info(f"  moved {len(runtime_moves)} runtime item(s)")
    for s, d in config_moves:
        _move(s, d)
        if d.name == ".env.local":
            try:
                d.chmod(0o600)
            except OSError:
                pass
    if config_moves:
        info(f"  moved {len(config_moves)} config/secret file(s)")

    # Drop the now-emptied legacy runtime dir (regenerated leftovers aside).
    for name in _REGENERATED:
        shutil.rmtree(legacy_rt / name, ignore_errors=True)
    try:
        if legacy_rt.exists() and not any(legacy_rt.iterdir()):
            legacy_rt.rmdir()
    except OSError:
        pass

    ok("Pure-data moved. Re-running to regenerate baked artifacts + verify…")
    # Re-exec so RUNTIME_DIR/COMPOSE_DIR/etc. recompute against the populated
    # base (the dispatch gate also regenerates compose with absolute mounts).
    os.execv(sys.executable,
             [sys.executable, str(ENTRY), "migrate", "--finalize"])


def _finalize(cfg) -> None:
    """Post-move pass (fresh module constants). Compose was already regenerated by
    the dispatch gate (write_compose_files). Recreate the venv, regenerate proxy,
    recreate each running instance's web tier, and verify it serves."""
    info(f"Finalizing migration. RUNTIME_DIR = {RUNTIME_DIR}")

    # Tools venv: recreated lazily on next use via the stale-path guard; nudge it.
    try:
        ensure_tools_venv()
    except Exception as e:
        info(f"  (tools venv will rebuild on next use: {e})")

    # Unreceipted aggregate proxy state is never regenerated during migration.
    # Receipt-owned services reconcile independently; per-port URLs remain the
    # deterministic fallback until that succeeds.

    instances = resolve_instances(cfg)
    failed = []
    for name in sorted(instances):
        inst_cfg = instances[name]
        # Herd (host) instances aren't docker — their WP dir moved with the base
        # and Herd serves from it directly; nothing to recreate here.
        if _is_herd_instance(name):
            continue
        if not _instance_running(name):
            continue
        info(f"  recreating web tier for '{name}' (new absolute mounts)…")
        try:
            compose("up", "-d", "--force-recreate", instance=name, check=True)
        except Exception as e:
            failed.append((name, str(e)))
            continue
        if not _wait_reachable(inst_cfg, timeout=40):
            failed.append((name, f"not reachable at {site_url(inst_cfg)}"))

    if failed:
        for n, why in failed:
            info(f"  ! {n}: {why}")
        die(f"Migration finished but {len(failed)} instance(s) did not verify. "
            f"Investigate with `./sb status --instance <name>` / `./sb doctor`.")
    ok(f"Migration complete. All state under {BASE}; instances verified.")


def cmd_home(cfg, args) -> None:
    """Print the resolved base, or relocate the base to a new directory."""
    target = getattr(args, "dir", None)
    if not target:
        info(f"SANDBOX_HOME base: {BASE}")
        info(f"  runtime: {RUNTIME_DIR}  (present: {_state_present(RUNTIME_DIR)})")
        info(f"  config : {CONFIG_FILE}  (exists: {CONFIG_FILE.exists()})")
        info("Relocate with `SANDBOX_HOME=<dir> ./sb migrate --apply`, or "
             "`./sb home <dir>`.")
        return
    new_dir = Path(target).expanduser().resolve()
    info(f"Relocating base → {new_dir}")
    # Reuse the migration engine pointed at the new base. The current state may be
    # in-repo (pre-migration) or already under the old base; in both cases the
    # move lands under SANDBOX_HOME=new_dir on the re-exec.
    os.environ["SANDBOX_HOME"] = str(new_dir)
    # Delegate: from the OLD base to the new one we move runtime + config.
    _relocate_existing_base(cfg, new_dir)


def _relocate_existing_base(cfg, new_dir: Path) -> None:
    """Move an already-consolidated base (old SANDBOX_HOME) to new_dir, then
    re-exec to regenerate + verify."""
    old_rt = RUNTIME_DIR  # resolved against the pre-exec env
    new_rt = new_dir / "runtime"
    if old_rt.resolve() == new_rt.resolve():
        ok("Base already at that location. Nothing to do.")
        return
    new_dir.mkdir(parents=True, exist_ok=True)
    new_rt.mkdir(parents=True, exist_ok=True)
    if old_rt.exists():
        for item in sorted(old_rt.iterdir()):
            if item.name in _REGENERATED:
                shutil.rmtree(item, ignore_errors=True)
                continue
            _move(item, new_rt / item.name)
    for name in ("sandbox.local.yml", ".env.local", "config.json"):
        src = BASE / name
        if src.exists():
            _move(src, new_dir / name)
            if name == ".env.local":
                try:
                    (new_dir / name).chmod(0o600)
                except OSError:
                    pass
    ok("Moved. Re-running to regenerate + verify under the new base…")
    os.execv(sys.executable,
             [sys.executable, str(ENTRY), "migrate", "--finalize"])


register({
    'migrate': cmd_migrate,
    'home': cmd_home,
})

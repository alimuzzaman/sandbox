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



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register
from sandbox.application.context import preflight_instance_capability



def cmd_snapshot(cfg, args) -> None:
    """Save the current DB + uploads under runtime/snapshots/<instance>/<name>/.

    DB is exported via `wp db export`; uploads are tar'd to keep symlinks intact.
    Restore with `./sb restore <name>`.
    """
    inst = args.resolved_instance
    error = preflight_instance_capability(cfg, inst, "wordpress.snapshot")
    if error is not None:
        die(error.message)
    name = _slug_snapshot_name(args.name)
    if _is_herd_instance(inst):
        die("snapshots aren't supported on herd (host) instances yet — "
            "use `./sb wp db export` / `db import` directly")
    if not name:
        die(f"could not derive a snapshot name from '{args.name}' — use "
            "letters, numbers, spaces or hyphens")
    if name == _BASELINE_DIR:
        die(f"'{name}' is reserved for the install baseline — use `./sb reset --rebaseline`")
    if name != (args.name or "").strip():
        info(f"Snapshot name slugified to '{name}'")
    snap_root = snapshots_dir(inst)
    snap_root.mkdir(parents=True, exist_ok=True)
    target = snap_root / name
    if target.exists() and not args.force:
        die(f"snapshot '{name}' exists — pass --force to overwrite")
    db_only = bool(getattr(args, "db_only", False))
    _capture_snapshot(inst, snap_root, name, db_only=db_only)
    ok(f"Snapshot '{name}' saved ({'db-only' if db_only else 'full'}).")


# Reserved dir for the post-install baseline (spec 008). Not a valid user snapshot
# name (leading underscore), so it can never collide with `./sb snapshot <name>`.
_BASELINE_DIR = "__install__"


def _capture_snapshot(inst: str, snap_root: Path, name: str, *, db_only: bool) -> None:
    """Export the DB (always) + uploads (unless db_only) into snap_root/<name>/,
    recording mode in META. Shared by cmd_snapshot, the baseline, and reset.

    On any failure the partial target dir is removed and the error re-raised, so a
    failed capture never leaves a half-written snapshot (e.g. an empty dir with no
    db.sql that later reads as a 0 KB snapshot)."""
    target = snap_root / name
    target.mkdir(parents=True, exist_ok=True)
    try:
        info(f"Exporting DB → {target}/db.sql")
        compose("run", "--rm", "-v", f"{snap_root}:/snapshots",
                "wpcli", "db", "export", f"/snapshots/{name}/db.sql", "--add-drop-table",
                instance=inst)
        if not (target / "db.sql").exists():
            raise RuntimeError(f"db export produced no db.sql for snapshot '{name}'")
        mode = "db-only"
        if not db_only:
            uploads = wp_dir(inst) / "wp-content" / "uploads"
            if uploads.exists():
                info(f"Archiving uploads → {target}/uploads.tgz")
                run(["tar", "-C", str(uploads.parent), "-czf",
                     str(target / "uploads.tgz"), "uploads"])
                mode = "full"
        active = _active_project_name(inst) or ""
        (target / "META").write_text(f"project={active}\ninstance={inst}\nmode={mode}\n")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)  # no half-written snapshot left behind
        raise


def capture_install_baseline(inst: str, force: bool = False) -> None:
    """Capture the reserved db-only @install baseline (spec 008), representing the
    post-provision state (after plugins/themes are wired). Captured ONCE — a no-op
    if a baseline already exists unless `force` (so `up`/`ensure` never overwrite a
    good baseline with later, dirtied state). Docker only.

    Never breaks provisioning, but a failure is LOGGED (not silently swallowed) —
    a swallowed failure used to leave an empty `__install__` dir reading as a 0 KB
    snapshot. _capture_snapshot now cleans up the partial dir on failure."""
    if _is_herd_instance(inst):
        return
    snap_root = snapshots_dir(inst)
    snap_root.mkdir(parents=True, exist_ok=True)
    if (snap_root / _BASELINE_DIR / "db.sql").exists() and not force:
        return
    try:
        _capture_snapshot(inst, snap_root, _BASELINE_DIR, db_only=True)
    except Exception as e:
        info(f"⚠ @install baseline capture failed for '{inst}' (reset won't have a "
             f"baseline until the next up): {e}")


# A full (DB + uploads) named snapshot of the clean post-install state, taken on
# first create/recreate so the install can be fully rolled back (vs the db-only
# __install__ baseline that `reset` uses). Listed + restorable like any snapshot.
INSTALL_FULL_SNAPSHOT = "install-baseline"


def capture_install_full_snapshot(inst: str, force: bool = False) -> None:
    """Capture a FULL named snapshot (DB + uploads) of the post-provision state as
    `install-baseline`, on first create/recreate. No-op if it already exists unless
    `force`. Docker only; logs (never breaks provisioning) on failure."""
    if _is_herd_instance(inst):
        return
    snap_root = snapshots_dir(inst)
    snap_root.mkdir(parents=True, exist_ok=True)
    if (snap_root / INSTALL_FULL_SNAPSHOT / "db.sql").exists() and not force:
        return
    try:
        _capture_snapshot(inst, snap_root, INSTALL_FULL_SNAPSHOT, db_only=False)
    except Exception as e:
        info(f"⚠ full install snapshot '{INSTALL_FULL_SNAPSHOT}' capture failed "
             f"for '{inst}': {e}")

def cmd_restore(cfg, args) -> None:
    inst = args.resolved_instance
    error = preflight_instance_capability(cfg, inst, "wordpress.restore")
    if error is not None:
        die(error.message)
    if _is_herd_instance(inst):
        die("snapshots aren't supported on herd (host) instances yet — "
            "use `./sb wp db export` / `db import` directly")
    snap_root = snapshots_dir(inst)
    # Accept the name as stored OR its slug, so `restore "snapshot 2"` resolves
    # the snapshot saved as "snapshot-2" (legacy exact names still match too).
    name = next((c for c in (args.name, _slug_snapshot_name(args.name))
                 if c and _valid_snapshot_name(c) and (snap_root / c).is_dir()),
                None)
    if name is None:
        die(f"no snapshot '{args.name}' under {snap_root}")
    _restore_snapshot(inst, snap_root, name)
    ok(f"Restored snapshot '{name}'.")


def _restore_snapshot(inst: str, snap_root: Path, name: str) -> None:
    """Drop+import the snapshot's DB (true point-in-time replacement) and restore
    uploads if the snapshot has them (db-only snapshots leave uploads untouched)."""
    target = snap_root / name
    sql = target / "db.sql"
    if not sql.exists():
        die(f"snapshot is missing db.sql: {sql}")
    # `db reset --yes` drops+recreates the empty schema first so restore is a true
    # replacement (tables created after the snapshot don't survive).
    info("Resetting DB (drop all tables) before import…")
    # Run via the dedicated `wpcli` service, NOT the wpcli() helper: that helper
    # execs into the web (php-fpm) container, which has no mysql client, so
    # `wp db reset` dies with "env: 'mysql': No such file or directory". The wpcli
    # service image ships the client — same path the import/export below use.
    compose("run", "--rm", "wpcli", "db", "reset", "--yes", instance=inst)
    info(f"Importing DB ← {sql}")
    compose("run", "--rm", "-v", f"{snap_root}:/snapshots",
            "wpcli", "db", "import", f"/snapshots/{name}/db.sql", instance=inst)
    tgz = target / "uploads.tgz"
    if tgz.exists():
        info(f"Restoring uploads ← {tgz}")
        run(["tar", "-C", str(wp_dir(inst) / "wp-content"), "-xzf", str(tgz)])


def cmd_reset(cfg, args) -> None:
    """Reset the DB to the post-install @install baseline (spec 008) — a fast
    in-place DB rollback (keeps uploads, containers, ports). `--rebaseline`
    re-captures the baseline from the current DB instead of restoring."""
    inst = args.resolved_instance
    error = preflight_instance_capability(cfg, inst, "wordpress.reset")
    if error is not None:
        die(error.message)
    if _is_herd_instance(inst):
        die("reset isn't supported on herd instances yet")
    snap_root = snapshots_dir(inst)
    baseline = snap_root / _BASELINE_DIR
    if getattr(args, "rebaseline", False):
        capture_install_baseline(inst, force=True)
        ok("Re-captured the @install baseline from the current DB.")
        return
    if not (baseline / "db.sql").exists():
        die("no @install baseline for this instance. Create one with "
            "`./sb reset --rebaseline` (captures the current DB as the baseline).")
    if not getattr(args, "yes", False):
        ans = input(f"This drops the current DB for '{inst}' and restores the "
                    f"post-install baseline. Continue? [y/N] ").strip().lower()
        if ans != "y":
            return
    _restore_snapshot(inst, snap_root, _BASELINE_DIR)
    ok("Reset to the post-install baseline (uploads untouched).")

def cmd_snapshots(cfg, args) -> None:
    inst = args.resolved_instance
    snap_root = snapshots_dir(inst)
    # Count only user snapshots (reserved baselines like __install__ use a leading
    # underscore and are hidden), so a baseline-only dir still reads as "empty".
    user_snaps = ([p for p in snap_root.iterdir() if p.is_dir() and not p.name.startswith("_")]
                  if snap_root.exists() else [])
    if not user_snaps:
        info(f"No snapshots yet for instance '{inst}'. "
             f"Save one: ./sb snapshot <name> --instance {inst}")
        return
    print()
    for entry in sorted(snap_root.iterdir()):
        # Hide reserved internal baselines (_BASELINE_DIR / __install__) — they are
        # not restorable as named user snapshots (leading-underscore convention).
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        m = entry / "META"
        meta = m.read_text().strip().replace("\n", " ") if m.exists() else ""
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        label = "@install (baseline)" if entry.name == _BASELINE_DIR else entry.name
        print(f"  {label:<24} {size // 1024:>6} KB   {meta}")
    print()

def cmd_clean(cfg, args) -> None:
    inst = args.resolved_instance
    error = preflight_instance_capability(cfg, inst, "wordpress.cli")
    if error is not None:
        die(error.message)
    if not args.yes:
        ans = input(f"This deletes the DB volume for instance '{inst}'. "
                    f"Continue? [y/N] ").lower()
        if ans != "y":
            return
    compose("down", "-v", instance=inst)
    # The DB volume is gone, so any multisite network it held is gone too. The
    # marker lives in the host-bind-mounted wp-dir (which `down -v` does NOT
    # wipe), so without this a later `ensure` would boot with the MULTISITE
    # constants active against a freshly-empty network → "Site not found" / 500.
    # Drop it: the next install re-converts and re-writes it from scratch.
    marker = wp_dir(inst) / MULTISITE_MARKER
    if marker.exists():
        marker.unlink()
    ok(f"Stopped and wiped DB volume for instance '{inst}'")

register({
    'snapshot': cmd_snapshot,
    'restore': cmd_restore,
    'snapshots': cmd_snapshots,
    'reset': cmd_reset,
    'clean': cmd_clean,
})

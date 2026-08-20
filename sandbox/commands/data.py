from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register
from sandbox.application.context import preflight_instance_capability


# Reserved directory for the post-install baseline (spec 008).  ``@install``
# is the public label; the on-disk name is deliberately not a valid ordinary
# snapshot name.  Keep both labels guarded before slugification so callers
# cannot accidentally create a normal ``install`` snapshot from the baseline
# label.
_BASELINE_DIR = "__install__"
_BASELINE_LABELS = frozenset((_BASELINE_DIR, "@install"))


def _is_reserved_baseline_name(name: str | None) -> bool:
    """Return whether *name* is an explicit ``@install`` baseline label."""
    return (name or "").strip().lower() in _BASELINE_LABELS



def cmd_snapshot(cfg, args) -> None:
    """Save the current DB + uploads under runtime/snapshots/<instance>/<name>/.

    DB is exported via `wp db export`; uploads are tar'd to keep symlinks intact.
    Restore with `./sb restore <name>`.
    """
    inst = args.resolved_instance
    error = preflight_instance_capability(cfg, inst, "wordpress.snapshot")
    if error is not None:
        die(error.message)
    raw_name = (getattr(args, "name", "") or "").strip()
    if _is_reserved_baseline_name(raw_name):
        die(f"'{raw_name}' is reserved for the install baseline — use `./sb reset --rebaseline`")
    name = _slug_snapshot_name(raw_name)
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
    if target.exists() and not bool(getattr(args, "force", False)):
        die(f"snapshot '{name}' exists — pass --force to overwrite")
    db_only = bool(getattr(args, "db_only", False))
    _capture_snapshot(inst, snap_root, name, db_only=db_only)
    ok(f"Snapshot '{name}' saved ({'db-only' if db_only else 'full'}).")


def _open_snapshot_dump(path: Path):
    """Open a new host-owned snapshot dump for direct child stdout streaming."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "wb")
    except BaseException:
        # Neither fchmod nor fdopen transfers ownership when it fails. Close
        # the raw descriptor on every error, including an interrupted call.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _capture_snapshot(inst: str, snap_root: Path, name: str, *, db_only: bool) -> None:
    """Export the DB (always) + uploads (unless db_only) into snap_root/<name>/,
    recording mode in META. Shared by cmd_snapshot, the baseline, and reset.

    On any failure the partial target dir is removed and the error re-raised, so a
    failed capture never leaves a half-written snapshot (e.g. an empty dir with no
    db.sql that later reads as a 0 KB snapshot)."""
    target = snap_root / name
    # Capture into a sibling first.  In particular, `--db-only --force` must
    # not retain uploads.tgz from the full snapshot it replaces; staging also
    # keeps the old snapshot usable if export fails.
    staging_name = f".{name}.tmp-{uuid.uuid4().hex}"
    staging = snap_root / staging_name
    staging_db = staging / "db.sql"
    try:
        staging.mkdir(parents=True, exist_ok=False, mode=0o700)
        info(f"Exporting DB → {target}/db.sql")
        # Stream stdout directly into a 0600 host file. The wpcli service keeps
        # its normal UID and no snapshot directory is bind-mounted into it.
        with _open_snapshot_dump(staging_db) as dump:
            compose("run", "--rm", "-T", "wpcli", "db", "export", "-", "--quiet",
                    "--add-drop-table",
                    instance=inst, stdout=dump)
        if not staging_db.exists() or staging_db.stat().st_size == 0:
            raise RuntimeError(f"db export produced no db.sql for snapshot '{name}'")
        mode = "db-only"
        if not db_only:
            uploads = wp_dir(inst) / "wp-content" / "uploads"
            if uploads.exists():
                info(f"Archiving uploads → {target}/uploads.tgz")
                run(["tar", "-C", str(uploads.parent), "-czf",
                     str(staging / "uploads.tgz"), "uploads"])
                mode = "full"
        active = _active_project_name(inst) or ""
        (staging / "META").write_text(f"project={active}\ninstance={inst}\nmode={mode}\n")
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    except (Exception, SystemExit):
        # Remove the whole sibling even when compose exits via SystemExit, so a
        # failed capture never leaves a half-written dump behind.
        shutil.rmtree(staging, ignore_errors=True)
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
    try:
        snap_root.mkdir(parents=True, exist_ok=True)
        if (snap_root / _BASELINE_DIR / "db.sql").exists() and not force:
            return
        _capture_snapshot(inst, snap_root, _BASELINE_DIR, db_only=True)
    except (Exception, SystemExit) as e:
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
    try:
        snap_root.mkdir(parents=True, exist_ok=True)
        if (snap_root / INSTALL_FULL_SNAPSHOT / "db.sql").exists() and not force:
            return
        _capture_snapshot(inst, snap_root, INSTALL_FULL_SNAPSHOT, db_only=False)
    except (Exception, SystemExit) as e:
        info(f"⚠ full install snapshot '{INSTALL_FULL_SNAPSHOT}' capture failed "
             f"for '{inst}': {e}")


def capture_install_snapshots(inst: str, force: bool = False) -> None:
    """Capture both post-provision install restore points together.

    The DB-only baseline powers `reset`; the full named snapshot remains the
    opt-in complete rollback.  Keeping this pair in one helper prevents fresh
    provisioning and seed onboarding from drifting in capture order.
    """
    capture_install_baseline(inst, force=force)
    capture_install_full_snapshot(inst, force=force)


def _confirmation_requested(args) -> bool:
    """Return whether the caller explicitly acknowledged a destructive action.

    ``--yes`` is the established CLI spelling.  ``confirm`` is also accepted
    here so callers that already use the command-layer confirmation convention
    can opt in without weakening the default interactive guard.
    """
    return bool(getattr(args, "yes", False) or getattr(args, "confirm", False))


def _confirm_destructive_action(inst: str, target: str, args, *, action: str) -> bool:
    """Require an explicit acknowledgement before dropping and importing DB data.

    A non-interactive caller must provide an explicit confirmation flag.  A
    terminal caller gets the same default-deny prompt used by reset; cancelling
    or closing the prompt returns before any DB command is dispatched.
    """
    if _confirmation_requested(args):
        return True
    if not bool(getattr(sys.stdin, "isatty", lambda: False)()):
        die(f"{action} requires --yes when stdin is not interactive")
    try:
        answer = input(
            f"This drops the current DB for '{inst}' and restores the {target}. "
            "Continue? [y/N] "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def cmd_restore(cfg, args) -> None:
    inst = args.resolved_instance
    error = preflight_instance_capability(cfg, inst, "wordpress.restore")
    if error is not None:
        die(error.message)
    if _is_herd_instance(inst):
        die("snapshots aren't supported on herd (host) instances yet — "
            "use `./sb wp db export` / `db import` directly")
    raw_name = (getattr(args, "name", "") or "").strip()
    if _is_reserved_baseline_name(raw_name):
        die(f"'{raw_name}' is the protected install baseline — use `./sb reset` instead")
    snap_root = snapshots_dir(inst)
    # Accept the name as stored OR its slug, so `restore "snapshot 2"` resolves
    # the snapshot saved as "snapshot-2" (legacy exact names still match too).
    name = next((c for c in (args.name, _slug_snapshot_name(args.name))
                 if c and _valid_snapshot_name(c) and (snap_root / c).is_dir()),
                None)
    if name is None:
        die(f"no snapshot '{args.name}' under {snap_root}")
    if not _confirm_destructive_action(inst, f"snapshot '{name}'", args,
                                       action="restore"):
        return
    _restore_snapshot(inst, snap_root, name)
    ok(f"Restored snapshot '{name}'.")


def _restore_snapshot(inst: str, snap_root: Path, name: str) -> None:
    """Drop+import the snapshot's DB (true point-in-time replacement) and restore
    uploads if the snapshot has them (db-only snapshots leave uploads untouched)."""
    target = snap_root / name
    sql = target / "db.sql"
    try:
        sql_stat = sql.lstat()
    except FileNotFoundError:
        die(f"snapshot is missing db.sql: {sql}")
    except OSError as e:
        die(f"snapshot db.sql cannot be inspected: {sql}: {e}")
    if stat.S_ISLNK(sql_stat.st_mode) or not stat.S_ISREG(sql_stat.st_mode):
        die(f"snapshot db.sql is not a regular file: {sql}")
    if sql_stat.st_size == 0:
        die(f"snapshot db.sql is empty: {sql}")
    try:
        dump = sql.open("rb")
    except OSError as e:
        die(f"snapshot db.sql cannot be opened: {sql}: {e}")
    # `db reset --yes` drops+recreates the empty schema first so restore is a true
    # replacement (tables created after the snapshot don't survive).
    with dump:
        info("Resetting DB (drop all tables) before import…")
        # Run via the dedicated `wpcli` service, NOT the wpcli() helper: that helper
        # execs into the web (php-fpm) container, which has no mysql client, so
        # `wp db reset` dies with "env: 'mysql': No such file or directory". The wpcli
        # service image ships the client — same path the import/export below use.
        compose("run", "--rm", "wpcli", "db", "reset", "--yes", instance=inst)
        info(f"Importing DB ← {sql}")
        compose("run", "--rm", "-T", "wpcli", "db", "import", "-",
                instance=inst, stdin=dump)
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
    if not _confirm_destructive_action(inst, "post-install baseline", args,
                                       action="reset"):
        return
    _restore_snapshot(inst, snap_root, _BASELINE_DIR)
    ok("Reset to the post-install baseline (uploads untouched).")

def cmd_snapshots(cfg, args) -> None:
    inst = args.resolved_instance
    snap_root = snapshots_dir(inst)
    # The protected baseline is displayed separately: it is informative for
    # reset readiness, but it is not a normal snapshot name/action target.
    user_snaps = ([p for p in snap_root.iterdir() if p.is_dir() and not p.name.startswith("_")]
                  if snap_root.exists() else [])
    baseline = snap_root / _BASELINE_DIR
    if not user_snaps and not (baseline / "db.sql").exists():
        info(f"No snapshots yet for instance '{inst}'. "
             f"Save one: ./sb snapshot <name> --instance {inst}")
        return
    print()
    if (baseline / "db.sql").exists():
        m = baseline / "META"
        meta = m.read_text().strip().replace("\n", " ") if m.exists() else ""
        size = sum(f.stat().st_size for f in baseline.rglob("*") if f.is_file())
        print(f"  {'@install (baseline)':<24} {size // 1024:>6} KB   {meta}  [protected; reset target]")
    for entry in sorted(snap_root.iterdir()):
        # The reserved internal baseline was emitted above; it cannot be
        # restored/deleted as a named snapshot.
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        m = entry / "META"
        meta = m.read_text().strip().replace("\n", " ") if m.exists() else ""
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        print(f"  {entry.name:<24} {size // 1024:>6} KB   {meta}")
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

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



def cmd_snapshot(cfg, args) -> None:
    """Save the current DB + uploads under runtime/snapshots/<instance>/<name>/.

    DB is exported via `wp db export`; uploads are tar'd to keep symlinks intact.
    Restore with `./sb restore <name>`.
    """
    inst = args.resolved_instance
    name = args.name
    if _is_herd_instance(inst):
        die("snapshots aren't supported on herd (host) instances yet — "
            "use `./sb wp db export` / `db import` directly")
    if not re.match(r"^[\w.-]+$", name or ""):
        die("snapshot name must be [A-Za-z0-9._-]+")
    snap_root = snapshots_dir(inst)
    snap_root.mkdir(parents=True, exist_ok=True)
    target = snap_root / name
    if target.exists() and not args.force:
        die(f"snapshot '{name}' exists — pass --force to overwrite")
    target.mkdir(parents=True, exist_ok=True)

    # DB → snapshot.sql inside the container's view of the snapshot dir.
    # `wp db export` writes from within the wpcli container, so we mount the
    # snapshots dir as /snapshots for the duration of the call.
    info(f"Exporting DB → {target}/db.sql")
    compose("run", "--rm",
            "-v", f"{snap_root}:/snapshots",
            "wpcli", "db", "export", f"/snapshots/{name}/db.sql",
            "--add-drop-table",
            instance=inst)

    # Uploads — preserve relative symlinks, skip if dir missing
    uploads = wp_dir(inst) / "wp-content" / "uploads"
    if uploads.exists():
        info(f"Archiving uploads → {target}/uploads.tgz")
        run(["tar", "-C", str(uploads.parent), "-czf",
             str(target / "uploads.tgz"), "uploads"])
    active = _active_project_name(inst) or ""
    (target / "META").write_text(f"project={active}\ninstance={inst}\n")
    ok(f"Snapshot '{name}' saved.")

def cmd_restore(cfg, args) -> None:
    inst = args.resolved_instance
    name = args.name
    if _is_herd_instance(inst):
        die("snapshots aren't supported on herd (host) instances yet — "
            "use `./sb wp db export` / `db import` directly")
    snap_root = snapshots_dir(inst)
    target = snap_root / name
    if not target.exists():
        die(f"no snapshot '{name}' under {snap_root}")
    sql = target / "db.sql"
    if not sql.exists():
        die(f"snapshot is missing db.sql: {sql}")
    # Drop ALL tables before importing so restore is a true point-in-time
    # replacement, not a merge. `wp db export` uses --add-drop-table, which only
    # drops tables that exist IN the dump — tables created AFTER the snapshot
    # (e.g. FSI sub-site wp_2_* tables) would otherwise survive the restore and
    # pollute cleanup. `db reset --yes` drops+recreates the empty schema first.
    info("Resetting DB (drop all tables) before import…")
    wpcli(["db", "reset", "--yes"], instance=inst)
    info(f"Importing DB ← {sql}")
    compose("run", "--rm",
            "-v", f"{snap_root}:/snapshots",
            "wpcli", "db", "import", f"/snapshots/{name}/db.sql",
            instance=inst)
    tgz = target / "uploads.tgz"
    if tgz.exists():
        info(f"Restoring uploads ← {tgz}")
        run(["tar", "-C", str(wp_dir(inst) / "wp-content"), "-xzf", str(tgz)])
    ok(f"Restored snapshot '{name}'.")

def cmd_snapshots(cfg, args) -> None:
    inst = args.resolved_instance
    snap_root = snapshots_dir(inst)
    if not snap_root.exists() or not any(snap_root.iterdir()):
        info(f"No snapshots yet for instance '{inst}'. "
             f"Save one: ./sb snapshot <name> --instance {inst}")
        return
    print()
    for entry in sorted(snap_root.iterdir()):
        if not entry.is_dir():
            continue
        meta = ""
        m = entry / "META"
        if m.exists():
            meta = m.read_text().strip().replace("\n", " ")
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        print(f"  {entry.name:<24} {size // 1024:>6} KB   {meta}")
    print()

def cmd_clean(cfg, args) -> None:
    inst = args.resolved_instance
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
    'clean': cmd_clean,
})

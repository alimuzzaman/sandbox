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



def cmd_wp(cfg, args) -> None:
    if not args.passthrough:
        die("usage: ./sb wp <wp-cli args>")
    wpcli(args.passthrough, instance=args.resolved_instance)

def cmd_seed(cfg, args) -> None:
    if not args.file:
        die("usage: ./sb seed <file-in-runtime/seeds>")
    # Containers mount runtime/seeds at /seeds; herd reads the host path.
    seed = (str(ROOT / "runtime" / "seeds" / args.file)
            if _is_herd_instance(args.resolved_instance)
            else f"/seeds/{args.file}")
    wpcli(["import", seed, "--authors=create"],
          instance=args.resolved_instance)

def cmd_visit(cfg, args) -> None:
    """Headless-browser inspection of a URL.

    All flags are forwarded to tools/visit/visit.py; this wrapper just
    ensures the venv + Chromium are present, then exec's the runner so
    the user sees its JSON output and exit code directly.
    """
    py = ensure_tools_venv()
    script = TOOLS_DIR / "visit" / "visit.py"
    cmd = [str(py), str(script), *args.passthrough]
    os.execv(str(py), cmd)

register({
    'wp': cmd_wp,
    'seed': cmd_seed,
    'visit': cmd_visit,
})

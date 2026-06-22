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


def _php_squote(value: str) -> str:
    """Emit a PHP single-quoted string literal whose VALUE is a shell-safe
    token for `value`. Used for WP_PHP_BINARY, which the WP test suite splices
    into a shell command unescaped — the Herd php path has spaces, so the
    stored value must already be shell-quoted. We shell-quote first (Python
    shlex), then PHP-single-quote-escape that for the literal."""
    import shlex
    shell_safe = shlex.quote(value)            # e.g. '/a b/php81'
    php_escaped = shell_safe.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{php_escaped}'"


def _onboard_instance(cfg: dict, name: str, args) -> None:
    from sandbox.commands.instances_cmd import cmd_focus
    from sandbox.commands.wp import cmd_seed
    """Post-install onboarding for a freshly created instance: install plugins/
    projects, enable WP_DEBUG, activate a theme, import seed content. Driven by
    flags (--project/--plugin/--seed/--theme/--wp-debug) for non-interactive
    callers (web UI, CI); on a terminal with NO such flags and without
    --minimal, prompt for each (like `./sb setup`'s post-setup picker). Every
    step is best-effort so one failure never leaves the instance half-made."""
    import types as _t
    interactive = sys.stdin.isatty() and not getattr(args, "minimal", False)

    # ---- collect choices (from flags, else prompt) ----
    slugs = list(getattr(args, "plugins", None) or [])
    seed = getattr(args, "seed", None)
    theme = getattr(args, "theme", None)
    wp_debug = bool(getattr(args, "wp_debug", False))
    flags_given = bool(slugs or seed or theme or wp_debug
                       or getattr(args, "site_title", None))

    if interactive and not flags_given:
        # Per-project model: there's no catalog to pick from. Install wp.org
        # plugins by slug below (or pass --plugin), and set up dev plugins by
        # cd-ing into their repo and running `./sb init`.
        try:
            raw = input("  Install wp.org plugins now? (space-separated slugs, "
                        "blank to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        slugs.extend(tok for tok in raw.replace(",", " ").split() if tok)
        seeds = _web_list_seeds()
        if seeds:
            print(f"\n  Seed demo content? [{', '.join(seeds)}] (blank to skip):")
            try:
                s = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                s = ""
            if s in seeds:
                seed = s
        try:
            d = input("  Enable WP_DEBUG? [y/N]: ").strip().lower()
            wp_debug = d in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            pass

    # ---- apply ----
    if slugs:
        # Per-project model: slugs are wp.org plugins installed into this
        # instance. Dev plugins live in their own repos (cd in + `./sb init`).
        print(f"\n▸ Installing wp.org plugins on '{name}': {', '.join(slugs)}")
        wpcli(["plugin", "install", *slugs, "--activate"],
              instance=name, check=False)
        # Default Claude's focus to the first plugin.
        try:
            cmd_focus(cfg, _t.SimpleNamespace(
                resolved_instance=name, slug=slugs[0], clear=False))
        except Exception:
            pass

    if wp_debug:
        info("enabling WP_DEBUG")
        wpcli(["config", "set", "WP_DEBUG", "true", "--raw",
               "--type=constant"], instance=name, check=False)

    if theme:
        info(f"activating theme {theme}")
        r = wpcli(["theme", "activate", theme], instance=name, check=False,
                  capture=True)
        if getattr(r, "returncode", 1) != 0:
            wpcli(["theme", "install", theme, "--activate"],
                  instance=name, check=False)

    if seed:
        print(f"\n▸ Importing seed content '{seed}'…")
        try:
            cmd_seed(cfg, _t.SimpleNamespace(resolved_instance=name, file=seed))
        except Exception as e:
            info(f"seed import failed: {e}")

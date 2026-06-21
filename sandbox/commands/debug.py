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



def cmd_xdebug(cfg, args) -> None:
    """Toggle Xdebug by writing a drop-in PHP ini into the WP container.

    Uses `wp_exec` semantics — writes the ini, restarts php via the wp service.
    """
    inst = args.resolved_instance
    state = args.state
    if state not in ("on", "off", "status"):
        die("usage: ./sb xdebug on|off|status")
    if _is_herd_instance(inst):
        die("xdebug toggling isn't wired for herd (host) instances — "
            "use Herd's own PHP/Xdebug settings")

    ini_path = "/usr/local/etc/php/conf.d/zz-sandbox-xdebug.ini"
    if state == "status":
        r = compose("exec", "wp", "sh", "-c",
                    f"test -f {ini_path} && echo on || echo off",
                    instance=inst, check=False, capture=True)
        print((r.stdout or "off").strip())
        return

    if state == "on":
        # Install xdebug if absent (pecl), then write the ini.
        info("Installing/enabling Xdebug in the wp container…")
        compose("exec", "wp", "sh", "-c",
                "command -v pecl >/dev/null && "
                "(pecl list | grep -qi xdebug || pecl install xdebug) && "
                f"printf 'zend_extension=xdebug\\n"
                f"xdebug.mode=debug\\n"
                f"xdebug.start_with_request=trigger\\n"
                f"xdebug.client_host=host.docker.internal\\n"
                f"xdebug.client_port=9003\\n' > {ini_path}",
                instance=inst)
    else:
        compose("exec", "wp", "sh", "-c", f"rm -f {ini_path}",
                instance=inst)

    compose("restart", "wp", instance=inst, check=False)
    ok(f"Xdebug {state}.")

def cmd_introspect(cfg, args) -> None:
    """Dump live block/widget/shortcode registries to runtime/cache/*.json.

    Runs PHP via wp-cli's eval-file inside the wpcli container so the WP
    install is fully bootstrapped (all plugins active, all registries populated).
    """
    target = args.target or "all"
    valid = ["blocks", "widgets", "shortcodes", "all"]
    if target not in valid:
        die(f"unknown target '{target}' — choose from: {', '.join(valid)}")

    cache_dir = ROOT / "runtime" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    targets = ["blocks", "widgets", "shortcodes"] if target == "all" else [target]

    inst = args.resolved_instance
    for t in targets:
        info(f"introspecting {t}…")
        php = INTROSPECT_PHP[t]
        # Pipe PHP source into wp eval-file - (stdin). We need to bypass our
        # `run` helper's stdout printing because we want to capture clean JSON.
        proc = subprocess.run(
            ["docker", "compose",
             "-p", project_name(inst),
             "-f", str(compose_file(inst)),
             "run", "--rm", "-T",
             "wpcli", "eval-file", "-"],
            input=php, text=True, capture_output=True, cwd=str(ROOT),
        )
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            die(f"introspect {t} failed (exit {proc.returncode})")

        # The wpcli container prints docker-compose log lines on stderr; stdout
        # is the JSON payload. But the wordpress:cli image sometimes emits
        # WP_DEBUG warnings inline. Strip anything before the opening { or [.
        raw = proc.stdout
        start = min((i for i in [raw.find('{'), raw.find('[')] if i >= 0), default=-1)
        if start < 0:
            die(f"introspect {t}: no JSON in output\n{raw[:500]}")
        json_text = raw[start:].strip()

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            die(f"introspect {t}: bad JSON ({e})\n{json_text[:500]}")

        out_path = cache_dir / f"{t}.json"
        out_path.write_text(json.dumps(parsed, indent=2))
        count = parsed.get("count", "?")
        ok(f"{t}: {count} entries → {out_path.relative_to(ROOT)}")

def cmd_test(cfg, args) -> None:
    """`./sb test [--project-dir DIR] [--provision-only] [-- <phpunit args>]` —
    provision the (cached) external WP test harness (suite + phpunit + polyfills
    + composer + the isolated wp_tests DB + sandbox wp-tests-config.php) for a
    project's instance, then run phpunit at the plugin dir."""
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    entry = sc.registry_get(pconf["root"])
    if not entry:
        die(f"no instance for {pconf['root']} — run `./sb ensure --project-dir {pd}` first.")
    inst = entry["instance"]

    info("Provisioning test harness (cached)…")
    h = _provision_test_harness(inst, pconf)
    suite, tools, config = h["suite"], h["tools"], h["config"]
    ok("Test harness ready:")
    print(f"  instance:   {inst}")
    print(f"  WP suite:   {suite}")
    print(f"  phpunit:    {tools['phpunit']}")
    print(f"  composer:   {tools['composer']}")
    print(f"  polyfills:  {tools['polyfills']}")
    tests_db = (_herd_tests_db(inst) if entry.get("server") == "herd"
                else TESTS_DB_NAME)
    print(f"  tests DB:   {tests_db} (prefix wptests_)")
    print(f"  config:     {config}")

    if getattr(args, "provision_only", False):
        return
    extra = [a for a in (getattr(args, "passthrough", None) or []) if a != "--"]
    print()
    if entry.get("server") == "herd":
        code = _run_tests_herd(inst, pconf["root"], suite, tools, extra)
    else:
        code = _run_tests(inst, pconf["root"], suite, tools, extra)
    print()
    if code == 0:
        ok("tests passed")
    else:
        die(f"tests failed (phpunit exit {code})")

register({
    'xdebug': cmd_xdebug,
    'introspect': cmd_introspect,
    'test': cmd_test,
})

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

from sandbox.registry import COMMANDS



import sandbox.commands.lifecycle  # noqa: F401  (registers commands)
import sandbox.commands.instances_cmd  # noqa: F401  (registers commands)
import sandbox.commands.config_setup  # noqa: F401  (registers commands)
import sandbox.commands.data  # noqa: F401  (registers commands)
import sandbox.commands.wp  # noqa: F401  (registers commands)
import sandbox.commands.net  # noqa: F401  (registers commands)
import sandbox.commands.debug  # noqa: F401  (registers commands)
import sandbox.commands.abilities  # noqa: F401  (registers commands)
import sandbox.commands.jobs  # noqa: F401  (registers commands)
import sandbox.commands.skill  # noqa: F401  (registers commands)
import sandbox.commands.integ  # noqa: F401  (registers commands)
import sandbox.commands.ui_dash  # noqa: F401  (registers commands)
import sandbox.commands.cache  # noqa: F401  (registers commands)
import sandbox.commands.uninstall  # noqa: F401  (registers commands)



def main():
    p = argparse.ArgumentParser(
        prog="sandbox",
        description="WPDeveloper Sandbox — real WP runtime for designers, devs, QA.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Quick start:
  ./sb setup               boot docker, install WP, build MCP, register the
                           single `sandbox` MCP server with Claude

Per-project (each plugin carries its own sandbox.config.json):
  cd <plugin-repo>
  ./sb init                scaffold config + boot a per-dir instance + harness
  ./sb test                run the plugin's phpunit tests
  ./sb ensure              just boot/refresh this project's instance
  ./sb doctor              audit everything is healthy
""",
    )
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("setup", help="One-shot: boot, install WP, build MCP, wire Claude")
    sp.add_argument("--no-pick", action="store_true",
                    help="skip the post-setup plugin picker (CI / re-run)")
    sp.add_argument("--no-domain", action="store_true",
                    help="skip clean-URL proxy setup (stay on localhost:<port>)")
    sp.add_argument("--server", action="store_true",
                    help="headless/server mode: no proxy/Claude/browser; reach "
                         "it via an SSH tunnel to localhost")
    sp.add_argument("--no-instances", action="store_true",
                    help="don't boot/install any WordPress instance — just "
                         "prepare the CLI + MCP. Create sites later from the "
                         "web UI or with ./sb instance create <name>.")
    ap = sub.add_parser("apply",
        help="With --project-dir: reconcile a project instance in place "
             "(no data loss). Without: alias for setup — re-apply sandbox.yml")
    ap.add_argument("--no-pick", action="store_true")
    ap.add_argument("--project-dir", dest="project_dir", default=None,
        help="reconcile this project's running instance with its current "
             "config (constants/plugins/themes/multisite) without dropping the DB")
    ap.add_argument("--json", action="store_true",
        help="print the reconciled instance record as JSON (for the MCP server)")

    cn = sub.add_parser("connect",
        help="Save credentials for an integration (fb/fluentboards/gh/github)")
    cn.add_argument("target", nargs="?",
                    help="fb, fluentboards, gh, or github (omit to list)")
    cn.add_argument("-n", "--non-interactive", action="store_true",
                    help="read values from env vars instead of prompting "
                         "(FLUENTBOARDS_URL/EMAIL/APP_PASSWORD, GITHUB_ORG)")
    sub.add_parser("up", help="Boot the docker stack")
    sub.add_parser("down", help="Stop the stack")
    sub.add_parser("status", help="Show container + project status")
    sub.add_parser("logs", help="Tail WP + DB logs")
    sub.add_parser("shell", help="Bash into the WP container")
    sub.add_parser("install", help="Install WP + create admin user")

    w = sub.add_parser("wp", help="Run any wp-cli command")
    w.add_argument("--async", dest="run_async", action="store_true",
                   help="run as a background job (spec 004) — prints a job id")
    w.add_argument("passthrough", nargs=argparse.REMAINDER)

    s = sub.add_parser("seed", help="Import a WXR from runtime/seeds/")
    s.add_argument("file")

    jb = sub.add_parser("job", help="Inspect/kill a background wp job (spec 004)")
    jb.add_argument("job_id")
    jb.add_argument("--follow", action="store_true", help="stream output until done")
    jb.add_argument("--kill", action="store_true", help="terminate the job")
    jbs = sub.add_parser("jobs", help="List background wp jobs")
    jbs.add_argument("--prune", action="store_true", help="remove old job artifacts")

    dp = sub.add_parser("dump", help="Tail/clear the dump()/dd() log (spec 007)")
    dp.add_argument("--follow", action="store_true")
    dp.add_argument("--clear", action="store_true")
    qm = sub.add_parser("qm", help="Capture Query Monitor data for a URL (spec 007)")
    qm.add_argument("url", nargs="?", default="/")
    qm.add_argument("--clear", action="store_true")

    sk = sub.add_parser("skill", help="Author/list skills (spec 006)")
    sk.add_argument("action", choices=["list", "write", "edit", "delete", "show"])
    sk.add_argument("slug", nargs="?")
    sk.add_argument("--title")
    sk.add_argument("--desc")
    sk.add_argument("--scope", choices=["project", "personal", "sandbox"])
    sk.add_argument("--file", help="body file, or '-' for stdin")
    sk.add_argument("--on-conflict", dest="on_conflict", choices=["fail", "replace", "rename"])
    sk.add_argument("--enable", dest="enable", action="store_true", default=True)
    sk.add_argument("--disable", dest="enable", action="store_false")

    v = sub.add_parser("visit",
        help="Load a URL in headless Chromium and report DOM/console/iframes as JSON")
    v.add_argument("passthrough", nargs=argparse.REMAINDER,
        help="<url> [--check-iframes] [--screenshot PATH] [--full-page] "
             "[--width N] [--height N] [--timeout S] [--wait-until COND]")

    sub.add_parser("doctor", help="Audit the stack and report problems")
    sub.add_parser("smoke",  help="Self-test: boot a fresh instance, REST probe, tear down")
    sub.add_parser("selftest", help="Run the sandbox tooling's own unit tests (tests/)")

    f = sub.add_parser("focus",
        help="Set/show which plugin Claude defaults to working on")
    f.add_argument("slug", nargs="?")
    f.add_argument("--clear", action="store_true")
    f.add_argument("--here", action="store_true",
        help="Focus here WITHOUT clearing the same plugin's focus on other "
             "instances (override the one-plugin-one-instance invariant).")

    sub.add_parser("mcp-install", help="Install Python deps for wp-mcp")
    cl = sub.add_parser("claude",
        help="Launch `claude` with sandbox + focused plugin context guaranteed loaded")
    cl.add_argument("--write-config", action="store_true",
        help="Instead of launching, (re)write the project-local .mcp.json.")
    cl.add_argument("passthrough", nargs=argparse.REMAINDER,
        help="Extra args forwarded to the underlying `claude` invocation.")

    sn = sub.add_parser("snapshot", help="Save DB + uploads to runtime/snapshots/")
    sn.add_argument("name")
    sn.add_argument("--force", action="store_true", help="overwrite if exists")
    sn.add_argument("--db-only", dest="db_only", action="store_true",
                    help="capture only the DB (skip uploads) — spec 008")

    re_ = sub.add_parser("restore", help="Restore a saved snapshot")
    re_.add_argument("name")

    sub.add_parser("snapshots", help="List saved snapshots")

    rs = sub.add_parser("reset", help="Reset DB to the post-install @install baseline (spec 008)")
    rs.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    rs.add_argument("--rebaseline", action="store_true", help="re-capture the baseline from the current DB")
    sub.add_parser("update", help="git pull the project repo this instance tracks")

    op = sub.add_parser("open", help="Open admin / site / mailpit in browser")
    op.add_argument("what", nargs="?", default="admin",
                    choices=["admin", "site", "mail"])

    xd = sub.add_parser("xdebug", help="Toggle Xdebug in the WP container")
    xd.add_argument("state", choices=["on", "off", "status"])

    ab = sub.add_parser("abilities", help="Toggle the in-instance WP Abilities layer (spec 003)")
    ab.add_argument("state", choices=["on", "off", "status", "connect"])

    isp = sub.add_parser("introspect",
        help="Dump live block/widget/shortcode registries to runtime/cache/*.json")
    isp.add_argument("target", nargs="?", default="all",
                     choices=["blocks", "widgets", "shortcodes", "all"])

    c = sub.add_parser("clean", help="Stop + wipe DB volume")
    c.add_argument("--yes", action="store_true")

    sub.add_parser("instances",
        help="List defined sandbox instances + their status + ports")

    sub.add_parser("dashboard",
        help="Interactive TUI to view + manage all instances")
    sub.add_parser("ui", help="Alias for dashboard")

    web = sub.add_parser("web",
        help="Serve a local browser dashboard (localhost)")
    web.add_argument("--port", type=int, default=8765,
        help="port to listen on (default 8765)")
    web.add_argument("--exact-port", action="store_true",
        help="bind only --port (no scan); used by the snapshot-bridge auto-start")
    web.add_argument("--open", action="store_true",
        help="open the dashboard in your browser on start")

    sub.add_parser("mcp",
        help="Run the MCP server over stdio (register: claude mcp add --scope user sandbox -- ./sb mcp)")

    ts = sub.add_parser("test",
        help="Run the plugin's phpunit tests (externally-provisioned WP harness)")
    ts.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)")
    ts.add_argument("--provision-only", dest="provision_only", action="store_true",
        help="set up the harness but don't run phpunit")
    ts.add_argument("passthrough", nargs=argparse.REMAINDER,
        help="args after `--` are passed to phpunit (e.g. --filter foo)")

    en = sub.add_parser("ensure",
        help="Boot the instance for a project dir (create-if-missing); per-project / MCP-first")
    en.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)")
    en.add_argument("--json", action="store_true",
        help="print the instance record as JSON (for the MCP server)")

    ini = sub.add_parser("init",
        help="Make a plugin dir a sandbox project (config + instance + test harness)")
    ini.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)")
    ini.add_argument("--force", action="store_true",
        help="regenerate sandbox.config.json even if one already exists")
    ini.add_argument("--no-test-harness", dest="no_test_harness", action="store_true",
        help="skip provisioning the phpunit test harness")

    ins = sub.add_parser("instance",
        help="Delete a sandbox instance (create via `./sb init` in a plugin dir)")
    ins.add_argument("action", choices=["delete"])
    ins.add_argument("name")
    ins.add_argument("--yes", action="store_true",
        help="skip the confirmation prompt on delete")

    sec = sub.add_parser("secure",
        help="Upgrade an instance to trusted https://<name>.tst (opt-in)")
    sec.add_argument("name", nargs="?",
        help="instance to secure (default: the targeted/main instance)")

    srv = sub.add_parser("server",
        help="Switch an instance's web server in place (apache|nginx|litespeed)")
    srv.add_argument("name", nargs="?",
        help="instance to switch (default: the targeted/main instance)")
    srv.add_argument("server_type", choices=list(SERVERS),
        help="web server to switch to")

    ob = sub.add_parser("onboard",
        help="Guided setup for an existing instance (plugins, https, focus)")
    ob.add_argument("--plugin", dest="plugins", action="append", default=None,
        help="wp.org plugin slug to install (repeatable)")
    ob.add_argument("--seed", default=None, help="WXR file in runtime/seeds/")
    ob.add_argument("--theme", default=None, help="theme slug to activate")
    ob.add_argument("--wp-debug", dest="wp_debug", action="store_true",
        help="enable WP_DEBUG")
    ob.add_argument("--minimal", action="store_true",
        help="skip prompts (non-interactive)")
    ob.add_argument("--site-title", dest="site_title", default=None)

    gl = sub.add_parser("global",
        help="Install a global `sb` command so you can run it from any folder")
    gl.add_argument("--remove", action="store_true",
        help="remove the global `sb` symlink instead of installing it")

    un = sub.add_parser("uninstall",
        help="Remove the whole sandbox (containers, volumes, proxy, MCP)")
    un.add_argument("--yes", action="store_true", help="skip confirmation")
    un.add_argument("--purge", action="store_true",
        help="also remove the install directory")

    dm = sub.add_parser("domains",
        help="Manage custom domains + HTTPS proxy (setup|up|down|teardown|list)")
    dm.add_argument("action", nargs="?",
        choices=["setup", "up", "down", "teardown", "repair-ca", "list"],
        default="list")
    dm.add_argument("tld", nargs="?",
        help="Local TLD for clean URLs (e.g. tst). On `setup`, prompted if omitted.")

    ca = sub.add_parser("cache",
        help="Inspect or clear the shared plugin/theme/core download cache")
    ca.add_argument("action", nargs="?", choices=["info", "clear"], default="info",
        help="info (default) shows size/counts; clear empties it")
    ca.add_argument("layer", nargs="?", choices=["wp-cli", "wp-http"],
        help="limit `clear` to one layer (default: both)")
    ca.add_argument("--yes", action="store_true",
        help="skip the confirmation prompt on `clear`")

    # Global --instance flag accepted both BEFORE and AFTER the subcommand.
    # argparse can't natively share a global flag with all subparsers, so
    # we register it on the top-level parser AND inject the same flag onto
    # every subparser. Default is None so we can distinguish "user chose an
    # instance" from "user didn't say"; resolved below.
    p.add_argument("--instance", default=None,
        help="Which sandbox instance to target (default: $SANDBOX_INSTANCE, "
             "else the instance registered for the current project dir; "
             "errors if neither resolves)")
    for sp_name, sp_parser in (sub.choices or {}).items():
        # Don't double-add when a subparser already has --instance
        # (none currently do; future-proof).
        if not any(a.option_strings == ["--instance"]
                   for a in sp_parser._actions):
            # default=SUPPRESS so that when --instance is NOT passed after the
            # subcommand, the subparser doesn't clobber a value the top-level
            # parser already set from a BEFORE-subcommand --instance. Without
            # this, `./sb --instance xx focus` silently resolved to main.
            sp_parser.add_argument("--instance", default=argparse.SUPPRESS)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    cfg = load_config()

    # Resolve which instance this invocation targets. Precedence:
    #   1. explicit --instance   2. $SANDBOX_INSTANCE
    #   3. the registry instance for the cwd's project   4. error
    # There is no implicit/global instance: `sb <cmd>` in a project dir targets
    # THAT project's instance; run outside any registered project (and not
    # project-routed) it aborts with guidance rather than targeting a fallback.
    instances = resolve_instances(cfg)
    explicit = getattr(args, "instance", None) or os.environ.get("SANDBOX_INSTANCE")
    chosen = explicit or _cwd_instance()
    # Project-dir-routed commands derive their instance from the project root
    # (registry / ensure_instance), not this global gate.
    PROJECT_ROUTED = {"init", "ensure", "test", "mcp", "smoke"}
    # `apply --project-dir` is project-routed (reconcile); bare `apply` is the
    # sandbox.yml setup alias.
    if args.cmd == "apply" and getattr(args, "project_dir", None):
        PROJECT_ROUTED = PROJECT_ROUTED | {"apply"}
    # Instance-scoped commands operate on ONE instance and require resolution.
    # Everything else is registry-wide/global (instances, dashboard, web, setup,
    # global, uninstall, domains, …) or takes its own name/project arg, so an
    # unresolved instance is fine for them.
    INSTANCE_SCOPED = {
        "up", "down", "status", "logs", "shell", "install", "wp", "seed", "visit",
        "doctor", "clean", "snapshot", "restore", "snapshots", "update", "open",
        "xdebug", "introspect", "secure", "server", "focus", "claude", "onboard",
        "abilities", "job", "jobs", "dump", "qm", "reset",
    }
    if chosen is None:
        if args.cmd in INSTANCE_SCOPED:
            _known = ", ".join(sorted(instances))
            die("no sandbox instance for this directory. cd into a registered "
                "project, or run `sb init` / `sb ensure` to create one."
                + (f"\nKnown instances: {_known}" if _known else ""))
    elif chosen not in instances and args.cmd not in PROJECT_ROUTED:
        die(f"unknown instance '{chosen}'. "
            f"Known: {', '.join(sorted(instances)) or '(none)'}.")
    args.resolved_instance = chosen

    # (Re)generate per-instance compose files so they're always in sync with the
    # registered instances. Cheap + idempotent.
    write_compose_files(cfg)
    # Keep the legacy `.env` populated so anyone still invoking the
    # checked-in docker-compose.yml directly (out-of-tree scripts, older
    # skills) doesn't break. New flow ignores this file.
    write_env_for_compose(cfg)

    # Dispatch via the command registry (populated by sandbox.commands.* imports).
    # `apply --project-dir` → in-place reconcile; bare `apply` → setup alias.
    handler = (COMMANDS["setup"] if (args.cmd == "apply"
                                     and not getattr(args, "project_dir", None))
               else COMMANDS[args.cmd])
    handler(cfg, args)



if __name__ == "__main__":

    main()

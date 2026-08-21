from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import COMMANDS, COMMAND_SPECS, compose_missing_parsers
from sandbox.application.context import preflight_instance_capability
from sandbox.commands.manifest import load_builtin_commands
from sandbox.transports.remote_jobs import RemoteJobAdmissionError



load_builtin_commands()


# Capability gates for historical handlers whose parsers remain in the
# compatibility bridge. New feature-owned commands should put equivalent
# metadata on their CommandSpec instead of extending this table.
CLI_CAPABILITIES = {
    "install": "wordpress.cli", "shell": "wordpress.exec",
    "doctor": "wordpress.cli", "server": "wordpress.cli",
    "xdebug": "wordpress.exec", "introspect": "wordpress.cli",
    "wp": "wordpress.cli", "seed": "wordpress.cli", "visit": "wordpress.rest",
    "snapshot": "wordpress.snapshot", "restore": "wordpress.restore",
    "reset": "wordpress.reset", "clean": "wordpress.cli",
    "dump": "wordpress.cli", "qm": "wordpress.cli",
    "abilities": "wordpress.abilities", "onboard": "wordpress.cli",
}


class _KVAction(argparse.Action):
    """argparse action for repeatable `--flag k=v` pairs, collected into a dict
    (used by `./sb ci run --matrix-filter php=8.1 --matrix-filter wp=6.4`)."""
    def __call__(self, parser, namespace, values, option_string=None):
        d = getattr(namespace, self.dest, None) or {}
        if "=" not in values:
            parser.error(f"{option_string} expects key=value, got {values!r}")
        k, v = values.split("=", 1)
        d[k] = v
        setattr(namespace, self.dest, d)




def _implied_project_dir(instance: str | None, label: str | None):
    """The project root a bare `apply` clearly meant, and where it came from.

    A named instance is the strongest signal — the registry knows which project
    owns it. Otherwise, standing inside a registered project means that project.
    Returns (root, source) or (None, None) when neither applies, which keeps the
    historical whole-sandbox behaviour for `./sb apply` run outside any project.
    """
    sc = _core()
    if instance:
        entry = sc.registry_find_instance(instance) or {}
        root = entry.get("root")
        if root and Path(root).is_dir():
            return str(root), f"registered root of instance '{instance}'"
        return None, None
    try:
        root = sc.find_project_root(Path.cwd())
    except Exception:
        return None, None
    if root and sc.registry_get(str(root), label=label):
        return str(root), "current working directory"
    return None, None


def _global_label_before_subcommand(argv: list[str]) -> str | None:
    """Recover a global ``--label`` that argparse subparser defaults may hide.

    A few historical subparsers own a semantic label option.  For the common
    instance-routing commands the top-level spelling must remain valid before
    the command as well as after it; parse the unambiguous pre-command token
    here and restore it after argparse has composed the namespace.
    """
    command_names = set(COMMANDS) | {"apply"}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token == "--label" and index + 1 < len(argv):
            # The next token is the option value, even when it happens to
            # share a command name (e.g. ``--label status ensure``).
            return argv[index + 1]
        if token.startswith("--label="):
            return token.split("=", 1)[1]
        if token in command_names:
            break
        index += 1
    return None


def _dispatch_remote_admission_error(exc: RemoteJobAdmissionError, args) -> None:
    """Render the transport's bounded admission envelope at the CLI edge."""
    payload = exc.to_payload()
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        recovery = payload.get("recovery") or {}
        guidance = recovery.get("guidance")
        suffix = f" {guidance}" if isinstance(guidance, str) and guidance else ""
        target = payload.get("target")
        remote = target.get("remote") if isinstance(target, dict) else None
        if isinstance(remote, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", remote):
            suffix += f" Recovery: run ./sb remote docker-pool {remote} --json."
        print(
            f"error: {payload['error']} ({payload['code']})."
            f"{suffix}",
            file=sys.stderr,
        )
    raise SystemExit(1)


def _explicit_global_label(argv: list[str]) -> str | None:
    """Return a parser-level label supplied before passthrough arguments.

    ``argparse.REMAINDER`` commands (notably ``wp`` and ``test``) may carry a
    child command's own ``--label`` after ``--``.  Stop at that delimiter so
    child arguments cannot be mistaken for Sandbox instance-selection intent.
    Semantic labels owned by ``domains``/``native``/``vrdiff`` are filtered by
    the caller; this helper only answers whether the global spelling appeared.
    """
    for index, token in enumerate(argv):
        if token == "--":
            break
        if token == "--label" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--label="):
            return token.split("=", 1)[1]
    return None


def _explicit_global_option(argv: list[str], option: str) -> bool:
    """Return whether a top-level selector option appeared in ``argv``.

    Setup has no passthrough payload, but keeping the ``--`` boundary here
    makes this helper safe to reuse for commands whose child arguments may
    contain a similarly named option.
    """
    prefix = f"{option}="
    for token in argv:
        if token == "--":
            break
        if token == option or token.startswith(prefix):
            return True
    return False


def _project_observation_route(args) -> tuple[bool, bool]:
    """Classify the two explicit project-root lifecycle observation routes.

    A remote observation is an outer controller request: the named remote owns
    target resolution and the staged checkout is only an input to that control
    plane.  A local observation is the inverse: the co-located CLI owns the
    instance lookup, but the explicit staged root (not the controller cwd) is
    authoritative.  Keeping this classification in one small seam prevents
    the ordinary instance gate from accidentally handling either form.
    """
    observation = (
        getattr(args, "cmd", None) in {"status", "logs"}
        and bool(getattr(args, "project_dir", None))
    )
    return (
        observation and bool(getattr(args, "remote", None))
        and not bool(getattr(args, "local", False)),
        observation and bool(getattr(args, "local", False))
        and not bool(getattr(args, "remote", None)),
    )


def _wp_timeout(value: str) -> int:
    """Parse the bounded synchronous ``sb wp`` timeout."""
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            "--timeout must be an integer from 1 to 3600 seconds"
        )
    if not 1 <= timeout <= 3600:
        raise argparse.ArgumentTypeError(
            "--timeout must be an integer from 1 to 3600 seconds"
        )
    return timeout


def main(*, invocation_started_monotonic: float | None = None):
    if invocation_started_monotonic is None:
        invocation_started_monotonic = time.monotonic()
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
    ap.add_argument("--label", default=argparse.SUPPRESS,
        help="which of --project-dir's instances to reconcile, when it owns "
             "more than one (multi-instance-per-root); default: the sole/"
             "default instance")

    cn = sub.add_parser("connect",
        help="Save credentials for an integration (fb/fluentboards/gh/github)")
    cn.add_argument("target", nargs="?",
                    help="fb, fluentboards, gh, or github (omit to list)")
    cn.add_argument("-n", "--non-interactive", action="store_true",
                    help="read values from env vars instead of prompting "
                         "(FLUENTBOARDS_URL/EMAIL/APP_PASSWORD, GITHUB_ORG)")
    # Lifecycle owns these parsers and handlers.  Keeping registration beside
    # the implementation prevents new lifecycle flags from enlarging this
    # compatibility composition root.
    from sandbox.commands.lifecycle import configure_parser as configure_lifecycle_parser
    configure_lifecycle_parser(sub)

    w = sub.add_parser("wp", help="Run any wp-cli command")
    wp_options = w.add_mutually_exclusive_group()
    wp_options.add_argument("--async", dest="run_async", action="store_true",
                            help="run as a background job (spec 004) — prints a job id")
    wp_options.add_argument("--timeout", type=_wp_timeout, default=60,
                            help="synchronous wait bound in seconds (default: 60; 1-3600)")
    w.add_argument("passthrough", nargs=argparse.REMAINDER)

    s = sub.add_parser("seed", help="Import a WXR from runtime/seeds/")
    s.add_argument("file")

    jb = sub.add_parser("job", help="Inspect/kill a background wp job (spec 004)")
    jb.add_argument("job_id")
    jb.add_argument("--follow", action="store_true", help="stream output until done")
    jb.add_argument("--kill", action="store_true", help="terminate the job")
    jbs = sub.add_parser("jobs", help="List background wp jobs")
    jbs.add_argument("--prune", action="store_true", help="remove old job artifacts")

    aj = sub.add_parser("async-job",
        help="Poll/follow/kill a background e2e/ci run started with --async "
             "(NOT instance-scoped, unlike `job`/`jobs`)")
    aj.add_argument("job_id")
    aj.add_argument("--follow", action="store_true", help="stream output until done")
    aj.add_argument("--kill", action="store_true", help="terminate the job")
    aj.add_argument("--offset", type=int, default=0,
        help="byte offset for incremental output (default: 0, full output so far)")
    aj.add_argument("--json", action="store_true", help="print status as JSON")

    dp = sub.add_parser("dump", help="Tail/clear the dump()/dd() log (spec 007)")
    dp.add_argument("--follow", action="store_true")
    dp.add_argument("--clear", action="store_true")
    qm = sub.add_parser("qm", help="Capture Query Monitor data for a URL; `off` deactivates it (spec 007)")
    qm.add_argument("url", nargs="?", default="/")
    qm.add_argument("--clear", action="store_true")
    qm.add_argument("--collectors", help="comma-separated Query Monitor collector ids to return")

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

    mg = sub.add_parser("migrate",
        help="Relocate all machine-state under the per-user base $SANDBOX_HOME (spec 009)")
    mg_mode = mg.add_mutually_exclusive_group()
    mg_mode.add_argument("--apply", action="store_true",
        help="Perform the migration (default is a dry-run plan)")
    mg_mode.add_argument("--dry-run", action="store_true",
        help="Print the migration plan without changing state (the default)")
    mg.add_argument("--force", action="store_true",
        help="Re-verify/regenerate an already migrated base; never merges conflicts")
    mg.add_argument("--finalize", action="store_true",
        help=argparse.SUPPRESS)  # internal: post-move re-exec pass
    hm = sub.add_parser("home",
        help="Show the $SANDBOX_HOME base, or relocate it: `./sb home <dir>`")
    hm.add_argument("dir", nargs="?", help="new base directory to relocate to")


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
    re_.add_argument("--yes", action="store_true",
                     help="confirm dropping the current DB before restore")

    sub.add_parser("snapshots", help="List saved snapshots")

    rs = sub.add_parser("reset", help="Reset DB to the post-install @install baseline (spec 008)")
    rs.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    rs.add_argument("--rebaseline", action="store_true", help="re-capture the baseline from the current DB")
    xd = sub.add_parser("xdebug", help="Toggle Docker Xdebug or report Herd host status")
    xd.add_argument("state", choices=["on", "off", "status"])

    ab = sub.add_parser("abilities", help="Toggle the in-instance WP Abilities layer (spec 003)")
    ab.add_argument("state", choices=["on", "off", "status", "connect"])

    isp = sub.add_parser("introspect",
        help="Dump live block/widget/shortcode registries to runtime/cache/*.json")
    isp.add_argument("target", nargs="?", default="all",
                     choices=["blocks", "widgets", "shortcodes", "all"])

    c = sub.add_parser("clean", help="Stop + wipe DB volume")
    c.add_argument("--yes", action="store_true")

    isc = sub.add_parser("instances",
        help="List defined sandbox instances + their status + ports")
    isc.add_argument("--project-dir", dest="project_dir", default=None,
        help="filter to one project root's instance(s) — useful once a root "
             "owns more than one labelled instance")
    isc.add_argument("--json", action="store_true", help="print the instance inventory as JSON")

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

    mcp_p = sub.add_parser("mcp",
        help="Run the MCP server over stdio (register: claude mcp add --scope user sandbox -- ./sb mcp)")
    mcp_p.add_argument("--project-dir", default=None,
        help="scope the catalog to this project's runtime (hides irrelevant tools)")
    mcp_p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio",
        help="stdio (default, local use) or streamable-http (spec 014 remote "
             "hosting -- started by `./sb remote provision` on the VPS, never "
             "invoked directly for local use)")
    mcp_p.add_argument("--bind", default=None,
        help="(--transport=streamable-http only) address to bind, e.g. a "
             "Tailscale interface IP or 127.0.0.1 behind HTTPS — NEVER "
             "0.0.0.0 (spec FR-014)")
    mcp_p.add_argument("--port", type=int, default=None,
        help="(--transport=streamable-http only) port to bind")
    mcp_p.add_argument("--token", default=None,
        help="(--transport=streamable-http only) bearer token required on every "
             "request — minted by `./sb remote provision`, never echoed elsewhere")
    mcp_p.add_argument("--public-url", default=None,
        help="(--transport=streamable-http only) public HTTPS URL when the "
             "server is behind a reverse proxy")

    ts = sub.add_parser("test",
        help="Run plugin unit or integration tests")
    ts.add_argument("mode", nargs="?",
        help="declared Compose mode, or WordPress auto/unit/integration/matrix")
    ts.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)")
    ts.add_argument("--label", default=argparse.SUPPRESS,
        help="which of --project-dir's instances to test, when it owns more "
             "than one (multi-instance-per-root, e.g. a CI matrix cell); "
             "default: the sole/default instance")
    ts.add_argument("--provision-only", dest="provision_only", action="store_true",
        help="set up the harness but don't run phpunit")
    test_target = ts.add_mutually_exclusive_group()
    test_target.add_argument("--local", action="store_true", help="run a durable local matrix")
    test_target.add_argument("--remote", help="run a durable remote matrix")
    ts.add_argument("--workspace", action="append", default=None,
        help="isolated matrix workspace label (repeat to fan out remote unit/integration tests)")
    ts.add_argument("--timeout", type=int, default=None,
        help="durable matrix/declared-plan deadline (overrides the plan profile)")
    ts.add_argument("--output-profile", default=None,
        help="durable matrix/declared-plan output profile (overrides the plan profile)")
    ts.add_argument("--json", action="store_true",
        help="print the durable remote submission result as JSON")
    ts.add_argument("passthrough", nargs=argparse.REMAINDER,
        help="args after `--` are passed to phpunit (e.g. --filter foo)")

    e2 = sub.add_parser("e2e",
        help="Run Playwright e2e tests with N workers, each against its OWN "
             "fresh WordPress instance (multi-instance-per-root)")
    e2.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)")
    e2.add_argument("--workers", type=int, default=2,
        help="number of parallel workers / fresh instances (default: 2)")
    e2.add_argument("--concurrency", type=int, default=None,
        help="cap on simultaneously-booting instances (default: auto, see "
             "docs/ci-e2e-runner-spec.md §2.4)")
    e2.add_argument("--playwright-config", dest="playwright_config", default=None,
        help="path to the project's playwright config, relative to --project-dir "
             "(default: auto-discovered at root/, tests/, test/, e2e/, tests/e2e/)")
    e2.add_argument("--grep", default=None, help="passed to `playwright test --grep`")
    e2.add_argument("--keep-on-fail", dest="keep_on_fail", action="store_true",
        help="preserve a failed worker's instance for inspection instead of tearing it down")
    e2.add_argument("--strict-provision", dest="strict_provision", action="store_true",
        help="abort the whole run if any worker's instance fails to boot "
             "(default: continue with the healthy workers)")
    e2_target = e2.add_mutually_exclusive_group()
    e2_target.add_argument("--local", action="store_true", help="run the E2E coordinator locally")
    e2_target.add_argument("--remote", help="run the detached E2E coordinator on a named remote")
    e2.add_argument("--workspace", default=None,
        help="logical remote workspace label; each E2E worker gets an isolated leaf")
    e2.add_argument("--timeout", type=int, default=None,
        help="per-worker playwright timeout in seconds (default: 900)")
    e2.add_argument("--shard-index", type=int, default=None, help=argparse.SUPPRESS)
    e2.add_argument("--shard-total", type=int, default=None, help=argparse.SUPPRESS)
    e2.add_argument("--json", action="store_true",
        help="print the aggregated result as JSON (for the MCP server)")
    e2.add_argument("--async", dest="run_async", action="store_true",
        help="run detached; prints {job_id} immediately — poll with "
             "`./sb async-job <job_id>`")
    e2.add_argument("passthrough", nargs=argparse.REMAINDER,
        help="args after `--` are passed to `playwright test`")

    ci_p = sub.add_parser("ci",
        help="Interpret + run a bounded subset of a GitHub Actions workflow "
             "locally against sandbox instances (see docs/ci-e2e-runner-spec.md)")
    ci_p.add_argument("action", choices=["plan", "preflight", "run"],
        help="'plan': parse + classify only, execute nothing (always safe). "
             "'run': actually execute (run: steps for real; deploy-class "
             "steps skipped by default)")
    ci_p.add_argument("workflow", help="path to a .github/workflows/*.yml file")
    ci_p.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory for 'run' (default: current directory)")
    ci_p.add_argument("--job", dest="jobs", action="append", default=None,
        help="run only this job id (repeatable); default: all jobs in the file")
    ci_p.add_argument("--matrix-filter", dest="matrix_filter", action=_KVAction,
        default=None, metavar="K=V",
        help="run only matrix cells matching key=value (repeatable)")
    ci_p.add_argument("--if-event", dest="if_event", default=None,
        help="only run if the workflow's `on:` triggers mention this event "
             "(e.g. push, pull_request); otherwise print 'nothing to run' and exit 0")
    ci_p.add_argument("--label-prefix", dest="label_prefix", default=None,
        help="prefix for the ephemeral per-cell instance labels (default: 'ci')")
    ci_p.add_argument("--concurrency", type=int, default=None,
        help="cap on simultaneously-booting instances (default: auto)")
    ci_p.add_argument("--allow-deploy", dest="allow_deploy", action="store_true",
        help="actually attempt deploy-class steps instead of skipping them "
             "(still requires every referenced secret to resolve; see §3.6)")
    ci_p.add_argument("--list-secrets", dest="list_secrets", action="store_true",
        help="print the secrets this workflow references and exit — no execution")
    ci_p.add_argument("--keep-on-fail", dest="keep_on_fail", action="store_true",
        help="preserve a failed cell's instance for inspection instead of tearing it down")
    ci_p.add_argument("--strict-provision", dest="strict_provision", action="store_true",
        help="abort the whole run if any cell's instance fails to boot")
    ci_p.add_argument("--dry-run", dest="dry_run", action="store_true",
        help="same as `ci plan` even when action=run — parse + classify only")
    ci_p.add_argument("--timeout", type=int, default=None,
        help="per-step timeout in seconds (default: 900)")
    ci_p.add_argument("--output-profile", default=None,
        help="durable retained-output presentation profile for remote jobs")
    ci_p.add_argument("--json", action="store_true",
        help="print the plan/result as JSON (for the MCP server)")
    ci_p.add_argument("--async", dest="run_async", action="store_true",
        help="(action=run only) run detached; prints {job_id} immediately — "
        "poll with `./sb async-job <job_id>`")
    ci_target = ci_p.add_mutually_exclusive_group()
    ci_target.add_argument("--local", action="store_true", help="force local act execution")
    ci_target.add_argument("--remote", default=None, help="provisioned remote for durable CI control")
    ci_p.add_argument("--workspace", default="ci", help="named reusable CI workspace")
    ci_p.add_argument("--accept-difference", dest="accepted_differences", action="append", default=None,
        help="named act compatibility difference accepted for this preflight (repeatable)")

    pcheck = sub.add_parser("plugin-check",
        help="Run WordPress.org's Plugin Check, gated by a committed baseline "
             "(see docs/plugin-check.md, specs/013-plugin-check/)")
    pcheck.add_argument("--project-dir", dest="project_dir", default=None,
        help="project to check (default: current directory)")
    pcheck.add_argument("--update", action="store_true",
        help="rewrite the baseline to match current findings exactly, "
             "instead of gating against it")
    pcheck.add_argument("--json", action="store_true",
        help="print the result as JSON (for the MCP server)")

    zp = sub.add_parser("zip",
        help="Build a distributable plugin zip from .distignore, with guards and a "
             "git build stamp — a wp dist-archive alternative (see docs/plugin-zip.md)")
    zp.add_argument("--project-dir", dest="project_dir", default=None,
        help="project to package (default: current directory)")
    zp.add_argument("--dev", action="store_true",
        help="keep the files .distignore marks development-only (source maps, dev tooling)")
    zp.add_argument("--clean", action="store_true",
        help="ship the declared version verbatim: no branch tag, no build number")
    zp.add_argument("--hash", action="store_true",
        help="append the short commit sha to the stamped version too")
    zp.add_argument("--out", dest="out", default=None,
        help="output directory (default: the dir shared by every worktree of this repo)")
    zp.add_argument("--json", action="store_true",
        help="print the result as JSON (for the MCP server)")

    remote_p = sub.add_parser("remote",
        help="Register/provision/manage remote VPS targets for sandbox instances "
             "(see docs/remote-hosting.md, specs/014-remote-vps-hosting/)")
    remote_p.add_argument("action", choices=["add", "list", "provision", "up", "down", "remove", "set-origin", "service", "docker-pool", "domains", "plugins"],
        help="add: register a VPS; list: show configured remotes + reachability; "
             "provision: install everything needed on a registered remote (idempotent); "
             "plugins: mirror the local pro-plugin store to the host so every remote "
             "instance offers those slugs on its wp-admin On-Demand page; "
             "up/down: start/stop the remote MCP server; docker-pool: plan/apply "
             "the fixed /24 daemon address pools; domains: list configured "
             "instance and hosted-route domains; remove: forget a remote "
             "locally (never touches the VPS itself)")
    remote_p.add_argument("name", nargs="?", default=None,
        help="remote name (required for every action except 'list')")
    remote_p.add_argument("ssh_url", nargs="?", default=None,
        help="SSH connection string (required for 'add')")
    remote_p.add_argument("--control", choices=["https", "tailscale"], default=None,
        help="control-plane transport for 'provision'/'up' (default: ask in "
             "interactive use, HTTPS in --json/non-interactive mode)")
    remote_p.add_argument("--control-host", default=None,
        help="public hostname for HTTPS control, e.g. sandbox-control.example.com")
    remote_p.add_argument("--ipv4", default=None, help="public IPv4 address for hosted DNS records")
    remote_p.add_argument("--ipv6", default=None, help="public IPv6 address for hosted DNS records")
    remote_p.add_argument("--yes", action="store_true",
        help="accept the default HTTPS control-plane choice without prompting")
    remote_p.add_argument("--plan", action="store_true",
        help="for `remote service migrate`: show the no-write service migration plan")
    remote_p.add_argument("--confirm", action="store_true",
        help="allow a protected remote service or Docker-pool mutation")
    remote_p.add_argument("--recover-interrupted", action="store_true",
        help="for `remote docker-pool`: plan/recover only containers proven to have stopped during the latest interrupted transaction")
    remote_p.add_argument("--expected-running", type=int, default=None,
        help="required interrupted-recovery assertion: exact pre-transaction running-container count")
    remote_p.add_argument("--expected-removed", type=int, default=0,
        help="interrupted-recovery assertion: baseline containers no longer present in Docker inventory")
    remote_p.add_argument("--recovery-since", default=None,
        help="required UTC transaction-start assertion when no daemon backup exists (YYYY-MM-DDTHH:MM:SSZ)")
    remote_p.add_argument("--force", action="store_true",
        help="for `remote plugins`: re-push the pro-plugin store even when its "
             "content fingerprint is unchanged since the last push")
    remote_p.add_argument("--dry-run", dest="dry_run", action="store_true",
        help="for `remote plugins`: report what would be mirrored, transfer nothing")
    remote_p.add_argument("--json", action="store_true",
        help="print the result as JSON (for the MCP server)")

    deploy_p = sub.add_parser("deploy",
        help="Deploy local project state (committed + uncommitted) to a remote "
             "target on demand — one-way, no continuous sync (see docs/remote-hosting.md)")
    deploy_p.add_argument("--project-dir", dest="project_dir", default=None,
        help="project to deploy (default: current directory)")
    deploy_p.add_argument("--remote", dest="remote", required=True,
        help="which registered, provisioned remote to deploy to")
    deploy_p.add_argument("--source-ref", dest="source_ref", default=None,
        help="immutable commit SHA or named ref to deploy; resolves before any remote mutation")
    deploy_p.add_argument("--ensure", action="store_true",
        help="after deploying, boot/refresh the remote project instance")
    deploy_p.add_argument("--expose", action="store_true",
        help="after ensuring, expose the remote instance through public HTTPS")
    deploy_p.add_argument("--domain", default=None,
        help="public hostname for --expose; default is "
             "default-<project-slug>.sandbox.asb.bd")
    deploy_p.add_argument("--alias", action="append", default=None,
        metavar="HOSTNAME",
        help="extra hostname the exposed instance also answers on (repeatable); "
             "defaults to the project's sandbox.config.json `aliases`")
    deploy_p.add_argument("--prune-routes", action="store_true",
        help="with --expose, delete remote routes pointing at this instance's "
             "port that are not the current domain or an alias")
    deploy_p.add_argument("--no-pro-plugins", dest="pro_plugins", action="store_false",
        default=True,
        help="skip the automatic pro-plugin store mirror (see `./sb remote plugins`)")
    deploy_p.add_argument("--plugin-slug", default=None,
        help="WordPress-only plugin slug to activate after --ensure; defaults to project slug")
    deploy_p.add_argument("--json", action="store_true",
        help="print the result as JSON (for the MCP server)")

    host_p = sub.add_parser("host", help="Validate, plan, apply, read logs, or issue a one-time hosting login URL")
    host_p.add_argument("action", choices=["validate", "plan", "apply", "logs", "secrets", "login-url"])
    host_p.add_argument("--project-dir", dest="project_dir", default=None,
        help="project containing sandbox.hosting.yml (default: current directory)")
    host_p.add_argument("--environment", default=None, help="manifest environment name")
    host_p.add_argument("--remote", default=None, help="registered remote for plan/apply")
    host_p.add_argument("--confirm", action="store_true", help="allow the protected apply action")
    host_p.add_argument("--allow-zone-ssl-change", action="store_true",
        help="acknowledge a zone-wide Cloudflare SSL mode change")
    host_p.add_argument("--set", dest="set_secret", default=None, metavar="SECRET_KEY",
        help="set one declared hosting secret through a hidden prompt")
    host_p.add_argument("--generate", dest="generate_secrets", action="store_true",
        help="generate any declared generated secrets that are missing")
    host_p.add_argument("--ttl-seconds", type=int, default=None,
        help="one-time login URL lifetime (60-3600 seconds; manifest default when omitted)")
    host_p.add_argument("--lines", type=int, default=200,
        help="bounded number of recent hosted-service log lines (1-1000)")
    host_p.add_argument("--json", action="store_true", help="print JSON")


    preview_p = sub.add_parser("preview", help="Create and remove disposable public remote Sandbox instances")
    preview_p.add_argument("action", choices=["create", "list", "destroy", "cleanup"])
    preview_p.add_argument("--remote", default=None, help="registered provisioned remote")
    preview_p.add_argument("--project-dir", default=None, help="project to deploy (default: current directory)")
    preview_p.add_argument("--name", default=None, help="optional stable preview name")
    preview_p.add_argument("--id", default=None, help="preview id for destroy")
    preview_p.add_argument("--base-domain", default="sandbox.asb.bd", help="Cloudflare-managed preview domain suffix")
    preview_p.add_argument("--ttl-hours", type=int, default=24, help="expiry for a created preview (default: 24)")
    preview_p.add_argument("--confirm", action="store_true", help="allow remote, DNS, and container mutation")
    preview_p.add_argument("--json", action="store_true", help="print JSON")

    hermes_p = sub.add_parser("hermes", help="Install and operate Hermes Agent on a configured remote")
    hermes_p.add_argument("action", choices=["install", "setup", "doctor", "status", "chat", "run", "job", "cron", "repo", "gateway", "worktree", "update", "backup", "cleanup", "policy", "health", "acceptance", "dashboard", "dashboard-ui", "state", "drive", "authorization"],
        help="core action, or repo/gateway/dashboard subcommand group")
    hermes_p.add_argument("subaction", nargs="?", default=None,
        help="repo: auth|clone|list|sync; job: status|kill; cron: list|output|validate|create|route|run|catalog|reconcile|verify; gateway: setup|install|start|stop|restart|status|logs|converge; worktree: list|inspect|preserve; update: plan|provenance|apply; backup: create|list|restore; policy: show|set; acceptance: v2; dashboard-ui: install|status|upgrade|uninstall|catalog; state: setup|sync|restore; drive: setup|backup|list|restore; authorization: sync|list|show|request|approve")
    hermes_p.add_argument("target", nargs="?", default=None,
        help="repo auth provider, or an optional subcommand target")
    hermes_p.add_argument("--remote", required=True, help="configured remote name")
    hermes_p.add_argument("--version", default=None, help="immutable Hermes release tag")
    hermes_p.add_argument("--commit", default=None, help="full commit expected for --version")
    hermes_p.add_argument("--repo", default=None, help="managed repository name")
    hermes_p.add_argument("--url", default=None, help="repository URL for `hermes repo clone`")
    hermes_p.add_argument("--state-repo", default=None, help="private GitHub state repository URL for `hermes state setup`")
    hermes_p.add_argument("--drive-destination", default=None, help="rclone Drive destination for `hermes drive setup`, e.g. gdrive:hermes-backups")
    hermes_p.add_argument("--passphrase-stdin", action="store_true", help="read a Drive recovery passphrase from standard input")
    hermes_p.add_argument("--token-stdin", action="store_true",
        help="read a fine-grained GitHub repository token from stdin for `repo auth github`")
    hermes_p.add_argument("--name", default=None, help="managed repository name for clone")
    hermes_p.add_argument("--job", default=None, help="catalog-managed Hermes job name for authorization")
    hermes_p.add_argument("--ref", default=None, help="branch, tag, or ref to clone")
    hermes_p.add_argument("--prompt", default=None, help="one-shot Hermes prompt")
    hermes_p.add_argument("--schedule", default=None, help="Hermes cron expression or interval")
    hermes_p.add_argument("--workdir", default=None, help="absolute remote working directory for a cron job")
    hermes_p.add_argument("--profile", choices=["luna", "terra", "sol"], default="terra",
        help="validated Sandbox route for a cron job (default: terra)")
    hermes_p.add_argument("--no-worktree", action="store_true", help="run against the primary checkout")
    hermes_p.add_argument("--scope", default=None, help="bounded authorization scope slug")
    hermes_p.add_argument("--replay-origin", default=None, help="exact HTTPS origin approved for replay")
    hermes_p.add_argument("--authorization-catalog", default=None,
        help="path to a standalone dashboard authorization catalog")
    hermes_p.add_argument("--reason", default=None, help="non-secret authorization rationale")
    hermes_p.add_argument("--expires-in-minutes", type=int, default=1440, help="authorization request expiry (1-1440)")
    hermes_p.add_argument("--async", dest="run_async", action="store_true", help="return a detached job id")
    hermes_p.add_argument("--timeout", type=int, default=1200, help="one-shot timeout in seconds")
    hermes_p.add_argument("--allow", dest="allowlist", action="append", default=None,
        help="explicit gateway allowlist entry (repeatable)")
    hermes_p.add_argument("--lines", type=int, default=200, help="maximum gateway log lines")
    hermes_p.add_argument("--confirm", action="store_true", help="confirm a protected Hermes operation")
    hermes_p.add_argument("--force-replace", action="store_true", help="replace every observed cron entry from the committed catalog")
    hermes_p.add_argument("--port", type=int, default=None, help="loopback dashboard port (default 9119)")
    hermes_p.add_argument("--fqdn", default=None, help="public dashboard hostname for expose")
    hermes_p.add_argument("--plan", action="store_true", help="show a read-only dashboard exposure plan")
    hermes_p.add_argument("--basic-auth-user", default=None, help="optional dashboard Basic Auth username")
    hermes_p.add_argument("--basic-auth-secret", default=None, help="approved secret reference for dashboard Basic Auth")
    hermes_p.add_argument("--backup-id", default=None, help="backup identifier for a protected restore")
    hermes_p.add_argument("--dry-run", action="store_true", help="show Hermes cleanup candidates without removing them")
    hermes_p.add_argument("--resolve-stale", action="store_true",
        help="with `hermes cleanup --confirm`, acknowledge provably dead sessions without deleting worktrees")
    hermes_p.add_argument("--max-jobs", type=int, default=None, help="maximum concurrent Hermes jobs")
    hermes_p.add_argument("--max-worktrees", type=int, default=None, help="maximum active Hermes worktrees")
    hermes_p.add_argument("--min-free-disk-mb", type=int, default=None, help="minimum free disk before launching Hermes")
    hermes_p.add_argument("--min-free-memory-mb", type=int, default=None, help="minimum free memory before launching Hermes")
    hermes_p.add_argument("--job-id", default=None, help="Hermes detached job identifier")
    hermes_p.add_argument("--offset", type=int, default=0, help="byte offset for incremental Hermes job output")
    hermes_p.add_argument("--json", action="store_true", help="print a stable JSON envelope")

    en = sub.add_parser("ensure",
        help="Boot the instance for a project dir (create-if-missing); per-project / MCP-first")
    en.add_argument("--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)")
    en.add_argument("--json", action="store_true",
        help="print the instance record as JSON (for the MCP server)")
    en.add_argument("--label", default=argparse.SUPPRESS,
        help="target an ADDITIONAL instance for this project root "
             "(multi-instance-per-root), e.g. 'qa' or 'php81'; default: the "
             "project's default instance. Minting a brand-new label needs --create.")
    en.add_argument("--create", action="store_true",
        help="deliberately mint a new instance for --label if one doesn't "
             "exist yet (guards against typo-spawning an extra stack)")
    en.add_argument("--reveal-login", dest="reveal_login", action="store_true",
        help="emit the usable autologin login_url in --json output instead of "
             "the redacted placeholder (LOCAL instances only; the token is a "
             "loopback-only dev credential already stored in sandbox.local.yml)")
    ensure_target = en.add_mutually_exclusive_group()
    ensure_target.add_argument("--local", action="store_true", help="force local execution")
    ensure_target.add_argument("--remote", help="ensure on a provisioned remote")
    en.add_argument("--workspace", dest="workspace", help="remote reusable workspace label")

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
    gl.add_argument("--node-ca", action="store_true",
        help="set NODE_EXTRA_CA_CERTS globally for local mkcert/Herd HTTPS")

    un = sub.add_parser("uninstall",
        help="Remove the whole sandbox (containers, volumes, proxy, MCP)")
    un.add_argument("--yes", action="store_true", help="skip confirmation")
    un.add_argument("--purge", action="store_true",
        help="also remove the install directory")

    ca = sub.add_parser("cache",
        help="Inspect or clear the shared plugin/theme/core download cache")
    ca.add_argument("action", nargs="?", choices=["info", "clear"], default="info",
        help="info (default) shows size/counts; clear empties it")
    ca.add_argument("layer", nargs="?", choices=["wp-cli", "wp-http"],
        help="limit `clear` to one layer (default: both)")
    ca.add_argument("--yes", action="store_true",
        help="skip the confirmation prompt on `clear`")

    lic = sub.add_parser("license",
        help="Manage Pro plugin license keys + sharing (Elementor Pro, WPDeveloper)")
    lic.add_argument("action", nargs="?",
        choices=["status", "set", "clear", "elementor-sync", "sync"],
        default="status",
        help="status (default, masked); set <family> <key>; clear [family]; "
             "elementor-sync (share a connected Elementor Pro activation across instances)")
    lic.add_argument("family", nargs="?", choices=["elementor"],
        help="license family for set/clear (clear without it clears all). "
             "WPDeveloper needs no key — its pro plugins are force-activated keylessly.")
    lic.add_argument("key", nargs="?",
        help="the license key for `set` (stored in the gitignored secret store; never echoed)")
    lic.add_argument("--from", dest="from_instance", default=None,
        help="for elementor-sync: the instance you connected Elementor Pro on "
             "(default: auto-detect the connected instance)")

    px = sub.add_parser("pxdiff",
        help="Pixel-diff two PNG screenshots (reference vs build) + locate the drift")
    px.add_argument("reference", help="reference PNG path")
    px.add_argument("build", help="build/screenshot PNG path")
    px.add_argument("--diff-out", dest="diff_out", default=None,
        help="write the red-overlay diff PNG here (e.g. tmp/diff.png)")
    px.add_argument("--threshold", type=float, default=0.1,
        help="pixelmatch colour sensitivity 0..1 (default 0.1)")
    px.add_argument("--bands", type=int, default=12,
        help="horizontal slices for the per-band locator (default 12)")
    px.add_argument("--json", action="store_true", help="emit raw JSON instead of a summary")

    vr = sub.add_parser("vrdiff",
        help="BackstopJS visual-regression diff (reference URL vs build URL) + browsable HTML web report")
    vr.add_argument("reference_url", help="reference design URL (captured first)")
    vr.add_argument("build_url", help="build URL to compare against the reference")
    vr.add_argument("--label", default="page", help="scenario label shown in the report (default: page)")
    vr.add_argument("--viewport", action="append", metavar="WxH",
        help="viewport WxH, repeatable for responsive checks (default: 1280x900)")
    vr.add_argument("--selector", default="document",
        help="capture selector; 'document' = full page (default)")
    vr.add_argument("--threshold", type=float, default=0.1,
        help="mismatch tolerance 0..1 (default 0.1)")
    vr.add_argument("--delay", type=int, default=1500,
        help="ms to wait after load before capture, lets fonts/images settle (default 1500)")
    vr.add_argument("--workdir", default="tmp/vrdiff",
        help="where to write bitmaps + the HTML report (default tmp/vrdiff)")
    vr.add_argument("--no-open", dest="no_open", action="store_true",
        help="don't auto-open the HTML report in a browser")
    vr.add_argument("--json", action="store_true", help="emit raw JSON instead of a summary")

    se = sub.add_parser("specextract",
        help="Run extract-web.js on a URL → DesignSpec v1 JSON (Phase 1 extraction, scripted)")
    se.add_argument("url", help="URL to extract (reference design or the build page)")
    se.add_argument("--out", default="-", help="write DesignSpec JSON here (default: stdout)")
    se.add_argument("--root", default=None,
        help="override the content-root selector (e.g. .elementor, .eb-fullwidth-content-wrapper)")
    se.add_argument("--extractor", default=None, help="extractor JS path (default: the skill's extract-web.js)")
    se.add_argument("--width", type=int, default=1280, help="viewport width — MUST match ref/build (default 1280)")
    se.add_argument("--height", type=int, default=900, help="viewport height (default 900)")
    se.add_argument("--dwell", type=int, default=450, help="ms dwell per scroll step so lazy media loads (default 450)")
    se.add_argument("--settle", type=int, default=30, help="max seconds to wait for image downloads to complete (default 30)")
    se.add_argument("--no-freeze", dest="no_freeze", action="store_true",
        help="do NOT pause CSS animation/transition/media before measuring")
    se.add_argument("--timeout", type=int, default=30, help="navigation timeout seconds (default 30)")
    se.add_argument("--login", action="store_true", help="log in via wp-login.php before navigating")
    se.add_argument("--auto-login", dest="auto_login", action="store_true", help="log in only when the URL is /wp-admin/")
    se.add_argument("--login-user", dest="login_user", default="", help="WP username (else $WP_ADMIN_USER or admin)")
    se.add_argument("--login-password", dest="login_password", default="", help="WP password (else $WP_ADMIN_PASSWORD)")

    sd = sub.add_parser("specdiff",
        help="DesignSpec v1 diff (reference vs build JSON) — Phase 3 ranked defect report")
    sd.add_argument("reference", help="reference DesignSpec JSON path (extract-web.js on the reference)")
    sd.add_argument("build", help="build DesignSpec JSON path (extract-web.js on the build)")
    sd.add_argument("--json", action="store_true", help="emit raw JSON instead of a summary")

    sg = sub.add_parser("specgate",
        help="DesignSpec v1 done-gate (reference vs build JSON) — Phase 5 numeric PASS/FAIL")
    sg.add_argument("reference", help="reference DesignSpec JSON path")
    sg.add_argument("build", help="build DesignSpec JSON path")
    sg.add_argument("--json", action="store_true", help="emit raw JSON instead of a summary")

    # Feature-owned commands are composed here. Existing parser definitions
    # above are the explicit compatibility bridge and are never duplicated.
    compose_missing_parsers(sub, COMMAND_SPECS.specs())

    # Global --instance flag accepted both BEFORE and AFTER the subcommand.
    # argparse can't natively share a global flag with all subparsers, so
    # we register it on the top-level parser AND inject the same flag onto
    # every subparser. Default is None so we can distinguish "user chose an
    # instance" from "user didn't say"; resolved below.
    p.add_argument("--instance", default=None,
        help="Which sandbox instance to target (default: $SANDBOX_INSTANCE, "
             "else the instance registered for the current project dir; "
             "errors if neither resolves)")
    # Global --label: sibling to --instance, for multi-instance-per-root — pick
    # WHICH of the cwd project's instances to target when it owns more than
    # one. Ignored when --instance (a globally-unique name) is also given.
    p.add_argument("--label", default=None,
        help="Which of the cwd project's instances to target, when it owns "
             "more than one (multi-instance-per-root), e.g. 'qa'. Ignored if "
             "--instance is also given. Default: the project's sole/default "
             "instance.")
    for sp_name, sp_parser in (sub.choices or {}).items():
        # Don't double-add when a subparser already has --instance/--label
        # (en/ap/vr define their own --label with different semantics).
        if not any(a.option_strings == ["--instance"]
                   for a in sp_parser._actions):
            # default=SUPPRESS so that when --instance is NOT passed after the
            # subcommand, the subparser doesn't clobber a value the top-level
            # parser already set from a BEFORE-subcommand --instance. Without
            # this, `./sb --instance xx focus` silently resolved to main.
            instance_help = None
            if sp_name == "ensure":
                instance_help = (
                    "invalid for ensure; use --project-dir/--label and --create "
                    "for a new label.\n"
                    "Use sb apply --instance NAME for an existing named instance"
                )
            sp_parser.add_argument("--instance", default=argparse.SUPPRESS,
                                   help=instance_help)
        if not any(a.option_strings == ["--label"] for a in sp_parser._actions):
            sp_parser.add_argument("--label", default=argparse.SUPPRESS)

    raw_argv = list(sys.argv[1:])
    args = p.parse_args(raw_argv)
    pre_command_label = _global_label_before_subcommand(raw_argv)
    if pre_command_label is not None and args.cmd not in {"domains", "native", "vrdiff"}:
        args.label = pre_command_label
    if not args.cmd:
        p.print_help()
        return

    # A project-root status/logs request has two deliberately separate target
    # domains.  The outer remote form is dispatched directly to the remote
    # lifecycle adapter: it must not require a local sandbox config, registry
    # record, cwd-selected instance, capability, migration, or generated-file
    # write.  An explicit --instance is an inner local selector and therefore
    # has no meaning on this outer route; reject it before any local helper can
    # run (including the automatic migration check below).
    outer_remote_observation, inner_local_observation = _project_observation_route(args)
    if outer_remote_observation and _explicit_global_option(raw_argv, "--instance"):
        die(
            f"{args.cmd} with --remote and --project-dir cannot combine --instance; "
            "omit --instance because the remote workspace resolves its inner "
            "instance, or use --local for a local project observation.",
            2,
        )

    if outer_remote_observation:
        # `cmd_status`/`cmd_logs` enter `_remote_lifecycle` before touching the
        # local instance.  Passing an empty config is intentional: the remote
        # target service loads the explicit project root itself, while the
        # local global config may not exist on a controller-only machine.
        args.resolved_instance = None
        try:
            COMMANDS[args.cmd]({}, args)
        except RemoteJobAdmissionError as exc:
            _dispatch_remote_admission_error(exc, args)
        return

    # `setup` prepares the whole registry and is deliberately not an
    # instance-routing command. Refuse selectors before migration, config
    # loading, preflight, or any runtime handler can cause side effects.
    if args.cmd == "setup" and (
        _explicit_global_option(raw_argv, "--instance")
        or _explicit_global_option(raw_argv, "--label")
    ):
        die(
            "setup is registry-wide; use `sb apply --instance NAME` or "
            "`sb ensure --project-dir DIR` for project-scoped setup.",
            2,
        )

    # ``ensure`` is project-routed and derives its target from
    # ``--project-dir``/``--label``.  A global ``--instance`` selector was
    # accepted by the shared parser but never consumed by the ensure handler,
    # so accepting it could boot a different project than the operator named.
    # Reject both parser placements before migration, config loading, or any
    # compose/environment/runtime handler can cause a side effect.
    if args.cmd == "ensure" and _explicit_global_option(raw_argv, "--instance"):
        die(
            "ensure is project-scoped and cannot target --instance NAME; use "
            "`sb ensure --project-dir DIR` with `--label LABEL` (and "
            "`--create` for a new label), or `sb apply --instance NAME` for "
            "an existing named instance.",
            2,
        )

    # Spec 009 upgrade path: before a normal command can touch the legacy
    # fallback, move it once when the selected base is genuinely empty.  The
    # helper re-execs this exact command after staging the data; explicit
    # migration/home commands keep their own dry-run and relocation semantics.
    command_spec = COMMAND_SPECS.get(args.cmd)
    predispatch_skip = bool(
        command_spec is not None
        and command_spec.predispatch_policy is not None
        and command_spec.predispatch_policy(args)
    ) or inner_local_observation
    if args.cmd not in {"migrate", "home", "ensure"} and not predispatch_skip:
        from sandbox.commands.migrate import maybe_auto_migrate
        maybe_auto_migrate()

    # Resource status owns an end-to-end request budget.  The executable
    # captures this timestamp before importing the CLI so parser, config, and
    # dispatch startup consume the same budget as the provider.
    if args.cmd == "resources" and getattr(args, "action", None) == "status":
        requested_budget = (
            args.budget if getattr(args, "budget", None) is not None
            else 10 if getattr(args, "fast", False)
            else 900 if getattr(args, "refresh", False)
            else 15
        )
        args._invocation_deadline_monotonic = (
            float(invocation_started_monotonic) + float(requested_budget)
        )

    cfg = load_config()

    # Resolve which instance this invocation targets. Precedence:
    #   1. explicit --instance   2. $SANDBOX_INSTANCE
    #   3. the registry instance for the cwd's project (scoped by --label/
    #      $SANDBOX_LABEL when given)   4. error
    # There is no implicit/global instance: `sb <cmd>` in a project dir targets
    # THAT project's instance; run outside any registered project (and not
    # project-routed) it aborts with guidance rather than targeting a fallback.
    explicit = getattr(args, "instance", None) or os.environ.get("SANDBOX_INSTANCE")
    cwd_label = getattr(args, "label", None) or os.environ.get("SANDBOX_LABEL")
    if args.cmd == "resources":
        # Resource monitoring is host-global (or explicitly --remote), never
        # instance-routed. Avoid the mutable registry lock and unrelated
        # instance resolution on this bounded global command path.
        instances = {}
        chosen = None
    else:
        instances = resolve_instances(cfg)
        # Generic adapters own their lifecycle records; they do not belong in
        # the WordPress-specific sandbox.local.yml instance map.
        for entry in _core().registry_all().values():
            if entry.get("kind") == "compose" and entry.get("instance"):
                instances.setdefault(entry["instance"], entry)
        if inner_local_observation and not explicit:
            # The controller may be running from a different checkout (the
            # normal case for a staged remote workspace).  Resolve the inner
            # instance from the explicit project root and its registry only;
            # `_cwd_instance()` would silently inspect the controller cwd.
            try:
                selected = resolve_registered_instance(
                    getattr(args, "project_dir", None), label=cwd_label,
                )
            except Exception as exc:
                die(str(exc), 2)
            chosen = selected.get("instance") if selected else None
        else:
            # An explicitly named instance (including an unknown name) keeps
            # its normal precedence.  Do not replace an unknown selector with
            # the project's default record.
            chosen = explicit or _cwd_instance(label=cwd_label)
    # Project-dir-routed commands derive their instance from the project root
    # (registry / ensure_instance), not this global gate.
    PROJECT_ROUTED = {"init", "ensure", "test", "mcp", "smoke", "e2e", "ci", "plugin-check", "deploy"}
    # `apply` reconciles a PROJECT. Without --project-dir it used to fall
    # through to the sandbox.yml setup alias even when the caller had named an
    # instance or was standing inside a project — so `apply --instance X`
    # silently re-applied the whole sandbox instead of reconciling X. Infer the
    # project the caller clearly meant, and say which one was chosen.
    if args.cmd == "apply" and not getattr(args, "project_dir", None):
        implied, source = _implied_project_dir(explicit, cwd_label)
        if implied:
            args.project_dir = implied
            info(f"apply: reconciling the project at {implied} ({source}). "
                 "Run `./sb setup` for the whole sandbox instead.")
    # `apply --project-dir` is project-routed (reconcile); bare `apply` is the
    # sandbox.yml setup alias.
    if args.cmd == "apply" and getattr(args, "project_dir", None):
        PROJECT_ROUTED = PROJECT_ROUTED | {"apply"}
    if args.cmd in {"status", "logs"} and getattr(args, "project_dir", None):
        PROJECT_ROUTED = PROJECT_ROUTED | {args.cmd}

    # Instance-scoped commands operate on ONE instance and require resolution.
    # Everything else is registry-wide/global (instances, dashboard, web, setup,
    # global, uninstall, domains, …) or takes its own name/project arg, so an
    # unresolved instance is fine for them.
    INSTANCE_SCOPED = {
        "up", "down", "status", "logs", "shell", "install", "wp", "seed", "visit",
        "doctor", "clean", "snapshot", "restore", "snapshots", "update", "open",
        "xdebug", "introspect", "secure", "server", "focus", "claude", "onboard",
        "abilities", "job", "jobs", "dump", "qm", "reset", "exec",
    }

    # An explicitly supplied instance label is intent, not a hint.  Resolve it
    # against the canonical project registry before dispatch so a typo cannot
    # silently fall back to ``default`` (or another label).  Remote workspace
    # labels and the domain/native/vrdiff semantic labels are separate
    # contracts, and ``ensure --create`` is the one deliberate minting path.
    explicit_label = _explicit_global_label(raw_argv)
    label_routed = args.cmd in INSTANCE_SCOPED or args.cmd in PROJECT_ROUTED
    semantic_label_command = args.cmd in {"domains", "native", "vrdiff"}
    explicit_instance = bool(getattr(args, "instance", None))
    remote_target = bool(getattr(args, "remote", None))
    allow_label_creation = args.cmd == "ensure" and bool(getattr(args, "create", False))
    if (
        explicit_label is not None
        and label_routed
        and not semantic_label_command
        and not explicit_instance
        and not remote_target
        and not allow_label_creation
        and not inner_local_observation
    ):
        label_project_dir = getattr(args, "project_dir", None) or Path.cwd()
        try:
            label_root = str(_core().find_project_root(label_project_dir))
        except Exception:
            label_root = None
        if label_root is not None:
            labels = _core().registry_list_for_root(label_root)
            if not any(item.get("label") == explicit_label for item in labels):
                known = ", ".join(str(item.get("label")) for item in labels) or "none"
                die(
                    f"label_not_found: no instance labelled '{explicit_label}' for "
                    f"{label_root} (existing labels: {known})",
                    2,
                )
    direct_instance_exec = args.cmd == "exec" and bool(getattr(args, "in_instance", False))
    durable_exec = args.cmd == "exec" and bool(
        getattr(args, "local", False) or getattr(args, "remote", None) or getattr(args, "detach", False)
    )
    if chosen is None:
        if inner_local_observation:
            project_root = Path(getattr(args, "project_dir", "")).expanduser().resolve()
            label_hint = f" with label '{cwd_label}'" if cwd_label else ""
            die(
                f"no sandbox instance for project directory {project_root}{label_hint}; "
                "run `sb ensure --project-dir DIR` to create one.",
                2,
            )
        if args.cmd in INSTANCE_SCOPED and not durable_exec and not direct_instance_exec:
            # Distinguish "no instance at all for this cwd" from "cwd's
            # project owns MULTIPLE instances and neither --label nor a
            # default disambiguates" (multi-instance-per-root) — the latter
            # needs a --label hint, not "go run sb init".
            sc = _core()
            try:
                cwd_root = str(sc.find_project_root(Path.cwd()))
                owned = sc.registry_list_for_root(cwd_root)
            except Exception:
                owned = []
            if len(owned) > 1:
                labels = ", ".join(e["label"] for e in owned)
                die(f"project has {len(owned)} instances ({labels}); pass --label.")
            _known = ", ".join(sorted(instances))
            die("no sandbox instance for this directory. cd into a registered "
                "project, or run `sb init` / `sb ensure` to create one."
                + (f"\nKnown instances: {_known}" if _known else ""))
    elif inner_local_observation and explicit and chosen not in instances:
        # A named selector remains an explicit selector even when the staged
        # root owns a valid default.  Preserve the ordinary unknown-instance
        # failure rather than silently switching to that default.
        die(f"unknown instance '{chosen}'. "
            f"Known: {', '.join(sorted(instances)) or '(none)'}.", 2)
    elif chosen not in instances and args.cmd not in PROJECT_ROUTED and not durable_exec and not direct_instance_exec:
        die(f"unknown instance '{chosen}'. "
            f"Known: {', '.join(sorted(instances)) or '(none)'}.")
    args.resolved_instance = chosen

    # WordPress-only legacy handlers retain their historical parser/dispatch
    # names, but capability checks happen at the composition boundary before
    # any handler can regenerate files or touch a runtime. Generic adapters
    # therefore receive a structured unsupported-capability error instead of a
    # late KeyError or an accidental WP mutation.
    required = CLI_CAPABILITIES.get(args.cmd)
    if required and chosen is not None:
        capability_error = preflight_instance_capability(cfg, chosen, required)
        if capability_error is not None:
            die(capability_error.message)

    # An automatic relocation re-execs the original command with a marker.
    # Finalize it immediately before the ordinary dispatch path can write
    # Compose or its environment.  In particular, stale PHP-extension
    # identity must raise a bounded migration error while no generated file
    # has been touched.  A successful finalizer already regenerated Compose,
    # so only that routine write is skipped for this invocation; the legacy
    # environment file is still refreshed below.  Keeping this after target
    # resolution and capability preflight preserves the historical ordering
    # for commands that must refuse an invalid target first.
    auto_migration_finalized = False
    if args.cmd != "ensure" and not predispatch_skip:
        from sandbox.commands.migrate import finalize_auto_migration
        auto_migration_finalized = finalize_auto_migration(cfg)

    # (Re)generate per-instance compose files so they're always in sync with the
    # registered instances. Cheap + idempotent.
    # Global resource observation neither consumes nor updates per-instance
    # runtime files.  Avoiding these legacy rewrites keeps status read-only and
    # removes unrelated initialization from its bounded request path.
    bounded_resource_status = (
        args.cmd == "resources" and getattr(args, "action", None) == "status"
    )
    # Project-routed ensure owns its ready-path attestation.  Pre-writing
    # Compose or the legacy environment here would mutate persistent state
    # before it can refuse a stale live mount set.
    ensure_attestation_gate = args.cmd == "ensure" and args.cmd in PROJECT_ROUTED
    if (not predispatch_skip and not bounded_resource_status and
            args.cmd != "secrets" and not ensure_attestation_gate):
        if not auto_migration_finalized:
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
    try:
        handler(cfg, args)
    except RemoteJobAdmissionError as exc:
        _dispatch_remote_admission_error(exc, args)



if __name__ == "__main__":

    main()

from __future__ import annotations
import argparse
import hashlib
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
        # Herd's PHP is a shared host install — toggling its global ini would
        # affect every Herd site + needs a restart we can't do per-instance.
        # Report status + actionable guidance rather than a hard abort (spec 007).
        info(f"xdebug on herd ({inst}) is host-managed (shared PHP install).")
        print("  Per-instance toggling is unsupported; manage Xdebug via Herd's")
        print("  PHP settings (herd) or your php.ini, then set XDEBUG_TRIGGER on requests.")
        return

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

    cache_dir = RUNTIME_DIR / "cache"
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
    """Run a project's resolved unit or integration PHPUnit environment."""
    if getattr(args, "mode", None) == "matrix":
        from sandbox.commands.jobs_runtime import cmd_job_matrix
        remainder = list(getattr(args, "passthrough", ()) or ())
        local = bool(getattr(args, "local", False)); remote = getattr(args, "remote", None)
        workspaces = list(getattr(args, "workspace", None) or [])
        timeout = getattr(args, "timeout", 900); output_profile = getattr(args, "output_profile", "smart")
        as_json = bool(getattr(args, "json", False))
        # ``argparse.REMAINDER`` intentionally preserves all tokens after the
        # positional mode. Parse the durable matrix subset here so the natural
        # `sb test matrix --workspace cell -- <argv>` spelling remains valid.
        command = []
        while remainder:
            token = remainder.pop(0)
            if token == "--": command = remainder; break
            if token == "--local": local = True; continue
            if token == "--remote" and remainder: remote = remainder.pop(0); continue
            if token == "--workspace" and remainder: workspaces.append(remainder.pop(0)); continue
            if token == "--timeout" and remainder: timeout = int(remainder.pop(0)); continue
            if token == "--output-profile" and remainder: output_profile = remainder.pop(0); continue
            if token == "--json": as_json = True; continue
            command.append(token)
        cmd_job_matrix(cfg, _types.SimpleNamespace(
            command=command, project_dir=getattr(args, "project_dir", None) or os.getcwd(),
            local=local, remote=remote, workspace=workspaces, timeout=timeout,
            output_profile=output_profile, json=as_json,
            spec_json=None,
        ))
        return
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    label = getattr(args, "label", None)
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    # Remote is the project-level default when configured. Submit the explicit
    # test command to the durable remote runtime before touching local instance
    # state; the remote command uses --local to prevent recursive remote
    # resolution on the deployed checkout.
    from sandbox.application.context import durable_job_dependencies
    from sandbox.application.target_service import TargetResolutionError
    from sandbox.jobs.models import JobSubmission, SourceIdentity, TargetRequest
    try:
        selected_target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
            project_dir=pd, local=bool(getattr(args, "local", False)),
            remote=getattr(args, "remote", None), workspace=(
                (getattr(args, "workspace", None) or [None])[0]
                if getattr(args, "mode", None) == "matrix" else None),
            required_capability="job.exec",
        ))
    except TargetResolutionError as exc:
        die(f"{exc.code}: {exc}")
    if selected_target.kind == "remote":
        try:
            mode = resolve_test_mode(
                selected_target.project_root,
                configured=pconf.get("tests", {}).get("suite", "auto"),
                explicit=getattr(args, "mode", None),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            die(str(exc))
        if getattr(args, "provision_only", False):
            die("--provision-only is only available for local integration harness setup")
        extra = [a for a in (getattr(args, "passthrough", None) or []) if a != "--"]
        command = ["sb", "test", mode, "--local", "--project-dir", "."]
        if extra:
            command += ["--", *extra]
        timeout = int(getattr(args, "timeout", 900) or 900)
        submission = JobSubmission(
            "test", selected_target.project_root,
            hashlib.sha256(selected_target.project_root.encode()).hexdigest(), "remote",
            selected_target.workspace_label, tuple(command), timeout,
            SourceIdentity("sha256:" + hashlib.sha256(selected_target.project_root.encode()).hexdigest()),
            remote_name=selected_target.remote_name, output_profile=getattr(args, "output_profile", "smart"),
            deadline_source="explicit" if getattr(args, "timeout", None) else "profile:test",
        )
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
            ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote).submit(submission)
        if getattr(args, "json", False):
            print(json.dumps(accepted, sort_keys=True))
        else:
            print(accepted["job_id"])
        return
    entry = sc.registry_get(pconf["root"], label=label)
    if not entry:
        if label is None and len(sc.registry_list_for_root(pconf["root"])) > 1:
            known = [e["label"] for e in sc.registry_list_for_root(pconf["root"])]
            die(f"'{pconf['root']}' has multiple instances ({', '.join(known)}); "
                f"pass --label to disambiguate.")
        die(f"no instance for {pconf['root']} — run `./sb ensure --project-dir {pd}` first.")
    # Re-load with the RESOLVED label so a per-label sandbox.config.<label>.json
    # layer (if present) applies — the first load above only existed to find root.
    pconf = sc.load_project_config(pd, label=entry.get("label"))
    inst = entry["instance"]
    try:
        mode = resolve_test_mode(
            pconf["root"], configured=pconf.get("tests", {}).get("suite", "auto"),
            explicit=getattr(args, "mode", None),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        die(str(exc))
    if getattr(args, "provision_only", False) and mode == "unit":
        die("--provision-only is only valid for integration test mode")

    print(f"  mode:       {mode}")
    if mode == "integration":
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
    else:
        tools = _ensure_test_runner_tools()
        suite = None
        print(f"  instance:   {inst}")
        print(f"  phpunit:    {tools['phpunit']}")
        print(f"  composer:   {tools['composer']}")

    extra = [a for a in (getattr(args, "passthrough", None) or []) if a != "--"]
    print()
    if mode == "unit" and entry.get("server") == "herd":
        code = _run_tests_unit_herd(inst, pconf["root"], tools, extra)
    elif mode == "unit":
        code = _run_tests_unit(inst, pconf["root"], tools, extra)
    elif entry.get("server") == "herd":
        code = _run_tests_herd(inst, pconf["root"], suite, tools, extra)
    else:
        code = _run_tests(inst, pconf["root"], suite, tools, extra)
    print()
    if code == 0:
        ok("tests passed")
    else:
        die(f"tests failed (phpunit exit {code})")

def cmd_selftest(cfg, args) -> None:
    """Run the sandbox tooling's OWN unit tests (tests/) — the CLI/package, not a
    plugin. Uses the .cli-venv python (PyYAML available); falls back to the
    current interpreter."""
    import subprocess
    py = CLI_VENV / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    rc = subprocess.run(
        [py, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
        cwd=str(ROOT)).returncode
    if rc != 0:
        die("selftest: FAILED")
    ok("selftest: passed")


def cmd_dump(cfg, args) -> None:
    """Tail or clear the dump()/dd() log (wp-content/debug-dump.log) — spec 007."""
    inst = args.resolved_instance
    log = wp_dir(inst) / "wp-content" / "debug-dump.log"
    if getattr(args, "clear", False):
        if log.exists():
            log.write_text("")
        ok("debug-dump.log cleared")
        return
    if not log.exists():
        info("no debug-dump.log yet — add dump($x) to plugin/theme code and load it")
        return
    if getattr(args, "follow", False):
        import subprocess
        subprocess.run(["tail", "-f", str(log)])
        return
    print(log.read_text()[-20000:], end="")


def cmd_qm(cfg, args) -> None:
    """Capture Query Monitor data for a URL → JSON (spec 007).

    Ensures QM is active, fires a real request (curl -k from the wp container so
    the self-signed .tst cert is accepted), then prints the last qm.jsonl line.
    """
    inst = args.resolved_instance
    if _is_herd_instance(inst):
        die("qm capture isn't wired for herd instances yet")
    if getattr(args, "clear", False):
        f = wp_dir(inst) / "wp-content" / "qm.jsonl"
        if f.exists():
            f.write_text("")
        ok("qm.jsonl cleared")
        return
    # ensure QM active (installs from wp.org on first use)
    r = wpcli(["plugin", "is-active", "query-monitor"], instance=inst, check=False, capture=True)
    if (getattr(r, "returncode", 1) != 0):
        info("activating Query Monitor…")
        wpcli(["plugin", "install", "query-monitor", "--activate"], instance=inst, check=False)
    path = args.url or "/"
    if path.startswith("http"):
        from urllib.parse import urlparse
        path = urlparse(path).path or "/"
    compose("exec", "-T", "wp", "sh", "-c",
            f"curl -s -k -o /dev/null 'http://localhost{path}'", instance=inst, check=False)
    qm = wp_dir(inst) / "wp-content" / "qm.jsonl"
    if not qm.exists():
        die("no qm.jsonl produced — is Query Monitor active?")
    last = qm.read_text().strip().splitlines()[-1:]
    print(last[0] if last else "(empty)")


register({
    'xdebug': cmd_xdebug,
    'dump': cmd_dump,
    'qm': cmd_qm,
    'introspect': cmd_introspect,
    'test': cmd_test,
    'selftest': cmd_selftest,
})

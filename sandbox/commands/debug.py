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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen
import ssl



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register


def _test_requests_explicit_local(args) -> bool:
    """Pin a named local instance instead of silently inferring a remote."""
    return bool(getattr(args, "local", False) or (
        getattr(args, "instance", None) and not getattr(args, "remote", None)
    ))


def _local_test_entry(sc, root: str, args):
    """Resolve an explicitly named local instance without default fallback."""
    explicit = getattr(args, "instance", None)
    if explicit:
        entries = sc.registry_list_for_root(root)
        entry = next((item for item in entries
                      if item.get("instance") == explicit), None)
        if not entry:
            die(f"unknown local instance '{explicit}' for {root}")
        return entry
    return sc.registry_get(root, label=getattr(args, "label", None))



def _remote_test_matrix_submissions(target, mode: str, extra: list[str],
                                    workspaces: list[str], timeout: int | None,
                                    output_profile: str) -> list:
    """Turn selected WordPress test workspaces into isolated remote leaves."""
    from sandbox.jobs.models import JobSubmission
    from sandbox.commands.jobs_runtime import (_resolved_execution_policy,
                                               _resolved_project_identity, _source_identity)

    identity = _resolved_project_identity(target)
    source = _source_identity(target.project_root)
    command = ["sb", "test", "--local", "--project-dir", ".", mode]
    if extra:
        command += ["--", *extra]
    submissions = []
    for workspace in workspaces:
        policy_target = _types.SimpleNamespace(
            runtime_policy=getattr(target, "runtime_policy", None), workspace_label=workspace)
        policy = _resolved_execution_policy(policy_target, _types.SimpleNamespace(
            execution_policy_json=None, profile=None, timeout=timeout, stall_seconds=None,
            cancel_grace_seconds=None, cancel_on_stall=None, cleanup_policy=None,
        ))
        submissions.append(JobSubmission(
            "test", target.project_root, identity, "remote", workspace, tuple(command),
            policy.deadline_seconds, source, remote_name=target.remote_name,
            workspace_mode="isolated", output_profile=output_profile,
            execution_profile=policy.execution_profile, deadline_source=policy.deadline_source,
            deadline_reminder=policy.deadline_reminder, stall_seconds=policy.stall_seconds,
            cancel_grace_seconds=policy.cancel_grace_seconds,
            cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
            execution_policy_provenance=policy.provenance,
        ))
    return submissions


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
        result = wpcli(
            ["eval", "echo extension_loaded('xdebug') ? 'on' : 'off';"],
            instance=inst, check=False, capture=True,
        )
        reported = (getattr(result, "stdout", "") or "").strip()
        if reported not in ("on", "off"):
            reported = "unknown"
        print(reported)
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
        from sandbox.commands.jobs_runtime import cmd_declared_test_plan, cmd_job_matrix
        remainder = list(getattr(args, "passthrough", ()) or ())
        local = bool(getattr(args, "local", False)); remote = getattr(args, "remote", None)
        workspaces = list(getattr(args, "workspace", None) or [])
        timeout = getattr(args, "timeout", None); output_profile = getattr(args, "output_profile", None)
        as_json = bool(getattr(args, "json", False))
        plan_name = None
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
            if token == "--plan" and remainder: plan_name = remainder.pop(0); continue
            if token == "--timeout" and remainder: timeout = int(remainder.pop(0)); continue
            if token == "--output-profile" and remainder: output_profile = remainder.pop(0); continue
            if token == "--json": as_json = True; continue
            command.append(token)
        if plan_name:
            if command:
                die("declared test plans do not accept an explicit command")
            cmd_declared_test_plan(cfg, _types.SimpleNamespace(
                plan=plan_name, project_dir=getattr(args, "project_dir", None) or os.getcwd(),
                local=local, remote=remote, timeout=timeout,
                output_profile=output_profile, json=as_json,
            ))
            return
        cmd_job_matrix(cfg, _types.SimpleNamespace(
            command=command, project_dir=getattr(args, "project_dir", None) or os.getcwd(),
            local=local, remote=remote, workspace=workspaces, timeout=timeout,
            output_profile=output_profile, json=as_json,
            spec_json=None,
        ))
        return
    # ``argparse.REMAINDER`` means an output flag after the optional mode is
    # captured as passthrough. Preserve explicit PHPUnit arguments after
    # ``--``, but consume the documented trailing CLI JSON flag.
    raw_passthrough = list(getattr(args, "passthrough", None) or [])
    cli_json = bool(getattr(args, "json", False))
    passthrough = []
    forwarding = False
    for token in raw_passthrough:
        if token == "--":
            forwarding = True
            passthrough.append(token)
        elif token == "--json" and not forwarding:
            cli_json = True
        else:
            passthrough.append(token)

    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    label = getattr(args, "label", None)
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    # Generic Compose projects never infer package scripts. Their checked-in
    # Sandbox descriptor declares every supported mode as an argv list.
    if pconf.get("kind") == "compose":
        as_json = cli_json
        mode = getattr(args, "mode", None) or "fast"
        declared = (pconf.get("tests") or {}).get("modes") or {}
        if mode not in declared:
            die(f"unknown declared Compose test mode {mode!r}; available: {', '.join(sorted(declared))}")
        if [token for token in passthrough if token != "--"]:
            die("declared Compose test modes do not accept extra command arguments")
        argv = list(declared[mode]["argv"])
        from sandbox.application.context import durable_job_dependencies
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import JobSubmission, TargetRequest
        try:
            target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
                project_dir=pd, local=_test_requests_explicit_local(args),
                remote=getattr(args, "remote", None),
                workspace=(getattr(args, "workspace", None) or [None])[0],
                required_capability="job.exec" if not _test_requests_explicit_local(args) else None,
            ))
        except TargetResolutionError as exc:
            die(f"{exc.code}: {exc}")
        if target.kind == "remote":
            from sandbox.commands.jobs_runtime import (_resolved_execution_policy,
                                                       _resolved_project_identity, _source_identity)
            policy = _resolved_execution_policy(target, _types.SimpleNamespace(
                execution_policy_json=None, profile=None, timeout=getattr(args, "timeout", None),
                stall_seconds=None, cancel_grace_seconds=None, cancel_on_stall=None,
                cleanup_policy=None,
            ))
            submission = JobSubmission("runtime-exec", target.project_root,
                _resolved_project_identity(target), "remote", target.workspace_label,
                tuple(argv), policy.deadline_seconds,
                _source_identity(target.project_root),
                remote_name=target.remote_name, output_profile=getattr(args, "output_profile", None) or "smart",
                execution_profile=policy.execution_profile, deadline_source=policy.deadline_source,
                deadline_reminder=policy.deadline_reminder, stall_seconds=policy.stall_seconds,
                cancel_grace_seconds=policy.cancel_grace_seconds,
                cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
                execution_policy_provenance=policy.provenance)
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path).submit(submission)
            print(json.dumps(accepted, sort_keys=True) if as_json else accepted["job_id"])
            return
        entry = _local_test_entry(sc, pconf["root"], args)
        if not entry:
            die(f"no instance for {pconf['root']} — run `./sb ensure --project-dir {pd}` first.")
        result = runtime_service(cfg).invoke(OperationRequest(project_root=pconf["root"], operation="exec",
            label=entry.get("label", "default"), arguments={"argv": argv}))
        if isinstance(result, OperationError):
            die(result.message)
        print(result.data.get("output", ""), end="")
        return
    # Resolve WordPress mode before target/capability selection.  In particular,
    # an invalid explicit mode must fail without consulting a remote target (and
    # therefore without its capability checks or deployment side effects).
    try:
        initial_mode = resolve_test_mode(
            pconf["root"], configured=pconf.get("tests", {}).get("suite", "auto"),
            explicit=getattr(args, "mode", None),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        die(str(exc))
    if getattr(args, "provision_only", False) and initial_mode == "unit":
        die("--provision-only is only valid for integration test mode")
    # Remote is the project-level default when configured. Submit the explicit
    # test command to the durable remote runtime before touching local instance
    # state; the remote command uses --local to prevent recursive remote
    # resolution on the deployed checkout.
    from sandbox.application.context import durable_job_dependencies
    from sandbox.application.target_service import TargetResolutionError
    from sandbox.jobs.models import JobSubmission, TargetRequest
    requested_workspaces = list(getattr(args, "workspace", None) or [])
    try:
        selected_target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
            project_dir=pd, local=_test_requests_explicit_local(args),
            remote=getattr(args, "remote", None), workspace=(
                requested_workspaces[0]
                if getattr(args, "mode", None) == "matrix" else None),
            required_capability="job.exec",
        ))
    except TargetResolutionError as exc:
        die(f"{exc.code}: {exc}")
    if selected_target.kind == "remote":
        from sandbox.commands.jobs_runtime import (_resolved_execution_policy,
                                                   _resolved_project_identity, _source_identity)
        mode = initial_mode
        if getattr(args, "provision_only", False):
            die("--provision-only is only available for local integration harness setup")
        extra = [a for a in passthrough if a != "--"]
        # ``test`` preserves everything after its positional mode as
        # phpunit passthrough.  Keep target-selection options before the mode
        # so the nested invocation cannot accidentally resolve the deployed
        # project's remote-first default again.
        command = ["sb", "test", "--local", "--project-dir", ".", mode]
        if extra:
            command += ["--", *extra]
        timeout = getattr(args, "timeout", None)
        output_profile = getattr(args, "output_profile", None) or "smart"
        if len(requested_workspaces) > 1:
            submissions = _remote_test_matrix_submissions(
                selected_target, mode, extra, requested_workspaces, timeout, output_profile)
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path).submit_many(submissions)
            if cli_json:
                print(json.dumps(accepted, sort_keys=True))
            else:
                print(accepted["parent_job_id"])
            return
        policy = _resolved_execution_policy(selected_target, _types.SimpleNamespace(
            execution_policy_json=None, profile=None, timeout=timeout, stall_seconds=None,
            cancel_grace_seconds=None, cancel_on_stall=None, cleanup_policy=None,
        ))
        submission = JobSubmission(
            "test", selected_target.project_root,
            _resolved_project_identity(selected_target), "remote",
            selected_target.workspace_label, tuple(command), policy.deadline_seconds,
            _source_identity(selected_target.project_root),
            remote_name=selected_target.remote_name, output_profile=output_profile,
            execution_profile=policy.execution_profile, deadline_source=policy.deadline_source,
            deadline_reminder=policy.deadline_reminder, stall_seconds=policy.stall_seconds,
            cancel_grace_seconds=policy.cancel_grace_seconds,
            cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
            execution_policy_provenance=policy.provenance,
        )
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
            ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
            remote_sb_path=_remote.remote_sb_path).submit(submission)
        if cli_json:
            print(json.dumps(accepted, sort_keys=True))
        else:
            print(accepted["job_id"])
        return
    entry = _local_test_entry(sc, pconf["root"], args)
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
    # Any mode other than "unit" routes into _run_tests(), which requires the
    # provisioned suite + polyfills. Keyed off "not unit" rather than
    # "== integration" so a new mode can never reach _run_tests() with an
    # unprovisioned tools dict (that mismatch was the KeyError: 'polyfills').
    if mode != "unit":
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
        from sandbox.application.context import managed_native_instance_selected
        if managed_native_instance_selected(inst) is not None:
            from sandbox.core._tests import (
                MANAGED_NATIVE_COMPOSER, MANAGED_NATIVE_PHPUNIT,
            )
            tools = {"phpunit": MANAGED_NATIVE_PHPUNIT,
                     "composer": MANAGED_NATIVE_COMPOSER}
        else:
            tools = _ensure_test_runner_tools()
        suite = None
        print(f"  instance:   {inst}")
        print(f"  phpunit:    {tools['phpunit']}")
        print(f"  composer:   {tools['composer']}")

    extra = [a for a in passthrough if a != "--"]
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

    Ensures QM is active, fires a real request, and returns the JSONL record
    tagged for that exact request. The tag prevents unrelated/stale requests
    from being mistaken for the requested capture.
    """
    inst = args.resolved_instance
    if getattr(args, "clear", False):
        f = wp_dir(inst) / "wp-content" / "qm.jsonl"
        if f.exists():
            f.write_text("")
        ok("qm.jsonl cleared")
        return
    if args.url == "off":
        wpcli(["plugin", "deactivate", "query-monitor"], instance=inst, check=False)
        ok("Query Monitor deactivated")
        return

    # QM is normally provisioned installed-but-inactive. Keep first capture
    # robust for older instances that predate that provisioning policy.
    installed = wpcli(["plugin", "is-installed", "query-monitor"], instance=inst,
                      check=False, capture=True)
    if getattr(installed, "returncode", 1) != 0:
        info("installing Query Monitor…")
        installed = wpcli(["plugin", "install", "query-monitor"], instance=inst,
                          check=False, capture=True)
        if getattr(installed, "returncode", 1) != 0:
            die("could not install Query Monitor for capture")

    r = wpcli(["plugin", "is-active", "query-monitor"], instance=inst, check=False, capture=True)
    if (getattr(r, "returncode", 1) != 0):
        info("activating Query Monitor…")
        activated = wpcli(["plugin", "activate", "query-monitor"], instance=inst,
                          check=False, capture=True)
        if getattr(activated, "returncode", 1) != 0:
            die("could not activate Query Monitor for capture")

    qm = wp_dir(inst) / "wp-content" / "qm.jsonl"
    before = qm.stat().st_size if qm.exists() else 0
    capture_id = os.urandom(16).hex()
    capture_url = _qm_capture_url(args.url or "/", capture_id)
    if _is_herd_instance(inst):
        _qm_fetch_herd(inst, capture_url)
    else:
        _qm_fetch_docker(inst, capture_url)

    payload = _qm_record_for_capture(qm, before, capture_id)
    if payload is None:
        die("no fresh qm.jsonl record for this request — is Query Monitor active?")
    print(json.dumps(_qm_filter_collectors(payload, getattr(args, "collectors", None))))


def _qm_capture_url(url: str, capture_id: str) -> str:
    """Add an internal, one-request QM correlation value to a local URL."""
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("sandbox_qm_capture", capture_id))
    return urlunsplit(("", "", path, urlencode(query), ""))


def _qm_fetch_docker(instance: str, path: str) -> None:
    """Issue an anonymous capture request from the Docker web container."""
    from shlex import quote

    result = compose("exec", "-T", "wp", "sh", "-c",
                     "curl -sS -k -o /dev/null -- " + quote("http://localhost" + path),
                     instance=instance, check=False, capture=True)
    if getattr(result, "returncode", 1) != 0:
        die("Query Monitor capture request failed")


def _qm_fetch_herd(instance: str, path: str) -> None:
    """Issue an anonymous Herd request without claiming Docker-only support."""
    instances = resolve_instances(load_config())
    config = instances.get(instance)
    if not config:
        die(f"unknown instance: {instance}")
    base = site_url(config).rstrip("/")
    try:
        with urlopen(base + path, timeout=30, context=ssl._create_unverified_context()) as response:
            response.read()
    except OSError as exc:
        die(f"Query Monitor Herd capture request failed: {exc}")


def _qm_record_for_capture(path: Path, offset: int, capture_id: str) -> dict | None:
    """Return this invocation's tagged JSONL record, never a stale last line."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as records:
        records.seek(offset)
        for line in records:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("capture_id") == capture_id:
                return payload
    return None


def _qm_filter_collectors(payload: dict, requested: str | None) -> dict:
    """Return only requested collectors; default remains the no-hooks safe set."""
    result = dict(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        return result
    if requested:
        names = {name.strip() for name in requested.split(",") if name.strip()}
        result["data"] = {name: value for name, value in data.items() if name in names}
    else:
        result["data"] = {name: value for name, value in data.items() if name != "hooks"}
    return result


register({
    'xdebug': cmd_xdebug,
    'dump': cmd_dump,
    'qm': cmd_qm,
    'introspect': cmd_introspect,
    'test': cmd_test,
    'selftest': cmd_selftest,
})

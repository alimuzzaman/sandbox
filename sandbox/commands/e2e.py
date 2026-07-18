from __future__ import annotations
import json
import hashlib
import os
import shlex
import subprocess
from pathlib import Path



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register


# Reserved ephemeral label prefix for e2e worker instances — never collides
# with a user's durable labels (default/qa/php81/...) and lets `./sb
# instances` hide these by default (docs/ci-e2e-runner-spec.md §2.2).
_E2E_LABEL_PREFIX = "e2e-w"


_PLAYWRIGHT_CONFIG_NAMES = ("playwright.config.ts", "playwright.config.js",
                           "playwright.config.mjs", "playwright.config.cjs")
_PLAYWRIGHT_CONFIG_DIRS = (".", "tests", "test", "e2e", "tests/e2e")

# The descriptor filename + shape a real observed convention writes/reads
# (Templately's scripts/lib/sandbox.js + tests/e2e/utils/wp-env-url.js): a
# SINGLE shared file at the project root, read once when Playwright's config
# module loads. Detected (not assumed) — see _detect_wp_env_port_convention.
_WP_ENV_PORT_FILE = ".wp-env-port"


def _find_playwright_config(root: Path, explicit: str | None = None) -> Path | None:
    if explicit:
        cand = (root / explicit) if not Path(explicit).is_absolute() else Path(explicit)
        return cand if cand.is_file() else None
    for d in _PLAYWRIGHT_CONFIG_DIRS:
        for name in _PLAYWRIGHT_CONFIG_NAMES:
            cand = root / d / name
            if cand.is_file():
                return cand
    return None


def _detect_wp_env_port_convention(config_path: Path) -> bool:
    """True if the project's OWN playwright config appears to read a single
    shared `.wp-env-port` descriptor for baseURL (observed convention: a
    `scripts/lib/sandbox.js` + `tests/e2e/utils/wp-env-url.js` pair that reads
    `{baseUrl, loginUrl, runtime, instance}` from a repo-root file written by
    `sb ensure --json`). Such a config is NOT worker-aware — every worker
    would share and race the same file — so this gates the workers>1 warning
    in cmd_e2e. Detected by grepping the config for the literal marker
    strings, never assumed."""
    try:
        text = config_path.read_text(errors="replace")
    except OSError:
        return False
    return "wp-env-port" in text or "wp-env-url" in text


def _write_wp_env_port(root: Path, entry: dict) -> None:
    """Write the `.wp-env-port` descriptor a detected project convention
    reads, in its exact observed shape, so `./sb e2e` works against such a
    project without editing the project's own test harness."""
    (root / _WP_ENV_PORT_FILE).write_text(json.dumps({
        "baseUrl": entry.get("url") or f"http://localhost:{entry.get('wordpress_port')}",
        "loginUrl": entry.get("login_url") or "",
        "runtime": "sandbox",
        "instance": entry.get("instance"),
    }, indent=2) + "\n")


def _run_shard(entry: dict, spec: dict, root: Path, config_path: Path | None,
              grep: str | None, extra_args: list[str], timeout: int,
              write_wp_env_port: bool) -> dict:
    """Run one Playwright shard against ITS OWN fresh instance.

    Sets the conventional baseURL env vars a project's playwright.config MAY
    read (docs/ci-e2e-runner-spec.md §2.3) — there is no single universal
    Playwright env var for this, so we set several common names. If this
    project's config was detected reading the `.wp-env-port` descriptor
    convention instead, that file is ALSO written (see
    _write_wp_env_port) — belt-and-suspenders, since we can't know in
    advance which mechanism a given project's config actually uses."""
    idx = spec["index"]
    total = spec["total"]
    url = entry.get("url") or f"http://localhost:{entry.get('wordpress_port')}"
    if write_wp_env_port:
        _write_wp_env_port(root, entry)
    env = dict(os.environ)
    env.update({
        "SANDBOX_E2E_BASE_URL": url,
        "WP_BASE_URL": url,
        "BASE_URL": url,
        "PLAYWRIGHT_TEST_BASE_URL": url,
    })
    cmd = ["npx", "playwright", "test", f"--shard={idx + 1}/{total}"]
    if config_path is not None:
        cmd.append(f"--config={config_path}")
    if grep:
        cmd += ["--grep", grep]
    cmd += extra_args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(root), env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": f"timed out after {timeout}s",
                "url": url, "cmd": " ".join(cmd)}
    except FileNotFoundError:
        return {"status": "failed",
                "error": "`npx` not found — Node/npm must be available to run "
                         "Playwright (install Node, or run this from within "
                         "the project's own toolchain).",
                "url": url}
    out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    return {
        "status": "passed" if res.returncode == 0 else "failed",
        "exit_code": res.returncode,
        "url": url,
        "cmd": " ".join(cmd),
        "output": out[-4000:],
    }


def cmd_e2e(cfg, args) -> None:
    """`./sb e2e --project-dir DIR [--workers N] [--concurrency N] [--grep P]
    [--playwright-config PATH] [--keep-on-fail] [--strict-provision] [--json]
    [-- <playwright args>]` — run Playwright e2e tests with N workers, EACH
    AGAINST ITS OWN FRESH WordPress instance (multi-instance-per-root),
    instead of N workers sharing one site's state. See
    docs/ci-e2e-runner-spec.md §2.

    Each worker is Playwright's own `--shard=i/N` process, run concurrently
    (capped — see --concurrency), targeting a dedicated ephemeral instance
    labelled `e2e-w<i>`. Instances are torn down after the run unless
    --keep-on-fail is passed AND that shard failed.

    Requires the target project to have a Playwright config — searched at the
    project root and common subdirs (tests/, test/, e2e/, tests/e2e/), or pass
    --playwright-config for a nonstandard location. Its `use.baseURL` should
    read one of SANDBOX_E2E_BASE_URL / WP_BASE_URL / BASE_URL /
    PLAYWRIGHT_TEST_BASE_URL from the environment (all four are set per
    shard). If the config is instead detected reading a shared `.wp-env-port`
    descriptor file (an observed convention — see
    _detect_wp_env_port_convention), that file is ALSO written; since that
    convention is a SINGLE shared file (not worker-aware), workers>1 against
    such a project is clamped to 1 with a clear message rather than racing.
    """
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    root = pconf["root"]
    root_path = Path(root)

    config_path = _find_playwright_config(root_path,
                                          getattr(args, "playwright_config", None))
    if config_path is None:
        die(f"no playwright config found under {root} (checked root, tests/, "
            f"test/, e2e/, tests/e2e/) — pass --playwright-config, or e2e "
            f"requires an existing Playwright setup in the project.")

    from sandbox.application.context import durable_job_dependencies
    from sandbox.application.target_service import TargetResolutionError
    from sandbox.jobs.models import JobSubmission, SourceIdentity, TargetRequest
    try:
        target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
            project_dir=root, local=bool(getattr(args, "local", False)),
            remote=getattr(args, "remote", None), workspace=getattr(args, "workspace", None),
            required_capability="job.exec" if getattr(args, "remote", None) else None,
        ))
    except TargetResolutionError as exc:
        die(f"{exc.code}: {exc}")
    if target.kind == "remote":
        timeout = int(getattr(args, "timeout", None) or 900)
        if timeout <= 0:
            die("E2E timeout must be a positive finite number of seconds")
        relative_config = os.path.relpath(config_path.resolve(), root_path.resolve())
        command = ["sb", "e2e", "--local", "--project-dir", ".",
                   "--workers", str(max(1, int(getattr(args, "workers", None) or 2))),
                   "--timeout", str(timeout), "--json"]
        if relative_config != ".": command += ["--playwright-config", relative_config]
        if getattr(args, "concurrency", None): command += ["--concurrency", str(args.concurrency)]
        if getattr(args, "grep", None): command += ["--grep", args.grep]
        if getattr(args, "keep_on_fail", False): command.append("--keep-on-fail")
        if getattr(args, "strict_provision", False): command.append("--strict-provision")
        passthrough = [item for item in (getattr(args, "passthrough", None) or ()) if item != "--"]
        if passthrough: command += ["--", *passthrough]
        identity = hashlib.sha256(target.project_root.encode()).hexdigest()
        submission = JobSubmission(
            "e2e", target.project_root, identity, "remote", target.workspace_label,
            tuple(command), timeout, SourceIdentity("sha256:" + identity),
            remote_name=target.remote_name, output_profile="smart", deadline_source="explicit",
        )
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        try:
            accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote).submit(submission)
        except Exception as exc:
            die(f"remote E2E acceptance failed: {exc}")
        if getattr(args, "json", False): print(json.dumps(accepted, sort_keys=True))
        else: print(accepted["job_id"])
        return

    workers = max(1, int(getattr(args, "workers", None) or 2))
    write_wp_env_port = _detect_wp_env_port_convention(config_path)
    as_json = bool(getattr(args, "json", False))

    if getattr(args, "run_async", False):
        # Detached run (sandbox/core/_asyncjobs.py) — NOT the same model as
        # commands/jobs.py's wp_cli_async (one command in one container); e2e
        # mints multiple instances itself. Re-invoke ourselves without
        # --async so the actual work happens in the detached process.
        argv = [str(ROOT / "sb"), "e2e", "--project-dir", root,
               "--workers", str(workers), "--json"]
        if getattr(args, "concurrency", None):
            argv += ["--concurrency", str(args.concurrency)]
        if getattr(args, "playwright_config", None):
            argv += ["--playwright-config", args.playwright_config]
        if getattr(args, "grep", None):
            argv += ["--grep", args.grep]
        if getattr(args, "keep_on_fail", False):
            argv.append("--keep-on-fail")
        if getattr(args, "strict_provision", False):
            argv.append("--strict-provision")
        argv += ["--timeout", str(getattr(args, "timeout", None) or 900)]
        passthrough = [a for a in (getattr(args, "passthrough", None) or []) if a != "--"]
        if passthrough:
            argv += ["--"] + passthrough
        jid = launch_background_job(argv, cwd=ROOT)
        print(json.dumps({"ok": True, "job_id": jid}))
        return

    if write_wp_env_port and workers > 1:
        msg = (f"{config_path.name} reads a single shared .wp-env-port "
               f"descriptor (not worker-aware) — {workers} concurrent "
               f"workers would race that one file. Clamping to --workers 1 "
               f"(still gets a genuinely fresh, isolated instance). For true "
               f"N-way parallelism, that project's baseURL resolver needs to "
               f"become worker-index-aware.")
        if not as_json:
            info(msg)
        workers = 1

    concurrency = getattr(args, "concurrency", None)
    keep_on_fail = bool(getattr(args, "keep_on_fail", False))
    strict_provision = bool(getattr(args, "strict_provision", False))
    grep = getattr(args, "grep", None)
    extra_args = [a for a in (getattr(args, "passthrough", None) or []) if a != "--"]
    timeout = int(getattr(args, "timeout", 900) or 900)

    specs = [{"label": f"{_E2E_LABEL_PREFIX}{i}", "index": i, "total": workers}
             for i in range(workers)]

    if not as_json:
        info(f"e2e: {workers} worker(s), concurrency cap "
             f"{concurrency or '(auto)'}, each on its own fresh instance…")

    def _progress(event):
        if as_json:
            return
        phase, label = event.get("phase"), event.get("label")
        if phase == "provision":
            info(f"  [{label}] provisioning fresh instance…")
        elif phase == "provision_failed":
            info(f"  [{label}] provision FAILED: {event.get('error')}")
        elif phase == "run":
            info(f"  [{label}] running playwright shard…")
        elif phase == "done":
            ok(f"  [{label}] {event.get('status')}")

    result = run_across_instances(
        cfg, root, specs,
        worker_fn=lambda entry, spec: _run_shard(entry, spec, root_path,
                                                 config_path, grep, extra_args,
                                                 timeout, write_wp_env_port),
        concurrency=concurrency, keep_on_fail=keep_on_fail,
        strict_provision=strict_provision, on_progress=_progress)

    by_worker = []
    passed = failed = 0
    for u in result["units"]:
        st = u["status"]
        if st == "passed":
            passed += 1
        else:
            failed += 1
        by_worker.append({
            "label": u["label"],
            "url": (u.get("provision") or {}).get("url"),
            "status": st,
            "error": u.get("error"),
            "exit_code": (u.get("result") or {}).get("exit_code"),
        })
    report = {"ok": result["ok"], "workers": workers,
              "concurrency": result["concurrency"],
              "passed": passed, "failed": failed, "by_worker": by_worker}

    if as_json:
        print(json.dumps(report))
        if not report["ok"]:
            import sys as _sys
            _sys.exit(1)
    else:
        print()
        for w in by_worker:
            mark = "✓" if w["status"] == "passed" else "✗"
            print(f"  {mark} {w['label']:<10} {w['status']:<16} {w.get('url') or ''}")
        print()
        if report["ok"]:
            ok(f"e2e: {passed}/{workers} worker(s) passed")
        else:
            die(f"e2e: {failed}/{workers} worker(s) failed")


register({'e2e': cmd_e2e})

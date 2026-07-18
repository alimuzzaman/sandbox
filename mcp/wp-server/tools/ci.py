from __future__ import annotations
import json
import subprocess

from app import SANDBOX_ROOT, _require_project_capability, _safe_json, mcp


@mcp.tool()
def ci_plan(workflow: str) -> dict:
    """Parse + classify a GitHub Actions workflow file WITHOUT executing
    anything — always safe. Shows every job's matrix cells, classifies each
    step (known action / run / deploy-class / unknown), and lists the secrets
    the workflow references. See docs/ci-e2e-runner-spec.md §3.

    workflow: path to a .github/workflows/*.yml file.

    Returns {workflow, name, on, jobs:[{id, cells:[{matrix}], steps:[{name,
    kind, would_run, secrets_needed}]}], secrets_needed}.
    """
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "ci", "plan", workflow, "--json"],
            capture_output=True, text=True, timeout=60, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ci_plan timed out after 60s"}
    lines = (res.stdout or "").strip().splitlines()
    plan = _safe_json(lines[-1]) if lines else None
    if isinstance(plan, dict) and "jobs" in plan:
        plan.setdefault("ok", True)
        return plan
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "ci plan failed").strip()[:2000]}


@mcp.tool()
def ci_run(project_dir: str, workflow: str, jobs: list[str] | None = None,
          matrix_filter: dict | None = None, if_event: str | None = None,
          label_prefix: str | None = None,
          concurrency: int | None = None, allow_deploy: bool = False,
          keep_on_fail: bool = False, strict_provision: bool = False,
          timeout: int = 900, async_: bool = False, local: bool = False,
          remote: str | None = None, workspace: str = "ci",
          accepted_differences: list[str] | None = None,
          output_profile: str = "smart") -> dict:
    """Execute a GitHub Actions workflow locally via `act` or durably on a
    provisioned remote using isolated retained-log child jobs.
    (full GitHub-Actions-equivalent fidelity: matrix, if:, needs, services:,
    composite/reusable actions) — one matrix cell per concurrent ephemeral
    sandbox instance (capped). See docs/ci-e2e-runner-spec.md §3.

    SAFETY: `act` itself has no concept of "don't actually deploy" — this
    tool neutralizes deploy/publish-class steps (e.g. the 10up WordPress.org
    SVN deploy actions, or anything matching a deploy/release/publish/push
    keyword heuristic) into no-op stubs in a patched COPY of the workflow
    BEFORE act ever sees it, SKIPPED BY DEFAULT (they only print what they
    would have done). Pass allow_deploy=true to attempt them for real (still
    requires every referenced `${{ secrets.X }}` to resolve from
    sandbox.local.yml's `ci_secrets:` block or $SANDBOX_CI_SECRET_X — an
    unresolved secret aborts the run BEFORE anything executes, never
    silently interpolates empty). Every `run:` step is also scanned for raw
    `git push`/`gh release create`/`gh pr merge`/`svn commit`.

    project_dir: the plugin project to run CI against.
    workflow: path to the .github/workflows/*.yml file.
    jobs: only run these job ids (default: all jobs in the file).
    matrix_filter: only run matrix cells matching these key=value pairs.
    if_event: only run if the workflow's `on:` triggers mention this event
      (e.g. "push"); otherwise returns {ok:true, skipped:true} and runs nothing.
    allow_deploy: actually attempt deploy-class steps (see SAFETY above).
    keep_on_fail: preserve a failed cell's instance for inspection.
    strict_provision: abort the whole run if any cell's instance fails to boot.
    async_: run detached and return {ok, job_id} immediately instead of
      blocking — poll with async_job_status(job_id), cancel with
      async_job_kill(job_id). Use for long matrix runs so the conversation
      isn't blocked; the job survives even if this MCP call itself times out.

    Each matrix cell's instance is provisioned with that cell's requested
    PHP/WP version when the workflow specifies one (matrix key or a
    `setup-php` step's `with.php-version`) — overriding the project's own
    sandbox.config.json for that instance only ("CI takes priority over
    sandbox.config"). Its URL is exposed to the act job container via
    `host.docker.internal` for workflows that test against a live WordPress
    site; workflows that don't reference it (classic self-contained
    phpunit-with-services: CI) simply ignore it.

    Returns {ok, workflow, run_id, jobs, cells:[{label, matrix, status,
    exit_code, url, warning, output, error}], neutralized:[...],
    summary:{cells, passed, failed}} — or {ok, job_id} when async_=true.
    """
    capability_error = _require_project_capability(project_dir, None, "wordpress.cli")
    if capability_error:
        return capability_error
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "ci", "run", workflow, "--project-dir", project_dir, "--json"]
    if local:
        cmd.append("--local")
    elif remote:
        cmd += ["--remote", remote]
    if workspace:
        cmd += ["--workspace", workspace]
    for j in (jobs or []):
        cmd += ["--job", j]
    for k, v in (matrix_filter or {}).items():
        cmd += ["--matrix-filter", f"{k}={v}"]
    if if_event:
        cmd += ["--if-event", if_event]
    if label_prefix:
        cmd += ["--label-prefix", label_prefix]
    if concurrency:
        cmd += ["--concurrency", str(concurrency)]
    if allow_deploy:
        cmd.append("--allow-deploy")
    if keep_on_fail:
        cmd.append("--keep-on-fail")
    if strict_provision:
        cmd.append("--strict-provision")
    if timeout:
        cmd += ["--timeout", str(timeout)]
    if output_profile:
        cmd += ["--output-profile", output_profile]
    for difference in (accepted_differences or []):
        cmd += ["--accept-difference", difference]
    if async_:
        cmd.append("--async")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=60, cwd=str(SANDBOX_ROOT))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ci_run --async launch timed out after 60s"}
        lines = (res.stdout or "").strip().splitlines()
        launched = _safe_json(lines[-1]) if lines else None
        if isinstance(launched, dict) and "job_id" in launched:
            return launched
        return {"ok": False, "code": res.returncode,
                "error": (res.stderr or res.stdout or "async launch failed").strip()[:2000]}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 300, cwd=str(SANDBOX_ROOT))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ci_run timed out after {timeout + 300}s"}
    lines = (res.stdout or "").strip().splitlines()
    report = _safe_json(lines[-1]) if lines else None
    if isinstance(report, dict) and report.get("skipped"):
        return report  # --if-event didn't match; nothing ran, not an error.
    if isinstance(report, dict) and "cells" in report:
        return report
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "ci run failed").strip()[:2000]}

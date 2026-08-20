from __future__ import annotations
import copy
import hashlib
import itertools
import json
import os
import re
import secrets as _secretsmod
import shutil
import subprocess
import tempfile
from pathlib import Path



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register


# See docs/ci-e2e-runner-spec.md §3 for the full design this module implements.
#
# EXECUTION ENGINE: real `act` (nektos/act) runs the actual workflow — full
# GitHub-Actions-equivalent fidelity (matrix, if:, needs, services:, composite
# and reusable actions, arbitrary real third-party actions), rather than a
# bespoke step interpreter that could only handle a small known-actions table.
# `act` was chosen over building our own interpreter because it already solves
# this exact problem correctly and is widely used for it; validated live
# (host.docker.internal reaches a booted sandbox instance from an act job
# container).
#
# Our own lightweight YAML parsing below is kept for exactly three things act
# doesn't do for us: (1) `ci plan` classification/listing (safe, no execution,
# no docker), (2) matrix-cell -> sandbox-instance mapping (php/wp version
# override, per-label config), (3) a SAFETY DENY-LIST pass that neutralizes
# deploy/publish-class steps into no-op stubs in a temp copy of the workflow
# BEFORE handing it to `act`, unless --allow-deploy is passed — `act` itself
# has no such gate and would happily execute a real WordPress.org SVN deploy
# if given real secrets, so this module must supply that gate itself.

_EXPR_RE = re.compile(r"\$\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")
_DANGEROUS_RUN_RE = re.compile(
    r"^\s*(git\s+push|gh\s+pr\s+merge|gh\s+release\s+create|svn\s+commit)\b",
    re.MULTILINE)

_KNOWN_ACTIONS = {
    # prefix (before @version) -> (kind, human label). Used by `ci plan` for
    # display only now — act executes all of these for real at run time.
    "actions/checkout": ("known", "checkout"),
    "shivammathur/setup-php": ("known", "setup-php"),
    "actions/setup-node": ("known", "setup-node"),
    "actions/cache": ("known", "cache"),
    "actions/upload-artifact": ("known", "upload-artifact"),
    "actions/download-artifact": ("known", "download-artifact"),
    "10up/action-wordpress-plugin-deploy": ("deploy", "wordpress-plugin-deploy"),
    "10up/action-wordpress-plugin-asset-update": ("deploy", "wordpress-plugin-asset-update"),
}

# The safety deny-list: explicit known-dangerous action prefixes, PLUS a
# keyword heuristic so an action we've never seen before that clearly LOOKS
# like a publish/deploy step is still caught (never rely on an allow-list of
# "safe" actions now that act runs arbitrary third-party actions for real).
# Deliberately excludes words like "upload" (actions/upload-artifact is common
# and safe) — narrow enough to avoid false-positiving on ordinary CI actions.
_DENY_LIST_PREFIXES = (
    "10up/action-wordpress-plugin-deploy",
    "10up/action-wordpress-plugin-asset-update",
)
_DENY_LIST_KEYWORDS = ("deploy", "release", "publish", "gh-pages", "svn", "push")

# Common `runs-on:` platform labels mapped to the already-pulled runner image
# so `act` doesn't try to pull a fresh one every invocation.
_ACT_PLATFORM_MAP = {
    "ubuntu-latest": "catthehacker/ubuntu:act-22.04",
    "ubuntu-22.04": "catthehacker/ubuntu:act-22.04",
}


def _load_workflow(path: Path) -> dict:
    sc = _core()
    ensure_pyyaml()  # back-filled from sandbox.core._config
    import yaml
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        raise sc.ConfigError(f"failed to parse {path}: {e}")
    if not isinstance(data, dict):
        raise sc.ConfigError(f"{path}: not a GitHub Actions workflow (expected a mapping)")
    # YAML 1.1's implicit-boolean resolution parses the bare `on:` trigger key
    # to the boolean True, not the string "on" — a well-known GH Actions YAML
    # gotcha. Normalize it back so `on:` reads naturally everywhere else.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _expand_matrix(strategy: dict | None) -> list[dict]:
    """Expand `jobs.<id>.strategy.matrix` into a list of cell dicts.

    Supports both the QM/real-world `matrix.include:`-only shape (a literal
    list of cells, no separate axes) and a cartesian `matrix: {k: [...]}`
    shape with `exclude:` honored. `include` entries alongside cartesian axes
    are merged into any cell matching all of the include entry's OTHER keys,
    else appended as their own standalone cell (approximates, doesn't fully
    replicate, GitHub's own include-merge semantics — see
    docs/ci-e2e-runner-spec.md §7 reusable-workflow/matrix caveats)."""
    if not strategy or not strategy.get("matrix"):
        return [{}]
    matrix = strategy["matrix"]
    include = matrix.get("include") or []
    exclude = matrix.get("exclude") or []
    axes = {k: v for k, v in matrix.items() if k not in ("include", "exclude")}

    if not axes:
        # QM's real shape: matrix.include IS the cell list.
        return list(include) if include else [{}]

    cells = []
    keys = list(axes.keys())
    for combo in itertools.product(*(axes[k] for k in keys)):
        cell = dict(zip(keys, combo))
        if any(all(cell.get(k) == v for k, v in ex.items()) for ex in exclude):
            continue
        cells.append(cell)
    for inc in include:
        matched = False
        for cell in cells:
            other_keys = [k for k in inc if k not in keys]
            same_keys = [k for k in inc if k in keys]
            if same_keys and all(cell.get(k) == inc[k] for k in same_keys):
                cell.update({k: inc[k] for k in other_keys})
                matched = True
        if not matched:
            cells.append(dict(inc))
    return cells or [{}]


def _cell_slug(cell: dict) -> str | None:
    """A STABLE, run-independent identifier for a matrix cell — e.g. '68-84'
    for {wp:'6.8', php:'8.4'} — the same across every `ci run` of the same
    cell. Used as the per-label sandbox.config lookup key (via
    ensure_instance's `config_label`), since the actual ephemeral instance
    LABEL is randomized per run (see _cell_label) and a user could never
    pre-author a config file matching a random label."""
    if not cell:
        return None
    slug = "-".join(re.sub(r"[^a-z0-9]+", "", str(v).lower()) for v in cell.values())
    return slug[:16] or None


def _cell_label(label_prefix: str, run_id: str, job_id: str, cell: dict) -> str:
    """Instance label for one (job, matrix-cell) pair within a `ci run` — MUST
    be unique across every job x cell combination, or two jobs race
    `ensure_instance(..., create=True)` for the identical instance NAME.

    Real bug this fixes, found live: a workflow with two matrix-less jobs
    (e.g. a reusable-workflow caller job + a plain job) both produce ONE cell
    with an empty `{}` matrix — `_cell_slug({})` is None for both, so the old
    version (keyed on run_id + cell only, no job_id) handed BOTH jobs the
    EXACT SAME label. `run_across_instances` then launched two concurrent
    `ensure_instance` calls for the same instance name: one thread's
    fresh-install `wp core install` raced the other's, and the results dict
    (keyed by label) silently dropped one unit's result entirely.

    Labels are capped at 21 chars (`^[a-z0-9][a-z0-9_-]{0,20}$`), too tight to
    just concatenate prefix + run_id + job_id + cell slug for realistic job
    names (e.g. "integration-tests") — hash (job_id, cell) into a short
    suffix instead of guessing a length budget that breaks on longer names."""
    import hashlib
    digest = hashlib.sha1(f"{job_id}\x00{sorted(cell.items())}".encode()).hexdigest()[:5]
    # A remote matrix workspace is an isolated child, but a caller-provided
    # workspace remains its visible namespace.  Preserve as much of that
    # prefix as the 21-character label contract allows rather than silently
    # replacing it with the historic generic ``ci`` prefix.
    prefix_budget = max(1, 21 - len(run_id) - len(digest) - 2)
    prefix = re.sub(r"[^a-z0-9-]", "", (label_prefix or "ci").lower())[:prefix_budget] or "ci"
    return f"{prefix}-{run_id}-{digest}"


def _classify_step(step: dict) -> dict:
    """Classify one step: kind in {known, deploy, run, unknown}, plus the
    secrets it references (never resolved here — classification is pure and
    side-effect-free, safe to run in `ci plan`)."""
    name = step.get("name") or step.get("uses") or step.get("run", "")[:40] or "(unnamed step)"
    text = json.dumps(step)
    secrets_needed = sorted(set(_EXPR_RE.findall(text)))
    secrets_needed = [s[len("secrets."):] for s in secrets_needed if s.startswith("secrets.")]

    if step.get("uses"):
        ref = step["uses"]
        prefix = ref.split("@", 1)[0]
        kind, action = _KNOWN_ACTIONS.get(prefix, ("unknown", prefix))
        would_run = {
            "checkout": "using local worktree (already checked out)",
            "setup-php": f"instance PHP pin (requested {step.get('with', {}).get('php-version')})",
            "setup-node": f"host Node (requested {step.get('with', {}).get('node-version') or step.get('with', {}).get('node-version-file')})",
            "cache": "no-op (local runs reuse the persistent instance filesystem)",
            "upload-artifact": f"copy to tmp/ci-artifacts (path={step.get('with', {}).get('path')})",
            "download-artifact": f"copy from tmp/ci-artifacts (name={step.get('with', {}).get('name')})",
            "wordpress-plugin-deploy": "push to WordPress.org SVN (plugin release)",
            "wordpress-plugin-asset-update": "push readme/assets to WordPress.org SVN",
        }.get(action, f"unknown action '{ref}' — not executed locally")
        return {"name": name, "kind": kind, "uses": ref, "with": step.get("with"),
                "env": step.get("env"),
                "would_run": would_run, "secrets_needed": secrets_needed}
    if step.get("run"):
        return {"name": name, "kind": "run", "run": step["run"],
                "env": step.get("env"),
                "would_run": "executed on host in the plugin dir",
                "secrets_needed": secrets_needed}
    return {"name": name, "kind": "unknown", "env": step.get("env"),
            "would_run": "no `uses:` or `run:` — skipped",
            "secrets_needed": secrets_needed}


def _plan_workflow(path: Path) -> dict:
    data = _load_workflow(path)
    jobs_out = []
    all_secrets = set()
    for job_id, job in (data.get("jobs") or {}).items():
        cells = _expand_matrix(job.get("strategy"))
        steps = [_classify_step(s) for s in (job.get("steps") or [])]
        for s in steps:
            all_secrets.update(s.get("secrets_needed") or [])
        jobs_out.append({
            "id": job_id, "needs": job.get("needs"), "env": job.get("env"),
            "fail_fast": bool((job.get("strategy") or {}).get("fail-fast", True)),
            "continue_on_error": bool(job.get("continue-on-error", False)),
            "cells": [{"matrix": c} for c in cells],
            "steps": steps,
        })
    return {
        "workflow": str(path), "name": data.get("name"), "on": data.get("on"),
        "env": data.get("env"),
        "jobs": jobs_out, "secrets_needed": sorted(all_secrets),
    }


def _resolve_secret(name: str, local_cfg: dict) -> str | None:
    ci_secrets = (local_cfg.get("ci_secrets") or {})
    # The mapping is an explicit allowlist by default. Environment-backed
    # values require an equally explicit list, so a workflow cannot forward an
    # arbitrary ambient SANDBOX_CI_SECRET_* value to a third-party action.
    configured = local_cfg.get("ci_secret_allowlist")
    if configured is None:
        allowed = set(ci_secrets) if isinstance(ci_secrets, dict) else set()
    elif isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        allowed = set(configured)
    else:
        return None
    if name not in allowed:
        return None
    if name in ci_secrets:
        return str(ci_secrets[name])
    env_key = f"SANDBOX_CI_SECRET_{name}"
    if env_key in os.environ:
        return os.environ[env_key]
    return None


def _merge_env(*layers: dict | None) -> dict:
    """GitHub Actions env precedence: later (more specific) layers win — step
    overrides job overrides workflow (docs.github.com/actions/learn-github-
    actions/variables). Call as `_merge_env(workflow_env, job_env, step_env)`."""
    merged: dict = {}
    for layer in layers:
        if layer:
            merged.update({str(k): str(v) for k, v in layer.items()})
    return merged


def _interpolate(text: str, matrix: dict, resolved_secrets: dict,
                 declared_env: dict | None = None) -> str:
    """Expand `${{ matrix.x }}` / `${{ secrets.X }}` / `${{ env.X }}`.

    `env.X` resolves from the workflow's OWN declared `env:` blocks
    (workflow/job/step-merged — see `_merge_env`), matching GitHub Actions'
    real `env` expression context; it does NOT read the host's ambient
    process environment (that's a separate, correct concern: ambient env is
    inherited by the `run:` subprocess directly, not through this context)."""
    declared_env = declared_env or {}
    def repl(m):
        expr = m.group(1)
        if expr.startswith("matrix."):
            return str(matrix.get(expr[len("matrix."):], ""))
        if expr.startswith("secrets."):
            key = expr[len("secrets."):]
            return str(resolved_secrets[key])  # KeyError -> caller fails loud
        if expr.startswith("env."):
            return str(declared_env.get(expr[len("env."):], ""))
        return m.group(0)
    return _EXPR_RE.sub(repl, text)


_PHP_MATRIX_KEYS = ("php", "php-version", "phpVersion", "php_version")
_WP_MATRIX_KEYS = ("wp", "wp-version", "wpVersion", "wp_version")


def _resolve_cell_versions(cell: dict, steps: list[dict]) -> tuple[str | None, str | None]:
    """Determine the PHP/WP version a matrix cell requests, so it can
    OVERRIDE the project's own sandbox.config.json for that cell's instance
    (docs/ci-e2e-runner-spec.md — "CI takes priority over sandbox.config").
    Prefers a `shivammathur/setup-php` step's interpolated `with.php-version`
    (the most explicit "what will actually run" signal) over a same-named
    matrix axis, since a project may reference the version only inside that
    step rather than naming its matrix axis literally `php`."""
    php = next((str(cell[k]) for k in _PHP_MATRIX_KEYS if cell.get(k)), None)
    wp = next((str(cell[k]) for k in _WP_MATRIX_KEYS if cell.get(k)), None)
    for step in steps:
        if step.get("kind") == "known" and \
                (step.get("uses") or "").split("@", 1)[0] == "shivammathur/setup-php":
            raw = (step.get("with") or {}).get("php-version")
            if raw is not None:
                val = _interpolate(str(raw), cell, {})
                if val and "${{" not in val:
                    php = val
    return php, wp


def _guard_dangerous_commands(script: str, allow_deploy) -> tuple[str, list[str]]:
    """Neutralize raw publish/push commands in a `run:` step unless
    --allow-deploy was passed. Returns (possibly-edited script, warnings)."""
    if allow_deploy:
        return script, []
    warnings = []
    out_lines = []
    for line in script.splitlines():
        if _DANGEROUS_RUN_RE.match(line):
            warnings.append(f"blocked: {line.strip()}")
            out_lines.append(f"# [sandbox-ci: blocked, pass --allow-deploy to run] {line}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines), warnings


def _is_deploy_class(uses_ref: str) -> bool:
    """The safety deny-list check: explicit known-dangerous prefixes, plus a
    keyword heuristic so an unfamiliar action that clearly LOOKS like a
    publish/deploy step is still caught. Deliberately NOT an allow-list —
    `act` executes any action we don't explicitly gate, for real."""
    prefix = uses_ref.split("@", 1)[0].lower()
    if prefix in _DENY_LIST_PREFIXES:
        return True
    return any(kw in prefix for kw in _DENY_LIST_KEYWORDS)


def _is_upload_artifact(uses_ref: object) -> bool:
    """Whether an action delegates artifact storage to GitHub's runner API.

    ``act`` is intentionally not a hosted GitHub runner, so this action cannot
    receive an ``ACTIONS_RUNTIME_TOKEN``.  Sandbox collects declared artifact
    paths from the completed cell workspace instead; keep the workflow step as
    an explicit local no-op rather than letting a token error fail a valid CI
    run.
    """
    return isinstance(uses_ref, str) and uses_ref.split("@", 1)[0].lower() == "actions/upload-artifact"


def _neutralize_workflow_for_safety(data: dict, allow_deploy: bool) -> tuple[dict, list[str]]:
    """Return a DEEP-COPIED, patched workflow dict safe to hand to `act`.

    Unless `allow_deploy`: every step whose `uses:` is deny-listed
    (`_is_deploy_class`) is replaced with a no-op `run: echo` stub — `act` has
    no concept of "don't actually deploy", so this module supplies that gate
    itself, BEFORE act ever sees the workflow. Every `run:` step is ALSO
    scanned for raw publish/push commands (`_guard_dangerous_commands`) as a
    second, independent net. Returns (patched_data, notes) — notes describe
    what was neutralized, for the run report."""
    patched = copy.deepcopy(data)
    notes: list[str] = []
    for job_id, job in (patched.get("jobs") or {}).items():
        steps = job.get("steps") or []
        for i, step in enumerate(steps):
            name = step.get("name") or step.get("uses") or "(unnamed step)"
            uses = step.get("uses")
            if _is_upload_artifact(uses):
                note = (f"job '{job_id}' step '{name}': upload-artifact action "
                        "replaced with Sandbox job-artifact collection")
                notes.append(note)
                steps[i] = {
                    "name": f"{name} (sandbox: collected after job)",
                    "run": 'echo "[sandbox-ci] artifact collected after job"',
                }
                continue
            if uses and not allow_deploy and _is_deploy_class(uses):
                note = (f"job '{job_id}' step '{name}': deploy-class action "
                        f"'{uses}' neutralized — pass --allow-deploy to run for real")
                notes.append(note)
                steps[i] = {
                    "name": f"{name} (sandbox: deploy-class, skipped)",
                    "run": f'echo "[sandbox-ci] skipped deploy-class step '
                           f'{json.dumps(name)} ({uses}) — pass --allow-deploy '
                           f'to run for real"',
                }
                continue
            if step.get("run"):
                guarded, warnings = _guard_dangerous_commands(step["run"], allow_deploy)
                if warnings:
                    notes.append(f"job '{job_id}' step '{name}': " + "; ".join(warnings))
                    step["run"] = guarded
    return patched, notes


def _runtime_secrets_needed(jobs: list[dict], allow_deploy: bool) -> list[str]:
    """Return secrets needed by steps that will actually reach `act`.

    Deploy-class actions are replaced with no-op stubs when deployment is not
    allowed, so their deploy-only secrets must not block the safe CI path.
    """
    needed = set()
    for job in jobs:
        for step in job.get("steps") or []:
            uses = step.get("uses")
            if uses and not allow_deploy and _is_deploy_class(uses):
                continue
            needed.update(step.get("secrets_needed") or [])
    return sorted(needed)


def _write_patched_workflow(data: dict) -> Path:
    ensure_pyyaml()
    import yaml
    fd, path = tempfile.mkstemp(prefix="sandbox-ci-", suffix=".yml")
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return Path(path)


def _write_secrets_file(resolved_secrets: dict) -> Path:
    """dotenv-style file for act's `--secret-file` — secrets NEVER touch the
    command line (visible in `ps`/shell history) or get logged; act reads
    this file directly. chmod 600, deleted by the caller after the run."""
    fd, path = tempfile.mkstemp(prefix="sandbox-ci-secrets-", suffix=".env")
    with os.fdopen(fd, "w") as f:
        for k, v in resolved_secrets.items():
            f.write(f"{k}={v}\n")
    os.chmod(path, 0o600)
    return Path(path)


def _act_binary() -> str | None:
    return shutil.which("act")


def _run_cell_with_act(entry: dict, spec: dict, root: Path,
                       patched_workflow_path: Path, secrets_path: Path,
                       allow_deploy: bool, timeout: int,
                       extra_act_args: list[str] | None = None) -> dict:
    """Execute one matrix cell's job via `act` against its own ephemeral
    sandbox instance. `act` handles the FULL step semantics (matrix, if:,
    needs, services:, composite/reusable actions) — this function's only job
    is to point it at the right instance and matrix cell.

    `entry` is the ALREADY-PROVISIONED instance record for this cell — its
    php_version/wp_version reflect whatever `_resolve_cell_versions` decided
    ("CI takes priority over sandbox.config"). The instance's URL is exposed
    to the act job container via `host.docker.internal` for workflows that
    test against a real WordPress site; workflows that don't reference it
    (classic self-contained phpunit-with-services: CI) simply ignore it —
    act runs them the same way GitHub would, independent of our instance.

    NOTE on a readiness probe that was tried and removed: an earlier version
    added a throwaway-container probe here to wait for the instance to become
    reachable via `host.docker.internal` before invoking act, on the theory
    that Docker Desktop's port-publish routing needed time to propagate to
    OTHER containers after a fresh boot. Live verification disproved that
    theory: EVERY run after the URL fix below succeeded immediately
    (hundreds of ms), with no readiness delay ever observed; the probe itself
    turned out to be unreliable in its own right (hung indefinitely under
    `--network host`, false-negatived under bridge mode) and never actually
    caught a real race in dozens of live runs. The ACTUAL root cause of the
    one early failure (`curl` `HTTP 000`) was simply the wrong URL (see
    below) — once fixed, there was nothing left to wait for. Removed rather
    than keep patching a mechanism with no evidence it ever helped."""
    cell = spec["cell"]
    job_id = spec["job_id"]
    # WordPress compatibility records use ``wordpress_port``; generic Compose
    # records expose the same host-reachable service as ``http_port``.
    port = entry.get("wordpress_port") or entry.get("http_port")
    # The instance's secured https://<name>.tst URL is a HOST-side proxy/DNS
    # convenience — it does not resolve inside an arbitrary container. This
    # WAS the actual bug behind the one observed HTTP 000 failure (mistaken,
    # at the time, for a network-propagation race — see the note above).
    url = f"http://host.docker.internal:{port}" if port else entry.get("url")
    act_bin = _act_binary()
    if not act_bin:
        return {"status": "failed",
                "error": "`act` not found — install via `brew install act` "
                         "(https://github.com/nektos/act); required for the "
                         "CI executor."}

    cmd = [act_bin, "-j", job_id, "-W", str(patched_workflow_path),
           "--directory", str(root),
           # Keep ordinary workflow output in the isolated workspace so the
           # outer durable supervisor can retain declared artifacts. Without
           # this, act copies the workspace into its runner container and a
           # successful `run:` output file disappears before collection.
           "--bind",
           "--secret-file", str(secrets_path),
           "--container-options", "--add-host=host.docker.internal:host-gateway",
           "--pull=false"]
    for k, v in cell.items():
        cmd += ["--matrix", f"{k}:{v}"]
    for platform, image in _ACT_PLATFORM_MAP.items():
        cmd += ["-P", f"{platform}={image}"]
    cmd += ["--env", f"WP_BASE_URL={url}",
            "--env", f"SANDBOX_INSTANCE={entry.get('instance')}"]
    for k, v in cell.items():
        cmd += ["--env", f"MATRIX_{str(k).upper()}={v}"]
    cmd += (extra_act_args or [])

    # ``act``'s Docker resources are not safely namespaced across independent
    # host processes.  Matrix cells retain their independent workspaces and
    # durable lifecycle, but the short act execution itself must serialize to
    # prevent one cell's cleanup from removing another cell's runner container.
    sc = _core()
    try:
        with sc.project_lock(RUNTIME_DIR / ".ci-act"):
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 cwd=str(root), timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": f"timed out after {timeout}s",
                "cmd": " ".join(cmd)}
    out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    return {
        "status": "passed" if res.returncode == 0 else "failed",
        "exit_code": res.returncode,
        "matrix": cell, "url": url,
        "cmd": " ".join(cmd),
        "output": out[-6000:],
    }


def _event_matches(on_trigger, event: str) -> bool:
    """Loose containment check for --if-event: `on:` may be a bare string, a
    list of event names, or a dict keyed by event name (with per-event
    config). No trigger simulation — just "does this workflow mention this
    event at all"."""
    if on_trigger is None:
        return False
    if isinstance(on_trigger, str):
        return on_trigger == event
    if isinstance(on_trigger, list):
        return event in on_trigger
    if isinstance(on_trigger, dict):
        return event in on_trigger
    return False


def _artifact_blocking_differences(gate: dict) -> list[str]:
    return sorted({item["id"] for item in gate.get("differences", ())
                   if item.get("id", "").startswith("sandbox.artifact-") and
                   item.get("severity") == "block" and not item.get("accepted")})


def _remote_ci_artifacts(job: dict) -> list[str]:
    """Extract only literal, project-relative upload paths for outer retention."""
    paths = []
    for step in job.get("steps") or []:
        if str(step.get("uses", "")).split("@", 1)[0] != "actions/upload-artifact":
            continue
        value = (step.get("with") or {}).get("path")
        if isinstance(value, str):
            for literal in value.splitlines():
                literal = literal.strip()
                if literal and not Path(literal).is_absolute() and ".." not in Path(literal).parts:
                    paths.append(literal)
    return sorted(set(paths))


def _remote_ci_submissions(target, root: str, wf_path: Path, plan: dict, args) -> list:
    """Build one durable explicit command per selected workflow matrix cell.

    The deployed remote invokes its existing local ``act`` adapter inside each
    child supervisor. The outer job runtime owns the deadline, retained logs,
    process identity, workspace label, and artifact boundary; no workflow
    process pipes are attached to the submitting SSH/MCP connection.
    """
    from sandbox.jobs.models import JobSubmission
    from sandbox.commands.jobs_runtime import _resolved_project_identity, _source_identity
    timeout = int(getattr(args, "timeout", 900) or 900)
    label_prefix = (getattr(args, "label_prefix", None) or
                    getattr(target, "workspace_label", None) or "ci")
    # Keep enough room for a caller-visible workspace prefix plus the
    # deterministic job/cell hash inside the strict label length cap.
    run_id = _secretsmod.token_hex(2)
    relative_workflow = os.path.relpath(wf_path.resolve(), Path(root).resolve())
    if relative_workflow.startswith(".."):
        die("remote CI workflow must be inside --project-dir")
    matrix_filter = getattr(args, "matrix_filter", None) or {}
    selected = getattr(args, "jobs", None)
    workflow_data = _load_workflow(wf_path)
    from sandbox.ci.workflow import preflight
    safe_gate = preflight(root, wf_path, selected_jobs=selected,
                          accepted_differences=getattr(args, "accepted_differences", None),
                          safe_mode=not getattr(args, "allow_deploy", False))
    safe_mode_differences = tuple({
        "id": action["id"], "accepted": True, "workflow": relative_workflow,
        "location": action["location"], "severity": "notice",
        "detail": "deployment/release/publish step was neutralized in safe mode",
        "catalog_version": safe_gate.get("catalog_version", "unknown"),
    } for action in safe_gate.get("safe_mode_actions", ())
        if action.get("action") == "neutralized")
    submissions = []
    selected_jobs = [job for job in plan["jobs"] if not selected or job["id"] in selected]
    labels_by_job = {}
    cells_by_job = {}
    for job in selected_jobs:
        matching = [item["matrix"] for item in job["cells"] if not matrix_filter or
                    all(str(item["matrix"].get(k)) == v for k, v in matrix_filter.items())]
        cells_by_job[job["id"]] = matching
        labels_by_job[job["id"]] = [_cell_label(label_prefix, run_id, job["id"], cell) for cell in matching]
    for job in selected_jobs:
        matching = cells_by_job[job["id"]]
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        dependencies = tuple(label for need in needs for label in labels_by_job.get(need, ()))
        for cell in matching:
            label = _cell_label(label_prefix, run_id, job["id"], cell)
            command = ["sb", "ci", "run", relative_workflow, "--project-dir", ".",
                       "--job", job["id"], "--label-prefix", label[:8],
                       "--timeout", str(timeout), "--json", "--local"]
            if getattr(args, "allow_deploy", False):
                command.append("--allow-deploy")
            if getattr(args, "keep_on_fail", False):
                command.append("--keep-on-fail")
            if getattr(args, "strict_provision", False):
                command.append("--strict-provision")
            for key, value in sorted(cell.items()):
                command += ["--matrix-filter", f"{key}={value}"]
            for difference in getattr(args, "accepted_differences", None) or ():
                command += ["--accept-difference", difference]
            accepted_compatibility = tuple({"id": value, "accepted": True,
                    "workflow": relative_workflow, "location": "workflow", "severity": "accepted",
                    "detail": "caller accepted compatibility difference"} for value in
                    (getattr(args, "accepted_differences", None) or ()))
            submissions.append(JobSubmission(
                kind="ci", project_root=target.project_root,
                project_identity=_resolved_project_identity(target),
                target_kind="remote", remote_name=target.remote_name,
                workspace_label=label, workspace_mode="isolated", argv=tuple(command),
                deadline_seconds=timeout, source=_source_identity(target.project_root),
                output_profile=getattr(args, "output_profile", "smart"),
                deadline_source="explicit", artifact_paths=tuple(_remote_ci_artifacts(
                    workflow_data.get("jobs", {}).get(job["id"], {}))),
                depends_on=dependencies,
                failure_policy="continue" if (job.get("continue_on_error") or not job.get("fail_fast", True))
                else "fail-fast",
                compatibility_differences=accepted_compatibility + safe_mode_differences,
            ))
    if not submissions:
        die("no matrix cells matched --matrix-filter")
    return submissions


def _run_remote_ci(target, root: str, wf_path: Path, plan: dict, args, *, as_json: bool) -> None:
    from sandbox.core import _remote
    from sandbox.transports.remote_jobs import RemoteJobTransport
    submissions = _remote_ci_submissions(target, root, wf_path, plan, args)
    result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
        ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
        remote_sb_path=_remote.remote_sb_path).submit_many(submissions)
    report = {"ok": True, "kind": "ci", "workflow": str(wf_path),
              "target": {"kind": target.kind, "remote": target.remote_name,
                         "workspace": target.workspace_label},
              "parent_job_id": result.get("parent_job_id"),
              "children": result.get("children", []),
              "summary": result.get("summary", {"submitted": len(submissions)}),
              "source": result.get("source"),
              "observation": "use job-status/job-output with parent_job_id; child logs remain retained remotely"}
    if as_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"remote CI accepted: {report['parent_job_id']}")
        for child in report["children"]:
            print(f"  {child['job_id']} {child.get('workspace', '')}")


def cmd_ci(cfg, args) -> None:
    """`./sb ci plan <workflow.yml>` / `./sb ci run <workflow.yml> --project-dir DIR
    [--job ID ...] [--matrix-filter k=v ...] [--if-event NAME] [--label-prefix P]
    [--concurrency N] [--allow-deploy] [--list-secrets] [--keep-on-fail]
    [--strict-provision] [--async] [--json]` — run a GitHub Actions workflow's
    job(s) locally via `act` (full GH-Actions-equivalent fidelity), fanned out
    one matrix cell per concurrent ephemeral sandbox instance (see
    docs/ci-e2e-runner-spec.md §3). `plan` parses + classifies only — always
    safe, no execution. `run` actually executes: deploy/publish-shaped steps
    are neutralized into no-op stubs UNLESS --allow-deploy (act itself has no
    such gate — this module supplies it), and any `${{ secrets.* }}` the
    workflow references must be explicitly allowlisted in sandbox.local.yml's
    `ci_secrets:` mapping (or `ci_secret_allowlist` for an environment-backed
    `$SANDBOX_CI_SECRET_*`) before anything runs, or the run aborts loud.
    `--async` runs detached (sandbox/core/_asyncjobs.py) — poll with
    `./sb async-job <job_id>`.
    """
    sc = _core()
    action = args.action
    wf_path = Path(args.workflow).expanduser()
    if not wf_path.is_absolute():
        wf_path = (Path.cwd() / wf_path).resolve()
    if not wf_path.is_file():
        die(f"workflow file not found: {wf_path}")

    if action == "preflight":
        from sandbox.ci.workflow import WorkflowError, preflight
        project_root = getattr(args, "project_dir", None) or str(wf_path.parent.parent.parent if ".github" in wf_path.parts else Path.cwd())
        try:
            result = preflight(project_root, wf_path, selected_jobs=getattr(args, "jobs", None),
                accepted_differences=getattr(args, "accepted_differences", None), safe_mode=not getattr(args, "allow_deploy", False))
        except WorkflowError as exc:
            die(str(exc))
        result["target"] = {"kind": "remote" if getattr(args, "remote", None) else "local",
                            "remote": getattr(args, "remote", None), "workspace": getattr(args, "workspace", "ci")}
        if getattr(args, "json", False): print(json.dumps(result))
        else:
            print(f"preflight: {'compatible' if result['ok'] else 'blocked'}")
            for item in result["differences"]: print(f"  {item['severity']}: {item['id']} at {item['location']}")
            for item in result["safe_mode_actions"]: print(f"  safe mode: {item['location']} {item['action']}")
        if not result["ok"]:
            import sys; sys.exit(1)
        return

    try:
        plan = _plan_workflow(wf_path)
    except sc.ConfigError as e:
        die(str(e))

    if action == "plan" or getattr(args, "dry_run", False):
        if getattr(args, "json", False):
            print(json.dumps(plan))
        else:
            print(f"workflow: {plan['workflow']}  ({plan.get('name') or '(unnamed)'})")
            for job in plan["jobs"]:
                print(f"\n  job: {job['id']}  ({len(job['cells'])} cell(s))")
                for cell in job["cells"]:
                    m = cell["matrix"]
                    print(f"    cell: {m or '(no matrix)'}")
                for step in job["steps"]:
                    print(f"    - [{step['kind']:<7}] {step['name']}  -> {step['would_run']}")
            if plan["secrets_needed"]:
                print(f"\n  secrets needed: {', '.join(plan['secrets_needed'])}")
        return

    as_json = bool(getattr(args, "json", False))
    if_event = getattr(args, "if_event", None)
    if if_event and not _event_matches(plan.get("on"), if_event):
        msg = f"workflow '{wf_path.name}' does not trigger on '{if_event}' — nothing to run."
        if as_json:
            print(json.dumps({"ok": True, "skipped": True, "reason": msg}))
        else:
            info(msg)
        return

    # action == "run"
    pd = getattr(args, "project_dir", None) or os.getcwd()
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    root = pconf["root"]

    # Resolve the target before checking for a local act binary. A configured
    # remote is the recommended execution path and needs only the co-located
    # remote runtime; local act remains available through --local.
    from sandbox.application.context import durable_job_dependencies
    from sandbox.application.target_service import TargetResolutionError
    from sandbox.jobs.models import TargetRequest
    try:
        target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
            project_dir=root, local=bool(getattr(args, "local", False)),
            remote=getattr(args, "remote", None), workspace=getattr(args, "workspace", None),
            required_capability="job.exec" if not getattr(args, "local", False) else None,
        ))
    except TargetResolutionError as exc:
        die(f"{exc.code}: {exc}")
    from sandbox.ci.workflow import WorkflowError, preflight
    try:
        gate = preflight(root, wf_path, selected_jobs=getattr(args, "jobs", None),
                         accepted_differences=getattr(args, "accepted_differences", None),
                         safe_mode=not getattr(args, "allow_deploy", False))
    except WorkflowError as exc:
        die(str(exc))
    if target.kind == "remote":
        blocking = gate["blocking"]
    else:
        # Preserve local act compatibility policy while preventing Sandbox's
        # upload-artifact replacement from silently changing path/options/missing
        # semantics. These named differences must be accepted before neutralization.
        blocking = _artifact_blocking_differences(gate)
    if blocking:
        blocked_gate = {**gate, "ok": False, "compatible": False, "blocking": blocking}
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": "ci_preflight_blocked",
                              "preflight": blocked_gate}, sort_keys=True))
        else:
            if target.kind == "remote":
                die("remote CI preflight blocked execution; use --accept-difference for each named difference")
            die("CI artifact preflight blocked execution; use literal paths, "
                "if-no-files-found: error, or accept each named difference")
        return
    if target.kind == "remote":
        _run_remote_ci(target, root, wf_path, plan, args, as_json=as_json)
        return

    if not _act_binary():
        die("`act` not found — the CI executor requires it "
            "(brew install act; https://github.com/nektos/act).")

    if getattr(args, "run_async", False):
        # Detached run (sandbox/core/_asyncjobs.py) — a ci run mints multiple
        # instances itself, so it doesn't fit commands/jobs.py's per-instance
        # wp_cli_async model. Re-invoke ourselves without --async.
        argv = [str(ROOT / "sb"), "ci", "run", str(wf_path),
               "--project-dir", root, "--json"]
        for j in (getattr(args, "jobs", None) or []):
            argv += ["--job", j]
        for k, v in (getattr(args, "matrix_filter", None) or {}).items():
            argv += ["--matrix-filter", f"{k}={v}"]
        if getattr(args, "label_prefix", None):
            argv += ["--label-prefix", args.label_prefix]
        if getattr(args, "concurrency", None):
            argv += ["--concurrency", str(args.concurrency)]
        if getattr(args, "allow_deploy", False):
            argv.append("--allow-deploy")
        if getattr(args, "keep_on_fail", False):
            argv.append("--keep-on-fail")
        if getattr(args, "strict_provision", False):
            argv.append("--strict-provision")
        argv += ["--timeout", str(getattr(args, "timeout", None) or 900)]
        jid = launch_background_job(argv, cwd=ROOT)
        print(json.dumps({"ok": True, "job_id": jid}))
        return

    job_filter = getattr(args, "jobs", None)
    jobs = [j for j in plan["jobs"] if not job_filter or j["id"] in job_filter]
    if not jobs:
        die(f"no matching job(s) in {wf_path} (have: "
            f"{', '.join(j['id'] for j in plan['jobs'])})")
    job_ids = [j["id"] for j in jobs]

    matrix_filter = getattr(args, "matrix_filter", None) or {}
    allow_deploy = getattr(args, "allow_deploy", False)
    concurrency = getattr(args, "concurrency", None)
    keep_on_fail = bool(getattr(args, "keep_on_fail", False))
    strict_provision = bool(getattr(args, "strict_provision", False))
    label_prefix = getattr(args, "label_prefix", None) or "ci"
    timeout = int(getattr(args, "timeout", 900) or 900)

    all_secrets_needed = _runtime_secrets_needed(jobs, allow_deploy)
    if getattr(args, "list_secrets", False):
        print(json.dumps({"secrets_needed": all_secrets_needed}) if as_json
              else "secrets needed: " + (", ".join(all_secrets_needed) or "(none)"))
        return

    local_cfg = _local_yaml()
    resolved_secrets = {}
    for name in all_secrets_needed:
        val = _resolve_secret(name, local_cfg)
        if val is None:
            die(f"secret '{name}' referenced by workflow '{wf_path.name}' is not "
                f"explicitly allowlisted in sandbox.local.yml ci_secrets (or "
                f"ci_secret_allowlist for $SANDBOX_CI_SECRET_{name}) — refusing to run with an "
                f"empty value. Use `./sb ci run ... --list-secrets` to see "
                f"what's needed.")
        resolved_secrets[name] = val

    # Safety gate: neutralize deploy-class steps into no-op stubs (unless
    # --allow-deploy) in a PATCHED COPY of the workflow — act never sees the
    # original deploy step. Written once, shared read-only across all cells.
    raw_data = _load_workflow(wf_path)
    patched_data, neutralize_notes = _neutralize_workflow_for_safety(raw_data, allow_deploy)
    patched_path = _write_patched_workflow(patched_data)
    secrets_path = _write_secrets_file(resolved_secrets)
    if neutralize_notes and not as_json:
        for note in neutralize_notes:
            info(f"  {note}")

    run_id = _secretsmod.token_hex(2)
    specs = []
    jobs_by_id = {j["id"]: j for j in jobs}
    for job in jobs:
        for cell_entry in job["cells"]:
            cell = cell_entry["matrix"]
            if matrix_filter and not all(str(cell.get(k)) == v for k, v in matrix_filter.items()):
                continue
            label = _cell_label(label_prefix, run_id, job["id"], cell)
            # "CI takes priority over sandbox.config": a matrix cell's own
            # requested php/wp version (or a setup-php step's with.php-version)
            # OVERRIDES the project's sandbox.config.json for THIS instance
            # only — see _resolve_cell_versions and ensure_instance's
            # php_version/wp_version params. config_label is the STABLE
            # per-cell slug (docs/multi-instance-spec.md per-label config) —
            # a user can author sandbox.config.<slug>.json once and have it
            # apply to every run of that matrix cell, independent of the
            # randomized instance label.
            php, wp = _resolve_cell_versions(cell, job["steps"])
            specs.append({"label": label, "config_label": _cell_slug(cell),
                         "cell": cell, "job_id": job["id"],
                         "php": php, "wp": wp})
    if not specs:
        _cleanup_ci_temp_files(patched_path, secrets_path)
        die("no matrix cells matched --matrix-filter")

    if not as_json:
        info(f"ci: running {len(specs)} cell(s) across job(s) {', '.join(job_ids)} via act…")

    def _progress(event):
        if as_json:
            return
        phase, label = event.get("phase"), event.get("label")
        if phase == "provision":
            info(f"  [{label}] provisioning…")
        elif phase == "done":
            ok(f"  [{label}] {event.get('status')}")

    try:
        provision_instance = None
        teardown_instance = None
        if pconf.get("kind") == "compose":
            from sandbox.application.runtime_service import OperationError, OperationRequest
            from sandbox.application.context import runtime_service

            service = runtime_service(cfg)

            def provision_instance(spec):
                result = service.invoke(OperationRequest(
                    root, "ensure", label=spec["label"], arguments={"create": True}))
                if isinstance(result, OperationError):
                    raise RuntimeError(result.message)
                return dict(result.data)

            def teardown_instance(entry):
                result = service.invoke(OperationRequest(
                    root, "destroy", label=entry.get("label", "default")))
                if isinstance(result, OperationError):
                    raise RuntimeError(result.message)

        result = run_across_instances(
            cfg, root, specs,
            worker_fn=lambda entry, spec: _run_cell_with_act(
                entry, spec, Path(root), patched_path, secrets_path,
                allow_deploy, timeout),
            concurrency=concurrency, keep_on_fail=keep_on_fail,
            strict_provision=strict_provision, on_progress=_progress,
            provision_instance=provision_instance, teardown_instance=teardown_instance)
    finally:
        _cleanup_ci_temp_files(patched_path, secrets_path)

    cells_out = []
    passed = failed = 0
    for u in result["units"]:
        r = u.get("result") or {}
        status = u["status"]
        if status == "passed":
            passed += 1
        else:
            failed += 1
        cells_out.append({"label": u["label"], "matrix": (u.get("spec") or {}).get("cell"),
                          "status": status, "exit_code": r.get("exit_code"),
                          "url": r.get("url"),
                          "output": r.get("output"), "error": u.get("error") or r.get("error")})

    report = {"ok": result["ok"], "workflow": str(wf_path), "run_id": run_id,
              "jobs": job_ids, "cells": cells_out,
              "neutralized": neutralize_notes,
              "summary": {"cells": len(cells_out), "passed": passed, "failed": failed}}

    if as_json:
        print(json.dumps(report))
        if not report["ok"]:
            import sys as _sys
            _sys.exit(1)
    else:
        print()
        for c in cells_out:
            mark = "✓" if c["status"] == "passed" else "✗"
            print(f"  {mark} {c['label']:<24} {c['status']:<10} {c['matrix']}")
        print()
        if report["ok"]:
            ok(f"ci: {passed}/{len(cells_out)} cell(s) passed")
        else:
            die(f"ci: {failed}/{len(cells_out)} cell(s) failed")


def _cleanup_ci_temp_files(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


register({'ci': cmd_ci})

"""Pure routing and validation rules for Sandbox-managed Hermes schedules."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


_EFFORT_SUFFIX_RE = re.compile(
    r"/(?:none|minimal|low|medium|high|xhigh|max)$", re.IGNORECASE
)


@dataclass(frozen=True)
class ScheduledRoute:
    profile: str
    provider: str
    model: str
    effort: str


SCHEDULED_ROUTES = {
    "luna": ScheduledRoute("luna", "openai-codex", "gpt-5.6-luna", "medium"),
    "terra": ScheduledRoute("terra", "openai-codex", "gpt-5.6-terra", "medium"),
    "sol": ScheduledRoute("sol", "openai-codex", "gpt-5.6-sol", "high"),
}

SCHEDULE_GUARD_START = "<!-- SANDBOX_CRON_GUARD_BEGIN -->"
SCHEDULE_GUARD_END = "<!-- SANDBOX_CRON_GUARD_END -->"
SCHEDULE_GUARD = f"""{SCHEDULE_GUARD_START}
Sandbox scheduled-execution contract:
- Keep HERMES_HOME and scheduler state in the operator home (use
  $HOME/.hermes/sandbox-cron-state when task-local state is needed); never create a .hermes
  directory in the repository/workdir.
- Discover and use the repository's declared test command. For this Sandbox repository, use
  ./.cli-venv/bin/python -m unittest when that interpreter exists, otherwise python3 -m unittest.
  Do not invoke pytest unless the selected interpreter can import pytest and the repository explicitly requires it.
- Do not install dependencies globally or mutate the host to satisfy a test command.
- Mark a task complete only after its implementation and required checks pass; tooling discovery alone is not work completion.
{SCHEDULE_GUARD_END}"""


def scheduled_route(profile: str) -> ScheduledRoute:
    """Resolve a named Sandbox route; free-form model strings are not accepted."""
    normalized = (profile or "").strip().lower()
    try:
        return SCHEDULED_ROUTES[normalized]
    except KeyError as exc:
        raise ValueError("scheduled profile must be luna, terra, or sol") from exc


def invalid_model_reason(model: object) -> str | None:
    """Return a safe diagnostic for a malformed per-job model snapshot."""
    if model is None or model == "":
        return None
    if not isinstance(model, str):
        return "model snapshot is not a string"
    value = model.strip()
    if _EFFORT_SUFFIX_RE.search(value):
        return "reasoning effort was appended to the model identifier"
    if "/" in value and value.startswith("gpt-5.6-"):
        return "unexpected suffix in Sandbox Codex model identifier"
    return None


def audit_jobs(jobs: object) -> list[dict[str, str]]:
    """Reduce Hermes job records to non-secret invalid-routing diagnostics."""
    invalid: list[dict[str, str]] = []
    if not isinstance(jobs, list):
        return [{"job_id": "", "reason": "jobs collection is not a list"}]
    for job in jobs:
        if not isinstance(job, dict):
            invalid.append({"job_id": "", "reason": "job record is not an object"})
            continue
        model = job.get("model_snapshot") or job.get("model")
        reason = invalid_model_reason(model)
        if reason:
            invalid.append({"job_id": str(job.get("id", "")), "reason": reason})
    return invalid


def guarded_prompt(prompt: str) -> str:
    """Append the idempotent Sandbox execution contract to a cron prompt."""
    value = prompt or ""
    if SCHEDULE_GUARD_START in value:
        return value
    return value.rstrip() + "\n\n" + SCHEDULE_GUARD + "\n"


@dataclass(frozen=True)
class DesiredCronEntry:
    name: str
    schedule: str
    kind: str
    script: str | None
    prompt: str
    profile: str | None
    workdir_template: str | None
    enabled: bool
    deliver: str = "local"


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_TEMPLATE_RE = re.compile(r"\{([a-z_]+)\}")
_SCHEDULE_RE = re.compile(r"^(?:(?:every )?\d+[smhdw]|[0-9*/?,\-]+(?:\s+[0-9*/?,\-]+){4})$")
_SECRET_LIKE_RE = re.compile(
    r"(?i)(?:\b(?:token|password|secret|authorization|cookie)\b\s*[:=]\s*\S+|"
    r"github_pat_[a-z0-9_]{20,}|sk-(?:proj-)?[a-z0-9_-]{20,}|xox[baprs]-[a-z0-9-]{20,})"
)
_ERROR_RE = re.compile(
    r"(?i)(?:http\s*400|unsupported model|not supported|provider error|client error|"
    r"authentication failed|rate.?limit|quota exceeded|request failed|provider_bad_request|unsupported_model)"
)
_TERMINAL_RESULT_RE = re.compile(
    r"(?m)(?:^|:\s*)(COMPLETED_SPEC_TASK|COMPLETED_TODO_TASK|NO_BACKLOG_WORK|REVIEW_REQUIRED)\b"
)


def classify_terminal_result(value: str, *, provider_failure: bool = False,
                            transition_observed: bool = True) -> dict[str, Any]:
    """Classify documented final output without allowing it to mask a provider error.

    A marker is protocol evidence, not a successful run by itself.  The
    scheduler must also have observed a terminal transition for it to recover
    an upstream wrapper error.
    """
    match = _TERMINAL_RESULT_RE.search(str(value or "")[:8000])
    marker = match.group(1) if match else None
    if provider_failure:
        classification = "provider_failure"
    elif marker and transition_observed:
        classification = "successful_terminal"
    elif marker:
        classification = "protocol_error"
    else:
        classification = "none"
    return {"terminal_result": marker, "terminal_classification": classification}


def catalog_path() -> Path:
    return Path(__file__).with_name("cron-catalog.json")


def scripts_path() -> Path:
    return Path(__file__).with_name("cron_scripts")


def load_catalog(path: Path | None = None, script_root: Path | None = None) -> dict[str, Any]:
    """Load and strictly validate the committed non-secret desired catalog."""
    source = path or catalog_path()
    root = script_root or scripts_path()
    try:
        raw = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Hermes cron catalog: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "jobs"}:
        raise ValueError("catalog must contain only schema_version and jobs")
    if raw["schema_version"] != 1 or not isinstance(raw["jobs"], list):
        raise ValueError("unsupported Hermes cron catalog schema")
    entries: list[DesiredCronEntry] = []
    names: set[str] = set()
    allowed = {f.name for f in DesiredCronEntry.__dataclass_fields__.values()}
    for index, item in enumerate(raw["jobs"]):
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError(f"catalog job {index} has missing or unknown fields")
        try:
            entry = DesiredCronEntry(**item)
        except TypeError as exc:
            raise ValueError(f"catalog job {index} is malformed") from exc
        if not _NAME_RE.fullmatch(entry.name) or entry.name in names:
            raise ValueError(f"invalid or duplicate cron name: {entry.name}")
        names.add(entry.name)
        if not _SCHEDULE_RE.fullmatch(entry.schedule.strip()) or len(entry.schedule) > 128:
            raise ValueError(f"invalid schedule for {entry.name}")
        if _SECRET_LIKE_RE.search(entry.prompt):
            raise ValueError(f"secret-like content in catalog prompt for {entry.name}")
        if entry.deliver != "local" or not isinstance(entry.enabled, bool):
            raise ValueError(f"invalid delivery or enabled value for {entry.name}")
        if entry.kind == "script":
            if entry.profile is not None or not entry.script or PurePosixPath(entry.script).name != entry.script:
                raise ValueError(f"invalid script job: {entry.name}")
            asset = root / entry.script
            if not asset.is_file():
                raise ValueError(f"missing cron script: {entry.script}")
            if _SECRET_LIKE_RE.search(asset.read_text(errors="replace")):
                raise ValueError(f"secret-like content in cron script: {entry.script}")
        elif entry.kind == "agent":
            if entry.script is not None or not entry.profile or not entry.prompt.strip():
                raise ValueError(f"invalid agent job: {entry.name}")
            route = scheduled_route(entry.profile)
            if invalid_model_reason(route.model):
                raise ValueError(f"invalid model route for {entry.name}")
        else:
            raise ValueError(f"invalid cron kind for {entry.name}")
        if entry.workdir_template:
            if ".." in PurePosixPath(entry.workdir_template).parts:
                raise ValueError(f"unsafe workdir template for {entry.name}")
            keys = set(_TEMPLATE_RE.findall(entry.workdir_template))
            if keys - {"repo_root", "sandbox_home", "worktrees"} or "{" in _TEMPLATE_RE.sub("", entry.workdir_template):
                raise ValueError(f"unsupported workdir template for {entry.name}")
        entries.append(entry)
    return {"schema_version": 1, "jobs": entries}


def render_entry(entry: DesiredCronEntry, paths: dict[str, str]) -> dict[str, Any]:
    """Resolve catalog-safe path templates using known remote path keys only."""
    workdir = entry.workdir_template
    if workdir:
        workdir = workdir.format(repo_root=paths["repo_root"], sandbox_home=paths["sandbox_home"],
                                 worktrees=paths.get("worktrees", paths["sandbox_home"] + "/runtime/hermes-worktrees"))
        target = PurePosixPath(workdir)
        roots = (PurePosixPath(paths["repo_root"]), PurePosixPath(paths["sandbox_home"]),
                 PurePosixPath(paths.get("worktrees", paths["sandbox_home"] + "/runtime/hermes-worktrees")))
        if not target.is_absolute() or not any(target == root or root in target.parents for root in roots):
            raise ValueError(f"rendered workdir escaped managed roots for {entry.name}")
    data = asdict(entry)
    if entry.kind == "agent":
        data["prompt"] = guarded_prompt(entry.prompt)
    data["workdir"] = workdir
    data.pop("workdir_template")
    return data


def catalog_fingerprint(catalog: dict[str, Any], script_root: Path | None = None) -> str:
    root = script_root or scripts_path()
    normalized = []
    for entry in catalog["jobs"]:
        item = asdict(entry)
        if entry.kind == "agent":
            item["prompt"] = guarded_prompt(entry.prompt)
        if entry.script:
            item["script_sha256"] = hashlib.sha256((root / entry.script).read_bytes()).hexdigest()
        normalized.append(item)
    payload = json.dumps({"schema_version": catalog["schema_version"], "jobs": normalized}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def classify_job(job: dict[str, Any]) -> str:
    return "script" if job.get("no_agent") or job.get("script") else "agent"


def effective_job_status(job: dict[str, Any], evidence: str = "") -> dict[str, Any]:
    """Derive truth from bounded evidence, preferring request failures over metadata."""
    last = str(job.get("last_status") or "").lower()
    route_reason = invalid_model_reason(job.get("model_snapshot") or job.get("model"))
    error = str(job.get("last_error") or "")
    combined = "\n".join((error, evidence))[:8000]
    evidence_failure = bool(_ERROR_RE.search(combined))
    transition_observed = bool(job.get("last_run_at"))
    terminal = classify_terminal_result(
        combined,
        provider_failure=evidence_failure,
        transition_observed=transition_observed,
    )
    if route_reason:
        status = "invalid"
    elif evidence_failure:
        status = "failed"
    elif terminal["terminal_classification"] == "successful_terminal":
        status = "ok"
    elif terminal["terminal_classification"] == "protocol_error":
        status = "failed"
    elif last in {"error", "failed", "failure"}:
        status = "failed"
    elif str(job.get("state") or "").lower() in {"running", "claimed"}:
        status = "running"
    elif not job.get("last_run_at"):
        status = "never_run"
    elif last in {"ok", "success", "completed"}:
        status = "idle_ok" if classify_job(job) == "script" and "no work" in evidence.lower() else "ok"
    else:
        status = last or "unknown"
    return {
        "effective_status": status,
        "false_success": evidence_failure and last in {"ok", "success", "completed"},
        "route_valid": route_reason is None,
        "reason": route_reason or (
            "bounded request evidence records a provider/client failure" if evidence_failure
            else "documented terminal result lacks an observed transition"
            if terminal["terminal_classification"] == "protocol_error" else ""
        ),
        **terminal,
        "result_protocol_error": bool(terminal["terminal_result"] and last in {"error", "failed", "failure"}),
    }


def _fingerprint(value: dict[str, Any]) -> str:
    """Hash one canonical, non-secret scheduler record."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _sha256(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _desired_job_fingerprint(entry: DesiredCronEntry, *, script_root: Path,
                              paths: dict[str, str] | None) -> str | None:
    """Fingerprint every field Sandbox controls without retaining task content."""
    if paths is None:
        return None
    rendered = render_entry(entry, paths)
    route = scheduled_route(entry.profile) if entry.profile else None
    return _fingerprint({
        "name": entry.name,
        "schedule": entry.schedule,
        "kind": entry.kind,
        "enabled": entry.enabled,
        "deliver": entry.deliver,
        "workdir": rendered["workdir"],
        "provider": route.provider if route else None,
        "model": route.model if route else None,
        "reasoning_effort": route.effort if route else None,
        "prompt_sha256": _sha256(rendered["prompt"]) if entry.kind == "agent" else None,
        "script": entry.script,
        "script_sha256": _sha256((script_root / entry.script).read_bytes()) if entry.script else None,
    })


def _observed_job_fingerprint(job: dict[str, Any]) -> str | None:
    """Fingerprint the pinned safe snapshot, failing closed on missing evidence."""
    kind = classify_job(job)
    required = ("name", "schedule", "enabled", "deliver", "workdir")
    if any(field not in job for field in required) or not isinstance(job.get("name"), str):
        return None
    if job.get("enabled") is not True:
        return None
    if kind == "agent":
        required = ("provider_snapshot", "model_snapshot", "reasoning_effort_snapshot", "prompt_sha256")
        if any(field not in job for field in required) or job.get("no_agent") is not False:
            return None
        if not _is_sha256(job.get("prompt_sha256")):
            return None
        provider = job["provider_snapshot"]
        model = job["model_snapshot"]
        effort = job["reasoning_effort_snapshot"]
        prompt_sha256 = job["prompt_sha256"]
        script = None
        script_sha256 = None
    else:
        required = ("script", "script_sha256")
        if any(field not in job for field in required) or job.get("no_agent") is not True:
            return None
        if not isinstance(job.get("script"), str) or not _is_sha256(job.get("script_sha256")):
            return None
        provider = None
        model = None
        effort = None
        prompt_sha256 = None
        script = job["script"]
        script_sha256 = job["script_sha256"]
    return _fingerprint({
        "name": job["name"],
        "schedule": job["schedule"],
        "kind": kind,
        "enabled": job["enabled"],
        "deliver": job["deliver"],
        "workdir": job["workdir"],
        "provider": provider,
        "model": model,
        "reasoning_effort": effort,
        "prompt_sha256": prompt_sha256,
        "script": script,
        "script_sha256": script_sha256,
    })


def reconciliation_plan(catalog: dict[str, Any], observed: list[dict[str, Any]], *,
                        force_replace: bool = False, script_root: Path | None = None,
                        paths: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a deterministic side-effect-free full replacement plan."""
    desired = {entry.name: entry for entry in catalog["jobs"] if entry.enabled}
    observed_by_name: dict[str, list[dict[str, Any]]] = {}
    for job in observed:
        observed_by_name.setdefault(str(job.get("name") or ""), []).append(job)
    desired_fingerprints = {
        name: _desired_job_fingerprint(entry, script_root=script_root or scripts_path(), paths=paths)
        for name, entry in desired.items()
    }
    blocked_by = [] if force_replace else [
        {"name": name, "reason": "controlled-state fingerprint unavailable"}
        for name, entry in desired.items()
        if desired_fingerprints[name] is None
        or (
            len(observed_by_name.get(name, [])) == 1
            and _observed_job_fingerprint(observed_by_name[name][0]) is None
        )
    ]
    exact = not force_replace and not blocked_by and set(observed_by_name) == set(desired) and all(
        len(observed_by_name[name]) == 1
        and _observed_job_fingerprint(observed_by_name[name][0]) == desired_fingerprints[name]
        for name in desired
    )
    remove = [] if exact else [
        {"id": str(job.get("id") or ""), "name": str(job.get("name") or "")}
        for job in observed
    ]
    create = [] if exact else [{"name": e.name, "kind": e.kind, "schedule": e.schedule} for e in desired.values()]
    return {
        "catalog_version": catalog["schema_version"],
        "catalog_fingerprint": catalog_fingerprint(catalog, script_root),
        "remove": remove,
        "create": create,
        "retain": list(desired) if exact else [],
        "blocked_by": blocked_by,
        "changes": not exact,
    }

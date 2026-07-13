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
    data["workdir"] = workdir
    data.pop("workdir_template")
    return data


def catalog_fingerprint(catalog: dict[str, Any], script_root: Path | None = None) -> str:
    root = script_root or scripts_path()
    normalized = []
    for entry in catalog["jobs"]:
        item = asdict(entry)
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
    if route_reason:
        status = "invalid"
    elif evidence_failure or last in {"error", "failed", "failure"}:
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
        "reason": route_reason or ("bounded request evidence records a provider/client failure" if evidence_failure else ""),
    }


def reconciliation_plan(catalog: dict[str, Any], observed: list[dict[str, Any]], *,
                        force_replace: bool = False, script_root: Path | None = None,
                        paths: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a deterministic side-effect-free full replacement plan."""
    desired = {entry.name: entry for entry in catalog["jobs"] if entry.enabled}
    observed_by_name: dict[str, list[dict[str, Any]]] = {}
    for job in observed:
        observed_by_name.setdefault(str(job.get("name") or ""), []).append(job)
    exact = not force_replace and set(observed_by_name) == set(desired) and all(
        len(observed_by_name[name]) == 1
        and str(observed_by_name[name][0].get("schedule")) == entry.schedule
        and classify_job(observed_by_name[name][0]) == entry.kind
        and observed_by_name[name][0].get("enabled") is True
        and not invalid_model_reason(observed_by_name[name][0].get("model_snapshot"))
        and (entry.kind != "agent" or observed_by_name[name][0].get("model_snapshot") == scheduled_route(entry.profile or "").model)
        and (entry.kind != "agent" or observed_by_name[name][0].get("provider_snapshot") == scheduled_route(entry.profile or "").provider)
        and (entry.kind != "script" or PurePosixPath(str(observed_by_name[name][0].get("script") or "")).name == entry.script)
        and (paths is None or observed_by_name[name][0].get("workdir") == render_entry(entry, paths)["workdir"])
        for name, entry in desired.items()
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
        "blocked_by": [],
        "changes": not exact,
    }

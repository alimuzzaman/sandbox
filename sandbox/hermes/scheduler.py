"""Pure routing and validation rules for Sandbox-managed Hermes schedules."""
from __future__ import annotations

from dataclasses import dataclass
import re


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
    "luna": ScheduledRoute("luna", "openai-codex", "gpt-5.6-luna", "low"),
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

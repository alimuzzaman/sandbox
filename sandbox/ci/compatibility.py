"""Versioned, explicit compatibility differences for ``act`` execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CATALOG_VERSION = "1"


@dataclass(frozen=True)
class Difference:
    id: str
    severity: str
    message: str


CATALOG = {
    "act.concurrency-ignored": Difference("act.concurrency-ignored", "block", "workflow concurrency is ignored by act"),
    "act.run-name-ignored": Difference("act.run-name-ignored", "warn", "workflow run-name is ignored by act"),
    "act.step-summary-discarded": Difference("act.step-summary-discarded", "warn", "step summaries are not retained by act"),
    "act.problem-matchers-ignored": Difference("act.problem-matchers-ignored", "warn", "problem matchers and annotations are incomplete"),
    "act.github-context-incomplete": Difference("act.github-context-incomplete", "warn", "GitHub context is incomplete outside GitHub Actions"),
    "act.cancellation-incomplete": Difference("act.cancellation-incomplete", "warn", "run-step cancellation differs; Sandbox process cancellation remains authoritative"),
    "act.job-permissions-ignored": Difference("act.job-permissions-ignored", "block", "job permissions are ignored by act"),
    "act.job-timeout-ignored": Difference("act.job-timeout-ignored", "block", "job timeout-minutes is ignored; Sandbox must enforce an outer deadline"),
    "act.continue-on-error-ignored": Difference("act.continue-on-error-ignored", "block", "job continue-on-error is ignored by act"),
    "act.oidc-unavailable": Difference("act.oidc-unavailable", "block", "OpenID Connect URLs are unavailable"),
    "act.environment-ignored": Difference("act.environment-ignored", "block", "deployment environments and environment secrets are ignored"),
    "act.docker-context-unsupported": Difference("act.docker-context-unsupported", "block", "Docker context execution is unsupported"),
    "act.non-linux-runner": Difference("act.non-linux-runner", "block", "only Linux runners are supported"),
}


def detect(workflow: dict[str, Any]) -> list[dict[str, str]]:
    differences = []
    def add(identifier: str, location: str):
        item = CATALOG[identifier]
        differences.append({"id": item.id, "location": location, "severity": item.severity, "message": item.message})
    if workflow.get("concurrency") is not None: add("act.concurrency-ignored", "concurrency")
    if workflow.get("run-name") is not None: add("act.run-name-ignored", "run-name")
    for job_id, job in (workflow.get("jobs") or {}).items():
        prefix = f"jobs.{job_id}"
        runner = job.get("runs-on")
        runner_values = runner if isinstance(runner, list) else [runner]
        if any(value and "ubuntu" not in str(value).lower() and "linux" not in str(value).lower() for value in runner_values): add("act.non-linux-runner", f"{prefix}.runs-on")
        if job.get("concurrency") is not None: add("act.concurrency-ignored", f"{prefix}.concurrency")
        if job.get("permissions") is not None: add("act.job-permissions-ignored", f"{prefix}.permissions")
        if job.get("timeout-minutes") is not None: add("act.job-timeout-ignored", f"{prefix}.timeout-minutes")
        if job.get("continue-on-error") is not None: add("act.continue-on-error-ignored", f"{prefix}.continue-on-error")
        if job.get("environment") is not None: add("act.environment-ignored", f"{prefix}.environment")
        if job.get("container") or job.get("defaults", {}).get("run", {}).get("working-directory", "").startswith("docker:"): add("act.docker-context-unsupported", f"{prefix}.container")
    return differences

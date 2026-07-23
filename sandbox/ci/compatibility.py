"""Versioned, explicit compatibility differences for ``act`` execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


CATALOG_VERSION = "2"


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
    "sandbox.artifact-pattern-unsupported": Difference(
        "sandbox.artifact-pattern-unsupported", "block",
        "upload-artifact paths must be literal project-relative paths; globs and expressions are unsupported"),
    "sandbox.artifact-missing-semantics": Difference(
        "sandbox.artifact-missing-semantics", "block",
        "upload-artifact must declare if-no-files-found: error for Sandbox collection parity"),
    "sandbox.artifact-options-unsupported": Difference(
        "sandbox.artifact-options-unsupported", "block",
        "upload-artifact options beyond name, path, and if-no-files-found are unsupported"),
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
        for index, step in enumerate(job.get("steps") or []):
            uses = step.get("uses")
            if not isinstance(uses, str) or uses.split("@", 1)[0].lower() != "actions/upload-artifact":
                continue
            location = f"{prefix}.steps[{index}].with"
            options = step.get("with") or {}
            if not isinstance(options, dict):
                add("sandbox.artifact-options-unsupported", location)
                add("sandbox.artifact-pattern-unsupported", f"{location}.path")
                add("sandbox.artifact-missing-semantics", f"{location}.if-no-files-found")
                continue
            path_value = options.get("path")
            literals = ([value.strip() for value in path_value.splitlines() if value.strip()]
                        if isinstance(path_value, str) else [])
            if (not literals or any(
                    "${{" in value or any(char in value for char in "*?[]") or
                    Path(value).is_absolute() or ".." in Path(value).parts
                    for value in literals)):
                add("sandbox.artifact-pattern-unsupported", f"{location}.path")
            if options.get("if-no-files-found") != "error":
                add("sandbox.artifact-missing-semantics", f"{location}.if-no-files-found")
            if set(options) - {"name", "path", "if-no-files-found"}:
                add("sandbox.artifact-options-unsupported", location)
    return differences

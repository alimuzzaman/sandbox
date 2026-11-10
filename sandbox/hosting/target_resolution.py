"""Resolve ``--target`` / ``--name`` to a recorded hosted target (spec 074).

A recorded stack wins: its rendered manifest under ``runtime/stacks/<slug>/``
is used, never the template in the current directory. Any other target is
read from the manifest in ``--project-dir`` (hosting records store no manifest
location), which must declare the same project. Either way the target must be
recorded for that remote in this controller's hosting state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sandbox.core._hosting as hosting
from sandbox.core._hosting import HostingRefusal
from sandbox.hosting import stack_registry, stack_template
from sandbox.hosting.names import TARGET_PART_RE

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

TARGET_INVALID = "host_target_invalid"
TARGET_UNKNOWN = "host_target_unknown"
TARGET_BUSY = "host_target_busy"


class TargetRefusal(HostingRefusal):
    """A ``--target`` / ``--name`` selection that names no usable target."""


@dataclass(frozen=True)
class ResolvedTarget:
    remote: str
    project: str
    environment: str
    kind: Literal["stack", "plain"]
    project_dir: str
    slug: str | None = None
    last_apply_job_id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.project}/{self.environment}"

    @property
    def state_key(self) -> str:
        return f"{self.remote}/{self.project}/{self.environment}"


def parse_target(value: object) -> tuple[str, str]:
    parts = str(value or "").strip().split("/")
    if len(parts) != 2 or not all(TARGET_PART_RE.fullmatch(part) for part in parts):
        raise TargetRefusal(TARGET_INVALID, "--target must be <project>/<environment>")
    return parts[0], parts[1]


def _stack_record(registry: dict, remote_name: str, *, slug: str | None = None,
                  project: str | None = None, environment: str | None = None) -> dict | None:
    matches = [
        record for key, record in (registry.get("stacks") or {}).items()
        if isinstance(record, dict) and record.get("remote") == remote_name
        and (record.get("slug") == slug if slug is not None else
             record.get("project") == project and record.get("environment") == environment)
    ]
    for record in matches:
        # A record is trusted only under its own key and with a path-safe slug.
        record_slug = record.get("slug")
        if not isinstance(record_slug, str) or not _SLUG_RE.fullmatch(record_slug) \
                or registry["stacks"].get(stack_registry.record_key(remote_name, record_slug)) is not record:
            raise TargetRefusal(TARGET_INVALID, "the stack registry holds a malformed stack record")
    if len(matches) > 1:
        raise TargetRefusal(TARGET_INVALID, "more than one recorded stack matches; use --name")
    return matches[0] if matches else None


def _stack_directory(record: dict) -> str:
    slug = str(record.get("slug") or "")
    expected = stack_registry.stack_dir(slug)
    manifest = Path(record.get("manifest") or expected / stack_template.MANIFEST_NAME)
    if manifest.resolve() != (expected / stack_template.MANIFEST_NAME).resolve():
        raise TargetRefusal(
            TARGET_INVALID, f"stack {slug} records a manifest outside its runtime directory")
    if not manifest.is_file():
        raise TargetRefusal(
            TARGET_UNKNOWN,
            f"stack {slug} has no rendered manifest on this controller; run `sb host stack up` again")
    return str(expected)


def resolve(remote_name: str | None, *, target: str | None = None, name: str | None = None,
            project_dir: str | None = None, environment: str | None = None,
            state: dict | None = None, registry: dict | None = None) -> ResolvedTarget:
    """Return the recorded target named by ``target`` or stack ``name``."""
    if not remote_name:
        raise TargetRefusal(TARGET_INVALID, "--remote is required with --target or --name")
    if bool(target) == bool(name):
        raise TargetRefusal(TARGET_INVALID, "pass exactly one of --target or --name")
    registry = stack_registry.read() if registry is None else registry
    if name:
        record = _stack_record(registry, remote_name, slug=name)
        if record is None:
            raise TargetRefusal(
                TARGET_UNKNOWN,
                f"no stack named {name!r} is recorded for remote {remote_name}; "
                f"see `sb host stack list --remote {remote_name}`")
        project, env = parse_target(f"{record.get('project')}/{record.get('environment')}")
    else:
        project, env = parse_target(target)
        record = _stack_record(registry, remote_name, project=project, environment=env)
    if environment and environment != env:
        raise TargetRefusal(
            TARGET_INVALID, f"--environment {environment} does not match target {project}/{env}")
    state = hosting.load_host_state() if state is None else state
    if f"{remote_name}/{project}/{env}" not in (state.get("hosts") or {}):
        hint = (f"; stack {record.get('slug')} has no successful apply yet (`sb host stack list`)"
                if record is not None else "")
        raise TargetRefusal(
            TARGET_UNKNOWN,
            f"{project}/{env} is not recorded in this controller's hosting state for "
            f"remote {remote_name}{hint}")
    if record is not None:
        return ResolvedTarget(
            remote=remote_name, project=project, environment=env, kind="stack",
            project_dir=_stack_directory(record), slug=str(record["slug"]),
            last_apply_job_id=(record.get("last_apply") or {}).get("job_id") or None)
    directory = str(Path(project_dir or ".").resolve())
    try:
        validated = hosting.validate_manifest(directory, env)
    except stack_template.StackTemplateError as exc:
        if exc.code != stack_template.TEMPLATE_REQUIRED:
            raise
        raise TargetRefusal(
            TARGET_UNKNOWN,
            f"no stack recorded for remote {remote_name} has target {project}/{env}, and "
            f"{directory} holds a stack template, not that target's manifest") from None
    if validated.get("project") != project:
        raise TargetRefusal(
            TARGET_UNKNOWN,
            f"the manifest in {directory} declares project {validated.get('project')!r}, "
            f"not {project!r}; pass the target's --project-dir")
    return ResolvedTarget(remote=remote_name, project=project, environment=env,
                          kind="plain", project_dir=directory)

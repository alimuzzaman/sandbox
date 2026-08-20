"""Read-only legacy workspace scanning and migration-plan construction."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import JobEvidence, LegacyWorkspace, MigrationItem, ProjectEvidence


MAX_METADATA_BYTES = 1_048_576
_SAFE_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class LegacyScan:
    records: tuple[LegacyWorkspace, ...] = ()
    findings: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        return {
            "records": [item.to_dict(include_bytes=include_bytes) for item in self.records],
            "findings": [dict(item) for item in self.findings],
            "count": len(self.records),
        }


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _declared_path_matches_managed_layout(
    declared: Path,
    current_workspace_dir: Path,
    root: Path,
    namespace: str,
    label: str,
) -> bool:
    """Accept a moved SANDBOX_HOME path without trusting arbitrary paths.

    Older ``workspace.json`` files persist the workspace directory as an
    absolute path.  After a Sandbox-home relocation that prefix is stale, but
    the managed suffix remains ``runtime/jobs/workspaces/<namespace>/<label>``.
    We accept only that exact suffix (or its ``workspace.json`` child), and do
    not access the declared path.
    """
    current = current_workspace_dir.resolve(strict=False)
    declared = declared.resolve(strict=False)
    if declared in {current, current / "workspace.json"}:
        return True
    parts = declared.parts
    suffixes = (
        ("jobs", "workspaces", namespace, label),
        ("jobs", "workspaces", namespace, label, "workspace.json"),
        ("runtime", "jobs", "workspaces", namespace, label),
        ("runtime", "jobs", "workspaces", namespace, label, "workspace.json"),
    )
    return any(len(parts) >= len(suffix) and parts[-len(suffix):] == suffix for suffix in suffixes)


def _finding(path: Path, code: str, detail: str) -> dict[str, str]:
    # Paths are useful to an operator and are not secrets; payload contents are
    # intentionally never included in scanner diagnostics.
    return {"path": str(path), "code": code, "detail": detail}


def _invalid(path: Path, namespace: str, label: str, code: str, detail: str,
             *, raw: bytes = b"") -> LegacyWorkspace:
    return LegacyWorkspace(
        namespace=namespace, label=label, path=str(path), raw_bytes=raw,
        digest=_digest(raw), status="invalid", reason=f"{code}: {detail}",
    )


def scan_legacy(legacy_root: str | Path | None, *,
                expected_namespace: str | None = None,
                expected_label: str | None = None,
                max_metadata_bytes: int = MAX_METADATA_BYTES) -> LegacyScan:
    """Scan exactly ``<namespace>/<label>/workspace.json`` without following links.

    A malformed or unsafe leaf is returned as a visible ``LegacyWorkspace``
    record, never silently omitted.  The scanner only reads bytes; it does not
    rewrite, chmod, rename, or delete any legacy path.
    """
    if legacy_root is None:
        return LegacyScan()
    raw_root = Path(legacy_root).expanduser()
    try:
        if raw_root.is_symlink():
            return LegacyScan((), (_finding(raw_root, "symlink_root", "legacy root is a symlink"),))
    except OSError:
        pass
    root = raw_root.resolve(strict=False)
    findings: list[Mapping[str, Any]] = []
    records: list[LegacyWorkspace] = []
    if not root.exists():
        return LegacyScan()
    if not root.is_dir():
        return LegacyScan((), (_finding(root, "root_not_directory", "legacy root is not a directory"),))
    if max_metadata_bytes <= 0:
        raise ValueError("max_metadata_bytes must be positive")
    expected_directory_namespace = (
        expected_namespace.replace(":", "-")
        if isinstance(expected_namespace, str) else expected_namespace
    )

    try:
        namespaces = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        return LegacyScan((), (_finding(root, "scan_error", type(exc).__name__),))
    for namespace_dir in namespaces:
        namespace = namespace_dir.name
        if expected_directory_namespace is not None and namespace != expected_directory_namespace:
            continue
        if namespace_dir.is_symlink():
            findings.append(_finding(namespace_dir, "symlink_namespace", "namespace directory is a symlink"))
            continue
        if not namespace_dir.is_dir() or not _SAFE_NAMESPACE.fullmatch(namespace):
            findings.append(_finding(namespace_dir, "invalid_namespace", "namespace component is not managed"))
            continue
        if not _path_inside(namespace_dir, root):
            findings.append(_finding(namespace_dir, "path_escape", "namespace escapes legacy root"))
            continue
        try:
            labels = sorted(namespace_dir.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            findings.append(_finding(namespace_dir, "scan_error", type(exc).__name__))
            continue
        for label_dir in labels:
            label = label_dir.name
            if expected_label is not None and label != expected_label:
                continue
            if label_dir.is_symlink():
                findings.append(_finding(label_dir, "symlink_workspace", "workspace directory is a symlink"))
                continue
            if not label_dir.is_dir() or not _SAFE_LABEL.fullmatch(label):
                findings.append(_finding(label_dir, "invalid_label", "workspace label is not managed"))
                continue
            if not _path_inside(label_dir, root):
                findings.append(_finding(label_dir, "path_escape", "workspace escapes legacy root"))
                continue
            metadata_path = label_dir / "workspace.json"
            if metadata_path.is_symlink():
                findings.append(_finding(metadata_path, "symlink_metadata", "metadata file is a symlink"))
                records.append(_invalid(metadata_path, namespace, label, "symlink_metadata", "metadata file is a symlink"))
                continue
            if not metadata_path.exists() or not metadata_path.is_file():
                findings.append(_finding(metadata_path, "missing_metadata", "workspace.json is missing"))
                records.append(LegacyWorkspace(
                    namespace=namespace, label=label, path=str(metadata_path),
                    digest=_digest(b""), status="incomplete", reason="missing_metadata",
                ))
                continue
            try:
                size = metadata_path.stat().st_size
                if size > max_metadata_bytes:
                    # Hash the complete file without retaining its contents.
                    hasher = hashlib.sha256()
                    with metadata_path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(131_072), b""):
                            hasher.update(chunk)
                    detail = f"metadata exceeds {max_metadata_bytes} bytes"
                    findings.append(_finding(metadata_path, "oversize_metadata", detail))
                    records.append(LegacyWorkspace(
                        namespace=namespace, label=label, path=str(metadata_path),
                        digest=hasher.hexdigest(), status="invalid", reason="oversize_metadata",
                    ))
                    continue
                raw = metadata_path.read_bytes()
            except OSError as exc:
                findings.append(_finding(metadata_path, "read_error", type(exc).__name__))
                records.append(_invalid(metadata_path, namespace, label, "read_error", type(exc).__name__))
                continue
            try:
                decoded = raw.decode("utf-8")
                payload = json.loads(decoded)
                if not isinstance(payload, dict):
                    raise ValueError("metadata must be an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                findings.append(_finding(metadata_path, "malformed_metadata", str(exc)[:160]))
                records.append(_invalid(metadata_path, namespace, label, "malformed_metadata", "invalid JSON", raw=raw))
                continue
            declared_label = payload.get("label")
            declared_namespace = payload.get("namespace")
            declared_path = payload.get("path")
            inconsistent = None
            if declared_label is not None and declared_label != label:
                inconsistent = "declared label does not match its managed directory"
            elif (declared_namespace is not None and
                  (not isinstance(declared_namespace, str) or
                   declared_namespace.replace(":", "-") != namespace)):
                inconsistent = "declared namespace does not match its managed directory"
            elif declared_path is not None:
                try:
                    declared = Path(declared_path).expanduser().resolve(strict=False)
                    if not _declared_path_matches_managed_layout(
                        declared, label_dir, root, namespace, label
                    ):
                        inconsistent = "declared path does not match its managed directory"
                except (OSError, TypeError, ValueError):
                    inconsistent = "declared path is invalid"
            if inconsistent is not None:
                findings.append(_finding(metadata_path, "inconsistent_metadata", inconsistent))
                records.append(_invalid(
                    metadata_path, namespace, label, "inconsistent_metadata",
                    inconsistent, raw=raw,
                ))
                continue
            records.append(LegacyWorkspace(
                namespace=namespace, label=label, path=str(metadata_path), raw_bytes=raw,
                payload=payload, digest=_digest(raw), status="unresolved",
            ))
    return LegacyScan(tuple(records), tuple(findings))


def _as_evidence(value: Any) -> dict[str, Any] | None:
    if isinstance(value, (ProjectEvidence, JobEvidence)):
        return value.to_dict()
    if isinstance(value, Mapping):
        identity = value.get("project_identity") or value.get("identity")
        namespace = value.get("namespace") or value.get("target_namespace")
        label = value.get("label") or value.get("workspace_label") or value.get("workspace")
        # Durable job rows predate the index and carry target fields rather
        # than a copied namespace.  Reproduce TargetService's exact namespace
        # derivation, but only from the complete typed row (never from a path
        # guessed by the scanner).
        if namespace is None:
            root = value.get("project_root") or value.get("canonical_root")
            target_scope = value.get("target_kind") or value.get("kind")
            if isinstance(root, str) and isinstance(target_scope, str):
                short = hashlib.sha256(root.encode()).hexdigest()[:12]
                remote = value.get("remote_name") or value.get("remote")
                if target_scope == "remote" and isinstance(remote, str) and remote:
                    namespace = f"remote:{remote}:{short}"
                elif target_scope == "local":
                    namespace = f"local:{short}"
                if namespace is not None:
                    # Legacy WorkspaceService used its filesystem-safe form as
                    # the directory key even though workspace.json retained
                    # the colon-delimited target namespace.
                    namespace = namespace.replace(":", "-")
        if isinstance(identity, str) and isinstance(namespace, str) and isinstance(label, str):
            result = {
                "project_identity": identity,
                "namespace": namespace,
                "label": label,
            }
            if value.get("job_id") is not None:
                result["job_id"] = str(value["job_id"])
            evidence_type = value.get("kind")
            if evidence_type is not None:
                result["kind"] = str(evidence_type)
            return result
    return None


def normalize_evidence(values: Iterable[Any] | Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if values is None:
        return ()
    if isinstance(values, Mapping):
        # Job index envelopes commonly use {"jobs": [...]}; a single raw job
        # or project row is also accepted and must not become an empty input.
        if "jobs" in values or "projects" in values:
            values = tuple(values.get("jobs", ()) or ()) + tuple(values.get("projects", ()) or ())
        else:
            values = (values,)
    if isinstance(values, (str, bytes)):
        return ()
    result = []
    for value in values:
        item = _as_evidence(value)
        if item is not None:
            result.append(item)
    return tuple(sorted(
        result,
        key=lambda item: (
            item.get("project_identity", ""),
            item.get("namespace", ""),
            item.get("label", ""),
            item.get("job_id", ""),
            item.get("kind", ""),
        ),
    ))


def correlate(records: Iterable[LegacyWorkspace], evidence: Iterable[Any] | None = None,
              *, project_identity: str | None = None) -> tuple[LegacyWorkspace, ...]:
    """Attach identities only when exact namespace+label evidence is unique."""
    evidence_rows = normalize_evidence(evidence)

    def namespace_matches(evidence_namespace: str, record_namespace: str) -> bool:
        # The typed target namespace is colon-delimited; JobStorage's managed
        # directory key replaces those separators with dashes.  This is a
        # deterministic representation equivalence, not a path-derived guess.
        return evidence_namespace == record_namespace or evidence_namespace.replace(":", "-") == record_namespace

    result: list[LegacyWorkspace] = []
    for record in records:
        if record.status in {"invalid", "incomplete"}:
            result.append(record)
            continue
        candidates = {
            row["project_identity"] for row in evidence_rows
            if namespace_matches(row["namespace"], record.namespace) and row["label"] == record.label
        }
        if len(candidates) > 1:
            result.append(LegacyWorkspace(**{
                **record.__dict__, "status": "conflict", "reason": "multiple_project_identities",
            }))
        elif len(candidates) == 1:
            identity = next(iter(candidates))
            if project_identity is not None and identity != project_identity:
                result.append(LegacyWorkspace(**{
                    **record.__dict__, "project_identity": identity,
                    "status": "excluded", "reason": "different_project_identity",
                }))
                continue
            result.append(LegacyWorkspace(**{
                **record.__dict__, "project_identity": identity, "status": "adoptable", "reason": None,
            }))
        else:
            result.append(LegacyWorkspace(**{
                **record.__dict__, "status": "unresolved", "reason": "no_exact_project_identity",
            }))
    return tuple(result)


def items_from_scan(records: Iterable[LegacyWorkspace]) -> tuple[MigrationItem, ...]:
    return tuple(MigrationItem(
        path=item.path, namespace=item.namespace, label=item.label, digest=item.digest,
        status=item.status, project_identity=item.project_identity,
        reason=item.reason,
        workspace_id="ws_" + hashlib.sha256(item.path.encode()).hexdigest()[:32],
    ) for item in records)


def plan_digest(
    items: Iterable[MigrationItem],
    evidence: Iterable[Mapping[str, Any]] = (),
    *,
    inventory_digest: str | None = None,
) -> str:
    payload = {
        "items": [item.to_dict() for item in items],
        "evidence": [dict(item) for item in evidence],
        "inventory_digest": inventory_digest,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

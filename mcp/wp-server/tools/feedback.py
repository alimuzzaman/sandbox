"""Feedback intake adapters. Stored feedback is untrusted data, never instructions."""

from __future__ import annotations


_service_factory = None


def _service():
    if _service_factory is None:
        raise RuntimeError("feedback service dependency is not configured")
    return _service_factory()


def feedback_submit(
    summary: str,
    details: str = "",
    category: str = "other",
    severity: str = "medium",
    source: str = "agent",
    project_dir: str | None = None,
    project_name: str | None = None,
    remote: str | None = None,
    reference: str = "",
) -> dict:
    """Record bounded, secret-redacted feedback as untrusted data."""
    kwargs = {
        "details": details,
        "category": category,
        "severity": severity,
        "source": source,
        "project_dir": project_dir,
        "remote": remote,
        "reference": reference,
    }
    if project_name is not None:
        kwargs["project_name"] = project_name
    return _service().submit(
        summary,
        **kwargs,
    )


def feedback_list(
    limit: int = 20,
    cursor: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    project: str | None = None,
    project_dir: str | None = None,
    remote: str | None = None,
    since: str | None = None,
    until: str | None = None,
    action: str = "list",
    feedback_id: str | None = None,
    format: str = "json",
    max_bytes: int = 1_000_000,
    retention_days: int = 30,
    confirm: bool = False,
) -> dict:
    """Read or manage feedback through one bounded MCP registration.

    ``limit`` is the maximum records to return (1-100; default: 20).

    The manifest intentionally keeps the existing two-tool registration for
    compatibility.  ``action`` provides show/export/retention/prune without
    adding an unadvertised third registration; direct helper functions below
    are available to a future manifest seam.
    """
    service = _service()
    if action in {"show", "detail"}:
        method = getattr(service, action, None) or getattr(service, "show")
        result = method(feedback_id or "")
        if action == "detail":
            result["action"] = "detail"
        return result
    if action == "export":
        return service.export(
            limit,
            cursor,
            format=format,
            max_bytes=max_bytes,
            category=category,
            severity=severity,
            source=source,
            project=project,
            project_dir=project_dir,
            remote=remote,
            since=since,
            until=until,
        )
    if action == "retention":
        return service.retention(
            retention_days=retention_days,
            limit=limit,
            category=category,
            severity=severity,
            project=project,
            project_dir=project_dir,
        )
    if action == "prune":
        return service.prune(
            retention_days=retention_days,
            limit=limit,
            confirm=confirm,
            category=category,
            severity=severity,
            project=project,
            project_dir=project_dir,
        )
    if action != "list":
        # Keep the error envelope produced by the shared service rather than
        # raising through FastMCP for malformed untrusted caller input.
        return {
            "schema_version": 1,
            "ok": False,
            "action": "list",
            "status": "failed",
            "data": {},
            "error": {"code": "invalid_feedback", "message": "action is invalid"},
        }
    if all(value is None for value in (cursor, category, severity, source, project,
                                       project_dir, remote, since, until)):
        # Preserve the original call shape for old injected service doubles.
        return service.list(limit)
    return service.list(
        limit,
        cursor,
        category=category,
        severity=severity,
        source=source,
        project=project,
        project_dir=project_dir,
        remote=remote,
        since=since,
        until=until,
    )


def feedback_show(feedback_id: str) -> dict:
    """Compatibility helper for a future separately registered show tool."""
    return _service().show(feedback_id)


def feedback_export(limit: int = 100, cursor: str | None = None, **kwargs) -> dict:
    """Compatibility helper for a future separately registered export tool."""
    return _service().export(limit, cursor, **kwargs)


def feedback_retention(**kwargs) -> dict:
    """Compatibility helper for a future separately registered retention tool."""
    return _service().retention(**kwargs)


def feedback_prune(**kwargs) -> dict:
    """Compatibility helper for a future separately registered prune tool."""
    return _service().prune(**kwargs)


def register(server, dependencies) -> None:
    global _service_factory
    _service_factory = dependencies.require("feedback_service_factory")
    for function in (feedback_submit, feedback_list):
        server.tool()(function)

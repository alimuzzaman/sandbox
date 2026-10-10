"""The notice for an explicit local run when the declared remote is not ready
(spec 063 FR-022): which remote the project declares, the row that failed, and
its reason. Read-only: it resolves the declaration and reads a stored proof,
and never probes the remote.
"""
from __future__ import annotations


def local_notice(project_dir: str, *, service=None, home=None,
                 config_file: str | None = None) -> dict | None:
    """``{remote_selection: local, declared_remote, failing_aspect, reason}``,
    or None when the project declares no remote or nothing recorded refuses it."""
    from sandbox.jobs.models import TargetRequest
    from sandbox.readiness import check
    try:
        if service is None:
            from sandbox.application.context import durable_job_dependencies
            service = durable_job_dependencies()["target_service"]
        declared = service.declared_remote(project_dir, config_file=config_file) \
            if config_file is not None else service.declared_remote(project_dir)
    except Exception:
        return None
    if not isinstance(declared, str) or not declared:
        return None
    try:
        target = service.resolve(TargetRequest(
            project_dir=project_dir, config_file=config_file, remote=declared,
            required_capability="job.exec"))
    except Exception as exc:
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or code == "invalid_project":
            return None
        return _notice(declared, "registration", code)
    if getattr(target, "kind", None) != "remote":
        return None
    identity = (getattr(target, "sources", None) or {}).get("identity") or target.project_root
    try:
        home = home if home is not None else check._home()
        proof = check._read(check.readiness_dir(home, target.remote_name)
                            / f"{check.project_key(identity)}.json")
    except Exception:
        return None
    if not isinstance(proof, dict):
        return None
    for row in proof.get("rows") or []:
        if isinstance(row, dict) and row.get("state") == "not_ready":
            return _notice(declared, row.get("aspect"), row.get("reason"))
    return None


def _notice(declared: str, aspect, reason) -> dict:
    from sandbox.readiness.rows import ASPECTS, row
    aspect = aspect if aspect in ASPECTS else "registration"
    # ``row`` bounds the reason to a safe code.
    reason = row(aspect, "not_ready", reason=reason if isinstance(reason, str)
                 else "not_ready")["reason"]
    return {"remote_selection": "local", "declared_remote": declared,
            "failing_aspect": aspect, "reason": reason}


def human_notice(found: dict) -> str:
    return (f"remote_selection: local; the declared remote {found['declared_remote']!r} "
            f"is not ready: {found['failing_aspect']} ({found['reason']})")



def run_selection(project_dir: str, *, local: bool, target=None,
                  config_file: str | None = None) -> dict:
    """How a run's target was selected (spec 063 FR-021), from the target the
    command itself used: ``remote_selection`` plus, for an explicit local run,
    the declared remote's notice. No target (a run that resolved none) or a
    local target reads as local."""
    if local:
        return local_notice(project_dir, config_file=config_file) or {"remote_selection": "local"}
    if target is not None and getattr(target, "kind", None) == "remote":
        return {"remote_selection": (getattr(target, "sources", None) or {}).get(
            "remote_selection")}
    return {"remote_selection": "local"}

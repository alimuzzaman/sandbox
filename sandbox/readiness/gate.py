"""Submission gate: refuse on the first ``not_ready`` row before any transfer.

Every remote submission (``test``/``run_tests``, ``e2e``/``run_e2e``,
``ci``/``ci_run``, ``exec --remote``, ``job-start``, ``ensure --remote``)
passes ``require_ready`` to its transport or calls it before deploying.
``unknown`` and ``not_applicable`` rows never refuse; the capacity admission
still runs afterwards during the deploy.
"""
from __future__ import annotations

from sandbox.readiness import check


def require_ready(project_dir: str, remote: str, submission=None, *,
                  target=None, probes: check.Probes | None = None) -> dict:
    """The reusable or fresh proof for ``remote``; raises ``RemoteNotReadyError``.

    ``target`` (a resolved target) or ``submission`` (a ``JobSubmission``) is
    the caller's own resolution; with either, the gate does not resolve again,
    so an explicit configuration selection is kept."""
    from sandbox.readiness.errors import RemoteNotReadyError
    probes = probes or check.default_probes()
    proof = None
    if target is None and submission is not None:
        target = _submission_target(submission)
    if target is None:
        try:
            target = probes.resolve(project_dir, remote)
        except Exception:
            target = None
        resolved = None
    else:
        resolved = target
    if target is not None and getattr(target, "kind", None) == "remote":
        identity = (getattr(target, "sources", None) or {}).get("identity") \
            or target.project_root
        proof = check.reusable(target.remote_name, identity, probes=probes)
    if proof is None:
        proof = check.run(project_dir, remote, probes=probes, target=resolved, full=True)
    for row in proof.get("rows") or []:
        if isinstance(row, dict) and row.get("state") == "not_ready":
            raise RemoteNotReadyError(row, remote=proof.get("remote") or remote,
                                      selection=proof.get("remote_selection"))
    return proof


def _submission_target(submission):
    from types import SimpleNamespace
    return SimpleNamespace(
        kind="remote", remote_name=submission.remote_name, remote=None,
        project_root=submission.project_root, workspace_label=submission.workspace_label,
        sources={"identity": submission.project_identity, "remote_selection": None})

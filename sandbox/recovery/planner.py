from __future__ import annotations

from .catalog import RecoveryCatalog
from .errors import RecoveryError
from .models import ArtifactPlan, RecoveryPlan


def build_plan(catalog: RecoveryCatalog, selected: tuple[str, ...] = ()) -> RecoveryPlan:
    by_id = catalog.by_id()
    requested = set(selected or by_id)
    if requested - set(by_id):
        raise RecoveryError("unknown recovery profile selection", "unknown_profile")
    ordered, visiting, visited = [], set(), set()
    def visit(profile_id):
        if profile_id in visiting:
            raise RecoveryError("recovery profile dependency cycle", "invalid_catalog")
        if profile_id in visited:
            return
        visiting.add(profile_id)
        for dependency in by_id[profile_id].dependencies:
            visit(dependency)
        visiting.remove(profile_id); visited.add(profile_id); ordered.append(profile_id)
    for profile_id in sorted(requested):
        visit(profile_id)
    artifacts = tuple(ArtifactPlan(
        profile_id=profile_id, artifact_id=f"{profile_id}-primary",
        source_type=by_id[profile_id].source_type,
        allowed_roots=by_id[profile_id].allowed_roots,
        sources=by_id[profile_id].sources,
        capture_mode=by_id[profile_id].capture_mode,
        consistency=by_id[profile_id].consistency,
        excludes=by_id[profile_id].excludes,
        restore_target=by_id[profile_id].restore_target,
        verification=by_id[profile_id].verification,
        dependencies=by_id[profile_id].dependencies,
        rationale=str(by_id[profile_id].metadata.get("rationale") or "declared valuable state"),
    ) for profile_id in ordered)
    excluded = ({"class": "containers-and-images", "reason": "reproducible runtime mechanism"},
                {"class": "development-wordpress-state", "reason": "disposable unless explicitly profiled"},
                {"class": "caches-logs-sockets", "reason": "transient state"})
    return RecoveryPlan(catalog.schema_version, tuple(ordered), artifacts, excluded)

"""Fresh-server recovery planning with no provisioning side effects."""
from __future__ import annotations

from pathlib import Path

from .errors import RecoveryError


def build_bootstrap_plan(root: str | Path, *, checkout: str | Path, profiles: tuple[str, ...],
                         prerequisites: tuple[str, ...]) -> dict:
    root, checkout = Path(root), Path(checkout)
    if root.exists() and any(root.iterdir()):
        raise RecoveryError("fresh-server target root must be empty", "bootstrap_root_not_empty")
    if not checkout.exists() or not (checkout / ".git").exists():
        raise RecoveryError("Sandbox checkout prerequisite is unavailable", "missing_checkout")
    if not profiles:
        raise RecoveryError("at least one recovery profile is required", "missing_bootstrap_profiles")
    return {"root": str(root), "checkout": str(checkout), "profiles": profiles,
            "prerequisites": prerequisites, "requires_confirmation": True,
            "actions": ("install-prerequisites", "restore-control-plane", "restore-selected-profiles", "run-acceptance")}

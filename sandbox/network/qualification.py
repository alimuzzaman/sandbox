"""Source-owned production qualification for resolver adoption."""

from __future__ import annotations

from dataclasses import dataclass

_UNSET = object()


@dataclass(frozen=True)
class ProductionResolverQualification:
    adapter_id: str
    managers: tuple[str, ...]
    platforms: tuple[str, ...]
    capabilities: frozenset[str]
    evidence_id: str

    def preflight(self, *, observation, adapter) -> dict | None:
        """Return strict live service identity evidence or fail closed."""
        required_control = (
            "plan", "ensure_helper", "ensure_authorized",
            "qualification_preflight", "revoke_authorization",
            "apply", "rollback", "observe",
        )
        if not all(callable(getattr(adapter, name, None))
                   for name in required_control):
            return None
        try:
            preflight = adapter.qualification_preflight(observation)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if not isinstance(preflight, dict) or set(preflight) != {
            "schema", "owner_id", "unit", "pid", "start_ticks", "uid",
            "control_group",
        }:
            return None
        valid = (
            preflight["schema"] == "sandbox-resolved-service-v1"
            and preflight["owner_id"] == observation.owner_id
            and preflight["unit"] == "systemd-resolved.service"
            and isinstance(preflight["pid"], int)
            and not isinstance(preflight["pid"], bool)
            and preflight["pid"] > 0
            and isinstance(preflight["start_ticks"], int)
            and not isinstance(preflight["start_ticks"], bool)
            and preflight["start_ticks"] > 0
            and isinstance(preflight["uid"], int)
            and not isinstance(preflight["uid"], bool)
            and preflight["uid"] >= 0
            and preflight["control_group"]
            == "/system.slice/systemd-resolved.service"
        )
        return dict(preflight) if valid else None

    def qualifies(self, *, observation, platform, capability, adapter,
                  preflight=_UNSET) -> bool:
        """Accept only the reviewed Linux resolved exact-name control shape."""
        if not self.shape_qualifies(
            observation=observation, platform=platform,
            capability=capability, adapter=adapter,
        ):
            return False
        evidence = (self.preflight(observation=observation, adapter=adapter)
                    if preflight is _UNSET else preflight)
        return evidence is not None

    def shape_qualifies(self, *, observation, platform, capability, adapter) -> bool:
        """Validate source-owned shape without touching the privileged helper."""
        if platform not in self.platforms or capability not in self.capabilities:
            return False
        if observation is None:
            return False
        extension = dict(observation.extension or {})
        if (observation.owner_id != "systemd-resolved:host"
                or observation.manager not in self.managers
                or observation.mode not in {"stub", "routed"}
                or extension != {
                    "kind": "route-only-domain", "global_takeover": False,
                }):
            return False
        required = (
            "plan", "ensure_helper", "ensure_authorized",
            "qualification_preflight", "revoke_authorization",
            "apply", "rollback", "observe",
        )
        return all(callable(getattr(adapter, name, None)) for name in required)


SYSTEMD_RESOLVED_QUALIFICATION = ProductionResolverQualification(
    adapter_id="systemd-resolved",
    managers=("resolved",),
    platforms=("linux",),
    capabilities=frozenset({"exact"}),
    evidence_id="038-t034-ubuntu-2404",
)

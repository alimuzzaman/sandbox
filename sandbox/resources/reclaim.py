"""Pure reclamation policy: classes, protections, tiers, leases, thresholds.

This module decides *what may be reclaimed*.  It performs no I/O at all, so
every safety rule below is unit testable without a host, a container engine, or
a disk.  Host-side evidence collection lives in the shipped probe
(:mod:`sandbox.resources.remote`); host-side mutation re-asserts the same
protections independently, because a policy decision made on the operator's
machine must never be the only thing standing between a command and live data.

The normative contract is
``specs/042-host-storage-reclamation/contracts/reclaim-policy.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence


LIFECYCLE_CLASSES = (
    "PROTECTED", "LIVE", "STOPPED", "REGONLY", "BASE", "ORPHAN", "UNKNOWN",
)
TIERS = ("safe", "tmp", "all")
WORKSPACE_MARKERS = ("-workspace-", ".workspace-")
HOSTS_ENTRY = "hosts"

# A workspace-scoped disposable package volume, and nothing else, is eligible.
# The suffix class deliberately excludes "_" so the greedy workspace capture
# always stops at the final separator.
WORKSPACE_VOLUME_PATTERN = re.compile(
    r"^sandbox-(?P<workspace>.+)_[A-Za-z0-9.-]*node[-_]?modules$"
)
SCRATCH_PATTERNS = (
    re.compile(r"^\.drive-volume-fallbacks-"),
)

DEFAULT_WORKSPACE_TTL_SECONDS = 7 * 86400
DEFAULT_BASE_TTL_SECONDS = 7 * 86400
DEFAULT_WARN_RATIO = 0.15
DEFAULT_CRITICAL_RATIO = 0.05

_DURATION = re.compile(r"^(?P<amount>[0-9]{1,6})(?P<unit>[smhdw])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_LEASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# Ordered so a report can explain itself: the first matching rule is the reason.
PROTECTION_RULES = (
    "hosted_site",
    "managed_root",
    "path_escape",
    "symlink",
    "instance_registry",
    "active_job",
    "volume_not_workspace_scoped",
)


class ReclaimPolicyError(ValueError):
    """Invalid policy input; never raised for a merely unreclaimable resource."""

    def __init__(self, message: str, code: str = "reclaim_policy_invalid") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# durations, timestamps
# --------------------------------------------------------------------------

def parse_duration(value: Any) -> int:
    """Return seconds for a ``<int><unit>`` retention duration such as ``14d``."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ReclaimPolicyError("duration must be a string like 2h or 14d",
                                 "invalid_duration")
    match = _DURATION.fullmatch(value.strip().lower())
    if match is None:
        raise ReclaimPolicyError("duration must be a string like 2h or 14d",
                                 "invalid_duration")
    seconds = int(match.group("amount")) * _DURATION_UNITS[match.group("unit")]
    if seconds <= 0 or seconds > 365 * 86400:
        raise ReclaimPolicyError("duration must be between 1 second and 365 days",
                                 "invalid_duration")
    return seconds


def valid_lease_name(value: Any) -> bool:
    return isinstance(value, str) and bool(_LEASE_NAME.fullmatch(value))


def iso(moment: datetime | float | None) -> str | None:
    if moment is None:
        return None
    if isinstance(moment, (int, float)) and not isinstance(moment, bool):
        moment = datetime.fromtimestamp(float(moment), timezone.utc)
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).astimezone(timezone.utc).timestamp()
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# leases
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LeaseState:
    state: str                      # released | expired | active | none
    expires_at: str | None
    released: bool
    source: str                     # lease | default_window | unknown

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "expires_at": self.expires_at,
            "released": self.released,
            "source": self.source,
        }


def lease_state(
    lease: Mapping[str, Any] | None,
    *,
    mtime: float | None,
    now: float,
    default_window_seconds: int = DEFAULT_WORKSPACE_TTL_SECONDS,
) -> LeaseState:
    """Resolve the effective retention state of one entry.

    An explicit release always wins.  Otherwise an explicit lease expiry wins.
    With neither, the entry expires ``default_window_seconds`` after its last
    modification; an entry whose mtime is unknown is treated as ``active`` so a
    measurement gap can never authorise a deletion.
    """
    if isinstance(lease, Mapping) and lease.get("released") is True:
        return LeaseState("released", iso(_epoch(lease.get("expires_at"))),
                          True, "lease")
    explicit = (_epoch(lease.get("expires_at"))
                if isinstance(lease, Mapping) else None)
    if explicit is not None:
        return LeaseState("expired" if explicit <= now else "active",
                          iso(explicit), False, "lease")
    if mtime is None:
        return LeaseState("active", None, False, "unknown")
    expiry = float(mtime) + float(default_window_seconds)
    return LeaseState("expired" if expiry <= now else "active",
                      iso(expiry), False, "default_window")


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ClassifiedEntry:
    name: str
    path: str
    lifecycle_class: str
    reason: str
    evidence: tuple[str, ...]
    size_bytes: int | None
    size_state: str
    mtime: float | None
    age_seconds: int | None
    is_workspace: bool
    lease: LeaseState
    in_use: bool
    in_use_reason: str | None
    containers: tuple[dict, ...] = ()
    indexed: bool = False
    registry: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "class": self.lifecycle_class,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "size_bytes": self.size_bytes,
            "size_state": self.size_state,
            "modified_at": iso(self.mtime),
            "age_seconds": self.age_seconds,
            "is_workspace": self.is_workspace,
            "lease": self.lease.to_dict(),
            "in_use": self.in_use,
            "in_use_reason": self.in_use_reason,
            "indexed": self.indexed,
            "registry": self.registry,
            "container_count": len(self.containers),
            "running_container_count": sum(
                1 for item in self.containers if item.get("running")
            ),
        }


def is_workspace_name(name: Any) -> bool:
    return isinstance(name, str) and any(
        marker in name for marker in WORKSPACE_MARKERS
    )


def _protection_for(entry: Mapping[str, Any],
                    hosted_sites: frozenset[str]) -> tuple[str, ...]:
    """Return the ordered protection rule names that apply to one entry."""
    reasons: list[str] = []
    name = str(entry.get("name") or "")
    path = str(entry.get("path") or "")
    if (entry.get("hosted") is True or name == HOSTS_ENTRY
            or name in hosted_sites
            or f"/{HOSTS_ENTRY}/" in path
            or path.endswith(f"/{HOSTS_ENTRY}")):
        reasons.append("hosted_site")
    if entry.get("managed_root") is True:
        reasons.append("managed_root")
    if entry.get("path_escape") is True:
        reasons.append("path_escape")
    if entry.get("is_symlink") is True:
        reasons.append("symlink")
    if entry.get("registry") is True:
        reasons.append("instance_registry")
    if entry.get("active_job") is True:
        reasons.append("active_job")
    for declared in entry.get("protections") or ():
        if isinstance(declared, str) and declared and declared not in reasons:
            reasons.append(declared)
    return tuple(reasons)


def classify_entry(
    entry: Mapping[str, Any],
    *,
    now: float,
    hosted_sites: Iterable[str] = (),
    leases: Mapping[str, Any] | None = None,
    inventory_complete: bool = True,
    default_window_seconds: int | None = None,
) -> ClassifiedEntry:
    """Assign exactly one lifecycle class to one deployment-root entry."""
    if not isinstance(entry, Mapping):
        raise ReclaimPolicyError("entry must be a mapping")
    name = str(entry.get("name") or "")
    path = str(entry.get("path") or "")
    if not name or not path:
        raise ReclaimPolicyError("entry requires a name and a path")
    hosted = frozenset(str(item) for item in hosted_sites or ())
    workspace = bool(entry.get("is_workspace")) or is_workspace_name(name)
    mtime = _epoch(entry.get("mtime"))
    window = default_window_seconds if default_window_seconds is not None else (
        DEFAULT_WORKSPACE_TTL_SECONDS if workspace else DEFAULT_BASE_TTL_SECONDS
    )
    lease = lease_state(
        (leases or {}).get(name) if isinstance(leases, Mapping) else None,
        mtime=mtime, now=now, default_window_seconds=window,
    )
    containers = tuple(
        item for item in (entry.get("containers") or ())
        if isinstance(item, Mapping)
    )
    running = tuple(item for item in containers if item.get("running") is True)
    size_state = str(entry.get("size_state") or "not_measured")
    size_bytes = entry.get("size_bytes")
    if size_state != "measured":
        size_bytes = None
    age = None if mtime is None else max(0, int(now - mtime))

    in_use, in_use_reason = _in_use(entry, lease, mtime, now, window)

    protections = _protection_for(entry, hosted)
    evidence: list[str] = []
    if protections:
        lifecycle, reason = "PROTECTED", protections[0]
        evidence.extend(protections)
    elif not inventory_complete:
        lifecycle, reason = "UNKNOWN", "container_inventory_unavailable"
        evidence.append("container_inventory_unavailable")
    elif running:
        lifecycle, reason = "LIVE", "live_container_bind"
        evidence.append("live_container_bind")
    elif entry.get("active_job") is True:
        lifecycle, reason = "LIVE", "active_job"
        evidence.append("active_job")
    elif containers:
        lifecycle, reason = "STOPPED", "stopped_container"
        evidence.append("stopped_container")
    elif entry.get("indexed") is True:
        lifecycle, reason = "REGONLY", "registry_only"
        evidence.extend(("no_container", "workspace_index_record"))
    elif not workspace:
        lifecycle, reason = "BASE", "base_deployment"
        evidence.extend(("no_container", "no_workspace_marker"))
    else:
        lifecycle, reason = "ORPHAN", "orphan_workspace"
        evidence.extend(("no_container", "no_registry", "no_index_record"))
    if lease.state == "released":
        evidence.append("lease_released")
    elif lease.state == "expired":
        evidence.append("lease_expired")
    return ClassifiedEntry(
        name=name,
        path=path,
        lifecycle_class=lifecycle,
        reason=reason,
        evidence=tuple(dict.fromkeys(evidence)),
        size_bytes=size_bytes,
        size_state=size_state,
        mtime=mtime,
        age_seconds=age,
        is_workspace=workspace,
        lease=lease,
        in_use=in_use,
        in_use_reason=in_use_reason,
        containers=containers,
        indexed=bool(entry.get("indexed")),
        registry=bool(entry.get("registry")),
    )


def _in_use(entry: Mapping[str, Any], lease: LeaseState, mtime: float | None,
            now: float, window: int) -> tuple[bool, str | None]:
    """Activity, not process existence, is what makes a workspace in use.

    Nine workspaces held 28.8 GiB behind idle keepalive containers that had
    done nothing for two days.  A running container is real evidence of
    *something* — it produces the LIVE class and keeps an entry out of the safe
    tier — but it cannot outvote an explicit release or an expired window.
    """
    if entry.get("active_job") is True:
        return True, "active_job"
    if lease.state == "released":
        return False, None
    if lease.state == "active" and lease.source == "lease":
        return True, "lease_active"
    if mtime is not None and (now - mtime) < window:
        return True, "recent_activity"
    if mtime is None:
        return True, "activity_unknown"
    return False, None


# --------------------------------------------------------------------------
# volumes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ClassifiedVolume:
    name: str
    workspace: str | None
    size_bytes: int | None
    mounted_running: bool
    eligible: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "workspace": self.workspace,
            "size_bytes": self.size_bytes,
            "mounted_running": self.mounted_running,
            "eligible": self.eligible,
            "reason": self.reason,
        }


def classify_volume(
    volume: Mapping[str, Any],
    *,
    reclaimable_workspaces: Iterable[str] = (),
    present_workspaces: Iterable[str] | None = None,
) -> ClassifiedVolume:
    """Deny by default.  Only workspace-scoped package volumes are eligible.

    A blanket prune would have destroyed ``lenzora-postgres-data``,
    ``sandbox-amarsonar-bangla-public_wordpress-db``, ``wordpress-uploads`` and
    ``lenzora-storage`` — all reported by the engine as unused while holding
    live site data.  Name shape alone is therefore never sufficient: the owning
    workspace must itself be reclaimable, or absent from disk entirely.
    """
    if not isinstance(volume, Mapping):
        raise ReclaimPolicyError("volume must be a mapping")
    name = str(volume.get("name") or "")
    if not name:
        raise ReclaimPolicyError("volume requires a name")
    size = volume.get("size_bytes")
    mounted = volume.get("mounted_running") is True
    match = WORKSPACE_VOLUME_PATTERN.fullmatch(name)
    if match is None:
        return ClassifiedVolume(name, None, size, mounted, False,
                                "volume_not_workspace_scoped")
    workspace = match.group("workspace")
    if not is_workspace_name(workspace):
        return ClassifiedVolume(name, workspace, size, mounted, False,
                                "volume_not_workspace_scoped")
    if mounted:
        return ClassifiedVolume(name, workspace, size, mounted, False,
                                "volume_mounted_by_running_container")
    reclaimable = frozenset(reclaimable_workspaces or ())
    present = (None if present_workspaces is None
               else frozenset(present_workspaces))
    # Compose truncates long project names, so the segment captured from a
    # volume name can be a PREFIX of the real directory
    # (`sandbox-lenzora-workspace-37a8ee_…` for `lenzora-workspace-37a8eec1ce1968`).
    # Matching exactly would call a live workspace's volume "orphaned" — the
    # live remote produced exactly that case on the first run.
    matches = _workspace_matches(workspace, reclaimable)
    if present is not None:
        living = _workspace_matches(workspace, present) - matches
        if living:
            return ClassifiedVolume(name, workspace, size, mounted, False,
                                    "owning_workspace_retained")
        if matches:
            return ClassifiedVolume(name, workspace, size, mounted, True,
                                    "workspace_scoped_volume")
        return ClassifiedVolume(name, workspace, size, mounted, True,
                                "workspace_scoped_volume_orphaned")
    if matches:
        return ClassifiedVolume(name, workspace, size, mounted, True,
                                "workspace_scoped_volume")
    return ClassifiedVolume(name, workspace, size, mounted, False,
                            "owning_workspace_retained")


def _workspace_matches(workspace: str, names: Iterable[str]) -> frozenset[str]:
    """Names that the (possibly truncated) volume segment could refer to."""
    return frozenset(
        name for name in names
        if name == workspace or name.startswith(workspace)
        or workspace.startswith(name)
    )


# --------------------------------------------------------------------------
# tiers
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReclaimCandidate:
    seq: int
    kind: str
    locator: str
    display_name: str
    bytes: int
    mtime: float | None
    lifecycle_class: str
    tier: str
    reason: str
    stop_containers: tuple[str, ...] = ()

    def identity(self) -> str:
        return self.kind + "-" + hashlib.sha256(
            self.locator.encode()
        ).hexdigest()[:20]

    def evidence_digest(self) -> str:
        canonical = "\n".join((
            self.kind, self.locator, self.lifecycle_class, self.reason,
            "" if self.mtime is None else f"{self.mtime:.0f}",
        ))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "locator": self.locator,
            "display_name": self.display_name,
            "bytes": self.bytes,
            "mtime": self.mtime,
            "modified_at": iso(self.mtime),
            "class": self.lifecycle_class,
            "tier": self.tier,
            "reason": self.reason,
            "stop_containers": list(self.stop_containers),
        }


@dataclass(frozen=True)
class TierSelection:
    tier: str
    candidates: tuple[ReclaimCandidate, ...]
    skipped: tuple[dict, ...] = ()
    totals: Mapping[str, int] = field(default_factory=dict)

    @property
    def estimated_bytes(self) -> int:
        return sum(item.bytes for item in self.candidates)


def _engine_complete(block: Mapping[str, Any]) -> bool:
    """Is the *container* inventory trustworthy?

    Deliberately not the same question as "was every directory measured".
    One unmeasured directory used to turn every classification into UNKNOWN,
    which hid the whole deployment root behind a single slow `du`.
    """
    declared = block.get("engine_complete")
    if isinstance(declared, bool):
        return declared
    return str(block.get("status") or "complete") == "complete"


def tier_rank(tier: str) -> int:
    if tier not in TIERS:
        raise ReclaimPolicyError(f"unknown reclaim tier {tier!r}", "invalid_tier")
    return TIERS.index(tier)


def _entry_tier(entry: ClassifiedEntry) -> tuple[str, str] | None:
    """Return ``(tier, reason)`` for a reclaimable entry, or None.

    PROTECTED covers hosted sites, registered instances, and active jobs, so a
    released workspace that is still bound by a live job never reaches here.
    """
    if entry.lifecycle_class in {"PROTECTED", "UNKNOWN"}:
        return None
    running = any(item.get("running") is True for item in entry.containers)
    if entry.lease.state == "released":
        # An explicit release is the strongest signal an agent can give, but a
        # running container still forces the broad tier: reclaiming it means
        # stopping something that is currently up.
        return ("all" if running else "safe"), "lease_released"
    if entry.in_use:
        return None
    if entry.lifecycle_class == "ORPHAN":
        return "safe", "orphan_workspace"
    if entry.lifecycle_class == "REGONLY" and entry.lease.state == "expired":
        return "safe", "registry_only_expired"
    if entry.lifecycle_class == "STOPPED" and entry.lease.state == "expired":
        return "all", "stopped_workspace_expired"
    if entry.lifecycle_class == "LIVE" and entry.lease.state == "expired":
        # Only an idle keepalive reaches here: in_use already covered activity.
        return "all", "live_container_idle_expired"
    if entry.lifecycle_class == "BASE" and entry.lease.state == "expired":
        return "all", "one_shot_base_expired"
    return None


def tier_candidates(
    inventory: Mapping[str, Any],
    tier: str,
    *,
    now: float,
    hosted_sites: Iterable[str] = (),
) -> TierSelection:
    """Select the reviewed candidate set for one tier.

    Tiers are strictly nested: ``safe`` candidates are a subset of ``tmp``
    candidates, which are a subset of ``all``.
    """
    rank = tier_rank(tier)
    complete = _engine_complete(inventory)
    entries = tuple(inventory.get("entries") or ())
    if entries and isinstance(entries[0], Mapping):
        leases = inventory.get("leases") or {}
        entries = tuple(
            classify_entry(item, now=now, hosted_sites=hosted_sites,
                           leases=leases, inventory_complete=complete)
            for item in entries
        )
    candidates: list[ReclaimCandidate] = []
    skipped: list[dict] = []
    reclaimable_names: dict[str, str] = {}
    present_names = {item.name for item in entries}

    for entry in sorted(entries, key=lambda item: item.name):
        decision = _entry_tier(entry)
        if decision is None:
            skipped.append({
                "kind": "worktree", "locator": entry.path,
                "display_name": entry.name, "class": entry.lifecycle_class,
                "reason": entry.in_use_reason or entry.reason,
                "bytes": entry.size_bytes,
            })
            continue
        entry_tier, reason = decision
        if tier_rank(entry_tier) > rank:
            skipped.append({
                "kind": "worktree", "locator": entry.path,
                "display_name": entry.name, "class": entry.lifecycle_class,
                "reason": f"requires_tier_{entry_tier}", "bytes": entry.size_bytes,
            })
            continue
        if entry.size_state != "measured" or entry.size_bytes is None:
            skipped.append({
                "kind": "worktree", "locator": entry.path,
                "display_name": entry.name, "class": entry.lifecycle_class,
                "reason": "size_unmeasured", "bytes": None,
            })
            continue
        reclaimable_names[entry.name] = entry_tier
        candidates.append(ReclaimCandidate(
            seq=0, kind="worktree", locator=entry.path, display_name=entry.name,
            bytes=int(entry.size_bytes), mtime=entry.mtime,
            lifecycle_class=entry.lifecycle_class, tier=entry_tier,
            reason=reason,
            stop_containers=tuple(
                str(item.get("id") or item.get("name") or "")
                for item in entry.containers
                if item.get("id") or item.get("name")
            ),
        ))

    for volume in sorted(inventory.get("volumes") or (),
                         key=lambda item: str(item.get("name") or "")):
        # A volume can only be proven unused when the complete container
        # inventory is available. A partial/failed engine probe must never
        # turn an absent mount row into an orphan candidate.
        if not complete:
            name = str(volume.get("name") or "")
            skipped.append({
                "kind": "volume", "locator": name, "display_name": name,
                "class": "VOLUME",
                "reason": "container_inventory_unavailable",
                "bytes": volume.get("size_bytes"),
            })
            continue
        decision = classify_volume(
            volume, reclaimable_workspaces=reclaimable_names.keys(),
            present_workspaces=present_names,
        )
        if not decision.eligible:
            skipped.append({
                "kind": "volume", "locator": decision.name,
                "display_name": decision.name, "class": "VOLUME",
                "reason": decision.reason, "bytes": decision.size_bytes,
            })
            continue
        # A volume inherits its owning workspace's tier so tier nesting stays
        # true: a volume that only becomes eligible at `all` is never labelled
        # as if a `safe` run would have taken it.
        owners = _workspace_matches(decision.workspace or "", reclaimable_names)
        volume_tier = max(
            (reclaimable_names[name] for name in owners),
            key=tier_rank, default="safe",
        )
        candidates.append(ReclaimCandidate(
            seq=0, kind="volume", locator=decision.name,
            display_name=decision.name,
            bytes=int(decision.size_bytes or 0), mtime=None,
            lifecycle_class="VOLUME", tier=volume_tier, reason=decision.reason,
        ))

    for scratch in sorted(inventory.get("scratch") or (),
                          key=lambda item: str(item.get("path") or "")):
        name = str(scratch.get("name") or "")
        path = str(scratch.get("path") or "")
        size = scratch.get("size_bytes")
        if not path or not any(
            pattern.search(name) for pattern in SCRATCH_PATTERNS
        ):
            skipped.append({
                "kind": "runtime", "locator": path or name,
                "display_name": name, "class": "SCRATCH",
                "reason": "not_disposable_scratch", "bytes": size,
            })
            continue
        if rank < tier_rank("tmp"):
            skipped.append({
                "kind": "runtime", "locator": path, "display_name": name,
                "class": "SCRATCH", "reason": "requires_tier_tmp", "bytes": size,
            })
            continue
        if not isinstance(size, int) or isinstance(size, bool):
            skipped.append({
                "kind": "runtime", "locator": path, "display_name": name,
                "class": "SCRATCH", "reason": "size_unmeasured", "bytes": None,
            })
            continue
        candidates.append(ReclaimCandidate(
            seq=0, kind="runtime", locator=path, display_name=name,
            bytes=int(size), mtime=_epoch(scratch.get("mtime")),
            lifecycle_class="SCRATCH", tier="tmp",
            reason="disposable_scratch",
        ))

    ordered = tuple(
        replace(item, seq=index + 1)
        for index, item in enumerate(sorted(
            candidates, key=lambda item: (item.kind, item.locator),
        ))
    )
    totals: dict[str, int] = {name: 0 for name in TIERS}
    for item in ordered:
        for name in TIERS[tier_rank(item.tier):]:
            totals[name] += item.bytes
    return TierSelection(tier, ordered, tuple(skipped), totals)


def tier_totals(inventory: Mapping[str, Any], *, now: float,
                hosted_sites: Iterable[str] = ()) -> dict[str, dict]:
    """Per-tier candidate counts and byte totals for one inventory."""
    result: dict[str, dict] = {}
    for name in TIERS:
        selection = tier_candidates(inventory, name, now=now,
                                    hosted_sites=hosted_sites)
        result[name] = {
            "candidates": len(selection.candidates),
            "bytes": selection.estimated_bytes,
        }
    return result


# --------------------------------------------------------------------------
# growth exclusion
# --------------------------------------------------------------------------

def growth_excluded(planned: Mapping[str, Any],
                    observed: Mapping[str, Any]) -> str | None:
    """Return an exclusion reason when a candidate changed since planning.

    mtime is the signal, not two size samples: during the manual audit a
    directory *looked* like it was growing because our own ``du`` was racing
    itself.  A size delta with an unchanged mtime is a measurement race and is
    deliberately not an exclusion.
    """
    planned_mtime = _epoch(planned.get("mtime"))
    observed_mtime = _epoch(observed.get("mtime"))
    if planned_mtime is not None and observed_mtime is not None:
        if observed_mtime > planned_mtime:
            return "candidate_modified_since_plan"
    planned_size = planned.get("bytes", planned.get("size_bytes"))
    observed_size = observed.get("bytes", observed.get("size_bytes"))
    if (isinstance(planned_size, int) and isinstance(observed_size, int)
            and not isinstance(planned_size, bool)
            and not isinstance(observed_size, bool)
            and observed_size > planned_size
            and planned_mtime != observed_mtime):
        return "candidate_growing"
    return None


# --------------------------------------------------------------------------
# capacity pressure
# --------------------------------------------------------------------------

def disk_capacity_pressure(
    capacity: Mapping[str, Any] | None,
    *,
    warn_ratio: float = DEFAULT_WARN_RATIO,
    critical_ratio: float = DEFAULT_CRITICAL_RATIO,
    auto_tier: str | None = None,
    auto_ratio: float | None = None,
) -> dict:
    """Classify free-space pressure and, when enabled, the automatic tier.

    ``auto_tier`` may only ever be ``safe``: an unattended run must not be able
    to reach the tier that removes stopped workspaces and base deployments.
    """
    if auto_tier is not None and auto_tier != "safe":
        raise ReclaimPolicyError(
            "automatic reclamation is limited to the safe tier",
            "invalid_auto_tier",
        )
    if not isinstance(capacity, Mapping):
        return {
            "level": "unknown", "free_ratio": None, "free_bytes": None,
            "total_bytes": None, "warn_ratio": warn_ratio,
            "critical_ratio": critical_ratio, "threshold_crossed": None,
            "auto_tier": None, "auto_eligible": False,
            "guidance": "capacity is unmeasured; rerun with --refresh",
        }
    total = capacity.get("total_bytes")
    free = capacity.get("available_bytes")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0 \
            or not isinstance(free, int) or isinstance(free, bool):
        return {
            "level": "unknown", "free_ratio": None, "free_bytes": None,
            "total_bytes": None, "warn_ratio": warn_ratio,
            "critical_ratio": critical_ratio, "threshold_crossed": None,
            "auto_tier": None, "auto_eligible": False,
            "guidance": "capacity is unmeasured; rerun with --refresh",
        }
    ratio = free / total
    if ratio <= critical_ratio:
        level, crossed = "critical", "critical_ratio"
    elif ratio <= warn_ratio:
        level, crossed = "warning", "warn_ratio"
    else:
        level, crossed = "normal", None
    trigger = critical_ratio if auto_ratio is None else auto_ratio
    eligible = bool(auto_tier) and ratio <= trigger
    guidance = {
        "normal": "no action required",
        "warning": (
            "free space is below the warning threshold; run "
            "`sb resources plan --tier safe` and review the candidates"
        ),
        "critical": (
            "free space is critically low; run `sb resources cleanup "
            "--tier safe --confirm` after reviewing the plan"
        ),
    }[level]
    return {
        "level": level,
        "free_ratio": round(ratio, 6),
        "free_bytes": free,
        "total_bytes": total,
        "warn_ratio": warn_ratio,
        "critical_ratio": critical_ratio,
        "auto_ratio": trigger,
        "threshold_crossed": crossed,
        "auto_tier": auto_tier if eligible else None,
        "auto_eligible": eligible,
        "guidance": guidance,
    }


# --------------------------------------------------------------------------
# manifest records
# --------------------------------------------------------------------------

def manifest_intent(run_id: str, candidate: ReclaimCandidate, *,
                    trigger: str = "manual", at: str | None = None) -> dict:
    return {
        "schema": 1,
        "run_id": run_id,
        "seq": candidate.seq,
        "phase": "intent",
        "path": candidate.locator,
        "kind": candidate.kind,
        "bytes": candidate.bytes,
        "class": candidate.lifecycle_class,
        "tier": candidate.tier,
        "reason": candidate.reason,
        "trigger": trigger,
        "at": at or iso(datetime.now(timezone.utc)),
    }


def manifest_outcome(run_id: str, seq: int, locator: str, *, status: str,
                     reason: str, elevated: bool = False,
                     verified_absent: bool = False,
                     bytes_removed: int | None = None,
                     at: str | None = None) -> dict:
    return {
        "schema": 1,
        "run_id": run_id,
        "seq": seq,
        "phase": "outcome",
        "path": locator,
        "status": status,
        "reason": reason,
        "elevated": bool(elevated),
        "verified_absent": bool(verified_absent),
        "bytes": bytes_removed,
        "at": at or iso(datetime.now(timezone.utc)),
    }


# --------------------------------------------------------------------------
# reporting helpers
# --------------------------------------------------------------------------

def summarize_classes(entries: Sequence[ClassifiedEntry]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for entry in entries:
        bucket = buckets.setdefault(entry.lifecycle_class, {
            "class": entry.lifecycle_class, "count": 0, "bytes": 0,
            "measured": 0, "unmeasured": 0,
        })
        bucket["count"] += 1
        if entry.size_state == "measured" and entry.size_bytes is not None:
            bucket["bytes"] += int(entry.size_bytes)
            bucket["measured"] += 1
        else:
            bucket["unmeasured"] += 1
    return [
        buckets[name] for name in LIFECYCLE_CLASSES if name in buckets
    ]


def index_drift(entries: Sequence[ClassifiedEntry],
                index_names: Iterable[str]) -> dict:
    """Report index-versus-disk disagreement in both directions.

    The index listed twelve ``lenzora-workspace-*`` records with four on disk,
    while the disk carried a hundred directories the index had never seen.
    Neither side is treated as truth; both gaps are named.
    """
    present = {entry.name for entry in entries}
    indexed = {str(item) for item in index_names or ()}
    absent = sorted(indexed - present)
    unindexed = sorted(
        entry.name for entry in entries
        if entry.is_workspace and not entry.indexed
    )
    return {
        "indexed_absent": len(absent),
        "present_unindexed": len(unindexed),
        "indexed_absent_names": absent[:50],
        "present_unindexed_names": unindexed[:50],
        "truncated": len(absent) > 50 or len(unindexed) > 50,
    }


def build_report(
    block: Mapping[str, Any] | None,
    capacity: Mapping[str, Any] | None,
    *,
    now: float,
    warn_ratio: float = DEFAULT_WARN_RATIO,
    critical_ratio: float = DEFAULT_CRITICAL_RATIO,
    auto_tier: str | None = None,
) -> dict | None:
    """Turn raw host evidence into the categorised report both surfaces show."""
    if not isinstance(block, Mapping):
        return None
    hosted = block.get("hosted_sites") or ()
    leases = block.get("leases") or {}
    complete = _engine_complete(block)
    entries = tuple(
        classify_entry(item, now=now, hosted_sites=hosted, leases=leases,
                       inventory_complete=complete)
        for item in block.get("entries") or ()
        if isinstance(item, Mapping)
    )
    present = {entry.name for entry in entries}
    volumes = []
    for item in block.get("volumes") or ():
        if not isinstance(item, Mapping):
            continue
        decision = classify_volume(
            item, reclaimable_workspaces=(), present_workspaces=present,
        )
        if not complete:
            decision = replace(
                decision, eligible=False,
                reason="container_inventory_unavailable",
            )
        volumes.append(decision.to_dict())
    eligible = [item for item in volumes if item["eligible"]]
    return {
        "deployment_root": block.get("deployment_root"),
        "runtime_root": block.get("runtime_root"),
        "status": block.get("status"),
        "reason": block.get("reason"),
        "classes": summarize_classes(entries),
        "entries": [entry.to_dict() for entry in entries],
        "volumes": {
            "eligible": len(eligible),
            "eligible_bytes": sum(
                int(item["size_bytes"] or 0) for item in eligible
            ),
            "protected": len(volumes) - len(eligible),
            "records": volumes,
        },
        "scratch": list(block.get("scratch") or ()),
        "drift": index_drift(entries, block.get("index_names") or ()),
        "tiers": tier_totals(block, now=now, hosted_sites=hosted),
        "truncated": bool(block.get("truncated")),
        "unmeasured_count": int(block.get("unmeasured_count") or 0),
        "index_available": bool(block.get("index_available")),
        "capacity_pressure": disk_capacity_pressure(
            capacity, warn_ratio=warn_ratio, critical_ratio=critical_ratio,
            auto_tier=auto_tier,
        ),
    }


__all__ = [
    "build_report",
    "ClassifiedEntry", "ClassifiedVolume", "LeaseState", "LIFECYCLE_CLASSES",
    "PROTECTION_RULES", "ReclaimCandidate", "ReclaimPolicyError",
    "SCRATCH_PATTERNS", "TIERS", "TierSelection", "WORKSPACE_MARKERS",
    "WORKSPACE_VOLUME_PATTERN", "DEFAULT_BASE_TTL_SECONDS",
    "DEFAULT_WORKSPACE_TTL_SECONDS", "classify_entry", "classify_volume",
    "disk_capacity_pressure", "growth_excluded", "index_drift", "iso",
    "lease_state", "manifest_intent", "manifest_outcome", "parse_duration",
    "summarize_classes", "tier_candidates", "tier_rank", "tier_totals",
    "valid_lease_name",
]

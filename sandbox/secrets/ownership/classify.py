"""Pure, names-only ownership classification for one selected profile.

Report names: source-side problems (``unmapped``, ``undecided``,
``multiply_assigned``) list source keys; every other state lists the selected
profile's destination names. Besides the ``BlockReason`` values, two blocking
state keys exist: ``consumer_collision`` (two entries of the profile share a
child consumer name) and ``source_key_missing`` (a mapped key is absent from
its source). No I/O, no values.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from types import MappingProxyType

from sandbox.secrets.models import SecretBrokerError

from .models import (
    BlockReason, ClassificationReport, Existence, MapEntry, OwnershipConfig,
)


CONSUMER_COLLISION = "consumer_collision"
SOURCE_KEY_MISSING = "source_key_missing"
BLOCKING_STATES = frozenset((
    *(reason.value for reason in BlockReason), CONSUMER_COLLISION, SOURCE_KEY_MISSING,
))
_PROVIDER_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_EXISTENCE_STATE = {
    Existence.ABSENT: "would_create",
    Existence.PRESENT: "pre_existing",
    Existence.UNCHECKED: "unchecked",
    Existence.UNKNOWN: "unknown",
}


def _existence_state(observed: object) -> str:
    if observed is None:
        return "unchecked"
    try:
        return _EXISTENCE_STATE[Existence(observed)]
    except (KeyError, ValueError):
        return "unknown"


def _target(config: OwnershipConfig, entry: MapEntry) -> tuple | None:
    profile = config.profiles.get(entry.profile)
    if profile is None:
        return None
    destination = profile.destination
    return (
        destination.endpoint, destination.project_id, destination.environment,
        destination.path, entry.name,
    )


def _domain(config: OwnershipConfig, entry: MapEntry) -> str | None:
    profile = config.profiles.get(entry.profile)
    return None if profile is None else profile.domain


def classify(config: OwnershipConfig, profile: str,
             source_names: Mapping[str, tuple[str, ...]],
             existence: Mapping[str, Existence] | None = None,
             *,
             proven_removed: frozenset[str] = frozenset()) -> ClassificationReport:
    """Classify the selected profile; ``source_names`` maps alias to present names.

    A referenced alias absent from ``source_names`` could not be listed as a
    supported dotenv source and is reported ``source_unsupported``.
    Unregistered aliases are structural normalization errors and never reach
    this point; the ``unregistered_source`` state is reserved for callers that
    report a runtime registration loss.
    """
    selected = config.profiles.get(profile)
    if selected is None:
        raise SecretBrokerError("profile_unknown", "secret ownership profile is not configured")
    states: dict[str, set[str]] = {}

    def add(state: str, name: str) -> None:
        states.setdefault(state, set()).add(name)

    own = [entry for entry in config.entries if entry.profile == profile]
    aliases = {entry.source for entry in own}
    by_key: dict[tuple[str, str], list[MapEntry]] = {}
    for entry in config.entries:
        by_key.setdefault((entry.source, entry.key), []).append(entry)

    # Source-side completeness over every alias the profile reads.
    for alias in sorted(aliases):
        present = source_names.get(alias)
        if present is None:
            continue
        for key in present:
            mapped = by_key.get((alias, key), [])
            if not mapped:
                add(BlockReason.UNMAPPED.value, key)
    for (alias, key), entries in by_key.items():
        if alias not in aliases:
            continue
        if any(entry.profile == "undecided" for entry in entries):
            add(BlockReason.UNDECIDED.value, key)
        if len(entries) > 1:
            add(BlockReason.MULTIPLY_ASSIGNED.value, key)
        domains = {_domain(config, entry) for entry in entries} - {None}
        if len(domains) > 1:
            for entry in entries:
                if entry.profile == profile:
                    add(BlockReason.OTHER_DOMAIN.value, entry.name)

    # Target scope shared with a different-domain profile (normally refused
    # structurally; kept as a fail-closed check).
    scope = (selected.destination.endpoint, selected.destination.project_id)
    foreign_scope = any(
        other.domain != selected.domain
        and (other.destination.endpoint, other.destination.project_id) == scope
        for name, other in config.profiles.items() if name != profile
    )

    targets: dict[tuple, int] = {}
    for entry in config.entries:
        target = _target(config, entry)
        if target is not None:
            targets[target] = targets.get(target, 0) + 1
    consumers: dict[str, int] = {}
    for entry in own:
        consumers[entry.consumer] = consumers.get(entry.consumer, 0) + 1

    for entry in own:
        name = entry.name
        blocked = False
        present = source_names.get(entry.source)
        is_removed = False
        if present is None or not _PROVIDER_NAME.fullmatch(name):
            add(BlockReason.SOURCE_UNSUPPORTED.value, name)
            blocked = True
        elif entry.key not in present:
            if name in proven_removed:
                add("removed", name)
                is_removed = True
            else:
                add(SOURCE_KEY_MISSING, name)
                blocked = True

        if foreign_scope:
            add(BlockReason.OTHER_DOMAIN.value, name)
            blocked = True
        if targets.get(_target(config, entry), 0) > 1:
            add(BlockReason.DUPLICATE_DESTINATION.value, name)
            blocked = True
        if consumers[entry.consumer] > 1:
            add(CONSUMER_COLLISION, name)
            blocked = True
        mapped = by_key.get((entry.source, entry.key), [])
        if len(mapped) > 1 or name in states.get(BlockReason.OTHER_DOMAIN.value, ()):
            blocked = True
        if blocked or is_removed:
            continue
        add(_existence_state(None if existence is None else existence.get(name)), name)

    frozen = {state: tuple(sorted(names)) for state, names in sorted(states.items())}
    return ClassificationReport(
        profile=profile,
        states=MappingProxyType(frozen),
        blocking=any(state in BLOCKING_STATES for state in frozen),
    )


__all__ = ["BLOCKING_STATES", "CONSUMER_COLLISION", "SOURCE_KEY_MISSING", "classify"]

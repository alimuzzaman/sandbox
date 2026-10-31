"""Explicit secret-source adapter registrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretSourceAdapterSpec:
    kind: str
    authority: str


SOURCE_ADAPTERS: dict[str, SecretSourceAdapterSpec] = {
    "file": SecretSourceAdapterSpec(kind="file", authority="registered_local_file"),
    "infisical": SecretSourceAdapterSpec(kind="infisical", authority="registered_exact_item"),
}


def source_kind(descriptor: object) -> str:
    """Return only a registered kind; missing type preserves the file default."""
    if isinstance(descriptor, Mapping):
        value = descriptor.get("type", descriptor.get("kind", "file"))
    else:
        value = getattr(descriptor, "type", getattr(descriptor, "kind", "file"))
    if not isinstance(value, str):
        value = getattr(value, "value", None)
    return value if value in SOURCE_ADAPTERS else "unknown"

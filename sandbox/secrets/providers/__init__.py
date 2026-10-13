"""Narrow provider adapters for the secret broker.

Concrete adapters are loaded lazily so file-source callers do not import HTTP
provider code as a side effect.
"""

from __future__ import annotations

from typing import Any

__all__ = ["CapabilityStatus", "Deadline", "ProviderError", "ProviderPolicy"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import base

        return getattr(base, name)
    if name == "InfisicalProvider":
        from .infisical import InfisicalProvider

        return InfisicalProvider
    raise AttributeError(name)

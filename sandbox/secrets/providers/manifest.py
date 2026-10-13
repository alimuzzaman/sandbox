"""Lazy, explicit provider adapter manifest."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from .base import ProviderError


@dataclass(frozen=True)
class ProviderAdapterSpec:
    module: str
    symbol: str


PROVIDER_MANIFEST_VERSION = 1
PROVIDER_ADAPTERS: dict[str, ProviderAdapterSpec] = {
    "infisical": ProviderAdapterSpec(
        module="sandbox.secrets.providers.infisical",
        symbol="InfisicalProvider",
    ),
}


def load_provider(name: str):
    """Load only the explicitly registered concrete adapter requested."""

    spec = PROVIDER_ADAPTERS.get(name)
    if spec is None:
        raise ProviderError("provider_unsupported")
    module = import_module(spec.module)
    return getattr(module, spec.symbol)

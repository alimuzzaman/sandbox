"""Project identity repository contracts and implementations."""

from .base import (
    AmbiguousRegistryIdentity,
    RegistryCorruption,
    RegistryRepository,
    UnsupportedRegistryVersion,
)
from .json import JsonRegistryRepository
from .memory import MemoryRegistryRepository

__all__ = [
    "AmbiguousRegistryIdentity", "JsonRegistryRepository", "MemoryRegistryRepository",
    "RegistryCorruption", "RegistryRepository", "UnsupportedRegistryVersion",
]

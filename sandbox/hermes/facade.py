"""Compatibility facade while callers migrate from the legacy Hermes module."""

from __future__ import annotations

import sandbox.core._hermes as _legacy


def __getattr__(name):
    return getattr(_legacy, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_legacy)))

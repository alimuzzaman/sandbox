from __future__ import annotations

from typing import Callable


class WordPressSchemaProvider:
    """Compatibility provider around the proven WordPress normalizer."""

    def __init__(self, legacy_loader: Callable) -> None:
        self._legacy_loader = legacy_loader

    def resolve(self, root, *, label=None) -> dict:
        result = dict(self._legacy_loader(root, label=label))
        result["kind"] = "wordpress"
        return result

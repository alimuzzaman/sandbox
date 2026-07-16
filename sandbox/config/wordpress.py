from __future__ import annotations

from typing import Callable


_TEST_SUITES = frozenset(("auto", "unit", "integration"))


class WordPressSchemaProvider:
    """Compatibility provider around the proven WordPress normalizer."""

    def __init__(self, legacy_loader: Callable) -> None:
        self._legacy_loader = legacy_loader

    def resolve(self, root, *, label=None) -> dict:
        result = dict(self._legacy_loader(root, label=label))
        if "tests" not in result:
            result["tests"] = {"suite": "auto"}
        tests = result["tests"]
        if not isinstance(tests, dict) or set(tests) != {"suite"}:
            raise ValueError("WordPress tests configuration must contain only suite")
        if tests["suite"] not in _TEST_SUITES:
            raise ValueError("WordPress tests suite must be auto, unit, or integration")
        result["kind"] = "wordpress"
        return result

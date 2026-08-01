from __future__ import annotations

from pathlib import Path
from typing import Callable

from .descriptors import _load_mapping
from .domains import raw_domain_layer
from .wordpress_runtime import raw_wordpress_runtime_layer


_TEST_SUITES = frozenset(("auto", "unit", "integration"))


class WordPressSchemaProvider:
    """Compatibility provider around the proven WordPress normalizer."""

    def __init__(self, legacy_loader: Callable) -> None:
        self._legacy_loader = legacy_loader

    def resolve(self, root, *, label=None) -> dict:
        root = Path(root)
        project_path = next((root / name for name in (
            "sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml",
        ) if (root / name).exists()), None)
        project_domains = raw_domain_layer(
            _load_mapping(project_path) if project_path is not None else {},
        )
        project_runtime = raw_wordpress_runtime_layer(
            _load_mapping(project_path) if project_path is not None else {},
        )
        machine_domains = {}
        machine_runtime = {}
        for path in (
            next((root / name for name in (
                "sandbox.config.override.json", "sandbox.config.override.yml",
                "sandbox.config.override.yaml",
            ) if (root / name).exists()), None),
            next((root / f"sandbox.config.{label}{suffix}"
                  for suffix in (".json", ".yml", ".yaml")
                  if label and label != "default" and
                  (root / f"sandbox.config.{label}{suffix}").exists()), None),
        ):
            if path is not None:
                document = _load_mapping(path)
                machine_domains.update(raw_domain_layer(document))
                machine_runtime.update(raw_wordpress_runtime_layer(document))
        result = dict(self._legacy_loader(root, label=label))
        result["_domains_raw"] = {
            "project": project_domains,
            "machine_override": machine_domains,
        }
        result["_wordpress_runtime_raw"] = {
            "project": project_runtime, "machine_override": machine_runtime,
        }
        if "tests" not in result:
            result["tests"] = {"suite": "auto"}
        tests = result["tests"]
        if not isinstance(tests, dict) or set(tests) != {"suite"}:
            raise ValueError("WordPress tests configuration must contain only suite")
        if tests["suite"] not in _TEST_SUITES:
            raise ValueError("WordPress tests suite must be auto, unit, or integration")
        result["kind"] = "wordpress"
        return result

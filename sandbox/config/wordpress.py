from __future__ import annotations

from pathlib import Path
import re
from typing import Callable

from .descriptors import config_home, config_layer, primary_config, _load_mapping
from .domains import raw_domain_layer
from .secrets import merge_secret_layers, raw_secret_layer
from .wordpress_runtime import raw_wordpress_runtime_layer


_TEST_SUITES = frozenset(("auto", "unit", "integration"))
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class WordPressSchemaProvider:
    """Compatibility provider around the proven WordPress normalizer."""

    def __init__(self, legacy_loader: Callable) -> None:
        self._legacy_loader = legacy_loader

    def resolve(self, root, *, label=None, config_file=None) -> dict:
        root = Path(root)
        home = config_home(root, config_file)
        project_path = primary_config(root, config_file)
        project_document = _load_mapping(project_path) if project_path is not None else {}
        project_hosting_images = {
            "declared": "hostingImages" in project_document,
            "project_primary": project_document.get("hostingImages"),
        }
        project_domains = raw_domain_layer(project_document)
        project_runtime = raw_wordpress_runtime_layer(project_document)
        project_secrets = raw_secret_layer(project_document)
        lifecycle_declared = "instanceLifecycle" in project_document
        lifecycle = project_document.get("instanceLifecycle")
        machine_domains = {}
        machine_runtime = {}
        machine_secrets = {}
        safe_label = isinstance(label, str) and bool(_SAFE_LABEL.fullmatch(label))
        label_names = tuple(
            f"sandbox.config.{label}{suffix}"
            for suffix in (".json", ".yml", ".yaml")
        ) if safe_label else ()
        for path in (
            config_layer(root, (
                "sandbox.config.override.json", "sandbox.config.override.yml",
                "sandbox.config.override.yaml",
            ), home=home),
            config_layer(root, label_names, home=home)
            if safe_label and label != "default" else None,
        ):
            if path is not None:
                document = _load_mapping(path)
                if "instanceLifecycle" in document:
                    lifecycle_declared = True
                    lifecycle = document["instanceLifecycle"]
                machine_domains.update(raw_domain_layer(document))
                machine_runtime.update(raw_wordpress_runtime_layer(document))
                merge_secret_layers(machine_secrets, raw_secret_layer(document))
        if config_file is None:
            result = dict(self._legacy_loader(root, label=label))
        else:
            result = dict(self._legacy_loader(root, label=label, config_file=config_file))
        result.setdefault("root", str(root))
        result["_domains_raw"] = {
            "project": project_domains,
            "machine_override": machine_domains,
        }
        result["_wordpress_runtime_raw"] = {
            "project": project_runtime, "machine_override": machine_runtime,
        }
        result["_secrets_raw"] = {
            "project": project_secrets, "machine_override": machine_secrets,
        }
        # Keep the optional project channel kind-neutral.  Common config owns
        # normalization, and absence must not alter legacy descriptors.
        result["_hosting_images_raw"] = project_hosting_images
        if lifecycle_declared:
            result["instanceLifecycle"] = lifecycle
        if "tests" not in result:
            result["tests"] = {"suite": "auto"}
        tests = result["tests"]
        if not isinstance(tests, dict) or set(tests) != {"suite"}:
            raise ValueError("WordPress tests configuration must contain only suite")
        if tests["suite"] not in _TEST_SUITES:
            raise ValueError("WordPress tests suite must be auto, unit, or integration")
        result["kind"] = "wordpress"
        return result

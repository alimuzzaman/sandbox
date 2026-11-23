from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Callable

from .descriptors import (
    _sandbox_home, config_home, config_layer, primary_config, _load_mapping,
)
from .domains import raw_domain_layer
from .secrets import merge_secret_layers, raw_secret_layer
from .wordpress_runtime import raw_wordpress_runtime_layer


_TEST_SUITES = frozenset(("auto", "unit", "integration"))
_EXPOSE_KEY = "exposeToNetwork"
_EXPOSE_NOTICES: set[str] = set()


class ExposeToNetworkInvalid(ValueError):
    """A non-boolean ``exposeToNetwork``; carries the stable error code."""

    code = "expose_to_network_invalid"


def _expose_notice(key: str, message: str) -> None:
    """One stderr line per process and reason (stdout may carry JSON)."""
    if key not in _EXPOSE_NOTICES:
        _EXPOSE_NOTICES.add(key)
        print(message, file=sys.stderr)


def resolve_expose_to_network(result: dict, *, declared: bool, value, root: Path,
                              user_global: bool) -> None:
    """Keep ``exposeToNetwork`` only when the project itself declared it.

    The key publishes an instance's site, database and Mailpit on every
    network interface, so only the project's own files count: the primary
    descriptor, its override and its label file. The machine-wide user-global
    config is dropped with one note, a copy that `sb deploy` placed under
    ``$SANDBOX_HOME/deploy-src/`` never exposes (on a server every interface
    usually includes the internet), and a non-boolean value is refused here,
    when the config is read and before ensure or apply writes anything.
    """
    result.pop(_EXPOSE_KEY, None)
    if not declared:
        if user_global:
            _expose_notice("user-global", (
                "note: exposeToNetwork in the machine-wide config is ignored; set it "
                "in the project's sandbox.config.override.json to publish one "
                "instance on every network interface."))
        return
    if not isinstance(value, bool):
        raise ExposeToNetworkInvalid(
            "exposeToNetwork must be true or false (it publishes the instance's "
            "WordPress, database and Mailpit ports on every network interface)")
    if value:
        deployed = _sandbox_home() / "deploy-src"
        try:
            inside = Path(root).resolve().is_relative_to(deployed)
        except (OSError, RuntimeError):
            inside = True  # cannot tell: stay on loopback
        if inside:
            _expose_notice(f"deployed:{root}", (
                "note: exposeToNetwork is ignored for a deployed copy under "
                "$SANDBOX_HOME/deploy-src; its ports stay on loopback."))
            return
    result[_EXPOSE_KEY] = value
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
        expose_declared = _EXPOSE_KEY in project_document
        expose = project_document.get(_EXPOSE_KEY)
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
                if _EXPOSE_KEY in document:
                    expose_declared = True
                    expose = document[_EXPOSE_KEY]
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
        resolve_expose_to_network(
            result, declared=expose_declared, value=expose, root=root,
            user_global=_EXPOSE_KEY in result and not expose_declared)
        if "tests" not in result:
            result["tests"] = {"suite": "auto"}
        tests = result["tests"]
        if not isinstance(tests, dict) or set(tests) != {"suite"}:
            raise ValueError("WordPress tests configuration must contain only suite")
        if tests["suite"] not in _TEST_SUITES:
            raise ValueError("WordPress tests suite must be auto, unit, or integration")
        result["kind"] = "wordpress"
        return result

"""Explicit manifest for common project configuration providers."""

from __future__ import annotations

from collections.abc import Mapping

from .domains import normalize_domain_policy
from .instance_lifecycle import normalize_instance_lifecycle
from .hosting_images import (
    HostingImageConfigError,
    machine_image_policy_provider,
    project_image_intent_provider,
)
from .php_extensions import normalize_php_extensions
from .runtime import normalize_runtime_policy
from .secrets import normalize_secret_config
from .storage_monitor import StorageMonitorConfigError, normalize_storage_monitor
from .wordpress_runtime import normalize_wordpress_runtime


COMMON_CONFIG_PROVIDERS = (
    ("runtime", lambda result: normalize_runtime_policy(result.get("runtime")),
     "sandbox.config.runtime", 10),
    ("instanceLifecycle", lambda result: normalize_instance_lifecycle(
        result.get("instanceLifecycle")),
     "sandbox.config.instance_lifecycle", 15),
    ("domains", normalize_domain_policy, "sandbox.config.domains", 20),
    ("wordpressRuntime", normalize_wordpress_runtime,
     "sandbox.config.wordpress_runtime", 30),
    ("phpExtensions", lambda result: normalize_php_extensions(result.get("phpExtensions")),
     "sandbox.config.php_extensions", 35),
    ("secrets", normalize_secret_config, "sandbox.config.secrets", 40),
    ("hostingImages", project_image_intent_provider,
     "sandbox.config.hosting_images", 50),
)


def _machine_storage_monitor(result: Mapping) -> dict:
    """Extract the raw nested machine block before normalizing it."""
    resources = result.get("resources")
    if resources is None:
        if "resources" in result:
            raise StorageMonitorConfigError(
                "machine resources configuration must be an object",
                "invalid_schedule_field",
            )
        raw = None
    elif not isinstance(resources, Mapping):
        raise StorageMonitorConfigError(
            "machine resources configuration must be an object",
            "invalid_schedule_field",
        )
    else:
        raw = resources.get("monitor")
    return normalize_storage_monitor(raw)


# Machine configuration has a different raw shape from project descriptors:
# the provider reads ``resources.monitor`` and apply_machine_config writes the
# normalized value back into that same nested block.  Keep this sibling tuple
# explicit rather than making project-scoped consumers aware of machine state.
MACHINE_CONFIG_PROVIDERS = (
    ("resources.monitor", _machine_storage_monitor,
     "sandbox.config.storage_monitor", 10),
    ("hosting.images.policies", machine_image_policy_provider,
     "sandbox.config.hosting_images", 20),
)


def apply_common_config(result: dict) -> dict:
    if type(result) is not dict:
        raise ValueError("project configuration must be a built-in object")
    resolved = dict(result)
    for key, provider, _owner, _order in sorted(COMMON_CONFIG_PROVIDERS, key=lambda item: item[3]):
        # PHP extensions remain additive. Instance lifecycle is different: its
        # default must be materialized into every newly resolved descriptor so
        # normal ensure/apply is the explicit adoption point. Persisted legacy
        # registry rows are not rewritten merely by loading the catalog.
        if key == "phpExtensions" and key not in resolved:
            continue
        if key == "hostingImages":
            raw_images = resolved.get("_hosting_images_raw")
            if raw_images is None:
                # No explicit primary-layer carrier means this value came from
                # a non-authoritative/global layer.  It must not survive as
                # project intent.
                resolved.pop(key, None)
                continue
            if (type(raw_images) is dict
                    and raw_images.get("declared") is False):
                # Override files, labels, and global defaults cannot introduce
                # or inherit project image intent when the primary file omits it.
                resolved.pop(key, None)
                continue
        if key in {"phpExtensions", "instanceLifecycle"} and resolved.get(key) is None and key in resolved:
            raise ValueError(f"{key} must be an object when declared")
        resolved[key] = provider(resolved)
    resolved.pop("_domains_raw", None)
    resolved.pop("_persisted_hostname", None)
    resolved.pop("_wordpress_runtime_raw", None)
    resolved.pop("_secrets_raw", None)
    resolved.pop("_hosting_images_raw", None)
    return resolved


def apply_machine_config(result: dict) -> dict:
    """Apply registered machine providers without mutating the raw mapping."""
    if type(result) is not dict:
        raise ValueError("machine configuration must be a built-in object")
    resolved = dict(result)
    for key, provider, _owner, _order in sorted(
        MACHINE_CONFIG_PROVIDERS, key=lambda item: item[3]
    ):
        if key == "resources.monitor":
            monitor = provider(resolved)
            resources = resolved.get("resources")
            resources = {} if resources is None else dict(resources)
            resources["monitor"] = monitor
            resolved["resources"] = resources
        elif key == "hosting.images.policies":
            # Optional means inert: do not materialize a new hosting block on
            # machines that have not opted in to immutable OCI verification.
            hosting = resolved.get("hosting")
            if hosting is None:
                continue
            if type(hosting) is not dict:
                raise HostingImageConfigError("input_invalid", "policy")
            if "images" not in hosting:
                continue
            policies = provider(resolved)
            hosting = dict(hosting)
            images = dict(hosting["images"])
            images["policies"] = policies
            hosting["images"] = images
            resolved["hosting"] = hosting
        else:
            resolved[key] = provider(resolved)
    return resolved

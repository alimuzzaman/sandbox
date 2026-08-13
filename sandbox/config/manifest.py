"""Explicit manifest for common project configuration providers."""

from __future__ import annotations

from .domains import normalize_domain_policy
from .php_extensions import normalize_php_extensions
from .runtime import normalize_runtime_policy
from .secrets import normalize_secret_config
from .wordpress_runtime import normalize_wordpress_runtime


COMMON_CONFIG_PROVIDERS = (
    ("runtime", lambda result: normalize_runtime_policy(result.get("runtime")),
     "sandbox.config.runtime", 10),
    ("domains", normalize_domain_policy, "sandbox.config.domains", 20),
    ("wordpressRuntime", normalize_wordpress_runtime,
     "sandbox.config.wordpress_runtime", 30),
    ("phpExtensions", lambda result: normalize_php_extensions(result.get("phpExtensions")),
     "sandbox.config.php_extensions", 35),
    ("secrets", normalize_secret_config, "sandbox.config.secrets", 40),
)


def apply_common_config(result: dict) -> dict:
    resolved = dict(result)
    for key, provider, _owner, _order in sorted(COMMON_CONFIG_PROVIDERS, key=lambda item: item[3]):
        # This field is deliberately additive: an omitted declaration must not
        # appear in the normalized descriptor or perturb legacy byte/behavior
        # compatibility.  Presence (including an explicit null) is validated
        # by the provider/manifest and therefore cannot be mistaken for
        # omission here.
        if key == "phpExtensions" and key not in resolved:
            continue
        if key == "phpExtensions" and resolved[key] is None:
            raise ValueError("phpExtensions must be an object when declared")
        resolved[key] = provider(resolved)
    resolved.pop("_domains_raw", None)
    resolved.pop("_persisted_hostname", None)
    resolved.pop("_wordpress_runtime_raw", None)
    resolved.pop("_secrets_raw", None)
    return resolved

"""Normalize the safe, declarative ``phpExtensions`` project field.

This module deliberately stops at an immutable requirement model.  It does
not accept package names, URLs, PECL commands, Dockerfile fragments, or INI
paths.  Runtime adapters are responsible for resolving the normalized
requirements through their own checked-in catalogs.
"""

from __future__ import annotations

from collections.abc import Mapping
import re

from sandbox.php_extensions.models import PhpExtensionRequirement, PhpExtensionsConfig


# Keep this list intentionally explicit.  It contains PHP's common built-ins,
# extensions used by WordPress and its test tooling, and the Sandbox-supported
# diagnostics extensions.  The list is a policy boundary, not a package map.
KNOWN_EXTENSIONS = frozenset({
    "bcmath", "calendar", "ctype", "curl", "dom", "exif", "fileinfo",
    "filter", "ftp", "gd", "gettext", "gmp", "hash", "iconv", "imagick",
    "imap", "intl", "json", "mbstring", "mysqli", "mysqlnd", "openssl",
    "opcache", "pcre", "pdo", "pdo_mysql", "pdo_sqlite", "phar", "posix",
    "readline", "redis", "session", "simplexml", "soap", "sodium", "sqlite3",
    "sockets", "sysvmsg", "sysvsem", "sysvshm", "tokenizer", "xml", "xmlreader",
    "xmlwriter", "xdebug", "zip",
})

WORDPRESS_PROFILE = "wordpress@1"
WORDPRESS_PROFILE_REQUIRED = (
    "curl", "dom", "exif", "fileinfo", "hash", "json", "mbstring", "mysqli",
    "openssl", "pcre", "xml",
)
WORDPRESS_PROFILE_CAPABILITIES = {
    "image": ("gd", "imagick"),
}

_VERSION_EXACT = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_VERSION_WILDCARD = re.compile(r"^[0-9]+\.[0-9]+\.\*$")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_+-]*$")


def _version(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"PHP extension {name} version must be exact, X.Y.*, or php")
    if value == "php" or _VERSION_EXACT.fullmatch(value) or _VERSION_WILDCARD.fullmatch(value):
        return value
    raise ValueError(f"PHP extension {name} version must be exact, X.Y.*, or php")


def _name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("PHP extension name must be a non-empty string")
    normalized = value.lower()
    if not _SAFE_NAME.fullmatch(normalized) or normalized not in KNOWN_EXTENSIONS:
        raise ValueError(f"unknown PHP extension {value!r}")
    return normalized


def _requirement(name: object, value: object) -> PhpExtensionRequirement:
    extension = _name(name)
    if value is True:
        return PhpExtensionRequirement(extension)
    if value is False:
        return PhpExtensionRequirement(extension, "disabled")
    if isinstance(value, str):
        return PhpExtensionRequirement(extension, "enabled", _version(value, extension))
    if not isinstance(value, Mapping):
        raise ValueError(
            f"PHP extension {extension} must be true, false, a version, or an object"
        )
    unknown = set(value) - {"state", "version"}
    if unknown:
        raise ValueError(
            f"PHP extension {extension} has unknown keys: {sorted(unknown)}"
        )
    state = value.get("state")
    if state not in {"enabled", "disabled"}:
        raise ValueError(f"PHP extension {extension} state must be enabled or disabled")
    version = value.get("version")
    if version is not None:
        version = _version(version, extension)
    return PhpExtensionRequirement(extension, state, version)


def _extension_map(raw: object) -> Mapping:
    if not isinstance(raw, Mapping):
        raise ValueError("phpExtensions extensions must be an object")
    return raw


def _canonical_profile(raw: object) -> str | None:
    if raw is None:
        return None
    if raw != WORDPRESS_PROFILE:
        raise ValueError(
            f"unknown PHP extension profile {raw!r}; supported profile is {WORDPRESS_PROFILE}"
        )
    return raw


def normalize_php_extensions(raw: object = None) -> PhpExtensionsConfig | None:
    """Normalize one project ``phpExtensions`` value.

    ``None`` means the field was omitted and is intentionally preserved as a
    no-op by the manifest.  Once present, malformed or unsupported values fail
    closed before any runtime adapter can mutate an instance.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("phpExtensions must be an object")

    # The documented form is {profile, extensions}.  A direct map is accepted
    # as a narrow shorthand for extension declarations, preserving the same
    # allowlist and validation rules without introducing a second model.
    if "profile" in raw or "extensions" in raw:
        unknown = set(raw) - {"profile", "extensions"}
        if unknown:
            raise ValueError(f"phpExtensions has unknown keys: {sorted(unknown)}")
        profile = _canonical_profile(raw.get("profile"))
        declarations = _extension_map(raw.get("extensions", {}))
    else:
        profile = None
        declarations = raw

    explicit: dict[str, PhpExtensionRequirement] = {}
    for name, value in declarations.items():
        normalized_name = _name(name)
        if normalized_name in explicit:
            raise ValueError(f"PHP extension {normalized_name} is declared more than once")
        explicit[normalized_name] = _requirement(normalized_name, value)

    # Profiles add immutable required members.  An explicit attempt to turn a
    # profile-required member off is rejected rather than silently overridden.
    required = WORDPRESS_PROFILE_REQUIRED if profile == WORDPRESS_PROFILE else ()
    for name in required:
        current = explicit.get(name)
        if current is not None and current.state != "enabled":
            raise ValueError(
                f"PHP extension {name} is required by profile {profile} and cannot be disabled"
            )
        if current is None:
            explicit[name] = PhpExtensionRequirement(name)

    capabilities = tuple(
        (name, tuple(values))
        for name, values in sorted(
            (WORDPRESS_PROFILE_CAPABILITIES.items() if profile == WORDPRESS_PROFILE else ())
        )
    )
    if profile == WORDPRESS_PROFILE:
        image = WORDPRESS_PROFILE_CAPABILITIES["image"]
        disabled_image = {name for name in image if name in explicit and explicit[name].state == "disabled"}
        enabled_image = {name for name in image if name in explicit and explicit[name].state == "enabled"}
        if len(disabled_image) == len(image) or (disabled_image and not enabled_image and len(disabled_image) == len(image)):
            raise ValueError(
                f"PHP extension profile {profile} requires at least one of {' or '.join(image)}"
            )

    requirements = tuple(explicit[name] for name in sorted(explicit))
    return PhpExtensionsConfig(
        profile=profile,
        requirements=requirements,
        capabilities=capabilities,
        profile_required=tuple(required),
    )


__all__ = [
    "KNOWN_EXTENSIONS", "WORDPRESS_PROFILE", "WORDPRESS_PROFILE_REQUIRED",
    "WORDPRESS_PROFILE_CAPABILITIES", "normalize_php_extensions",
]

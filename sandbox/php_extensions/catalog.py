"""Immutable, allow-listed PHP extension recipes and WordPress capability profiles.

This module deliberately contains policy data, not an installer.  A project can
request an extension by its canonical capability name, but it cannot introduce a
package name, URL, PECL reference, Dockerfile fragment, or shell command.  Runtime
adapters may use the recipe metadata to build a plan after their own approval and
mutation gates have run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


class PhpExtensionCatalogError(ValueError):
    """A request cannot be represented by the checked-in catalog."""

    def __init__(self, message: str, *, code: str = "invalid_extension_request",
                 extension: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.extension = extension


_EXTENSION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_VERSION_EXACT = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,3}(?:[-+~][A-Za-z0-9.-]+)?$"
)
_VERSION_WILDCARD = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.\*$")
_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_SAFE_METADATA_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+@-]{0,127}$")


@dataclass(frozen=True)
class ExtensionRecipe:
    """One immutable, official distribution recipe.

    ``package_template`` is an internal catalog value.  It is never accepted
    from project configuration and is expanded only by an adapter that already
    validated the PHP version.  Core extensions have no package template.
    """

    name: str
    capability: str
    package_template: str | None = None
    package_source: str = "official-distribution"
    provisionable: bool = False
    supports_disable: bool = False
    version_observable: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        if not _EXTENSION_NAME.fullmatch(self.name):
            raise ValueError("extension recipe name is invalid")
        if not _EXTENSION_NAME.fullmatch(self.capability):
            raise ValueError("extension recipe capability is invalid")
        if self.package_source not in {"official-distribution", "runtime-observation"}:
            raise ValueError("extension recipes must use an approved source")
        if self.provisionable and self.package_source != "official-distribution":
            raise ValueError("provisionable extension recipes require official distribution metadata")
        if self.package_template is not None:
            if not self.provisionable:
                raise ValueError("runtime-only extension recipes cannot have a package template")
            if not re.fullmatch(r"php\{php_minor\}-[a-z][a-z0-9-]{0,48}",
                                self.package_template):
                raise ValueError("extension recipe package template is invalid")
        if self.notes and not _SAFE_METADATA_TEXT.fullmatch(self.notes):
            raise ValueError("extension recipe notes are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capability": self.capability,
            "package_template": self.package_template,
            "package_source": self.package_source,
            "provisionable": self.provisionable,
            "supports_disable": self.supports_disable,
            "version_observable": self.version_observable,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ExtensionProfile:
    """A named capability profile with required and recommended extensions."""

    profile_id: str
    version: int
    required: tuple[str, ...]
    recommended: tuple[str, ...] = ()
    capability_alternatives: tuple[tuple[str, ...], ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}@\d+", self.profile_id):
            raise ValueError("extension profile id is invalid")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("extension profile version is invalid")
        required = tuple(self.required)
        recommended = tuple(self.recommended)
        alternatives = tuple(tuple(group) for group in self.capability_alternatives)
        for name in (*required, *recommended, *(name for group in alternatives for name in group)):
            if not _EXTENSION_NAME.fullmatch(name):
                raise ValueError("extension profile contains an invalid name")
        if len(set(required)) != len(required) or len(set(recommended)) != len(recommended):
            raise ValueError("extension profile contains duplicate names")
        if set(required) & set(recommended):
            raise ValueError("extension cannot be both required and recommended")
        if any(not group for group in alternatives):
            raise ValueError("extension capability alternative cannot be empty")
        if self.description and not _SAFE_METADATA_TEXT.fullmatch(self.description):
            raise ValueError("extension profile description is invalid")
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "recommended", recommended)
        object.__setattr__(self, "capability_alternatives", alternatives)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "required": list(self.required),
            "recommended": list(self.recommended),
            "capability_alternatives": [list(group) for group in self.capability_alternatives],
            "description": self.description,
        }


@dataclass(frozen=True)
class CatalogIssue:
    code: str
    message: str
    extension: str | None = None
    expected: str | None = None
    observed: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        for key, value in (("extension", self.extension), ("expected", self.expected),
                           ("observed", self.observed)):
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class CatalogValidation:
    ok: bool
    requirements: tuple[dict[str, Any], ...]
    issues: tuple[CatalogIssue, ...] = ()
    profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "requirements": [dict(item) for item in self.requirements],
            "issues": [issue.to_dict() for issue in self.issues],
            "profile": self.profile,
        }


def _field(value: object, key: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalise_one(raw: object) -> dict[str, Any]:
    """Convert the config model or mapping to one safe canonical requirement."""
    if isinstance(raw, str):
        name, state, version = raw, "enabled", None
    elif isinstance(raw, bool):
        raise PhpExtensionCatalogError("extension requirement needs a name", code="invalid_extension_request")
    else:
        name = _field(raw, "name")
        state = _field(raw, "state", "enabled")
        version = _field(raw, "version")
    if not isinstance(name, str):
        raise PhpExtensionCatalogError("extension name is required")
    name = name.strip().lower()
    if not _EXTENSION_NAME.fullmatch(name):
        raise PhpExtensionCatalogError("extension name is invalid", extension=name)
    if state is True:
        state = "enabled"
    elif state is False:
        state = "disabled"
    if state not in {"enabled", "disabled"}:
        raise PhpExtensionCatalogError("extension state is invalid", extension=name)
    if version is not None:
        if not isinstance(version, str):
            raise PhpExtensionCatalogError("extension version is invalid", extension=name)
        version = version.strip()
        if version != "php" and not (_VERSION_EXACT.fullmatch(version) or _VERSION_WILDCARD.fullmatch(version)):
            raise PhpExtensionCatalogError("extension version must be exact, X.Y.*, or php",
                                           extension=name)
    return {"name": name, "state": state, "version": version}


def normalize_requirements(requirements: object) -> tuple[dict[str, Any], ...]:
    """Return sorted canonical requirements without accepting package metadata.

    Accepted forms are the config model's iterable, a list of requirement
    objects, or a mapping of ``name -> true/false/{state,version}``.  Unknown
    mapping keys are rejected.  A caller cannot smuggle package names, URLs, or
    command fragments through this boundary.
    """
    # Config models intentionally expose a Mapping view for compatibility, but
    # their typed ``requirements`` tuple is the authoritative source.  Resolve
    # it before the generic mapping branch so state/version are not discarded.
    if requirements is not None and hasattr(requirements, "requirements"):
        requirements = getattr(requirements, "requirements")
    elif requirements is not None and hasattr(requirements, "to_dict") and callable(requirements.to_dict):
        requirements = requirements.to_dict()
    if requirements is None:
        return ()
    values: list[object] = []
    if isinstance(requirements, Mapping):
        # A normalized config model uses ``extensions`` as a tuple field; a
        # direct mapping is interpreted as name -> state/version only.
        if "extensions" in requirements:
            extra = set(requirements) - {"extensions", "profile", "required", "capability"}
            if extra:
                raise PhpExtensionCatalogError("unknown PHP extension configuration key")
            raw_extensions = requirements.get("extensions") or {}
            if isinstance(raw_extensions, Mapping):
                for name, value in raw_extensions.items():
                    if isinstance(value, Mapping):
                        unknown = set(value) - {"state", "version"}
                        if unknown:
                            raise PhpExtensionCatalogError("extension requirement contains unknown keys",
                                                           extension=str(name))
                        values.append({"name": name, **dict(value)})
                    elif isinstance(value, str):
                        # The public config shorthand ``{"gd": "2.3.*"}``
                        # means enabled at that runtime-reported version.
                        values.append({"name": name, "state": "enabled", "version": value})
                    else:
                        values.append({"name": name, "state": value})
            else:
                values = list(raw_extensions)
        else:
            for name, value in requirements.items():
                if not isinstance(name, str):
                    raise PhpExtensionCatalogError("extension name is invalid")
                if isinstance(value, Mapping):
                    unknown = set(value) - {"state", "version"}
                    if unknown:
                        raise PhpExtensionCatalogError("extension requirement contains unknown keys",
                                                       extension=name)
                    item = {"name": name, **dict(value)}
                elif isinstance(value, str):
                    item = {"name": name, "state": "enabled", "version": value}
                else:
                    item = {"name": name, "state": value}
                values.append(item)
    elif isinstance(requirements, (str, bytes)):
        values = [requirements]
    else:
        try:
            values = list(requirements)  # type: ignore[arg-type]
        except TypeError as exc:
            raise PhpExtensionCatalogError("extension requirements must be iterable") from exc
    normalized = [_normalise_one(item) for item in values]
    by_name: dict[str, dict[str, Any]] = {}
    for item in normalized:
        previous = by_name.get(item["name"])
        if previous is not None and previous != item:
            raise PhpExtensionCatalogError("extension has conflicting requirements",
                                           extension=item["name"], code="conflicting_requirement")
        by_name[item["name"]] = item
    return tuple(by_name[name] for name in sorted(by_name))


def normalize_version_constraint(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PhpExtensionCatalogError("extension version is invalid", code="invalid_version")
    value = value.strip()
    if value == "php" or _VERSION_EXACT.fullmatch(value) or _VERSION_WILDCARD.fullmatch(value):
        return value
    raise PhpExtensionCatalogError("extension version must be exact, X.Y.*, or php",
                                   code="invalid_version")


@dataclass(frozen=True)
class PhpExtensionCatalog:
    schema_version: int
    recipes: tuple[ExtensionRecipe, ...]
    profiles: tuple[ExtensionProfile, ...]
    _recipe_by_name: Mapping[str, ExtensionRecipe] = field(init=False, repr=False, compare=False)
    _profile_by_id: Mapping[str, ExtensionProfile] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("PHP extension catalog schema version is unsupported")
        recipes = tuple(self.recipes)
        profiles = tuple(self.profiles)
        by_name = {recipe.name: recipe for recipe in recipes}
        by_profile = {profile.profile_id: profile for profile in profiles}
        if len(by_name) != len(recipes) or len(by_profile) != len(profiles):
            raise ValueError("PHP extension catalog contains duplicate entries")
        for profile in profiles:
            names = set(profile.required) | set(profile.recommended)
            names.update(name for group in profile.capability_alternatives for name in group)
            missing = names - set(by_name)
            if missing:
                raise ValueError(f"profile {profile.profile_id} references unknown extensions")
        object.__setattr__(self, "recipes", recipes)
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "_recipe_by_name", MappingProxyType(by_name))
        object.__setattr__(self, "_profile_by_id", MappingProxyType(by_profile))

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()

    def recipe(self, name: str) -> ExtensionRecipe:
        try:
            return self._recipe_by_name[name]
        except (KeyError, TypeError) as exc:
            raise PhpExtensionCatalogError("extension is not in the immutable catalog",
                                           code="unknown_extension", extension=str(name)) from exc

    def profile(self, profile_id: str = "wordpress@1") -> ExtensionProfile:
        try:
            return self._profile_by_id[profile_id]
        except (KeyError, TypeError) as exc:
            raise PhpExtensionCatalogError("extension profile is not in the immutable catalog",
                                           code="unknown_profile") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recipes": [recipe.to_dict() for recipe in self.recipes],
            "profiles": [profile.to_dict() for profile in self.profiles],
        }

    def validate(self, requirements: object, *, profile: str | None = None,
                 require_provisioning: bool = False) -> CatalogValidation:
        try:
            normalized = normalize_requirements(requirements)
        except PhpExtensionCatalogError as exc:
            issue = CatalogIssue(exc.code, str(exc), exc.extension)
            return CatalogValidation(False, (), (issue,), profile)
        issues: list[CatalogIssue] = []
        for item in normalized:
            try:
                recipe = self.recipe(item["name"])
            except PhpExtensionCatalogError as exc:
                issues.append(CatalogIssue(exc.code, str(exc), item["name"]))
                continue
            if item["state"] == "disabled" and not recipe.supports_disable:
                issues.append(CatalogIssue("unsupported_disable",
                                           f"extension {item['name']} cannot be disabled by this adapter",
                                           item["name"]))
            if require_provisioning and item["state"] == "enabled" and not recipe.provisionable:
                issues.append(CatalogIssue("unsupported_provisioning",
                                           f"extension {item['name']} has no approved provisioning recipe",
                                           item["name"]))
        selected_profile = None
        if profile is not None:
            try:
                selected_profile = self.profile(profile)
            except PhpExtensionCatalogError as exc:
                issues.append(CatalogIssue(exc.code, str(exc), None))
            else:
                by_name = {item["name"]: item for item in normalized}
                for required in selected_profile.required:
                    item = by_name.get(required)
                    if item is None:
                        issues.append(CatalogIssue("profile_required_missing",
                                                   f"profile {profile} requires {required}",
                                                   required))
                    elif item["state"] == "disabled":
                        issues.append(CatalogIssue("profile_required_disabled",
                                                   f"profile {profile} requires {required} to be enabled",
                                                   required))
                for alternative in selected_profile.capability_alternatives:
                    if not any(by_name.get(name, {}).get("state") == "enabled"
                               for name in alternative):
                        issues.append(CatalogIssue(
                            "missing_capability",
                            f"profile {profile} requires one of {' or '.join(alternative)}",
                        ))
        return CatalogValidation(not issues, normalized, tuple(issues), profile)

    def profile_requirements(self, profile: str = "wordpress@1") -> tuple[dict[str, Any], ...]:
        selected = self.profile(profile)
        return tuple({"name": name, "state": "enabled", "version": None}
                     for name in selected.required)


# This is intentionally a literal, reviewed catalog.  It contains only names
# and package templates for official distribution packages.  No PECL source,
# URL, arbitrary package, Dockerfile, or shell string is represented.
_RECIPES = (
    ExtensionRecipe("curl", "curl", "php{php_minor}-curl", provisionable=True),
    ExtensionRecipe("dom", "dom", "php{php_minor}-xml", provisionable=True, version_observable=False),
    ExtensionRecipe("exif", "exif", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("fileinfo", "fileinfo", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("gd", "gd", "php{php_minor}-gd", provisionable=True),
    ExtensionRecipe("hash", "hash", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("imagick", "imagick", "php{php_minor}-imagick", provisionable=True),
    ExtensionRecipe("intl", "intl", "php{php_minor}-intl", provisionable=True),
    ExtensionRecipe("json", "json", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("mbstring", "mbstring", "php{php_minor}-mbstring", provisionable=True),
    ExtensionRecipe("mysqli", "mysqli", "php{php_minor}-mysql", provisionable=True),
    ExtensionRecipe("openssl", "openssl", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("opcache", "opcache", "php{php_minor}-opcache", provisionable=True),
    ExtensionRecipe("pcre", "pcre", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("sodium", "sodium", "php{php_minor}-common", provisionable=True, version_observable=False),
    ExtensionRecipe("xml", "xml", "php{php_minor}-xml", provisionable=True, version_observable=False),
    ExtensionRecipe("zip", "zip", "php{php_minor}-zip", provisionable=True),
    # Known observation-only capabilities remain valid requests, but a
    # runtime adapter must report unsupported_provisioning rather than invent
    # a package or source.  This includes extensions commonly used by PHPUnit,
    # WordPress plugins, and diagnostics.
    ExtensionRecipe("bcmath", "bcmath", package_source="runtime-observation"),
    ExtensionRecipe("filter", "filter", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("iconv", "iconv", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("mysqlnd", "mysqlnd", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("pdo", "pdo", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("pdo_mysql", "pdo_mysql", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("pdo_sqlite", "pdo_sqlite", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("phar", "phar", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("session", "session", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("simplexml", "simplexml", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("sqlite3", "sqlite3", package_source="runtime-observation"),
    ExtensionRecipe("tokenizer", "tokenizer", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("xmlreader", "xmlreader", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("xmlwriter", "xmlwriter", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("xdebug", "xdebug", package_source="runtime-observation"),
    ExtensionRecipe("gmp", "gmp", package_source="runtime-observation"),
    ExtensionRecipe("redis", "redis", package_source="runtime-observation"),
    ExtensionRecipe("calendar", "calendar", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("ctype", "ctype", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("ftp", "ftp", package_source="runtime-observation"),
    ExtensionRecipe("gettext", "gettext", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("imap", "imap", package_source="runtime-observation"),
    ExtensionRecipe("posix", "posix", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("readline", "readline", package_source="runtime-observation"),
    ExtensionRecipe("soap", "soap", package_source="runtime-observation"),
    ExtensionRecipe("sockets", "sockets", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("sysvmsg", "sysvmsg", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("sysvsem", "sysvsem", package_source="runtime-observation", version_observable=False),
    ExtensionRecipe("sysvshm", "sysvshm", package_source="runtime-observation", version_observable=False),
)

WORDPRESS_PROFILE = ExtensionProfile(
    profile_id="wordpress@1",
    version=1,
    required=("curl", "dom", "exif", "fileinfo", "hash", "json", "mbstring",
              "mysqli", "openssl", "pcre", "xml"),
    recommended=("intl", "sodium", "zip", "opcache"),
    capability_alternatives=(("gd", "imagick"),),
    description="WordPress core with one supported image capability",
)

DEFAULT_CATALOG = PhpExtensionCatalog(1, _RECIPES, (WORDPRESS_PROFILE,))
WORDPRESS_CATALOG = DEFAULT_CATALOG


def get_catalog() -> PhpExtensionCatalog:
    """Return the process-wide immutable catalog."""
    return DEFAULT_CATALOG


def recipe_for(name: str) -> ExtensionRecipe:
    return DEFAULT_CATALOG.recipe(name)


def profile_for(profile_id: str = "wordpress@1") -> ExtensionProfile:
    return DEFAULT_CATALOG.profile(profile_id)


def catalog_digest(catalog: PhpExtensionCatalog = DEFAULT_CATALOG) -> str:
    return catalog.digest


__all__ = [
    "CatalogIssue", "CatalogValidation", "DEFAULT_CATALOG", "ExtensionProfile",
    "ExtensionRecipe", "PhpExtensionCatalog", "PhpExtensionCatalogError",
    "WORDPRESS_CATALOG", "WORDPRESS_PROFILE", "catalog_digest", "get_catalog",
    "normalize_requirements", "normalize_version_constraint", "profile_for", "recipe_for",
]

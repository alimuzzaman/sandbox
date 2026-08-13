"""Pure planner for Sandbox-owned PHP extension images.

This module deliberately does *not* invoke Docker, a package manager, or a
shell.  It turns an already-normalised ``phpExtensions`` requirement into a
reviewable plan for two child images (the web tier and ``wpcli``), with a
content-addressed build context under ``$SANDBOX_HOME``.  The actual build and
the service restart belong to a later, explicitly approved lifecycle step.

Only the official ``wordpress`` Apache/FPM images are buildable.  A custom
image, OpenLiteSpeed, or a native runtime can use the same requirement model
for validation, but must not accidentally fall through to this builder.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


CATALOG_VERSION = "wordpress@1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^(?:\d+\.\d+(?:\.\d+)?|\d+\.\d+\.\*)$")
_IMAGE_REF_RE = re.compile(
    r"^(?P<repo>[a-z0-9./_-]+)(?::(?P<tag>[a-z0-9][a-z0-9._-]*))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?$",
    re.IGNORECASE,
)


class ComposeBuilderError(ValueError):
    """Base class for deterministic, pre-build validation failures."""


class UnsupportedParentImageError(ComposeBuilderError):
    """The selected image/server is outside the safe builder boundary."""


class UnsupportedExtensionError(ComposeBuilderError):
    """The extension has no Sandbox-owned recipe in the selected catalog."""

    def __init__(self, message: str, *, code: str = "unsupported_provisioning") -> None:
        super().__init__(message)
        self.code = code


class DigestMismatchError(ComposeBuilderError):
    """A caller supplied a fingerprint which does not match the plan."""


class BuildContextError(ComposeBuilderError):
    """A requested context path is not the managed cache path."""


@dataclass(frozen=True)
class ExtensionRequirement:
    """One normalized runtime assertion.

    ``version`` is a runtime-reported extension constraint.  It is not a
    package URL or a Dockerfile fragment and can never affect the generated
    recipe commands.
    """

    name: str
    state: str = "enabled"
    version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.version is not None:
            out["version"] = self.version
        return out


@dataclass(frozen=True)
class Recipe:
    """Immutable, checked-in recipe metadata.

    The command tuples are catalog data, never user input.  They are retained
    in the plan as provenance so a future builder can render exactly the same
    context and prove which recipe was selected.
    """

    recipe_id: str
    extension: str
    build_commands: tuple[str, ...]
    packages: tuple[str, ...] = ()
    supported_versions: tuple[str, ...] = ()
    provisionable: bool = True
    assertion_only: bool = False
    ini_disableable: bool = False


@dataclass(frozen=True)
class ImagePlan:
    role: str
    parent_image: str
    parent_digest: str
    image: str
    digest: str
    context_dir: Path
    recipes: tuple[str, ...]
    provenance: Mapping[str, Any]
    cache_state: str

    @property
    def cache_hit(self) -> bool:
        return self.cache_state == "hit"

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "parent_image": self.parent_image,
            "parent_digest": self.parent_digest,
            "image": self.image,
            "digest": self.digest,
            "context_dir": str(self.context_dir),
            "recipes": list(self.recipes),
            "provenance": _json_safe(self.provenance),
            "cache_state": self.cache_state,
            "cache_hit": self.cache_hit,
        }


@dataclass(frozen=True)
class ComposeExtensionPlan:
    """Complete paired web/wpcli child-image plan."""

    server: str
    platform: str
    architecture: str
    catalog_version: str
    requirements: Mapping[str, Any]
    parent_digests: Mapping[str, str]
    digest: str
    context_dir: Path
    web: ImagePlan
    wpcli: ImagePlan
    cache_state: str
    buildable: bool = True

    @property
    def cache_hit(self) -> bool:
        return self.cache_state == "hit"

    # Aliases make the seam pleasant for callers that use ``cli``/``wp_cli``
    # terminology while keeping ``wpcli`` aligned with the Compose service.
    @property
    def cli(self) -> ImagePlan:
        return self.wpcli

    @property
    def wp_cli(self) -> ImagePlan:
        return self.wpcli

    def as_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "platform": self.platform,
            "architecture": self.architecture,
            "catalog_version": self.catalog_version,
            "requirements": _json_safe(self.requirements),
            "parent_digests": dict(self.parent_digests),
            "digest": self.digest,
            "context_dir": str(self.context_dir),
            "web": self.web.as_dict(),
            "wpcli": self.wpcli.as_dict(),
            "cache_state": self.cache_state,
            "cache_hit": self.cache_hit,
            "buildable": self.buildable,
        }


# The catalog is intentionally small.  Adding a recipe is a code-reviewed
# change; callers cannot provide package names, URLs, shell, or Dockerfiles.
# Core PHP extensions that official WordPress images can compile are listed
RECIPES: dict[str, Recipe] = {
    "bcmath": Recipe("php-bcmath", "bcmath", ("docker-php-ext-install bcmath",)),
    "gd": Recipe(
        "php-gd",
        "gd",
        (
            "docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp",
            "docker-php-ext-install gd",
        ),
        ("libfreetype6-dev", "libjpeg62-turbo-dev", "libpng-dev", "libwebp-dev"),
    ),
    "imagick": Recipe(
        "assert-imagick",
        "imagick",
        (),
        (),
        (),
        provisionable=False,
    ),
    "intl": Recipe("php-intl", "intl", ("docker-php-ext-install intl",), ("libicu-dev",)),
    # Official WordPress web and CLI parents already provide the profile's
    # core mbstring/mysqli capabilities. Treat them as assertions and verify
    # them on every runtime plane instead of recompiling bundled PHP sources:
    # recompilation is both unnecessary and can fail when a parent omits the
    # build-only headers that its already-installed module no longer needs.
    "mbstring": Recipe(
        "assert-mbstring", "mbstring", (), (), (),
        provisionable=False, assertion_only=True,
    ),
    "mysqli": Recipe(
        "assert-mysqli", "mysqli", (), (), (),
        provisionable=False, assertion_only=True,
    ),
    "opcache": Recipe("php-opcache", "opcache", ("docker-php-ext-install opcache",)),
    "pdo_mysql": Recipe("php-pdo-mysql", "pdo_mysql", ("docker-php-ext-install pdo_mysql",)),
    "zip": Recipe("php-zip", "zip", ("docker-php-ext-install zip",), ("libzip-dev",)),
    "xdebug": Recipe(
        "assert-xdebug",
        "xdebug",
        (),
        (),
        (),
        provisionable=False,
    ),
}

# The official WordPress web parents are Debian-based while the official
# ``wordpress:cli`` parent is Alpine-based. Keep the translation literal and
# code-reviewed; configuration never supplies package names.
_WPCLI_ALPINE_PACKAGES = {
    "libfreetype6-dev": "freetype-dev",
    "libjpeg62-turbo-dev": "libjpeg-turbo-dev",
    "libpng-dev": "libpng-dev",
    "libwebp-dev": "libwebp-dev",
    "libicu-dev": "icu-dev",
    "libzip-dev": "libzip-dev",
}

# Profile-required extensions are usually already present in the official
# WordPress PHP image.  They still need an immutable assertion in the plan,
# but do not need a package-install recipe.  Keeping them in the catalog makes
# a normalized ``wordpress@1`` model consumable without turning those names
# into user-controlled build instructions.
for _builtin_name in (
    "curl", "dom", "exif", "fileinfo", "filter", "hash", "iconv", "json",
    "mysqlnd", "openssl", "pcre", "pdo", "pdo_sqlite", "phar", "session",
    "simplexml", "sodium", "sqlite3", "tokenizer", "xml", "xmlreader", "xmlwriter",
    "calendar", "ctype", "ftp", "gettext", "imap", "posix", "readline", "soap",
    "sockets", "sysvmsg", "sysvsem", "sysvshm",
):
    RECIPES.setdefault(
        _builtin_name,
        Recipe(f"assert-{_builtin_name}", _builtin_name, (), assertion_only=True),
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _recipe_catalog_digest() -> str:
    """Bind generated Dockerfile behavior, including role package mapping."""
    return "sha256:" + _sha256({
        "dockerfile_generator": 2,
        "recipes": {
            name: {
                "id": recipe.recipe_id,
                "extension": recipe.extension,
                "commands": recipe.build_commands,
                "packages": recipe.packages,
                "versions": recipe.supported_versions,
                "provisionable": recipe.provisionable,
                "assertion_only": recipe.assertion_only,
                "ini_disableable": recipe.ini_disableable,
            }
            for name, recipe in sorted(RECIPES.items())
        },
        "wpcli_alpine_packages": _WPCLI_ALPINE_PACKAGES,
    })


def _sandbox_home() -> Path:
    return Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()


def extension_build_root() -> Path:
    """Return the managed cache root without creating it."""
    return _sandbox_home() / "runtime" / "build" / "php-extensions"


def _normalise_version(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ComposeBuilderError(f"extension {name!r} version must be a string")
    value = value.strip()
    if value == "php" or _VERSION_RE.fullmatch(value):
        return value
    raise ComposeBuilderError(f"extension {name!r} version is invalid: {value!r}")


def normalize_requirements(requirements: Any) -> dict[str, Any]:
    """Normalize a config/model representation into canonical requirements.

    Accepted public forms are the Sol-reviewed ``{profile, extensions}``
    mapping and an object exposing ``to_dict()``.  The normalizer purposely
    rejects arbitrary keys so a package/url/shell cannot leak into a recipe.
    """
    if hasattr(requirements, "to_dict") and callable(requirements.to_dict):
        requirements = requirements.to_dict()
    if not isinstance(requirements, Mapping):
        raise ComposeBuilderError("php extension requirements must be a mapping")
    allowed = {
        "profile", "extensions", "catalog_version", "catalogVersion",
        # Derived, immutable fields emitted by PhpExtensionsConfig.to_dict().
        # They are accepted for model interoperability but are not recipe
        # inputs; the canonical extension map remains the source of identity.
        "required", "capabilities",
    }
    unknown = set(requirements) - allowed
    if unknown:
        raise ComposeBuilderError("unknown php extension requirement keys: " + ", ".join(sorted(map(str, unknown))))
    raw_ext = requirements.get("extensions", {})
    if not isinstance(raw_ext, Mapping):
        raise ComposeBuilderError("phpExtensions.extensions must be a mapping")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_value in raw_ext.items():
        if not isinstance(raw_name, str):
            raise ComposeBuilderError("php extension names must be strings")
        name = raw_name.strip().lower().replace("-", "_")
        if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ComposeBuilderError(f"invalid php extension name: {raw_name!r}")
        if name not in RECIPES:
            raise UnsupportedExtensionError(f"php extension {name!r} has no allowlisted recipe")
        if isinstance(raw_value, bool):
            state, version = ("enabled" if raw_value else "disabled"), None
        elif isinstance(raw_value, str):
            state, version = "enabled", _normalise_version(raw_value, name=name)
        elif isinstance(raw_value, Mapping):
            extra = set(raw_value) - {"state", "version"}
            if extra:
                raise ComposeBuilderError(f"unknown keys for extension {name!r}: {', '.join(sorted(map(str, extra)))}")
            state = str(raw_value.get("state", "enabled")).strip().lower()
            if state not in {"enabled", "disabled"}:
                raise ComposeBuilderError(f"extension {name!r} state must be enabled or disabled")
            version = _normalise_version(raw_value.get("version"), name=name)
        else:
            raise ComposeBuilderError(f"extension {name!r} must be boolean, version, or state mapping")
        if state == "disabled" and version is not None:
            raise ComposeBuilderError(f"disabled extension {name!r} cannot specify a version")
        recipe = RECIPES[name]
        if state == "disabled" and not recipe.ini_disableable:
            raise UnsupportedExtensionError(
                f"extension {name!r} cannot be disabled by the Compose builder",
                code="unsupported_disable",
            )
        if state == "enabled" and not recipe.provisionable and not recipe.assertion_only:
            raise UnsupportedExtensionError(
                f"extension {name!r} has no allowlisted v1 provisioning recipe",
                code="unsupported_provisioning",
            )
        if version and recipe.supported_versions and version not in recipe.supported_versions and version != "php":
            raise UnsupportedExtensionError(
                f"extension {name!r} version {version!r} is not in the {CATALOG_VERSION} catalog"
            )
        normalized[name] = {"state": state, **({"version": version} if version is not None else {})}
    profile = requirements.get("profile")
    if profile is not None:
        if not isinstance(profile, str) or profile.strip() != CATALOG_VERSION:
            raise ComposeBuilderError(f"unsupported php extension profile: {profile!r}")
        profile = CATALOG_VERSION
    catalog = requirements.get("catalog_version", requirements.get("catalogVersion", CATALOG_VERSION))
    if catalog != CATALOG_VERSION:
        raise ComposeBuilderError(f"unsupported php extension catalog: {catalog!r}")
    return {"profile": profile, "catalog_version": CATALOG_VERSION, "extensions": normalized}


def _parse_image(image: str, *, role: str) -> tuple[str, str | None, str | None]:
    if not isinstance(image, str) or not image.strip():
        raise UnsupportedParentImageError(f"{role} parent image is required")
    match = _IMAGE_REF_RE.fullmatch(image.strip())
    if not match:
        raise UnsupportedParentImageError(f"invalid {role} parent image reference")
    return match.group("repo").lower(), match.group("tag"), match.group("digest")


def _verify_digest(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value.lower()):
        raise ComposeBuilderError(f"{label} must be a full sha256 digest")
    return value.lower()


def _official_wordpress_parent(image: str, *, server: str) -> tuple[str, str | None]:
    repo, tag, embedded = _parse_image(image, role="web")
    if repo not in {"wordpress", "docker.io/library/wordpress", "index.docker.io/library/wordpress"}:
        raise UnsupportedParentImageError("PHP extension builds require an official wordpress image")
    server = server.lower()
    if server not in {"apache", "nginx"}:
        raise UnsupportedParentImageError(
            f"PHP extension child images support Apache/nginx only; {server!r} is validate-only"
        )
    if tag:
        is_fpm = tag.endswith("-fpm")
        is_apache = tag.endswith("-apache") or (not is_fpm and tag not in {"cli", "cli-latest"})
        if server == "nginx" and not is_fpm:
            raise UnsupportedParentImageError("nginx PHP extension builds require an official wordpress FPM image")
        if server == "apache" and is_fpm:
            raise UnsupportedParentImageError("Apache PHP extension builds require an official wordpress Apache image")
        if not is_apache and server == "apache":
            raise UnsupportedParentImageError("parent image is not an official wordpress Apache image")
    return image.strip(), embedded


def _official_wpcli_parent(image: str) -> tuple[str, str | None]:
    repo, tag, embedded = _parse_image(image, role="wpcli")
    if repo not in {"wordpress", "docker.io/library/wordpress", "index.docker.io/library/wordpress"}:
        raise UnsupportedParentImageError("PHP extension builds require an official wordpress wpcli image")
    if not tag or not tag.startswith("cli"):
        raise UnsupportedParentImageError("wpcli child image requires an official wordpress:cli parent")
    return image.strip(), embedded


def _parent_digest(value: Any, *, image: str, embedded: str | None, role: str) -> str:
    if value is None:
        value = embedded
    if value is None:
        raise ComposeBuilderError(
            f"{role} parent digest is required; resolve the official image digest before planning"
        )
    return _verify_digest(value, label=f"{role} parent digest")


def _cache_state(context_dir: Path, digest: str) -> str:
    provenance = context_dir / "provenance.json"
    if not provenance.is_file():
        return "miss" if not context_dir.exists() else "invalidated"
    try:
        data = json.loads(provenance.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "invalidated"
    if data.get("digest") != digest:
        return "invalidated"
    file_digests = data.get("context_files")
    if (not isinstance(file_digests, Mapping)
            or set(file_digests) != {"Dockerfile.web", "Dockerfile.wpcli"}):
        return "invalidated"
    for name, expected in file_digests.items():
        path = context_dir / str(name)
        try:
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "invalidated"
        if observed != expected:
            return "invalidated"
    return "hit"


def _recipe_ids(reqs: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        RECIPES[name].recipe_id
        for name, value in reqs.get("extensions", {}).items()
        if value.get("state") == "enabled" and RECIPES[name].provisionable
    )


def plan_compose_extension_images(
    requirements: Any,
    *,
    parent_image: str,
    wpcli_image: str,
    parent_digest: str | None = None,
    wpcli_parent_digest: str | None = None,
    server: str = "nginx",
    php_version: str | None = None,
    platform: str | None = None,
    architecture: str | None = None,
    catalog_version: str = CATALOG_VERSION,
    expected_digest: str | None = None,
) -> ComposeExtensionPlan:
    """Return a deterministic, side-effect-free paired image plan.

    ``parent_digest`` and ``wpcli_parent_digest`` must be resolved from the
    registry/runtime by the caller.  Tags are intentionally insufficient:
    resolving a rolling tag inside this function would make cache identity and
    provenance unverifiable.
    """
    if catalog_version != CATALOG_VERSION:
        raise ComposeBuilderError(f"unsupported php extension catalog: {catalog_version!r}")
    server = (server or "nginx").strip().lower()
    normalized = normalize_requirements(requirements)
    if php_version is not None:
        if not isinstance(php_version, str) or not re.fullmatch(r"\d+\.\d+", php_version.strip()):
            raise ComposeBuilderError("php_version must be a major.minor string")
        php_version = php_version.strip()
    web_parent, embedded_web = _official_wordpress_parent(parent_image, server=server)
    cli_parent, embedded_cli = _official_wpcli_parent(wpcli_image)
    web_digest = _parent_digest(parent_digest, image=web_parent, embedded=embedded_web, role="web")
    cli_digest = _parent_digest(wpcli_parent_digest, image=cli_parent, embedded=embedded_cli, role="wpcli")
    platform_name = (platform or _platform.system().lower()).strip().lower()
    arch_name = (architecture or _platform.machine()).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", platform_name):
        raise ComposeBuilderError("platform is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", arch_name):
        raise ComposeBuilderError("architecture is invalid")
    canonical = {
        "catalog_version": CATALOG_VERSION,
        "recipe_catalog_digest": _recipe_catalog_digest(),
        "requirements": normalized,
        "parent_digests": {"web": web_digest, "wpcli": cli_digest},
        "parent_images": {"web": web_parent, "wpcli": cli_parent},
        "php_version": php_version,
        "server": server,
        "platform": platform_name,
        "architecture": arch_name,
    }
    digest = "sha256:" + _sha256(canonical)
    if expected_digest is not None:
        expected = expected_digest if expected_digest.startswith("sha256:") else "sha256:" + expected_digest
        if expected.lower() != digest:
            raise DigestMismatchError(f"supplied extension plan digest does not match computed digest {digest}")
    context_dir = extension_build_root() / digest
    recipe_ids = _recipe_ids(normalized)
    provenance = {
        "schema": "sandbox.php_extensions.compose_builder/1",
        "digest": digest,
        **canonical,
        "recipe_ids": list(recipe_ids),
        "preserves": ["database volumes", "uploads", "project files"],
    }
    web_role_digest = "sha256:" + _sha256({"plan": digest, "role": "web"})
    cli_role_digest = "sha256:" + _sha256({"plan": digest, "role": "wpcli"})
    web_state = _cache_state(context_dir, digest)
    web = ImagePlan(
        "web", web_parent, web_digest,
        f"sandbox/php-extensions-web:{web_role_digest.removeprefix('sha256:')}",
        web_role_digest, context_dir, recipe_ids, provenance, web_state,
    )
    wpcli = ImagePlan(
        "wpcli", cli_parent, cli_digest,
        f"sandbox/php-extensions-wpcli:{cli_role_digest.removeprefix('sha256:')}",
        cli_role_digest, context_dir, recipe_ids, provenance, web_state,
    )
    return ComposeExtensionPlan(
        server, platform_name, arch_name, CATALOG_VERSION, normalized,
        {"web": web_digest, "wpcli": cli_digest}, digest, context_dir,
        web, wpcli, web_state,
    )


def _dockerfile(plan: ComposeExtensionPlan, role: str) -> str:
    image = plan.web if role == "web" else plan.wpcli
    lines = [
        "# Generated by Sandbox from an immutable allowlisted recipe catalog.",
        f"# plan-digest: {plan.digest}",
        # Pin the build input by digest even when the human-facing image
        # reference was supplied as a tag.  Provenance then remains useful
        # after the registry tag moves.
        f"FROM {image.parent_image.split('@', 1)[0]}@{image.parent_digest}",
    ]
    if role == "wpcli":
        # The official CLI image runs as the WordPress user. Elevate only in
        # the build layer for catalogued packages/extension compilation, then
        # restore the published runtime identity.
        lines.append("USER root")
    enabled = [
        (name, value)
        for name, value in plan.requirements["extensions"].items()
        if value.get("state") == "enabled"
    ]
    for name, value in enabled:
        recipe = RECIPES[name]
        if recipe.packages:
            if role == "wpcli":
                try:
                    packages = " ".join(_WPCLI_ALPINE_PACKAGES[item]
                                        for item in recipe.packages)
                except KeyError as exc:
                    raise BuildContextError(
                        f"wpcli package mapping is unavailable for {exc.args[0]}"
                    ) from exc
                lines.append(f"RUN apk add --no-cache {packages}")
            else:
                packages = " ".join(recipe.packages)
                lines.append(f"RUN apt-get update && apt-get install -y --no-install-recommends {packages} && rm -rf /var/lib/apt/lists/*")
        lines.extend(f"RUN {command}" for command in recipe.build_commands)
    if role == "wpcli":
        lines.append("USER 33:33")
    lines.append(f"LABEL org.sandbox.php-extensions.digest={plan.digest}")
    lines.append(f"LABEL org.sandbox.php-extensions.role={role}")
    # The catalog digest is the provenance receipt for the generated recipe
    # set.  Lifecycle code verifies all three labels before trusting a cached
    # tag; a retagged/unrelated image therefore cannot satisfy this plan.
    lines.append(
        "LABEL org.sandbox.php-extensions.provenance="
        f"{plan.web.provenance['recipe_catalog_digest']}"
    )
    return "\n".join(lines) + "\n"


def materialize_compose_extension_context(plan: ComposeExtensionPlan) -> Path:
    """Write a deterministic managed context, on explicit caller request.

    This function only creates files below the digest selected by
    :func:`plan_compose_extension_images`; it never runs Docker or installs a
    package.  Existing matching contexts are reused.  A context with a
    different provenance is rejected rather than overwritten.
    """
    expected_root = extension_build_root().resolve()
    context = plan.context_dir.resolve()
    try:
        context.relative_to(expected_root)
    except ValueError as exc:
        raise BuildContextError("extension build context must remain under SANDBOX_HOME runtime build root") from exc
    context.mkdir(parents=True, exist_ok=True)
    provenance_file = context / "provenance.json"
    if provenance_file.exists():
        if _cache_state(context, plan.digest) != "hit":
            raise BuildContextError("refusing to overwrite an invalidated extension build context")
        return context
    (context / "Dockerfile.web").write_text(_dockerfile(plan, "web"), encoding="utf-8")
    (context / "Dockerfile.wpcli").write_text(_dockerfile(plan, "wpcli"), encoding="utf-8")
    context_files = {
        name: hashlib.sha256((context / name).read_bytes()).hexdigest()
        for name in ("Dockerfile.web", "Dockerfile.wpcli")
    }
    payload = plan.as_dict() | {
        "digest": plan.digest,
        "recipe_catalog_digest": plan.web.provenance["recipe_catalog_digest"],
        "context_files": context_files,
    }
    provenance_file.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return context


# Compatibility aliases for the planning seam; keep the canonical function
# name explicit while allowing service code to use the shorter terminology.
plan_extension_images = plan_compose_extension_images
materialize_extension_build_context = materialize_compose_extension_context


__all__ = [
    "CATALOG_VERSION", "RECIPES", "BuildContextError", "ComposeBuilderError",
    "ComposeExtensionPlan", "DigestMismatchError", "ExtensionRequirement",
    "ImagePlan", "UnsupportedExtensionError", "UnsupportedParentImageError",
    "extension_build_root", "materialize_compose_extension_context",
    "materialize_extension_build_context", "normalize_requirements",
    "plan_compose_extension_images", "plan_extension_images",
]

"""Archive Plugin Check integration boundaries.

The host CLI owns archive validation and target creation.  The disposable
runtime is launched in a fresh Python process with the run-local Sandbox
environment already set, so importing the legacy runtime modules cannot reuse
the caller's registry, Compose directory, or secrets.  This module also keeps
the child result deliberately JSON-shaped: the parent can gate the caller's
baseline only after the cleanup receipt is complete.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

from .journal import (
    PLANE_ORDER,
    ArchiveCleanupError,
    ArchiveCleanupService,
    ArchivePhaseError,
    ArchiveReviewJournal,
    CleanupPlane,
)
from .result import (
    ArchiveResultError,
    archive_error_counts,
    normalize_archive_findings,
    persist_archive_artifact,
)
from .target import ArchiveTargetError, PluginCheckPin


RESULT_PREFIX = "SANDBOX_ARCHIVE_RESULT="
_VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.+_-]*$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ArchiveRunnerError(ValueError):
    """A deterministic archive runtime/provenance boundary failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _archive_block(pconf: Mapping[str, object]) -> Mapping[str, object]:
    plugin_check = pconf.get("pluginCheck")
    if not isinstance(plugin_check, Mapping):
        return {}
    archive = plugin_check.get("archive")
    return archive if isinstance(archive, Mapping) else {}


def _source_from_entry(entry: object) -> str | None:
    if isinstance(entry, Mapping):
        source = entry.get("source")
        if isinstance(source, Mapping):
            value = source.get("value")
            return value if isinstance(value, str) else None
        if isinstance(source, str):
            return source
        for key in ("zip", "url"):
            value = entry.get(key)
            if isinstance(value, str):
                return value
    return entry if isinstance(entry, str) else None


def _version_from_source(source: str | None) -> str | None:
    if not source:
        return None
    match = re.search(r"plugin-check\.([0-9][A-Za-z0-9.+_-]*)\.zip(?:$|[?#])", source)
    return match.group(1) if match else None


def _validated_version(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise ArchiveRunnerError("archive_provenance_missing", f"{label} is not pinned")
    return value


def _validated_revision(value: object) -> str:
    if not isinstance(value, str) or not _REVISION_RE.fullmatch(value):
        raise ArchiveRunnerError("archive_provenance_missing", "Sandbox revision is not pinned")
    return value.lower()


def _git_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    revision = (result.stdout or "").strip() if result is not None and result.returncode == 0 else ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ArchiveRunnerError("archive_provenance_missing", "Sandbox revision is unavailable")
    return revision.lower()


def resolve_archive_provenance(
    pconf: Mapping[str, object],
    *,
    sandbox_root: os.PathLike[str] | str,
) -> tuple[PluginCheckPin, dict[str, str]]:
    """Resolve an explicit checker/WP/PHP/Sandbox provenance tuple.

    Archive mode never treats a floating ``latest`` dependency as evidence.
    The checker may inherit its URL/version from the resolved plugin map, but
    its digest and the runtime's WP/PHP pins must be declared under
    ``pluginCheck.archive``.  This keeps normal source-tree checks unchanged
    while making an archive result reproducible.
    """

    archive = _archive_block(pconf)
    resolved = pconf.get("plugins_resolved")
    entry = resolved.get("plugin-check") if isinstance(resolved, Mapping) else None
    source = archive.get("source") if isinstance(archive.get("source"), str) else _source_from_entry(entry)
    version = archive.get("version") or _version_from_source(source if isinstance(source, str) else None)
    digest = archive.get("sha256") or archive.get("digest")
    if not isinstance(source, str) or not source.startswith("https://") or not source.endswith(".zip"):
        raise ArchiveRunnerError("archive_provenance_missing", "Plugin Check source must be a pinned HTTPS ZIP URL")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ArchiveRunnerError("archive_provenance_missing", "Plugin Check SHA-256 digest is missing")
    try:
        pin = PluginCheckPin(
            source=source,
            version=_validated_version(version, label="Plugin Check version"),
            sha256=digest,
        )
    except ArchiveTargetError as exc:
        raise ArchiveRunnerError(exc.code, str(exc)) from exc

    wordpress = archive.get("wordpressVersion") or pconf.get("wpVersion")
    php = archive.get("phpVersion") or pconf.get("phpVersion")
    wordpress_version = _validated_version(wordpress, label="WordPress version")
    php_version = _validated_version(php, label="PHP version")
    revision = archive.get("sandboxRevision")
    sandbox_revision = (
        _validated_revision(revision)
        if revision is not None
        else _git_revision(Path(sandbox_root).expanduser().resolve())
    )
    return pin, {
        "plugin_check": f"{pin.version}@{pin.sha256.lower()}",
        "wordpress": wordpress_version,
        "php": php_version,
        "sandbox": sandbox_revision,
    }


def _safe_runtime_config(sandbox_home: Path) -> dict[str, object]:
    """Return bounded runtime defaults with no machine-global inputs."""

    return {
        "version": "0.1.0",
        "defaults": {
            "plugins_home": str(sandbox_home / "plugins"),
            "github_org": "",
        },
        "runtime": {
            "wordpress_port": 8188,
            "db_port": 3318,
            "mailpit_port": 8125,
            "wordpress_image": "wordpress:latest",
            "mariadb_image": "mariadb:latest",
            "wpcli_image": "wordpress:cli",
            "server": "nginx",
            "admin": {
                "user": "admin",
                "password": "admin",
                "email": "admin@example.com",
                "site_title": "Sandbox Archive Review",
            },
        },
        "instances": {},
        "mcp": {},
        "resources": {},
    }


def _isolated_runtime_loader(base: Mapping[str, object], sandbox_home: Path):
    """Load only the run-local defaults plus its generated local state.

    Provisioning writes ports, mounts, and generated credentials to
    ``sandbox.local.yml``.  Returning only ``base`` after that write makes the
    next Compose render forget the archive mount.  Merge this one owner-only
    file and nothing else; the normal machine config and user-global catalog
    are never consulted.
    """

    def merge(left: Mapping[str, object], right: Mapping[str, object]) -> dict[str, object]:
        output = dict(left)
        for key, value in right.items():
            if isinstance(value, Mapping) and isinstance(output.get(key), Mapping):
                output[key] = merge(output[key], value)
            else:
                output[key] = value
        return output

    def load() -> dict[str, object]:
        local_path = sandbox_home / "sandbox.local.yml"
        local: Mapping[str, object] = {}
        if local_path.is_file() and not local_path.is_symlink():
            try:
                import yaml
                value = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
                if isinstance(value, Mapping):
                    local = value
            except (OSError, UnicodeError, ValueError, TypeError):
                local = {}
        return merge(base, local)

    return load


def _isolated_project_config(
    descriptor: Mapping[str, object],
    *,
    review_root: Path,
    plugin_path: Path,
    plugin_slug: str,
) -> dict[str, object]:
    """Materialize the descriptor without invoking the global config loader.

    ``ensure_instance`` historically asks the legacy ``sandbox_core`` loader
    for a project descriptor.  That loader intentionally merges the user's
    machine catalog and local overrides, which is correct for normal projects
    but unsafe for an untrusted archive.  Archive mode supplies this small,
    already-normalized config instead; the child patches the loader to return
    it for this process only.
    """

    resolved: dict[str, dict[str, object]] = {
        "plugin-check": {
            "source": {
                "kind": "zip",
                "value": str(
                    ((descriptor.get("archiveReview") or {}).get("pluginCheck") or {}).get("source", "")
                ),
            },
            "active": True,
            "on_demand": False,
        },
    }
    # The descriptor is allowlisted by the host builder.  Keep only its
    # scalar/runtime fields and the two exact plugin identities above; never
    # copy arbitrary hooks, aliases, credentials, or mappings into the child.
    review = descriptor.get("archiveReview")
    review = review if isinstance(review, Mapping) else {}
    plugin_check = descriptor.get("pluginCheck")
    plugin_check = plugin_check if isinstance(plugin_check, Mapping) else {}
    return {
        "root": str(review_root),
        "source": "archive-descriptor",
        "kind": "wordpress",
        "slug": plugin_slug,
        # The archive tree is mounted explicitly and linked into the WP
        # plugin directory after core install.  Keeping it out of
        # ``plugins_resolved`` prevents the normal provisioning reconciler
        # from issuing a failing ``plugin deactivate`` for a plugin whose
        # non-slug entrypoint is intentionally inactive.
        "plugins": [str(plugin_path)],
        "plugins_resolved": resolved,
        "themes": [],
        "mappings": {},
        "mappings_inactive": {},
        "server": "nginx",
        "phpVersion": review.get("provenance", {}).get("php") if isinstance(review.get("provenance"), Mapping) else None,
        "wpVersion": review.get("provenance", {}).get("wordpress") if isinstance(review.get("provenance"), Mapping) else None,
        "multisite": False,
        "config": {},
        "port": None,
        "pluginCheck": {
            "excludeDirectories": list(plugin_check.get("excludeDirectories") or []),
            "versionFile": plugin_check.get("versionFile"),
            "baselineFile": plugin_check.get("baselineFile"),
        },
        "wpCron": {"enabled": False},
        "instanceLifecycle": None,
    }


def _load_descriptor(path: os.PathLike[str] | str) -> dict[str, object]:
    descriptor_path = Path(path).expanduser().resolve(strict=False)
    try:
        data = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ArchiveRunnerError("archive_target_failed", "review descriptor cannot be read") from exc
    if not isinstance(data, dict):
        raise ArchiveRunnerError("archive_target_failed", "review descriptor is not an object")
    review = data.get("archiveReview")
    if not isinstance(review, Mapping):
        raise ArchiveRunnerError("archive_target_failed", "review descriptor lacks archive identity")
    required = (
        "archiveSlug", "mainFile", "reviewInstance", "artifactDir",
        "provenance", "reviewProjectRoot", "extractionRoot", "sandboxHome",
    )
    if any(not isinstance(review.get(key), (str, Mapping)) for key in required):
        raise ArchiveRunnerError("archive_target_failed", "review descriptor is incomplete")
    return data


def _result_base(descriptor: Mapping[str, object]) -> dict[str, object]:
    review = descriptor["archiveReview"]
    assert isinstance(review, Mapping)
    provenance = review.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    plugin_check = provenance.get("pluginCheck")
    if isinstance(plugin_check, Mapping):
        provenance["plugin_check"] = (
            f"{plugin_check.get('version')}@{str(plugin_check.get('sha256', '')).lower()}"
        )
        provenance.pop("pluginCheck", None)
    return {
        "ok": False,
        "action": "check",
        "plugin_slug": review.get("archiveSlug"),
        "errors": 0,
        "warnings": 0,
        "baseline_total": 0,
        "new_count": 0,
        "violations": [],
        "baseline_exists": None,
        "message": None,
        "error": None,
        "input_mode": "archive",
        "archive_sha256": descriptor.get("archiveReview", {}).get("archiveSha256"),
        "archive_slug": review.get("archiveSlug"),
        "main_file": review.get("mainFile"),
        "member_count": descriptor.get("archiveReview", {}).get("memberCount"),
        "member_manifest_sha256": review.get("memberManifestSha256"),
        "review_instance": review.get("reviewInstance"),
        "checker_provenance": provenance,
        "findings": [],
    }


def _docker_ids(core, kind: str, instance: str) -> list[str] | None:
    compose_path = core.compose_file(instance)
    if not compose_path.exists():
        return []
    try:
        result = core.run(
            ["docker", kind, "ls", "-q", "--filter", f"label=com.docker.compose.project={core.project_name(instance)}"],
            check=False,
            capture=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    return [line.strip() for line in (getattr(result, "stdout", "") or "").splitlines() if line.strip()]


def _compose_empty(core, instance: str) -> bool:
    if not core.compose_file(instance).exists():
        return True
    try:
        result = core.compose("ps", "--format", "json", instance=instance, check=False, capture=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0 and not (getattr(result, "stdout", "") or "").strip()


def _cleanup_planes(
    core,
    journal: ArchiveReviewJournal,
    *,
    instance: str,
    review_root: Path,
    sandbox_home: Path,
    extraction_root: Path,
    artifact_dir: Path,
) -> dict[str, object]:
    compose_path = core.compose_file(instance)

    def down() -> None:
        core.compose(
            "down", "--volumes", "--remove-orphans", instance=instance,
            check=False, capture=True, timeout=120,
        )

    def no_containers() -> bool:
        return _compose_empty(core, instance)

    def no_network() -> bool:
        ids = _docker_ids(core, "network", instance)
        return ids is not None and not ids

    def no_volume() -> bool:
        ids = _docker_ids(core, "volume", instance)
        return ids is not None and not ids

    def remove_runtime() -> None:
        # Keep the registry file until its own plane runs.  The journal itself
        # lives beside the run root and is intentionally retained for audit.
        for path in (
            compose_path,
            sandbox_home / "runtime" / "compose",
            sandbox_home / "runtime" / f"wp-{instance}",
            sandbox_home / "runtime" / "dl-cache",
            sandbox_home / "runtime" / "bin",
            sandbox_home / "runtime" / "snapshots",
            sandbox_home / "runtime" / "test-suite",
            sandbox_home / "runtime" / "test-tools",
            review_root,
        ):
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

    def runtime_absent() -> bool:
        return not any(
            path.exists() for path in (
                compose_path,
                sandbox_home / "runtime" / "compose",
                sandbox_home / "runtime" / f"wp-{instance}",
                review_root,
            )
        )

    def remove_registry() -> None:
        try:
            remover = getattr(core, "registry_remove", None)
            if not callable(remover):
                # The split core package currently exposes the read/put
                # facades but not the legacy remove facade.  Resolve the
                # supported public compatibility API only as a fallback; do
                # not parse registry JSON in the archive runner.
                import sandbox_core as legacy_core
                remover = legacy_core.registry_remove
            remover(str(review_root), label="default")
        except Exception:
            # A boot failure can leave no registry record.  Verification below
            # is authoritative and keeps this callback idempotent.
            pass
        registry = sandbox_home / "runtime" / "registry.json"
        if registry.exists():
            registry.unlink()
        if sandbox_home.is_symlink():
            sandbox_home.unlink()
        elif sandbox_home.is_dir():
            shutil.rmtree(sandbox_home)

    def registry_absent() -> bool:
        return not sandbox_home.exists()

    def remove_extraction() -> None:
        if extraction_root.is_symlink():
            extraction_root.unlink()
        elif extraction_root.is_dir():
            shutil.rmtree(extraction_root)

    def extraction_absent() -> bool:
        return not extraction_root.exists()

    def report_complete() -> bool:
        return (
            artifact_dir.is_dir()
            and (artifact_dir / "result.json").is_file()
            and (artifact_dir / "plugin-check-report.html").is_file()
        )

    planes = (
        CleanupPlane("container", down, no_containers),
        CleanupPlane("network", down, no_network),
        CleanupPlane("volume", down, no_volume),
        CleanupPlane("runtime", remove_runtime, runtime_absent),
        CleanupPlane("registry", remove_registry, registry_absent),
        CleanupPlane("extraction", remove_extraction, extraction_absent),
        CleanupPlane("report", lambda: None, report_complete),
    )
    return ArchiveCleanupService(journal, planes).cleanup()


def run_archive_child(
    descriptor_path: os.PathLike[str] | str,
    journal_path: os.PathLike[str] | str,
) -> dict[str, object]:
    """Run the disposable runtime and always attempt journaled cleanup."""

    descriptor = _load_descriptor(descriptor_path)
    review = descriptor["archiveReview"]
    assert isinstance(review, Mapping)
    result = _result_base(descriptor)
    journal = ArchiveReviewJournal.open(journal_path)
    review_root = Path(str(review["reviewProjectRoot"])).expanduser().resolve()
    extraction_root = Path(str(review["extractionRoot"])).expanduser().resolve()
    sandbox_home = Path(str(review["sandboxHome"])).expanduser().resolve()
    artifact_dir = Path(str(review["artifactDir"])).expanduser().resolve()
    instance = str(review["reviewInstance"])
    plugin_slug = str(review["archiveSlug"])
    plugin_path = extraction_root / plugin_slug
    error_code: str | None = None
    error_message: str | None = None
    report_html = ""
    legacy_core = None
    instances_module = None
    original_project_loader = None
    original_runtime_loader = None
    original_legacy_runtime_loader = None

    try:
        # All legacy runtime imports happen after the parent supplied the
        # run-local SANDBOX_HOME.  The safe loader patch prevents a later
        # ensure_instance refresh from consulting the repository's global
        # sandbox.yml defaults or local state.
        import sandbox.core as core
        import sandbox.core._instances as instances_module
        import sandbox_core as legacy_core
        from sandbox.commands.plugin_check import (
            _parse_findings,
            _read_version_header,
            _run_wp_plugin_check,
        )

        safe_cfg = _safe_runtime_config(sandbox_home)
        isolated_pconf = _isolated_project_config(
            descriptor,
            review_root=review_root,
            plugin_path=plugin_path,
            plugin_slug=plugin_slug,
        )
        # ``ensure_instance`` reaches this compatibility loader through its
        # lazy ``_core()`` import.  Replace it only in this fresh child, so no
        # caller process or global config is modified.
        original_project_loader = legacy_core.load_project_config
        original_legacy_runtime_loader = getattr(legacy_core, "load_config", None)
        original_runtime_loader = instances_module.load_config
        legacy_core.load_project_config = lambda _project_dir, label=None: dict(isolated_pconf)
        isolated_loader = _isolated_runtime_loader(safe_cfg, sandbox_home)
        if original_legacy_runtime_loader is not None:
            legacy_core.load_config = isolated_loader
        instances_module.load_config = isolated_loader
        result["action"] = "check"

        def boot() -> object:
            return core.ensure_instance(
                safe_cfg,
                str(review_root),
                label="default",
                create=True,
            )

        entry = journal.execute_phase("boot", boot)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("instance"), str):
            raise ArchiveRunnerError("archive_isolation_failed", "review instance identity was not returned")
        actual_instance = str(entry["instance"])
        if actual_instance != instance:
            raise ArchiveRunnerError("archive_isolation_failed", "review instance identity drifted")

        def attach_target() -> object:
            # The host builder already mounted the extracted tree read-only
            # through the descriptor's absolute path.  Add exactly one
            # inactive symlink after WordPress core provisioning; this avoids
            # the normal managed-plugin reconciler trying to deactivate a
            # non-standard main filename during boot.
            from sandbox.core._provision import _force_symlink
            plugin_dir = core.plugins_dir(actual_instance)
            plugin_dir.mkdir(parents=True, exist_ok=True)
            _force_symlink(plugin_dir / plugin_slug, plugin_path)
            return str(plugin_dir / plugin_slug)

        journal.execute_phase("target", attach_target)
        active = core.wpcli(
            ["plugin", "is-active", plugin_slug],
            instance=actual_instance,
            check=False,
            capture=True,
            timeout=30,
        )
        if getattr(active, "returncode", 1) == 0:
            raise ArchiveRunnerError("archive_check_failed", "archive target became active")

        def check() -> list[dict[str, object]]:
            raw = _run_wp_plugin_check(
                actual_instance,
                plugin_slug,
                list((descriptor.get("pluginCheck") or {}).get("excludeDirectories") or []),
            )
            parsed = _parse_findings(raw, root=plugin_path)
            return normalize_archive_findings(parsed, plugin_path)

        findings = journal.execute_phase("check", check)
        if not isinstance(findings, list):
            raise ArchiveRunnerError("archive_check_failed", "Plugin Check findings are not a list")
        result["findings"] = findings
        result["errors"] = sum(1 for item in findings if item.get("type") == "ERROR")
        result["warnings"] = sum(1 for item in findings if item.get("type") == "WARNING")
        result["plugin_slug"] = plugin_slug
        result["archive_slug"] = plugin_slug
        result["member_count"] = int(review.get("memberCount") or 0)
        result["baseline_total"] = 0
        result["new_count"] = 0
        provenance = result.get("checker_provenance")
        checker_version = "unknown"
        wp_version = "unknown"
        php_version = "unknown"
        if isinstance(provenance, Mapping):
            checker_version = str(provenance.get("plugin_check", "unknown")).split("@", 1)[0]
            wp_version = str(provenance.get("wordpress", "unknown"))
            php_version = str(provenance.get("php", "unknown"))
        report_meta = {
            "plugin_slug": plugin_slug,
            "plugin_version": _read_version_header(plugin_path / Path(str(review["mainFile"])).name),
            "checker_version": checker_version,
            "wp_version": wp_version,
            "php_version": php_version,
            "exclude_directories": [],
            "baseline_total": 0,
            "new_count": 0,
            "baseline_file": "archive-review-baseline.json",
        }
        from sandbox.core._plugin_check_report import render_report
        report_html = render_report(findings, report_meta)
        result["ok"] = True
    except ArchivePhaseError as exc:
        error_code = {
            "boot": "archive_isolation_failed",
            "check": "archive_check_failed",
        }.get(exc.phase, "archive_check_failed")
        error_message = f"archive phase {exc.phase} failed"
    except ArchiveRunnerError as exc:
        error_code, error_message = exc.code, str(exc)
    except (ArchiveResultError, ArchiveTargetError) as exc:
        error_code, error_message = getattr(exc, "code", "archive_artifact_failed"), type(exc).__name__
    except SystemExit:
        error_code, error_message = "archive_check_failed", "disposable Plugin Check command failed"
    except Exception:
        error_code, error_message = "archive_check_failed", "disposable Plugin Check runtime failed"

    result["error"] = error_code
    if error_message is not None:
        result["message"] = error_message if "/" not in error_message and "\\" not in error_message else None

    # Retain an artifact before cleanup so the report plane has a concrete
    # postcondition.  A final write below records the complete receipt.
    try:
        if not report_html:
            from sandbox.core._plugin_check_report import render_report
            report_html = render_report([], {
                "plugin_slug": plugin_slug,
                "plugin_version": "unknown",
                "checker_version": "unknown",
                "wp_version": "unknown",
                "php_version": "unknown",
                "exclude_directories": [],
                "baseline_total": 0,
                "new_count": 0,
                "baseline_file": "archive-review-baseline.json",
            })
        persist_archive_artifact(artifact_dir, result, report_html, reports_root=artifact_dir.parent)
    except Exception:
        result["ok"] = False
        result["error"] = "archive_artifact_failed"
        result["message"] = None

    try:
        cleanup = _cleanup_planes(
            core,
            journal,
            instance=instance,
            review_root=review_root,
            sandbox_home=sandbox_home,
            extraction_root=extraction_root,
            artifact_dir=artifact_dir,
        )
    except BaseException:
        cleanup = {
            "status": "unknown",
            "receipt": journal.receipt_id,
            "planes": {name: "unknown" for name in PLANE_ORDER},
            "recovery_required": True,
            "journal": str(journal.path),
        }
    result["cleanup"] = cleanup
    if not isinstance(cleanup, Mapping) or cleanup.get("status") != "complete":
        result["ok"] = False
        result["error"] = "archive_cleanup_unknown"
        result["message"] = None
    elif result.get("error") is None:
        result["ok"] = True

    try:
        persist_archive_artifact(artifact_dir, result, report_html, reports_root=artifact_dir.parent)
    except Exception:
        result["ok"] = False
        result["error"] = "archive_artifact_failed"
        result["message"] = None
    if legacy_core is not None and original_project_loader is not None:
        legacy_core.load_project_config = original_project_loader
    if instances_module is not None and original_runtime_loader is not None:
        instances_module.load_config = original_runtime_loader
    if legacy_core is not None and original_legacy_runtime_loader is not None:
        legacy_core.load_config = original_legacy_runtime_loader
    return result


def launch_archive_runner(
    target,
    journal_path: os.PathLike[str] | str,
    *,
    timeout: int = 900,
    root: os.PathLike[str] | str,
) -> dict[str, object]:
    """Launch the child runner with only the target's environment allowlist."""

    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(
            item for item in (str(Path(root).expanduser().resolve()), os.environ.get("PYTHONPATH", ""))
            if item
        ),
        **target.environment,
    }
    command = [
        sys.executable,
        "-m",
        "sandbox.plugin_check.runner",
        "--descriptor",
        str(target.descriptor_path),
        "--journal",
        str(journal_path),
    ]

    def unknown_cleanup() -> dict[str, object]:
        return {
            "ok": False,
            "action": "check",
            "input_mode": "archive",
            "error": "archive_cleanup_unknown",
            "message": None,
            "cleanup": {
                "status": "unknown",
                "receipt": None,
                "planes": {name: "unknown" for name in PLANE_ORDER},
                "recovery_required": True,
                "journal": str(Path(journal_path).expanduser().resolve(strict=False)),
            },
        }
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(root).expanduser().resolve()),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        # A process that never returned a structured receipt may have left
        # containers or registry state behind.  Do not downgrade that to a
        # normal check failure; the journal must be recovered explicitly.
        return unknown_cleanup()
    for line in reversed((completed.stdout or "").splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                payload = json.loads(line[len(RESULT_PREFIX):])
            except (TypeError, ValueError):
                break
            if isinstance(payload, dict):
                return payload
    return unknown_cleanup()


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="sandbox archive runner")
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--journal", required=True)
    args = parser.parse_args(argv)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = run_archive_child(args.descriptor, args.journal)
    except BaseException:
        result = {
            "ok": False,
            "input_mode": "archive",
            "error": "archive_cleanup_unknown",
            "message": None,
            "cleanup": {
                "status": "unknown",
                "receipt": None,
                "planes": {name: "unknown" for name in PLANE_ORDER},
                "recovery_required": True,
                "journal": str(Path(args.journal).expanduser().resolve(strict=False)),
            },
        }
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "ArchiveRunnerError",
    "RESULT_PREFIX",
    "launch_archive_runner",
    "resolve_archive_provenance",
    "run_archive_child",
]

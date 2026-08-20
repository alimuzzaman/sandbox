"""Standalone, bounded PHP extension probes and cross-plane verification.

The probe is intentionally independent of WordPress and project code. It runs
only PHP built-ins (extension_loaded and ReflectionExtension), accepts a small
allow-listed name payload, and emits one bounded JSON document. Runtime adapters
can invoke the generated argv through their existing process gateway; this module
never invokes a shell or mutates an image/host.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import re
from typing import Any, Mapping

from .catalog import (
    DEFAULT_CATALOG,
    PhpExtensionCatalog,
    PhpExtensionCatalogError,
    normalize_requirements,
)


PROBE_MAX_OUTPUT_BYTES = 64 * 1024
PROBE_TIMEOUT_SECONDS = 5
_PLANES = frozenset({"web", "cli", "exec", "phpunit"})


# Do not add project paths, WordPress includes, shell calls, or environment
# dumps here. The input is supplied as a base64 JSON argument and the output is
# bounded and deterministic. The surrounding process runner supplies timeout
# and output limits.
STANDALONE_PROBE_PAYLOAD = r'''declare(strict_types=1);
$encoded = $argv[1] ?? '';
$decoded = base64_decode($encoded, true);
$names = is_string($decoded) ? json_decode($decoded, true) : null;
if (!is_array($names)) { fwrite(STDERR, "invalid probe input\n"); exit(2); }
$extensions = [];
foreach ($names as $name) {
    if (!is_string($name)) { fwrite(STDERR, "invalid extension name\n"); exit(2); }
    $enabled = extension_loaded($name);
    $version = null;
    if ($enabled) {
        try {
            $reflection = new ReflectionExtension($name);
            $version = $reflection->getVersion();
        } catch (Throwable $error) {
            $version = null;
        }
    }
    $extensions[$name] = [
        "enabled" => $enabled,
        "version" => is_string($version) && $version !== '' ? $version : null,
    ];
}
$result = [
    "schema_version" => 1,
    "php_version" => PHP_VERSION,
    "sapi" => PHP_SAPI,
    "extensions" => $extensions,
];
echo json_encode($result, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
'''

# A compatibility spelling used by callers that prefer the shorter name.
PHP_EXTENSION_PROBE_PAYLOAD = STANDALONE_PROBE_PAYLOAD


class PhpExtensionProbeError(ValueError):
    """A probe result cannot be trusted or cannot satisfy a requirement."""

    def __init__(self, message: str, *, code: str = "probe_failed",
                 plane: str | None = None, extension: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.plane = plane
        self.extension = extension


@dataclass(frozen=True)
class ProbeError:
    code: str
    message: str
    plane: str | None = None
    extension: str | None = None
    expected: str | None = None
    observed: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        for key, value in (("plane", self.plane), ("extension", self.extension),
                           ("expected", self.expected), ("observed", self.observed)):
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class ExtensionObservation:
    name: str
    enabled: bool
    version: str | None = None
    plane: str = "cli"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("extension observation name is invalid")
        if not isinstance(self.enabled, bool):
            raise ValueError("extension observation enabled state is invalid")
        if self.version is not None and (not isinstance(self.version, str) or not self.version):
            raise ValueError("extension observation version is invalid")
        _validate_plane(self.plane)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "enabled": self.enabled,
                "version": self.version, "plane": self.plane}


@dataclass(frozen=True)
class PlaneObservation:
    plane: str
    php_version: str | None
    extensions: tuple[ExtensionObservation, ...]
    sapi: str | None = None
    errors: tuple[ProbeError, ...] = ()

    def __post_init__(self) -> None:
        _validate_plane(self.plane)
        values = tuple(self.extensions)
        if len({item.name for item in values}) != len(values):
            raise ValueError("plane observation contains duplicate extensions")
        if self.php_version is not None and (not isinstance(self.php_version, str)
                                             or not self.php_version):
            raise ValueError("PHP runtime version is invalid")
        object.__setattr__(self, "extensions", values)
        object.__setattr__(self, "errors", tuple(self.errors))

    @property
    def by_name(self) -> dict[str, ExtensionObservation]:
        return {item.name: item for item in self.extensions}

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "plane": self.plane,
            "php_version": self.php_version,
            "sapi": self.sapi,
            "extensions": [item.to_dict() for item in self.extensions],
            "errors": [error.to_dict() for error in self.errors],
            "ok": self.ok,
        }


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    plane: str
    observation: PlaneObservation | None = None
    errors: tuple[ProbeError, ...] = ()
    stderr: str = ""
    exit_code: int | None = None

    def __post_init__(self) -> None:
        _validate_plane(self.plane)
        object.__setattr__(self, "errors", tuple(self.errors))
        if self.stderr and len(self.stderr) > 4096:
            object.__setattr__(self, "stderr", self.stderr[:4096])

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "plane": self.plane,
            "observation": self.observation.to_dict() if self.observation else None,
            "errors": [error.to_dict() for error in self.errors],
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }


@dataclass(frozen=True)
class PlaneComparison:
    ok: bool
    planes: tuple[str, ...]
    errors: tuple[ProbeError, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "planes": list(self.planes),
                "errors": [error.to_dict() for error in self.errors]}


def _validate_plane(plane: str) -> None:
    if plane not in _PLANES:
        raise ValueError(f"PHP observation plane is invalid: {plane!r}")


def _runner_result(result: object) -> tuple[int, str, str]:
    """Read the narrow ProcessResult contract without importing its class."""
    try:
        code = getattr(result, "returncode")
        stdout = getattr(result, "stdout")
        stderr = getattr(result, "stderr")
    except AttributeError as exc:
        raise PhpExtensionProbeError("probe runner returned an invalid result") from exc
    if (isinstance(code, bool) or not isinstance(code, int) or
            not isinstance(stdout, str) or not isinstance(stderr, str)):
        raise PhpExtensionProbeError("probe runner returned an invalid result")
    return code, stdout[:PROBE_MAX_OUTPUT_BYTES], stderr[:PROBE_MAX_OUTPUT_BYTES]


def probe_names(requirements: object, *, catalog: PhpExtensionCatalog = DEFAULT_CATALOG) -> tuple[str, ...]:
    normalized = normalize_requirements(requirements)
    for item in normalized:
        catalog.recipe(item["name"])
    return tuple(item["name"] for item in normalized)


def build_probe_command(requirements: object, *, catalog: PhpExtensionCatalog = DEFAULT_CATALOG,
                        php_binary: str = "php") -> tuple[str, ...]:
    """Build an argv-only invocation of the standalone payload.

    php_binary is an adapter-owned executable selected from its trusted runtime
    descriptor. It is deliberately not accepted from project config; callers
    may pass an already validated binary for testing.
    """
    if not isinstance(php_binary, str) or not php_binary or "\x00" in php_binary:
        raise ValueError("PHP executable is invalid")
    names = probe_names(requirements, catalog=catalog)
    encoded = base64.b64encode(json.dumps(names, separators=(",", ":")).encode()).decode("ascii")
    return (php_binary, "-d", "display_errors=0", "-d", "log_errors=0", "-r",
            STANDALONE_PROBE_PAYLOAD, encoded)


def _version_parts(value: str) -> tuple[int, ...] | None:
    match = re.match(r"^(\d+(?:\.\d+){0,3})", value)
    if not match:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None


def version_matches(expected: str | None, observed: str | None, *, php_version: str | None = None) -> bool:
    """Match exact, X.Y.*, or php constraints.

    php means the observed extension version belongs to the active PHP
    major/minor family. If either value is unavailable, the caller receives a
    separate version_unobservable error instead of a false mismatch.
    """
    if expected is None:
        return True
    if not isinstance(expected, str) or not expected:
        return False
    if observed is None or not isinstance(observed, str) or not observed:
        return False
    if expected == "php":
        expected_parts = _version_parts(php_version or "")
        observed_parts = _version_parts(observed)
        return bool(expected_parts and observed_parts and observed_parts[:2] == expected_parts[:2])
    if expected.endswith(".*"):
        prefix = expected[:-2]
        return observed == prefix or observed.startswith(prefix + ".")
    return observed == expected


def _parse_observation(document: object, *, plane: str) -> PlaneObservation:
    _validate_plane(plane)
    if not isinstance(document, Mapping):
        raise PhpExtensionProbeError("probe output must be a JSON object", plane=plane)
    if document.get("schema_version") != 1:
        raise PhpExtensionProbeError("probe output schema is unsupported", plane=plane)
    php_version = document.get("php_version")
    if php_version is not None and not isinstance(php_version, str):
        raise PhpExtensionProbeError("probe PHP version is invalid", plane=plane)
    sapi = document.get("sapi")
    if sapi is not None and not isinstance(sapi, str):
        raise PhpExtensionProbeError("probe SAPI is invalid", plane=plane)
    raw_extensions = document.get("extensions")
    if not isinstance(raw_extensions, Mapping):
        raise PhpExtensionProbeError("probe extensions are invalid", plane=plane)
    observations: list[ExtensionObservation] = []
    for name, raw in raw_extensions.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            raise PhpExtensionProbeError("probe extension row is invalid", plane=plane)
        enabled = raw.get("enabled")
        version = raw.get("version")
        if not isinstance(enabled, bool) or (version is not None and not isinstance(version, str)):
            raise PhpExtensionProbeError("probe extension state is invalid", plane=plane, extension=name)
        observations.append(ExtensionObservation(name, enabled, version, plane))
    return PlaneObservation(plane, php_version, tuple(sorted(observations, key=lambda item: item.name)), sapi)


def parse_probe_output(stdout: str, requirements: object = (), *, plane: str = "cli",
                       catalog: PhpExtensionCatalog = DEFAULT_CATALOG,
                       exit_code: int = 0, stderr: str = "") -> ProbeResult:
    """Parse and validate one bounded standalone probe result."""
    _validate_plane(plane)
    if not isinstance(stdout, str) or len(stdout) > PROBE_MAX_OUTPUT_BYTES:
        error = ProbeError("probe_output_too_large", "probe output exceeded the bounded limit", plane=plane)
        return ProbeResult(False, plane, errors=(error,), stderr=stderr, exit_code=exit_code)
    try:
        normalized = normalize_requirements(requirements)
        for item in normalized:
            catalog.recipe(item["name"])
    except PhpExtensionCatalogError as exc:
        error = ProbeError(exc.code, str(exc), plane=plane, extension=exc.extension)
        return ProbeResult(False, plane, errors=(error,), stderr=stderr, exit_code=exit_code)
    if exit_code != 0:
        code = "probe_timeout" if exit_code == 124 else "probe_failed"
        message = "PHP extension probe timed out" if code == "probe_timeout" else "PHP extension probe failed"
        return ProbeResult(False, plane,
                           errors=(ProbeError(code, message, plane=plane),),
                           stderr=stderr[:4096], exit_code=exit_code)
    try:
        document = json.loads(stdout)
        observation = _parse_observation(document, plane=plane)
    except (json.JSONDecodeError, PhpExtensionProbeError, ValueError) as exc:
        error = ProbeError("probe_output_invalid", str(exc), plane=plane)
        return ProbeResult(False, plane, errors=(error,), stderr=stderr[:4096], exit_code=exit_code)
    errors: list[ProbeError] = []
    by_name = observation.by_name
    for item in normalized:
        name = item["name"]
        expected_state = item["state"]
        row = by_name.get(name)
        if row is None or (expected_state == "enabled" and not row.enabled):
            errors.append(ProbeError("missing", f"extension {name} is not enabled", plane=plane,
                                     extension=name, expected=expected_state,
                                     observed="disabled" if row is not None else None))
            continue
        if expected_state == "disabled" and row.enabled:
            errors.append(ProbeError("unsupported_disable", f"extension {name} is enabled",
                                     plane=plane, extension=name, expected="disabled", observed="enabled"))
            continue
        expected_version = item.get("version")
        if expected_version is None:
            continue
        if row.version is None:
            errors.append(ProbeError("version_unobservable",
                                     f"extension {name} version is not observable", plane=plane,
                                     extension=name, expected=expected_version))
        elif not version_matches(expected_version, row.version, php_version=observation.php_version):
            errors.append(ProbeError("version_mismatch", f"extension {name} version does not match",
                                     plane=plane, extension=name, expected=expected_version,
                                     observed=row.version))
    all_errors = tuple(errors)
    return ProbeResult(not all_errors, plane, observation=observation, errors=all_errors,
                       stderr=stderr[:4096], exit_code=exit_code)


def run_probe(runner: object, requirements: object, *, plane: str = "cli", timeout: float = PROBE_TIMEOUT_SECONDS,
              catalog: PhpExtensionCatalog = DEFAULT_CATALOG, php_binary: str = "php") -> ProbeResult:
    """Run through an injected bounded process runner and parse the result."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 60:
        raise ValueError("probe timeout must be between 0 and 60 seconds")
    argv = build_probe_command(requirements, catalog=catalog, php_binary=php_binary)
    try:
        result = runner.run(argv, timeout=timeout)
        exit_code, stdout, stderr = _runner_result(result)
    except TimeoutError:
        return ProbeResult(False, plane,
                           errors=(ProbeError("probe_timeout", "PHP extension probe timed out", plane=plane),),
                           exit_code=None)
    except OSError as exc:
        return ProbeResult(False, plane,
                           errors=(ProbeError("probe_unavailable", "PHP extension probe unavailable", plane=plane),),
                           stderr=str(exc)[:4096], exit_code=None)
    return parse_probe_output(stdout, requirements, plane=plane, catalog=catalog,
                              exit_code=exit_code, stderr=stderr)


def compare_planes(observations: Mapping[str, PlaneObservation | ProbeResult],
                   requirements: object = (), *, catalog: PhpExtensionCatalog = DEFAULT_CATALOG,
                   profile: str | None = None) -> PlaneComparison:
    """Require web, CLI, exec, and PHPUnit to expose identical requested state."""
    normalized = normalize_requirements(requirements)
    errors: list[ProbeError] = []
    selected: dict[str, PlaneObservation] = {}
    for plane, value in observations.items():
        try:
            _validate_plane(plane)
        except ValueError:
            errors.append(ProbeError("plane_drift", "unknown PHP observation plane", plane=plane))
            continue
        if isinstance(value, ProbeResult):
            if value.observation is None:
                errors.extend(value.errors or (ProbeError("plane_drift", "plane has no observation", plane=plane),))
                continue
            selected[plane] = value.observation
            errors.extend(value.errors)
        elif isinstance(value, PlaneObservation):
            selected[plane] = value
            errors.extend(value.errors)
        else:
            errors.append(ProbeError("plane_drift", "plane observation is invalid", plane=plane))
    for plane in _PLANES - set(selected):
        errors.append(ProbeError("plane_drift", "required PHP observation plane is missing", plane=plane))
    if selected:
        expected_names = {item["name"] for item in normalized}
        baseline_plane = next(iter(sorted(selected)))
        baseline = selected[baseline_plane]
        baseline_rows = baseline.by_name
        for plane, observation in sorted(selected.items()):
            if plane == baseline_plane:
                continue
            if observation.php_version != baseline.php_version:
                errors.append(ProbeError("plane_drift", "PHP versions differ between execution planes",
                                         plane=plane, expected=baseline.php_version,
                                         observed=observation.php_version))
            for name in sorted(expected_names):
                left, right = baseline_rows.get(name), observation.by_name.get(name)
                if left is None or right is None or (left.enabled, left.version) != (right.enabled, right.version):
                    errors.append(ProbeError("plane_drift", f"extension {name} differs between execution planes",
                                             plane=plane, extension=name,
                                             expected=(f"{left.enabled}:{left.version}" if left else None),
                                             observed=(f"{right.enabled}:{right.version}" if right else None)))
        if profile is not None:
            selected_profile = catalog.profile(profile)
            for alternative in selected_profile.capability_alternatives:
                for plane, observation in sorted(selected.items()):
                    if not any(observation.by_name.get(name, ExtensionObservation(name, False, None, plane)).enabled
                               for name in alternative):
                        errors.append(ProbeError(
                            "missing_capability",
                            f"profile {profile} requires one of {' or '.join(alternative)}",
                            plane=plane, expected="|".join(alternative),
                        ))
    return PlaneComparison(not errors, tuple(sorted(selected)), tuple(errors))


def probe_all_planes(runner_by_plane: Mapping[str, object], requirements: object, *,
                     timeout: float = PROBE_TIMEOUT_SECONDS,
                     catalog: PhpExtensionCatalog = DEFAULT_CATALOG) -> dict[str, ProbeResult]:
    """Probe every supplied plane; comparison is a separate explicit operation."""
    return {plane: run_probe(runner, requirements, plane=plane, timeout=timeout, catalog=catalog)
            for plane, runner in runner_by_plane.items()}


__all__ = [
    "ExtensionObservation", "PHP_EXTENSION_PROBE_PAYLOAD", "PlaneComparison",
    "PlaneObservation", "ProbeError", "ProbeResult", "PhpExtensionProbeError",
    "PROBE_MAX_OUTPUT_BYTES", "PROBE_TIMEOUT_SECONDS", "STANDALONE_PROBE_PAYLOAD",
    "build_probe_command", "compare_planes", "parse_probe_output", "probe_all_planes",
    "probe_names", "run_probe", "version_matches",
]

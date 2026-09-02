from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable

from sandbox.runtimes.base import (
    AdapterRegistry,
    OperationError,
    OperationRequest,
    OperationResult,
)
from sandbox.runtimes.wordpress import capability_envelope, safe_alternative


# PHP-extension status is deliberately owned by this application seam rather
# than by either transport.  Incumbent adapters supply only bounded, read-only
# plane runners; the producer below resolves and closes the public report.
PHP_EXTENSION_PLANES = ("web", "cli", "exec", "phpunit")
PHP_EXTENSION_STATES = frozenset({"ready", "drift", "unavailable", "error"})
PHP_EXTENSION_ISSUE_CODES = frozenset({
    "missing", "version_mismatch", "version_unobservable",
    "unsupported_provisioning", "unsupported_disable", "plane_drift",
})
PHP_EXTENSION_ISSUE_MESSAGES = {
    "missing": "required PHP extension is missing",
    "version_mismatch": "PHP extension version does not match the requirement",
    "version_unobservable": "PHP extension version cannot be observed",
    "unsupported_provisioning": "PHP extension provisioning is unsupported",
    "unsupported_disable": "disabling this PHP extension is unsupported",
    "plane_drift": "PHP extension observations differ between execution planes",
}
_PHP_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@|*-]{0,127}$")
_PHP_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PHP_FORBIDDEN_TEXT = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:password|passphrase|secret|token|credential|"
    r"authorization|cookie|private(?:[_-]?key)?|bearer|basic|api[_-]?key)"
    r"(?![A-Za-z0-9])"
)


def _php_safe_value(value: object) -> str | None:
    if (not isinstance(value, str) or not _PHP_SAFE_VALUE.fullmatch(value)
            or _PHP_FORBIDDEN_TEXT.search(value)):
        return None
    return value


def _php_safe_digest(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and _PHP_DIGEST.fullmatch(value.lower()) else None


def _php_issue(value: object) -> dict | None:
    raw = value.to_dict() if hasattr(value, "to_dict") else value
    if not isinstance(raw, Mapping):
        return None
    code = raw.get("code")
    if code in {"missing_capability", "profile_required_missing", "profile_required_disabled"}:
        code = "missing"
    if code not in PHP_EXTENSION_ISSUE_CODES:
        code = "plane_drift"
    row = {"code": code, "message": PHP_EXTENSION_ISSUE_MESSAGES[code]}
    for key in ("plane", "extension", "expected", "observed"):
        item = _php_safe_value(raw.get(key))
        if item is not None:
            row[key] = item
    return row


def _php_issues(values: object) -> list[dict]:
    if not isinstance(values, (list, tuple)):
        return []
    rows: list[dict] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for value in values:
        row = _php_issue(value)
        if row is None:
            continue
        identity = tuple(sorted((key, str(item)) for key, item in row.items()
                                if key != "message"))
        if identity not in seen:
            seen.add(identity)
            rows.append(row)
    return rows


def _php_requirements(value: object) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _php_safe_value(item.get("name"))
        state = item.get("state")
        version = item.get("version")
        if name is None or state not in {"enabled", "disabled"}:
            continue
        if version is not None and _php_safe_value(version) is None:
            continue
        rows.append({"name": name, "state": state, "version": version})
    return rows


def _php_extensions(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    rows: dict[str, dict] = {}
    for name, item in value.items():
        name = _php_safe_value(name)
        if name is None or not isinstance(item, Mapping):
            continue
        enabled = item.get("enabled")
        version = item.get("version")
        if not isinstance(enabled, bool):
            continue
        if version is not None and _php_safe_value(version) is None:
            continue
        rows[name] = {"enabled": enabled, "version": version}
    return rows


def _php_observed(value: object) -> dict:
    if not isinstance(value, Mapping):
        value = {}
    observed: dict[str, dict] = {}
    for plane in PHP_EXTENSION_PLANES:
        item = value.get(plane)
        item = item if isinstance(item, Mapping) else {}
        state = item.get("state") if item.get("state") in PHP_EXTENSION_STATES else "unavailable"
        php_version = item.get("php_version")
        sapi = item.get("sapi")
        observed[plane] = {
            "state": state,
            "php_version": (None if php_version is None else _php_safe_value(php_version)),
            "sapi": (None if sapi is None else _php_safe_value(sapi)),
            "extensions": _php_extensions(item.get("extensions")),
            "issues": _php_issues(item.get("issues")),
        }
    return observed


def _php_desired(value: object) -> dict:
    value = value if isinstance(value, Mapping) else {}
    profile = value.get("profile")
    profile = None if profile is None else _php_safe_value(profile)
    catalog = value.get("catalog") if isinstance(value.get("catalog"), Mapping) else {}
    revision = catalog.get("revision")
    digest = _php_safe_digest(catalog.get("digest"))
    desired = {
        "profile": profile,
        "catalog": {
            "revision": revision if isinstance(revision, int) and not isinstance(revision, bool) else 0,
            "digest": digest or "sha256:" + "0" * 64,
        },
        "requirements": _php_requirements(value.get("requirements")),
        "resolution_digest": _php_safe_digest(value.get("resolution_digest"))
                            or "sha256:" + "0" * 64,
    }
    build_digest = _php_safe_digest(value.get("build_digest"))
    if build_digest is not None:
        desired["build_digest"] = build_digest
    return desired


def project_php_extension_status(report: object) -> dict | None:
    """Project one canonical extension report into its closed public shape.

    This projector is intentionally transport-neutral.  It drops unknown
    fields instead of recursively forwarding receipts, paths, argv, output,
    or credentials.  The defaults keep a malformed adapter report fail-closed
    while retaining the exact required envelope for well-formed reports.
    """
    if not isinstance(report, Mapping):
        return None
    ok = report.get("ok") if isinstance(report.get("ok"), bool) else False
    exit_code = report.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code not in {0, 1}:
        exit_code = 0 if ok else 1
    desired = _php_desired(report.get("desired"))
    provenance_raw = report.get("provenance")
    provenance = {"state": "unavailable"}
    if isinstance(provenance_raw, Mapping) and provenance_raw.get("state") == "unavailable":
        # Incumbent adapters are validate-only: this is their complete
        # provenance contract.  Do not copy any extra receipt-like fields.
        provenance = {"state": "unavailable"}
    elif isinstance(provenance_raw, Mapping):
        # Compose compatibility retains its existing safe receipt identity.
        provenance = {}
        state = provenance_raw.get("state")
        if isinstance(state, str) and state in {
                "unavailable", "ready", "missing", "stale", "discarded",
        }:
            provenance["state"] = state
        digest = _php_safe_digest(provenance_raw.get("recipe_catalog_digest"))
        if digest is not None:
            provenance["recipe_catalog_digest"] = digest
        parents = provenance_raw.get("parent_digests")
        if isinstance(parents, Mapping):
            safe = {role: _php_safe_digest(parents.get(role))
                    for role in ("web", "wpcli")}
            provenance["parent_digests"] = {k: v for k, v in safe.items() if v}
        ids = provenance_raw.get("recipe_ids")
        if isinstance(ids, (list, tuple)):
            provenance["recipe_ids"] = [item for item in (_php_safe_value(raw) for raw in ids)
                                          if item is not None]
        if not provenance:
            provenance = {"state": "unavailable"}
    observed = _php_observed(report.get("observed"))
    readiness_raw = report.get("readiness")
    readiness_state = readiness_raw.get("state") if isinstance(readiness_raw, Mapping) else None
    if readiness_state not in {"ready", "blocked", "unavailable"}:
        readiness_state = "blocked"
    staleness_raw = report.get("staleness")
    staleness_state = staleness_raw.get("state") if isinstance(staleness_raw, Mapping) else None
    staleness_reason = staleness_raw.get("reason") if isinstance(staleness_raw, Mapping) else None
    if staleness_state not in {"fresh", "stale"}:
        staleness_state = "stale"
    if staleness_reason not in {"all_four_planes_observed", "one_or_more_planes_unavailable"}:
        staleness_reason = ("all_four_planes_observed" if staleness_state == "fresh"
                            else "one_or_more_planes_unavailable")
    drift_raw = report.get("drift")
    drift_state = drift_raw.get("state") if isinstance(drift_raw, Mapping) else None
    if drift_state not in {"ready", "drift", "unknown"}:
        drift_state = "unknown"
    return {
        "ok": ok,
        "exit_code": exit_code,
        "desired": desired,
        "provenance": provenance,
        "observed": observed,
        "readiness": {"state": readiness_state},
        "staleness": {"state": staleness_state, "reason": staleness_reason},
        "drift": {"state": drift_state},
        "issues": _php_issues(report.get("issues")),
    }


def _php_requirement_input(requirements: object) -> tuple[str | None, object]:
    if hasattr(requirements, "to_dict") and callable(requirements.to_dict):
        requirements = requirements.to_dict()
    if isinstance(requirements, Mapping):
        profile = requirements.get("profile")
        if "extensions" in requirements:
            return profile, requirements.get("extensions") or {}
        if "requirements" in requirements:
            return profile, requirements.get("requirements") or ()
    return None, requirements


def _php_effective_requirements(requirements: object, catalog) -> tuple[str | None, dict]:
    profile, raw = _php_requirement_input(requirements)
    if profile is not None:
        selected = catalog.profile(profile)
        if isinstance(raw, Mapping):
            values = dict(raw)
        else:
            values = {item["name"]: {"state": item["state"], "version": item.get("version")}
                      for item in raw}
        for name in selected.required:
            values.setdefault(name, True)
        raw = values
    return profile, {"extensions": raw}


def php_extension_status(requirements: object, *, plane_runners: Mapping[str, object] | None = None,
                         timeout: float = 5, validate_only: bool = False,
                         build_digest: str | None = None,
                         build_receipt_owned: bool = False) -> dict | None:
    """Resolve and verify incumbent PHP extensions with bounded read-only probes."""
    if requirements is None:
        return None
    from sandbox.php_extensions.catalog import DEFAULT_CATALOG, PhpExtensionCatalogError
    from sandbox.php_extensions.probe import ProbeError, ProbeResult, compare_planes, probe_all_planes
    from sandbox.php_extensions.service import PhpExtensionService

    try:
        profile, effective = _php_effective_requirements(requirements, DEFAULT_CATALOG)
    except (PhpExtensionCatalogError, TypeError, ValueError) as exc:
        # Invalid declarations are already rejected by config normalization in
        # normal composition.  Keep a deterministic closed report for direct
        # adapter callers without echoing the exception text.
        profile = None
        effective = {"extensions": {}}
        resolution = None
        resolution_error = {"code": "plane_drift"}
    else:
        service = PhpExtensionService()
        resolution = service.resolve(effective["extensions"])
        resolution_error = None
    if resolution is None:
        desired = {
            "profile": profile,
            "catalog": {"revision": DEFAULT_CATALOG.schema_version,
                         "digest": DEFAULT_CATALOG.digest},
            "requirements": [],
            "resolution_digest": "sha256:" + "0" * 64,
        }
        report = {
            "ok": False, "exit_code": 1, "desired": desired,
            "provenance": {"state": "unavailable"},
            "observed": {plane: {"state": "error", "php_version": None,
                                  "sapi": None, "extensions": {}, "issues": []}
                         for plane in PHP_EXTENSION_PLANES},
            "readiness": {"state": "blocked"},
            "staleness": {"state": "stale", "reason": "one_or_more_planes_unavailable"},
            "drift": {"state": "drift"},
            "issues": [resolution_error],
        }
        return project_php_extension_status(report)

    desired = {
        "profile": profile,
        "catalog": {"revision": DEFAULT_CATALOG.schema_version,
                     "digest": resolution.catalog.lower()},
        "requirements": [dict(item) for item in resolution.requirements],
        "resolution_digest": resolution.digest.lower(),
    }
    if build_receipt_owned and _php_safe_digest(build_digest) is not None:
        desired["build_digest"] = build_digest.lower()

    runners = plane_runners if isinstance(plane_runners, Mapping) else {}
    if resolution.ok and runners:
        selected = {plane: runners[plane] for plane in PHP_EXTENSION_PLANES if plane in runners}
        # Keep the adapter contract small: a runner may expose the bounded
        # ``.run(argv, timeout=...)`` process shape or be a callable with the
        # same arguments.  No shell text or unbounded process API is accepted.
        class _CallableRunner:
            def __init__(self, value):
                self.value = value

            def run(self, argv, *, timeout=None):
                if callable(self.value):
                    return self.value(argv, timeout=timeout)
                try:
                    return self.value.run(argv, timeout=timeout)
                except AttributeError as exc:
                    raise OSError("PHP extension probe runner unavailable") from exc

        selected = {plane: _CallableRunner(runner) for plane, runner in selected.items()}
        probes = probe_all_planes(selected, effective["extensions"], timeout=timeout)
    elif resolution.ok:
        probes = {}
    else:
        probes = {}
    for plane in PHP_EXTENSION_PLANES:
        if plane not in probes:
            probes[plane] = ProbeResult(
                False, plane,
                errors=(ProbeError("probe_unavailable",
                                   "PHP extension probe plane unavailable", plane=plane),),
            )

    verification = PhpExtensionService().verify(effective["extensions"], probes)
    comparison = compare_planes(probes, effective["extensions"], catalog=DEFAULT_CATALOG,
                                profile=profile if profile in {item.profile_id for item in DEFAULT_CATALOG.profiles}
                                else None)
    raw_issues = list(resolution.issues)
    raw_issues.extend(verification.errors)
    raw_issues.extend(comparison.errors)
    # Incumbents are validate-only. A missing provisionable extension is not a
    # license to install host packages; classify it explicitly as unsupported
    # provisioning while retaining observation-only ``missing`` failures.
    if validate_only:
        for issue in tuple(raw_issues):
            code = getattr(issue, "code", None)
            extension = getattr(issue, "extension", None)
            if code == "missing" and extension:
                try:
                    recipe = DEFAULT_CATALOG.recipe(extension)
                except Exception:
                    continue
                if recipe.provisionable:
                    raw_issues.append(ProbeError("unsupported_provisioning",
                                                 "PHP extension cannot be provisioned by this runtime",
                                                 plane=getattr(issue, "plane", None),
                                                 extension=extension))

    observed: dict[str, dict] = {}
    any_unavailable = False
    any_drift = False
    all_fresh = True
    for plane in PHP_EXTENSION_PLANES:
        probe = probes[plane]
        observation = probe.observation
        if observation is None:
            all_fresh = False
            codes = {getattr(error, "code", None) for error in probe.errors}
            state = "unavailable" if "probe_unavailable" in codes else "error"
            any_unavailable = True
            observed[plane] = {"state": state, "php_version": None, "sapi": None,
                               "extensions": {}, "issues": _php_issues(probe.errors)}
            continue
        extensions = {
            item.name: {"enabled": item.enabled, "version": item.version}
            for item in observation.extensions
            if _php_safe_value(item.name) is not None
            and (item.version is None or _php_safe_value(item.version) is not None)
        }
        row_issues = _php_issues(probe.errors)
        state = "ready" if probe.ok and not row_issues else "drift"
        if state != "ready":
            any_drift = True
        observed[plane] = {
            "state": state,
            "php_version": observation.php_version if _php_safe_value(observation.php_version) else None,
            "sapi": observation.sapi if _php_safe_value(observation.sapi) else None,
            "extensions": extensions,
            "issues": row_issues,
        }

    issues = _php_issues(raw_issues)
    # Cross-plane drift is a global readiness failure; mark every observed
    # plane drifted so consumers cannot mistake a clean baseline for agreement.
    if any(issue.get("code") == "plane_drift" for issue in issues):
        any_drift = True
        for row in observed.values():
            if row["state"] == "ready":
                row["state"] = "drift"
    ready = bool(resolution.ok and verification.ok and comparison.ok and
                 not issues and all(row["state"] == "ready" for row in observed.values()))
    declared_failure = any(issue.get("code") in {
        "missing", "version_mismatch", "version_unobservable",
        "unsupported_provisioning", "unsupported_disable",
    } for issue in issues)
    readiness = ("ready" if ready else "blocked" if declared_failure
                 else "unavailable" if any_unavailable else "blocked")
    report = {
        "ok": ready,
        "exit_code": 0 if ready else 1,
        "desired": desired,
        "provenance": {"state": "unavailable"},
        "observed": observed,
        "readiness": {"state": readiness},
        "staleness": {"state": "fresh" if all_fresh else "stale",
                       "reason": ("all_four_planes_observed" if all_fresh
                                  else "one_or_more_planes_unavailable")},
        "drift": {"state": "ready" if ready else "unknown" if any_unavailable else "drift"},
        "issues": issues,
    }
    return project_php_extension_status(report)


class RuntimeService:
    def __init__(self, *, resolve_descriptor: Callable, adapters: AdapterRegistry,
                 backends=None, resolve_persisted=None) -> None:
        self._resolve_descriptor = resolve_descriptor
        self._adapters = adapters
        self._backends = backends
        self._resolve_persisted = resolve_persisted

    def resolve_descriptor(self, project_root: str, *, label: str = "default"):
        """Load the exact, label-scoped descriptor through the application boundary."""
        return self._resolve_descriptor(project_root, label=label)

    @staticmethod
    def _descriptor_kind(descriptor: object) -> str:
        if isinstance(descriptor, Mapping):
            # Older WordPress descriptors omitted kind; retain that compatibility
            # default while validating any explicit value.
            kind = descriptor.get("kind", "wordpress")
        elif hasattr(descriptor, "kind"):
            kind = descriptor.kind
        else:
            raise ValueError("runtime descriptor must be a mapping or descriptor object")
        if (not isinstance(kind, str) or not kind or
                any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in kind)):
            raise ValueError("runtime descriptor kind is invalid")
        return kind

    def _resolve_kind(self, project_root: str, *, label: str,
                      capability: str | None = None) -> tuple[str | None, OperationError | None]:
        try:
            descriptor = self._resolve_descriptor(project_root, label=label)
            return self._descriptor_kind(descriptor), None
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return None, OperationError(
                code="invalid_descriptor",
                message=f"runtime descriptor is invalid: {exc}",
                requested_capability=capability,
            )

    @staticmethod
    def _unsupported_capability(kind: str, capability: str, adapter) -> OperationError:
        capabilities = frozenset(getattr(adapter, "capabilities", ()))
        return OperationError(
            code="unsupported_capability",
            message=f"project kind {kind!r} does not support {capability!r}",
            project_kind=kind,
            requested_capability=capability,
            available_capabilities=tuple(sorted(capabilities)),
            suggestion=safe_alternative(capability) or "Use an operation listed in available_capabilities.",
        )

    @staticmethod
    def _observe_status(data: dict) -> dict:
        """Attach an explicit freshness receipt to one status observation.

        Status adapters are allowed to return a bounded snapshot, but callers
        must never mistake that snapshot for current runtime truth.  Live
        adapters opt in with ``observation.freshness == 'live'``; everything
        else is retained as evidence and marked stale.  The generation is a
        digest of the observed state, so a later live observation can prove a
        mutation without relying on a process-local cache.
        """
        raw_observation = data.get("observation")
        observation = dict(raw_observation) if isinstance(raw_observation, Mapping) else {}
        freshness = observation.get("freshness")
        live = freshness == "live"
        state = {key: value for key, value in data.items() if key != "observation"}
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
        observation.update({
            "freshness": "live" if live else "snapshot",
            "stale": not live,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "observation_generation": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        })
        data["observation"] = observation
        # ``ok`` describes whether the adapter call completed.  ``state_current``
        # is the separate truth claim and is false for an unrefreshed snapshot.
        data["state_current"] = live
        return data

    @classmethod
    def _with_capability_envelope(cls, result: OperationResult, adapter, *, runtime=None) -> OperationResult:
        data = dict(result.data)
        effective_ok = bool(result.ok)
        if result.operation == "status" and isinstance(data.get("php_extensions"), Mapping):
            # Adapters own bounded probes; the application seam owns the
            # public shape and failure promotion before either transport sees
            # the result.  Generic Compose is unchanged when no report exists.
            projected = project_php_extension_status(data["php_extensions"])
            if isinstance(projected, dict):
                data["php_extensions"] = projected
                effective_ok = bool(effective_ok and projected.get("ok", False))
                if not projected.get("ok", False):
                    data.update({"state": "blocked", "exit_code": 1, "mutated": False})
                else:
                    data.setdefault("exit_code", 0)
        if result.operation == "status":
            data = cls._observe_status(data)
        data.setdefault("capabilities", capability_envelope(adapter))
        if isinstance(runtime, Mapping):
            mode = runtime.get("mode", "compose")
            adapter_id = runtime.get("adapter", "compose")
            data.setdefault("runtime", {
                "mode": mode, "adapter": adapter_id,
                "isolation": "compose_container" if mode == "compose" else "declared",
            })
        return OperationResult(effective_ok, result.operation, result.project_root,
                               result.project_kind, data)

    def _capability_error(self, kind: str, capability: str) -> OperationError | None:
        spec = self._adapters.for_kind(kind)
        if spec is None:
            return OperationError(
                code="unsupported_kind",
                message=f"no runtime adapter is registered for project kind {kind!r}",
                project_kind=kind,
                requested_capability=capability,
            )
        capabilities = frozenset(getattr(spec.adapter, "capabilities", ()))
        if capability not in capabilities:
            return self._unsupported_capability(kind, capability, spec.adapter)
        return None

    def _runtime_selection_error(self, descriptor, request: OperationRequest):
        """Reject implicit native selection and populated mode switches first."""
        runtime = descriptor.get("wordpressRuntime") if isinstance(descriptor, Mapping) else None
        if not isinstance(runtime, Mapping) or self._backends is None:
            return None, None
        mode = runtime.get("mode", "compose")
        adapter_id = runtime.get("adapter", "compose")
        persisted = self._resolve_persisted(request.project_root, request.label) \
            if self._resolve_persisted else None
        if persisted and persisted.get("populated") and (
            persisted.get("mode") != mode or persisted.get("adapter") != adapter_id
        ):
            return None, OperationError(
                "runtime_mode_change",
                "a populated instance cannot change runtime mode through an ordinary operation",
                "wordpress", request.operation,
                suggestion="Export, recreate in the explicit mode, then import.",
            )
        if mode != "compose" and not runtime.get("explicit"):
            return None, OperationError(
                "explicit_selection_required",
                "native runtime requires an explicit machine-local selection",
                "wordpress", request.operation, suggestion="Use a gitignored machine override.",
            )
        spec = self._backends.resolve("wordpress", mode, adapter_id)
        if spec is None:
            return None, OperationError("unsupported_runtime",
                                        f"runtime backend {adapter_id!r} is unavailable for {mode!r}",
                                        "wordpress", request.operation)
        return spec, None

    @staticmethod
    def _native_request(request: OperationRequest, descriptor: Mapping) -> OperationRequest:
        """Pass immutable descriptor requirements to an explicitly selected native adapter.

        Native adapters are resolved through the backend registry, but their
        operation request remains the transport-neutral contract.  Carry only
        the normalized, declarative values needed for validation; never expose
        the full descriptor or any private/runtime receipt fields.
        """
        runtime = descriptor.get("wordpressRuntime")
        arguments = dict(request.arguments)
        if "phpExtensions" not in arguments and descriptor.get("phpExtensions") is not None:
            arguments["phpExtensions"] = descriptor["phpExtensions"]
        if isinstance(runtime, Mapping):
            for key in ("php", "database"):
                if key not in arguments and runtime.get(key) is not None:
                    arguments[key] = runtime[key]
        if arguments == dict(request.arguments):
            return request
        return OperationRequest(
            project_root=request.project_root, operation=request.operation,
            label=request.label, arguments=arguments,
        )

    def invoke(self, request: OperationRequest) -> OperationResult | OperationError:
        try:
            config_file = request.arguments.get("config_file")
            if config_file is None:
                descriptor = self._resolve_descriptor(request.project_root, label=request.label)
            else:
                descriptor = self._resolve_descriptor(
                    request.project_root, label=request.label, config_file=config_file,
                )
            kind = self._descriptor_kind(descriptor)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return OperationError("invalid_descriptor", f"runtime descriptor is invalid: {exc}",
                                  requested_capability=request.operation)
        if kind == "wordpress":
            spec, selection_error = self._runtime_selection_error(descriptor, request)
            if selection_error is not None:
                return selection_error
            if spec is not None:
                capabilities = frozenset(getattr(spec.adapter, "capabilities", ()))
                if request.operation not in capabilities:
                    return self._unsupported_capability(kind, request.operation, spec.adapter)
                adapter_request = self._native_request(request, descriptor)
                result = spec.adapter.invoke(adapter_request)
                if not isinstance(result, OperationResult) or result.operation != request.operation \
                        or result.project_kind != kind:
                    return OperationError("invalid_adapter_result",
                                          "runtime adapter returned an invalid or mismatched operation result",
                                          kind, request.operation)
                runtime = descriptor.get("wordpressRuntime") \
                    if isinstance(descriptor, Mapping) else None
                return self._with_capability_envelope(result, spec.adapter, runtime=runtime)

        error = self._capability_error(kind, request.operation)
        if error is not None:
            return error
        spec = self._adapters.for_kind(kind)
        result = spec.adapter.invoke(request)
        expected_adapter_label = kind
        valid_result = isinstance(result, OperationResult)
        mismatch = False
        if valid_result:
            mismatch = (result.operation != request.operation or
                        result.project_kind != expected_adapter_label)
        if not valid_result or mismatch:
            return OperationError(
                code="invalid_adapter_result",
                message="runtime adapter returned an invalid or mismatched operation result",
                project_kind=kind,
                requested_capability=request.operation,
            )
        return self._with_capability_envelope(result, spec.adapter)

    def check(self, project_root: str, capability: str, *, label: str = "default",
              config_file: str | None = None) -> OperationError | None:
        try:
            descriptor = (self._resolve_descriptor(
                project_root, label=label, config_file=config_file,
            ) if config_file is not None else self._resolve_descriptor(project_root, label=label))
            kind = self._descriptor_kind(descriptor)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return OperationError(
                code="invalid_descriptor",
                message=f"runtime descriptor is invalid: {exc}",
                requested_capability=capability,
            )
        if kind == "wordpress":
            spec, selection_error = self._runtime_selection_error(
                descriptor, OperationRequest(project_root, capability, label=label),
            )
            if selection_error is not None:
                return selection_error
            if spec is not None:
                capabilities = frozenset(getattr(spec.adapter, "capabilities", ()))
                if capability not in capabilities:
                    return self._unsupported_capability(kind, capability, spec.adapter)
                return None
        return self._capability_error(kind, capability)

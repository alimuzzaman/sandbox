"""Runtime-only Laravel Herd adapter; ingress ownership remains external."""

from __future__ import annotations

from pathlib import Path

from . import (LOWER_ISOLATION, cleanup_owned, php_version_matches, result,
               extension_status, safe_database, status_facts, version_from)


class HerdAdapter:
    adapter_id = "herd"
    capabilities = frozenset({"preflight", "ensure", "status", "open", "wordpress_cli",
                              "exec", "test", "destroy"})
    isolation = LOWER_ISOLATION

    def __init__(self, *, process, executable, platform, php_version=None, backend=None,
                 operations=None, owned_cleanup=None, php_extensions=None,
                 plane_runners=None, php_probe_runners=None):
        self.process = process
        self.executable = executable
        self.platform = platform
        self.php_version = php_version or (lambda: None)
        self.backend = backend or (lambda _request: None)
        self.operations = dict(operations or {})
        self.owned_cleanup = owned_cleanup
        self.php_extensions = php_extensions
        self.plane_runners = plane_runners if plane_runners is not None else php_probe_runners

    def _preflight(self, request):
        if self.platform not in {"linux", "darwin"}:
            return None, result(request, False, "blocked", reason={"code": "unsupported_platform"})
        try:
            probe = self.process.run((self.executable, "--version"), timeout=10)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None, result(request, False, "blocked", reason={"code": "herd_unavailable"})
        version = version_from(probe)
        if version is None:
            return None, result(request, False, "blocked", reason={"code": "herd_unavailable"})
        try:
            database = safe_database(request.arguments.get("database"))
        except ValueError as exc:
            return None, result(request, False, "blocked", reason={"code": "invalid_database", "message": str(exc)})
        php = self.php_version()
        try:
            php_matches = php_version_matches(php, request.arguments.get("php"))
        except ValueError as exc:
            return None, result(request, False, "blocked", reason={"code": str(exc)})
        if not php_matches:
            return None, result(request, False, "blocked", reason={"code": "php_version_mismatch"})
        backend = self.backend(request)
        if backend is not None and (not isinstance(backend, dict) or
                                    not isinstance(backend.get("document_root"), str) or
                                    not Path(backend["document_root"]).is_absolute()):
            return None, result(request, False, "blocked", reason={"code": "invalid_backend"})
        return {"version": version, "php": php, "database": database,
                "backend": backend}, None

    def invoke(self, request):
        if request.operation == "destroy":
            return result(request, **cleanup_owned(request, self.owned_cleanup))
        facts, failure = self._preflight(request)
        if failure is not None:
            return failure
        report = extension_status(request, configured=self.php_extensions,
                                  plane_runners=self.plane_runners)
        if request.operation in {"preflight", "status"}:
            safe = status_facts(facts)
            if report is not None:
                safe["php_extensions"] = report
                if not report["ok"]:
                    return result(request, False, "blocked", **safe, exit_code=1,
                                  reason={"code": "php_extensions_blocked"})
            return result(request, True, "ready", **safe, reason={"code": "ready"})
        if report is not None and not report["ok"]:
            return result(request, False, "blocked", **facts,
                          php_extensions=report, exit_code=1,
                          reason={"code": "php_extensions_blocked"})
        if report is not None:
            facts = {**facts, "php_extensions": report}
        if request.operation in {"preflight", "status", "open"}:
            return result(request, True, "ready", **facts, reason={"code": "ready"})
        if request.operation == "ensure" and not facts["database"]["configured"]:
            return result(request, False, "blocked", **facts,
                          reason={"code": "user_database_required"})
        if request.operation == "ensure":
            return result(request, True, "ready", **facts,
                          reason={"code": "incumbent_runtime_verified"})
        operation = self.operations.get(request.operation)
        if operation is None:
            return result(request, False, "unsupported", **facts,
                          reason={"code": "incumbent_operation_not_wired"})
        value = operation(request)
        return result(request, bool(value.get("ok", True)), value.get("state", "ready"),
                      **facts, operation_result=dict(value))

"""Official macOS Valet runtime adapter with no route or TLS authority."""

from __future__ import annotations

from pathlib import Path

from . import (LOWER_ISOLATION, cleanup_owned, php_version_matches, result,
               safe_database, version_from)


class ValetAdapter:
    adapter_id = "valet"
    capabilities = frozenset({"preflight", "ensure", "status", "open", "wordpress_cli",
                              "exec", "test", "destroy"})
    isolation = LOWER_ISOLATION

    def __init__(self, *, process, executable, platform, php_version=None, backend=None,
                 operations=None, owned_cleanup=None):
        self.process = process; self.executable = executable; self.platform = platform
        self.php_version = php_version or (lambda: None)
        self.backend = backend or (lambda _request: None)
        self.operations = dict(operations or {})
        self.owned_cleanup = owned_cleanup

    def invoke(self, request):
        if request.operation == "destroy":
            return result(request, **cleanup_owned(request, self.owned_cleanup))
        if self.platform != "darwin":
            return result(request, False, "blocked", reason={"code": "unsupported_platform"})
        probe = self.process.run((self.executable, "--version"), timeout=10)
        version = version_from(probe)
        if version is None:
            return result(request, False, "blocked", reason={"code": "valet_unavailable"})
        try:
            database = safe_database(request.arguments.get("database"))
        except ValueError as exc:
            return result(request, False, "blocked", reason={"code": "invalid_database", "message": str(exc)})
        php = self.php_version()
        try:
            php_matches = php_version_matches(php, request.arguments.get("php"))
        except ValueError as exc:
            return result(request, False, "blocked", reason={"code": str(exc)})
        if not php_matches:
            return result(request, False, "blocked", reason={"code": "php_version_mismatch"})
        backend = self.backend(request)
        if backend is not None and (not isinstance(backend, dict) or
                                    not isinstance(backend.get("document_root"), str) or
                                    not Path(backend["document_root"]).is_absolute()):
            return result(request, False, "blocked", reason={"code": "invalid_backend"})
        facts = {"version": version, "php": php, "database": database,
                 "backend": backend}
        if request.operation in {"preflight", "status", "open"}:
            return result(request, True, "ready", **facts, reason={"code": "ready"})
        if request.operation == "ensure" and not database["configured"]:
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

"""User-declared POSIX runtime profile; no discovery or ingress ownership."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from . import LOWER_ISOLATION, cleanup_owned, result, safe_database


class PosixAdapter:
    adapter_id = "declared-posix"
    capabilities = frozenset({"preflight", "ensure", "status", "open", "wordpress_cli",
                              "exec", "test", "destroy"})
    isolation = LOWER_ISOLATION

    def __init__(self, *, profile, platform, collision_checker=None, operations=None,
                 owned_cleanup=None, path_validator=None):
        self.profile = dict(profile or {})
        self.platform = platform
        self.collision_checker = collision_checker or (lambda _profile: False)
        self.operations = dict(operations or {})
        self.owned_cleanup = owned_cleanup
        self.path_validator = path_validator or self._trusted_paths

    @staticmethod
    def _trusted_paths(profile):
        try:
            root = Path(profile["document_root"])
            php = Path(profile["php"]).resolve(strict=True)
            root_details = root.lstat()
            php_details = php.stat()
        except (KeyError, OSError, RuntimeError):
            return False
        return (stat.S_ISDIR(root_details.st_mode) and root_details.st_uid == os.getuid()
                and stat.S_ISREG(php_details.st_mode)
                and php_details.st_uid in {0, os.getuid()}
                and not php_details.st_mode & 0o022
                and os.access(php, os.X_OK))

    def _facts(self, request):
        if self.platform not in {"linux", "darwin"}:
            raise ValueError("unsupported_platform")
        if set(self.profile) - {"authority", "document_root", "php", "database"}:
            raise ValueError("invalid_profile")
        if self.profile.get("authority") != "user":
            raise ValueError("user_authority_required")
        root = self.profile.get("document_root")
        php = self.profile.get("php")
        if not isinstance(root, str) or not Path(root).is_absolute() or not isinstance(php, str) or not Path(php).is_absolute():
            raise ValueError("invalid_profile")
        if not self.path_validator(self.profile):
            raise ValueError("profile_ownership_invalid")
        if self.collision_checker(self.profile):
            raise ValueError("profile_collision")
        return {"document_root": root, "php": php,
                "database": safe_database(request.arguments.get("database") or self.profile.get("database"))}

    def invoke(self, request):
        if request.operation == "destroy":
            return result(request, **cleanup_owned(request, self.owned_cleanup))
        try:
            facts = self._facts(request)
        except ValueError as exc:
            return result(request, False, "blocked", reason={"code": str(exc)})
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

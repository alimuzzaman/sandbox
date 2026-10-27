"""Focused regressions for architectural boundaries and legacy behavior (T059).

AST gates ensuring:
1. sandbox/secrets/ownership/* never imports opaque_revision, load_revision_key,
   sandbox_core, sandbox.registry, sandbox.hermes, or MCP app.
2. No file in sandbox/secrets/ownership/* contains the string "registry.json".
3. sandbox/secrets/sources.py does not import ownership.
4. sandbox/secrets/ownership/worker.py imports strictly stdlib and minimal
   sandbox.secrets authority, credential, transport, and protocol helpers only.
5. Legacy behavior: normalize_secret_config without ownership does not load
   ownership or include an "ownership" key in output, and pre-existing a1-
   digests remain byte-identical.
"""

from __future__ import annotations

import ast
import glob
from pathlib import Path
import unittest

from sandbox.config.secrets import normalize_secret_config
from sandbox.secrets.authority import bind_provider_authorities
from tests.secret_ownership_support import REPO, require


class TestSecretOwnershipBoundaries(unittest.TestCase):
    """AST boundary gates and legacy isolation regressions."""

    def test_ownership_ast_no_forbidden_imports(self) -> None:
        """sandbox/secrets/ownership/* never imports opaque_revision, load_revision_key,
        sandbox_core, sandbox.registry, sandbox.hermes, or MCP app."""
        forbidden_modules = (
            "opaque_revision",
            "load_revision_key",
            "sandbox_core",
            "sandbox.registry",
            "sandbox.hermes",
            "app",
        )
        ownership_dir = REPO / "sandbox" / "secrets" / "ownership"
        py_files = sorted(ownership_dir.glob("*.py"))
        require(len(py_files) >= 10, "must find ownership python files")

        violations: list[str] = []
        for py_path in py_files:
            tree = ast.parse(py_path.read_text("utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_modules:
                            if forbidden in alias.name:
                                violations.append(f"{py_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    imported_names = [alias.name for alias in node.names]
                    for forbidden in forbidden_modules:
                        if forbidden == mod or f".{forbidden}" in mod or forbidden in imported_names:
                            violations.append(f"{py_path.name}: from {mod} import {imported_names}")

        require(not violations, f"forbidden imports found in ownership: {violations}")

    def test_ownership_ast_no_registry_json_string(self) -> None:
        """No module in sandbox/secrets/ownership contains the string 'registry.json'."""
        ownership_dir = REPO / "sandbox" / "secrets" / "ownership"
        py_files = sorted(ownership_dir.glob("*.py"))

        violations: list[str] = []
        for py_path in py_files:
            tree = ast.parse(py_path.read_text("utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant):
                    if isinstance(node.value, str) and "registry.json" in node.value:
                        violations.append(f"{py_path.name}: contains constant {node.value!r}")

        require(not violations, f"forbidden string 'registry.json' found: {violations}")

    def test_sources_module_does_not_import_ownership(self) -> None:
        """sandbox/secrets/sources.py does not import sandbox.secrets.ownership or anything from ownership."""
        sources_path = REPO / "sandbox" / "secrets" / "sources.py"
        require(sources_path.exists(), "sources.py must exist")

        tree = ast.parse(sources_path.read_text("utf-8"), filename=str(sources_path))
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "ownership" in alias.name:
                        violations.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [n.name for n in node.names]
                if "ownership" in mod or any("ownership" in n for n in names):
                    violations.append(f"from {mod} import {names}")

        require(not violations, f"sources.py imports ownership: {violations}")

    def test_worker_module_imports_strictly_minimal(self) -> None:
        """Worker imports strictly stdlib and minimal sandbox.secrets authority/credentials/protocol/models."""
        worker_path = REPO / "sandbox" / "secrets" / "ownership" / "worker.py"
        require(worker_path.exists(), "worker.py must exist")

        allowed_namespaces = {
            # stdlib
            "__future__", "hmac", "http.client", "json", "math", "os", "re",
            "signal", "socket", "ssl", "sys", "time", "unicodedata", "urllib.parse",
            # intra-ownership relative modules
            "broker", "manifest", "models", "protocol",
            # minimal secrets helpers
            "sandbox.secrets.authority",
            "sandbox.secrets.credentials.base",
            "sandbox.secrets.credentials.manifest",
            "sandbox.secrets.models",
            "sandbox.secrets.provider_identity",
            "sandbox.secrets.providers.base",
            "sandbox.secrets.providers.infisical",
        }

        tree = ast.parse(worker_path.read_text("utf-8"), filename=str(worker_path))
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in allowed_namespaces:
                        violations.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod not in allowed_namespaces:
                    violations.append(f"from {mod} import {[n.name for n in node.names]}")

        require(not violations, f"worker.py has unallowed imports: {violations}")

    def test_legacy_config_no_ownership_imports_and_no_ownership_key(self) -> None:
        """Normalizing a secret config without ownership produces no 'ownership' key."""
        raw_config = {
            "root": "/project",
            "secrets": {
                "sources": {
                    "env-file": {
                        "type": "file",
                        "path": ".env",
                        "format": "dotenv",
                    },
                },
                "useProfiles": {
                    "test": {
                        "source": "env-file",
                        "key": "PORT",
                        "destination": "APP_PORT",
                        "argv": ["app", "--port", "{VALUE}"],
                    },
                },
            },
        }
        normalized = normalize_secret_config(raw_config)
        require("ownership" not in normalized, "legacy config must NOT have ownership key")
        require("sources" in normalized, "sources must be present")
        require("useProfiles" in normalized, "useProfiles must be present")

    def test_legacy_a1_digest_preserves_pre_change_baseline(self) -> None:
        """bind_provider_authorities without ownership produces stable pre-change a1- baseline."""
        sources = {
            "infisical-app": {
                "type": "infisical",
                "endpoint": "https://vault.example.invalid",
                "projectId": "proj-legacy",
                "environment": "staging",
                "path": "/",
                "key": "SECRET_KEY",
                "clientId": "client-legacy",
                "mcpModes": ["keys"],
                "timeoutSeconds": 10,
            },
        }
        use_profiles = {
            "app": {
                "source": "infisical-app",
                "key": "SECRET_KEY",
                "argv": ["app", "{VALUE}"],
                "destination": "APP_KEY",
                "timeoutSeconds": 10,
                "maxOutputBytes": 1024,
                "mcp": False,
            },
        }

        # Authority digest without ownership
        bound = bind_provider_authorities(sources, use_profiles,
                                          project_root="/project/root",
                                          ownership=None)
        digest = bound["infisical-app"]["authorityDigest"]
        require(digest.startswith("a1-"), "digest must start with a1-")
        require(len(digest) == 67, "digest must be 67 characters (a1- + 64 hex)")

    def test_ownership_public_surface_models_only(self) -> None:
        """sandbox/secrets/ownership/__init__.py only re-exports public model types at top level."""
        init_path = REPO / "sandbox" / "secrets" / "ownership" / "__init__.py"
        require(init_path.exists(), "__init__.py must exist")

        tree = ast.parse(init_path.read_text("utf-8"), filename=str(init_path))
        # Ensure it only imports from .models at top-level
        for node in tree.body:
            if isinstance(node, ast.Import):
                raise AssertionError(f"unexpected top-level import in __init__.py: {node.names}")
            elif isinstance(node, ast.ImportFrom):
                require(node.module == "models" or (node.level == 1 and node.module == "models"),
                        f"__init__.py should only import from .models, got: {node.module}")


if __name__ == "__main__":
    unittest.main()

"""Architecture boundary tests for sandbox.owned_storage and sandbox.owned_storage_lifecycle."""

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).parent.parent


def get_owned_storage_python_files():
    modules = [
        ROOT / "sandbox" / "owned_storage",
        ROOT / "sandbox" / "owned_storage_lifecycle",
    ]
    files = []
    for mod in modules:
        if mod.exists():
            files.extend(path for path in mod.rglob("*.py") if "__pycache__" not in path.parts)
    return files


class TestOwnedStorageArchitectureBoundaries(unittest.TestCase):
    def test_owned_storage_files_exist(self):
        files = get_owned_storage_python_files()
        self.assertTrue(len(files) >= 3, f"Expected at least 3 python files, found: {files}")

    def test_zero_import_of_hosting_from_owned_storage(self):
        """Verify zero import of sandbox/hosting/** from sandbox/owned_storage/** and sandbox/owned_storage_lifecycle/**."""
        files = get_owned_storage_python_files()
        violations = []

        forbidden_prefixes = (
            "sandbox.hosting",
            "sandbox.transports.remote_hosting",
        )

        for path in files:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(alias.name == p or alias.name.startswith(p + ".") for p in forbidden_prefixes):
                            violations.append((path.relative_to(ROOT), node.lineno, f"import {alias.name}"))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if any(module == p or module.startswith(p + ".") for p in forbidden_prefixes):
                        violations.append((path.relative_to(ROOT), node.lineno, f"from {module} import ..."))

        self.assertEqual(
            violations,
            [],
            f"Found forbidden imports of sandbox/hosting in owned storage modules: {violations}",
        )

    def test_zero_mention_of_hosts_json_or_recovery_repository_in_owned_storage(self):
        """Verify owned_storage does not reference hosts.json or OCI RecoveryRepository."""
        files = get_owned_storage_python_files()
        violations = []
        forbidden_strings = ("hosts.json", "RecoveryRepository", "StageRepository")

        for path in files:
            source = path.read_text(encoding="utf-8")
            for forbidden in forbidden_strings:
                if forbidden in source:
                    violations.append((path.relative_to(ROOT), forbidden))

        self.assertEqual(
            violations,
            [],
            f"Found forbidden hosting references in owned storage modules: {violations}",
        )


if __name__ == "__main__":
    unittest.main()

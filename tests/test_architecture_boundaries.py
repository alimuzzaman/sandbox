import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def production_python_files():
    files = list((ROOT / "sandbox").rglob("*.py"))
    files += list((ROOT / "mcp" / "wp-server" / "tools").glob("*.py"))
    return [path for path in files if ".venv" not in path.parts]


class TestArchitectureBoundaries(unittest.TestCase):
    def test_new_boundary_packages_do_not_use_legacy_wildcards(self):
        roots = (
            ROOT / "sandbox" / "application",
            ROOT / "sandbox" / "config",
            ROOT / "sandbox" / "project_registry",
            ROOT / "sandbox" / "runtimes",
            ROOT / "sandbox" / "services",
            ROOT / "sandbox" / "hermes",
        )
        violations = []
        for package in roots:
            if not package.exists():
                continue
            for path in package.rglob("*.py"):
                text = path.read_text()
                if re.search(r"from (sandbox\.core|app) import \*", text):
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_legacy_wildcard_baseline_does_not_grow(self):
        count = 0
        for path in production_python_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {"sandbox.core", "app"}:
                    count += sum(alias.name == "*" for alias in node.names)
        self.assertLessEqual(count, 39, "legacy wildcard imports grew beyond the source baseline")

    def test_new_modules_do_not_import_composition_roots(self):
        forbidden = re.compile(r"(?:from|import) (?:sandbox\.cli|server)(?:\s|$)")
        violations = []
        for package in (ROOT / "sandbox" / "runtimes", ROOT / "sandbox" / "project_registry"):
            if not package.exists():
                continue
            for path in package.rglob("*.py"):
                if forbidden.search(path.read_text()):
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_mcp_helpers_do_not_read_registry_json_directly(self):
        app = (ROOT / "mcp" / "wp-server" / "app.py").read_text()
        self.assertNotIn('(RUNTIME_DIR / "registry.json").read_text()', app)

    def test_recovery_modules_do_not_depend_on_runtime_mechanisms(self):
        forbidden = re.compile(r"(?:docker\s+compose|subprocess\.run\(\[?['\"]docker|from sandbox\.core import)")
        violations = []
        for path in (ROOT / "sandbox" / "recovery").glob("*.py"):
            if path.name == "inventory.py":
                continue  # read-only remote inventory is the boundary adapter
            if forbidden.search(path.read_text()):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

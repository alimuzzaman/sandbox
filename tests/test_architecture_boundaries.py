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
    def test_compatibility_facade_consumer_baseline_does_not_grow(self):
        sandbox_core_consumers = set()
        hermes_facade_consumers = set()
        facade_files = production_python_files() + [ROOT / "mcp" / "wp-server" / "app.py"]
        for path in facade_files:
            tree = ast.parse(path.read_text())
            relative = str(path.relative_to(ROOT))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    alias.name == "sandbox_core" for alias in node.names
                ):
                    sandbox_core_consumers.add(relative)
                if isinstance(node, ast.ImportFrom) and node.module == "sandbox_core":
                    sandbox_core_consumers.add(relative)
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "sandbox.hermes", "sandbox.hermes.facade"
                }:
                    if any(alias.name == "facade" for alias in node.names) \
                            or node.module == "sandbox.hermes.facade":
                        hermes_facade_consumers.add(relative)

        self.assertEqual(sandbox_core_consumers, {
            "mcp/wp-server/app.py",
            "sandbox/application/context.py",
            "sandbox/core/_instances.py",
        })
        self.assertEqual(hermes_facade_consumers, {"sandbox/commands/hermes.py"})

    def test_exact_owned_cli_and_mcp_inventories_are_enforced(self):
        from sandbox.commands.manifest import load_builtin_commands, validate_builtin_command_coverage
        from sandbox.registry import COMMANDS

        load_builtin_commands()
        self.assertEqual(len(COMMANDS), 78)
        self.assertEqual(validate_builtin_command_coverage(), ())

        import sys
        mcp_root = ROOT / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        try:
            from tools.manifest import BUILTIN_TOOL_GROUPS, BUILTIN_TOOL_NAMES
            self.assertEqual(len(BUILTIN_TOOL_GROUPS), 20)
            tool_names = tuple(
                name for group_id in BUILTIN_TOOL_GROUPS
                for name in BUILTIN_TOOL_NAMES[group_id]
            )
            self.assertEqual(len(tool_names), 94)
            self.assertEqual(len(tool_names), len(set(tool_names)))
        finally:
            sys.path.remove(str(mcp_root))

    def test_new_boundary_packages_do_not_use_legacy_wildcards(self):
        roots = (
            ROOT / "sandbox" / "application",
            ROOT / "sandbox" / "jobs",
            ROOT / "sandbox" / "config",
            ROOT / "sandbox" / "ci",
            ROOT / "sandbox" / "project_registry",
            ROOT / "sandbox" / "runtimes",
            ROOT / "sandbox" / "services",
            ROOT / "sandbox" / "transports",
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
        for package in (
            ROOT / "sandbox" / "runtimes",
            ROOT / "sandbox" / "project_registry",
            ROOT / "sandbox" / "jobs",
            ROOT / "sandbox" / "transports",
            ROOT / "sandbox" / "ci",
        ):
            if not package.exists():
                continue
            for path in package.rglob("*.py"):
                if forbidden.search(path.read_text()):
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_remote_job_runtime_has_explicit_manifest_owners(self):
        command_manifest = (ROOT / "sandbox" / "commands" / "manifest.py").read_text()
        tool_manifest = (ROOT / "mcp" / "wp-server" / "tools" / "manifest.py").read_text()
        self.assertIn('"sandbox.commands.jobs_runtime"', command_manifest)
        self.assertIn('"sandbox.commands.workspaces"', command_manifest)
        self.assertIn('"jobs"', tool_manifest)
        self.assertTrue((ROOT / "sandbox" / "jobs" / "manifest.py").is_file())

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

    def test_hermes_bounded_modules_do_not_reach_back_into_legacy_control_plane(self):
        """Only the compatibility facade may import the pre-extraction module."""
        hermes_root = ROOT / "sandbox" / "hermes"
        violations = []
        for path in hermes_root.glob("*.py"):
            if path.name in {"facade.py", "__init__.py"}:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "sandbox.core._hermes":
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_hermes_command_uses_the_public_facade_not_the_legacy_module(self):
        tree = ast.parse((ROOT / "sandbox" / "commands" / "hermes.py").read_text())
        imports = [
            (node.module, tuple(alias.name for alias in node.names))
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertIn(("sandbox.hermes", ("facade",)), imports)
        self.assertNotIn(("sandbox.core._hermes", ("*",)), imports)


if __name__ == "__main__":
    unittest.main()

"""Static modularity inventory guards for generic-project work."""

from __future__ import annotations

import ast
from collections import Counter
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent
REGISTERED_PROJECT_KINDS = frozenset({"compose", "wordpress"})


def production_python_files() -> list[Path]:
    roots = (ROOT / "sandbox", ROOT / "mcp" / "wp-server")
    return [
        path
        for root in roots
        for path in root.rglob("*.py")
        if ".venv" not in path.parts
    ]


def _has_tool_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
        for decorator in node.decorator_list
    )


def _enclosing_function(node: ast.AST, functions: tuple[str, ...]) -> str:
    """Return the nearest feature function for an AST condition."""

    return functions[-1] if functions else "<module>"


def _is_project_kind_test(test: ast.AST) -> bool:
    """Recognize a registered project-kind discriminator without text matching.

    The broad inventory below remains a historical regression proxy. This
    predicate is deliberately narrower: it requires a comparison against one
    of the registered project kinds and an AST access to ``kind`` or
    ``project_kind``. That keeps unrelated resource/job ``kind`` fields out of
    the runtime-adapter inventory while retaining direct ``project_kind``
    comparisons.
    """

    nodes = tuple(ast.walk(test))
    values = {
        node.value
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    direct_project_kind = any(
        (isinstance(node, ast.Name) and node.id == "project_kind")
        or (isinstance(node, ast.Attribute) and node.attr == "project_kind")
        or (isinstance(node, ast.Constant) and node.value == "project_kind")
        for node in nodes
    )
    if not values & REGISTERED_PROJECT_KINDS and not direct_project_kind:
        return False
    return direct_project_kind or any(
        (isinstance(node, ast.Name) and node.id in {"kind", "project_kind"})
        or (isinstance(node, ast.Attribute) and node.attr in {"kind", "project_kind"})
        or (isinstance(node, ast.Constant) and node.value in {"kind", "project_kind"})
        for node in nodes
    )


def _runtime_kind_locations(tree: ast.AST, relative: str) -> Counter[tuple[str, str]]:
    """Count approved runtime-kind conditionals by file and enclosing function."""

    locations: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, functions: tuple[str, ...] = ()) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions = (*functions, node.name)

        tests: tuple[ast.AST, ...]
        if isinstance(node, (ast.If, ast.IfExp)):
            tests = (node.test,)
        elif isinstance(node, ast.comprehension):
            tests = tuple(node.ifs)
        else:
            tests = ()

        if any(_is_project_kind_test(test) for test in tests):
            locations[(relative, _enclosing_function(node, functions))] += sum(
                _is_project_kind_test(test) for test in tests
            )

        for child in ast.iter_child_nodes(node):
            visit(child, functions)

    visit(tree)
    return locations


def approved_runtime_kind_locations() -> Counter[tuple[str, str]]:
    locations: Counter[tuple[str, str]] = Counter()
    for path in production_python_files():
        tree = ast.parse(path.read_text())
        locations.update(
            _runtime_kind_locations(tree, str(path.relative_to(ROOT)))
        )
    return locations


def audit_metrics() -> dict[str, int]:
    wildcard_imports = 0
    mcp_tools = 0
    kind_referencing_conditionals = 0
    for path in production_python_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                wildcard_imports += sum(alias.name == "*" for alias in node.names)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mcp_tools += _has_tool_decorator(node)
            if isinstance(node, (ast.If, ast.IfExp)):
                test = ast.unparse(node.test)
                kind_referencing_conditionals += "kind" in test

    from sandbox.commands.manifest import load_builtin_commands
    from sandbox.registry import COMMANDS

    load_builtin_commands()
    return {
        "cli_commands": len(COMMANDS),
        "mcp_tools": mcp_tools,
        "wildcard_imports": wildcard_imports,
        "kind_referencing_conditionals": kind_referencing_conditionals,
    }


class TestModularityInventory(unittest.TestCase):
    def test_server_config_uses_only_typed_composition_boundaries(self):
        package = ROOT / "sandbox/server_config"
        violations = []
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imported = {item.name for item in node.names}
                    if module == "sandbox.registry" and "COMMANDS" in imported:
                        violations.append(str(path.relative_to(ROOT)))
                    if module in {
                        "sandbox.hermes.facade",
                        "sandbox_core",
                        "mcp.wp-server.app",
                    }:
                        violations.append(str(path.relative_to(ROOT)))
                elif isinstance(node, ast.Import):
                    if any(
                        item.name in {
                            "sandbox_core",
                            "sandbox.hermes.facade",
                            "mcp.wp-server.app",
                        }
                        for item in node.names
                    ):
                        violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_instance_delete_declares_legacy_compose_helper(self):
        # The delete compatibility path still needs the canonical compose-file
        # resolver after wildcard imports are removed from instances_cmd.
        import sandbox.commands.instances_cmd as instances_cmd

        self.assertTrue(callable(instances_cmd.compose_file))

    def test_pre_generic_project_inventory_is_explicit(self):
        self.assertEqual(
            audit_metrics(),
            {
                "cli_commands": 90,
                "mcp_tools": 44,
                "wildcard_imports": 20,
                "kind_referencing_conditionals": 212,
            },
        )

    def test_approved_runtime_kind_locations_are_explicit(self):
        self.assertEqual(
            approved_runtime_kind_locations(),
            Counter({
                ("sandbox/activation/catalog.py", "build_catalog"): 2,
                ("sandbox/cli.py", "main"): 1,
                ("sandbox/core/_instances.py", "resolve_instances"): 1,
                ("sandbox/core/_domains.py", "_generic_proxy_entries"): 1,
                ("sandbox/core/_domains.py", "secure_generic_instance"): 1,
                ("sandbox/runtimes/compose.py", "_descriptor"): 1,
                ("sandbox/commands/ci.py", "cmd_ci"): 1,
                ("sandbox/commands/instances_cmd.py", "cmd_ensure"): 1,
                ("sandbox/commands/instances_cmd.py", "cmd_init"): 2,
                ("sandbox/commands/instances_cmd.py", "cmd_instance"): 1,
                ("sandbox/commands/net.py", "cmd_secure"): 1,
                ("sandbox/commands/wp.py", "_cmd_remote_wp"): 1,
                ("sandbox/commands/activation.py", "invoke"): 1,
                ("sandbox/commands/activation.py", "observe"): 1,
                ("sandbox/commands/lifecycle.py", "cmd_up"): 1,
                ("sandbox/commands/lifecycle.py", "cmd_down"): 1,
                ("sandbox/commands/lifecycle.py", "cmd_status"): 1,
                ("sandbox/commands/lifecycle.py", "_status_json_payload"): 1,
                ("sandbox/commands/lifecycle.py", "cmd_logs"): 1,
                ("sandbox/commands/lifecycle.py", "cmd_open"): 1,
                ("sandbox/commands/debug.py", "cmd_test"): 1,
                ("sandbox/application/runtime_service.py", "invoke"): 2,
                ("sandbox/application/runtime_service.py", "check"): 1,
                ("mcp/wp-server/tools/instances.py", "destroy_instance"): 1,
                ("mcp/wp-server/tools/instances.py", "recreate_instance"): 1,
                ("mcp/wp-server/tools/instances.py", "secure_instance"): 1,
                ("mcp/wp-server/tools/runtime.py", "_typed_invoke"): 1,
                ("mcp/wp-server/tools/runtime.py", "_wordpress_extension_status"): 1,
                ("mcp/wp-server/server.py", "_remote_wp_contract"): 1,
            }),
        )


if __name__ == "__main__":
    unittest.main()

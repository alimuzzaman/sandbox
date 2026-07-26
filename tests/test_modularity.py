"""Static modularity inventory guards for generic-project work."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


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


def audit_metrics() -> dict[str, int]:
    wildcard_imports = 0
    mcp_tools = 0
    runtime_kind_branches = 0
    for path in production_python_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                wildcard_imports += sum(alias.name == "*" for alias in node.names)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mcp_tools += _has_tool_decorator(node)
            if isinstance(node, (ast.If, ast.IfExp)):
                test = ast.unparse(node.test)
                runtime_kind_branches += "kind" in test

    from sandbox.commands.manifest import load_builtin_commands
    from sandbox.registry import COMMANDS

    load_builtin_commands()
    return {
        "cli_commands": len(COMMANDS),
        "mcp_tools": mcp_tools,
        "wildcard_imports": wildcard_imports,
        "runtime_kind_branches": runtime_kind_branches,
    }


class TestModularityInventory(unittest.TestCase):
    def test_instance_delete_declares_legacy_compose_helper(self):
        # The delete compatibility path still needs the canonical compose-file
        # resolver after wildcard imports are removed from instances_cmd.
        import sandbox.commands.instances_cmd as instances_cmd

        self.assertTrue(callable(instances_cmd.compose_file))

    def test_pre_generic_project_inventory_is_explicit(self):
        self.assertEqual(
            audit_metrics(),
            {
                "cli_commands": 84,
                "mcp_tools": 50,
                "wildcard_imports": 20,
                "runtime_kind_branches": 75,
            },
        )


if __name__ == "__main__":
    unittest.main()

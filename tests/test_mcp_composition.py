import sys
import unittest
from pathlib import Path

MCP_ROOT = Path(__file__).parent.parent / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_ROOT))


class TestMcpComposition(unittest.TestCase):
    def test_group_specs_are_deterministic_and_duplicates_fail(self):
        from composition import ToolGroupRegistry, ToolGroupSpec

        registry = ToolGroupRegistry()
        registry.add(ToolGroupSpec("zeta", lambda _server, _deps: None, owner="tests", order=20))
        registry.add(ToolGroupSpec("alpha", lambda _server, _deps: None, owner="tests", order=10))
        self.assertEqual([item.group_id for item in registry.specs()], ["alpha", "zeta"])
        with self.assertRaisesRegex(ValueError, "duplicate tool group"):
            registry.add(ToolGroupSpec("alpha", lambda _server, _deps: None, owner="other"))

    def test_dependencies_fail_closed_for_missing_key(self):
        from dependencies import ToolDependencies

        dependencies = ToolDependencies({"known": object()})
        self.assertIsNotNone(dependencies.require("known"))
        with self.assertRaisesRegex(KeyError, "missing MCP dependency"):
            dependencies.require("unknown")

    def test_builtin_group_manifest_is_exact_and_deterministic(self):
        from tools.manifest import BUILTIN_TOOL_GROUPS, built_in_tool_registry

        expected = (
            "instances", "wp", "net", "data", "fs", "mail", "context", "cache",
            "abilities", "skills", "debug", "e2e", "ci", "asyncjobs",
            "plugin_check", "remote", "hermes", "recovery",
        )
        self.assertEqual(BUILTIN_TOOL_GROUPS, expected)
        self.assertEqual(built_in_tool_registry().group_ids(), expected)
        self.assertIn("recovery", BUILTIN_TOOL_GROUPS)

    def test_test_group_composes_with_isolated_dependencies(self):
        from composition import ToolGroupRegistry, ToolGroupSpec
        from dependencies import ToolDependencies

        calls = []
        registry = ToolGroupRegistry()
        registry.add(ToolGroupSpec(
            "fixture", lambda server, deps: calls.append((server, deps.require("fixture"))),
            owner="tests", dependencies=("fixture",),
        ))
        server = object()
        dependency = object()
        registry.compose(server, ToolDependencies({"fixture": dependency}))
        self.assertEqual(calls, [(server, dependency)])


if __name__ == "__main__":
    unittest.main()

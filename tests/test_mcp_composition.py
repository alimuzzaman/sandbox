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

    def test_duplicate_tool_ownership_fails_closed(self):
        from composition import ToolGroupRegistry, ToolGroupSpec

        registry = ToolGroupRegistry()
        registry.add(ToolGroupSpec("first", lambda _server, _deps: None,
                                   owner="tests", tool_names=("shared_tool",)))
        with self.assertRaisesRegex(ValueError, "duplicate MCP tool"):
            registry.add(ToolGroupSpec("second", lambda _server, _deps: None,
                                       owner="tests", tool_names=("shared_tool",)))

    def test_dependencies_fail_closed_for_missing_key(self):
        from dependencies import ToolDependencies

        dependencies = ToolDependencies({"known": object()})
        self.assertIsNotNone(dependencies.require("known"))
        with self.assertRaisesRegex(KeyError, "missing MCP dependency"):
            dependencies.require("unknown")

    def test_builtin_group_manifest_is_exact_and_deterministic(self):
        from tools.manifest import BUILTIN_TOOL_GROUPS, built_in_tool_registry

        expected = (
            "instances", "runtime", "wp", "net", "data", "fs", "mail", "context", "cache",
            "abilities", "skills", "debug", "e2e", "ci", "asyncjobs",
            "plugin_check", "remote", "hermes", "recovery",
        )
        self.assertEqual(BUILTIN_TOOL_GROUPS, expected)
        self.assertEqual(built_in_tool_registry().group_ids(), expected)
        self.assertIn("recovery", BUILTIN_TOOL_GROUPS)

    def test_builtin_group_manifest_supports_an_opt_in_small_catalog(self):
        from tools.manifest import built_in_tool_registry
        selected = ("instances", "runtime", "wp", "net", "data", "fs", "context")
        self.assertEqual(built_in_tool_registry(selected).group_ids(), selected)
        with self.assertRaisesRegex(ValueError, "unknown MCP tool group"):
            built_in_tool_registry(("instances", "missing"))

    def test_builtin_groups_declare_a_compatibility_registration_boundary(self):
        from tools.manifest import BUILTIN_TOOL_GROUPS, built_in_tool_registry

        specs = built_in_tool_registry().specs()
        self.assertEqual(tuple(spec.group_id for spec in specs), BUILTIN_TOOL_GROUPS)
        self.assertEqual(
            {spec.group_id: spec.dependencies for spec in specs if spec.group_id in {"instances", "runtime", "hermes"}},
            {
                "instances": (
                    "sandbox_root", "proxy_tld", "core", "load_sandbox_yml",
                    "project_instance", "resolve_instance", "safe_json", "site_url",
                ),
                "runtime": ("core", "project_instance", "runtime_service"),
                "hermes": ("hermes_service",),
            },
        )
        self.assertTrue(all(spec.dependencies == ("app",) for spec in specs
                            if spec.group_id not in {"instances", "runtime", "hermes"}))

    def test_instance_and_hermes_groups_register_against_an_isolated_fake_context(self):
        from dependencies import ToolDependencies
        from tools import hermes, instances

        class FakeServer:
            def __init__(self):
                self.registered = []

            def tool(self):
                def decorator(function):
                    self.registered.append(function.__name__)
                    return function
                return decorator

        instance_server = FakeServer()
        instances.register(instance_server, ToolDependencies({
            "sandbox_root": Path("/isolated/sandbox"), "proxy_tld": "test",
            "core": object(), "load_sandbox_yml": lambda: {},
            "project_instance": lambda *_args: (None, {"ok": False}),
            "resolve_instance": lambda _instance: {}, "safe_json": lambda _value: None,
            "site_url": lambda _instance: "https://example.test",
        }))
        self.assertEqual(instance_server.registered, [
            "ensure_instance", "destroy_instance", "recreate_instance", "setup_domains",
            "secure_instance", "apply_config",
        ])

        class RecordingHermesService:
            def __init__(self):
                self.calls = []

            def run(self, args, timeout):
                self.calls.append((args, timeout))
                return {"ok": True}

        command_service = RecordingHermesService()
        hermes_server = FakeServer()
        hermes.register(hermes_server, ToolDependencies({"hermes_service": command_service}))
        self.assertIn("hermes_status", hermes_server.registered)
        self.assertIn("hermes_cron_verify", hermes_server.registered)
        self.assertEqual(hermes.hermes_status("fixture"), {"ok": True})
        self.assertEqual(command_service.calls, [
            (["hermes", "status", "--remote", "fixture"], 30),
        ])

    def test_instance_and_hermes_groups_have_no_app_import_or_import_registration_side_effect(self):
        import ast

        root = Path(__file__).parent.parent / "mcp" / "wp-server" / "tools"
        for group in ("instances", "hermes"):
            tree = ast.parse((root / f"{group}.py").read_text())
            app_imports = [node for node in ast.walk(tree)
                           if isinstance(node, ast.ImportFrom) and node.module == "app"]
            self.assertEqual(app_imports, [], group)
            decorators = [decorator for node in ast.walk(tree)
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                          for decorator in node.decorator_list]
            self.assertFalse(any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                for decorator in decorators
            ), group)

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

    def test_migrated_groups_do_not_wildcard_import_the_legacy_app_namespace(self):
        root = Path(__file__).parent.parent / "mcp" / "wp-server" / "tools"
        groups = (
            "instances", "wp", "net", "data", "fs", "mail", "context", "cache",
            "abilities", "skills", "debug", "e2e", "ci", "asyncjobs",
            "plugin_check", "remote", "hermes", "recovery",
        )
        for group in groups:
            self.assertNotIn("from app import *", (root / f"{group}.py").read_text())


if __name__ == "__main__":
    unittest.main()

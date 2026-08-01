import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_workspace_tools_delegate_to_the_shared_workspace_service(self):
        from tools import jobs

        class Workspace:
            def create(self, request):
                return {"ok": True, "action": "create", "workspace": request.workspace,
                        "local": request.local}

        previous = jobs._workspace_service
        try:
            jobs._workspace_service = Workspace()
            self.assertEqual(jobs.workspace_create("/tmp/project", local=True, workspace="dev"), {
                "ok": True, "action": "create", "workspace": "dev", "local": True})
        finally:
            jobs._workspace_service = previous

    def test_remote_job_tools_use_the_staged_remote_cli_path(self):
        from sandbox.core import _remote
        from tools import jobs

        transport = jobs._remote_transport()
        self.assertIs(transport.remote_sb_path, _remote.remote_sb_path)

    def test_remote_instance_exec_submits_the_in_instance_job_kind(self):
        from tools import jobs, runtime

        accepted = {"ok": True, "job_id": "remote-job"}
        with patch.object(jobs, "_submit_explicit_job", return_value=accepted) as submit:
            result = runtime.instance_exec(["npm", "test"], "/tmp/project", remote="remote-a",
                                           workspace="node-unit", timeout_seconds=120)
        self.assertEqual(result, {"ok": True, "operation": "exec", **accepted})
        self.assertEqual(submit.call_args.kwargs["kind"], "runtime-exec")
        self.assertEqual(submit.call_args.kwargs["remote"], "remote-a")

    def test_builtin_group_manifest_is_exact_and_deterministic(self):
        from tools.manifest import BUILTIN_TOOL_GROUPS, built_in_tool_registry

        expected = (
            "instances", "domains", "runtime", "jobs", "wp", "net", "data", "fs", "mail", "context", "cache",
            "resources",
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

    def test_runtime_scoped_catalogs_hide_irrelevant_tools(self):
        from tools.manifest import project_default_groups

        wordpress = project_default_groups("wordpress")
        compose = project_default_groups("compose")
        self.assertIn("wp", wordpress)
        self.assertNotIn("runtime", wordpress)
        self.assertIn("runtime", compose)
        self.assertNotIn("wp", compose)
        self.assertIn("remote", wordpress)
        self.assertIn("remote", compose)
        self.assertIn("resources", wordpress)
        self.assertIn("resources", compose)
        with self.assertRaisesRegex(ValueError, "no MCP catalog"):
            project_default_groups("unsupported")

    def test_cli_scoped_mcp_forwards_canonical_project_root(self):
        import sandbox.commands.integ as integ

        args = type("Args", (), {
            "project_dir": "/tmp/project", "transport": "stdio",
        })()
        project = {"root": "/canonical/project", "kind": "compose"}
        with patch.object(integ, "MCP_VENV", Path(sys.executable).parent.parent), \
             patch.object(integ, "MCP_DIR", Path("/tmp/mcp-server")), \
             patch.object(integ, "_core") as core, \
             patch.object(integ.os, "execv", side_effect=SystemExit) as execv, \
             patch.dict(integ.os.environ, {}, clear=False):
            core.return_value.load_project_config.return_value = project
            with patch.object(Path, "exists", return_value=True):
                with self.assertRaises(SystemExit):
                    integ.cmd_mcp({}, args)
            self.assertEqual(integ.os.environ["SANDBOX_MCP_PROJECT_DIR"], "/canonical/project")
            self.assertEqual(execv.call_args.args[1], [
                str(Path(sys.executable).parent.parent / "bin" / "python"),
                "/tmp/mcp-server/server.py",
            ])

    def test_builtin_groups_declare_a_compatibility_registration_boundary(self):
        from tools.manifest import BUILTIN_TOOL_GROUPS, built_in_tool_registry

        specs = built_in_tool_registry().specs()
        self.assertEqual(tuple(spec.group_id for spec in specs), BUILTIN_TOOL_GROUPS)
        self.assertEqual(
            {spec.group_id: spec.dependencies for spec in specs if spec.group_id in {"instances", "domains", "runtime", "jobs", "hermes", "resources"}},
            {
                "instances": (
                    "sandbox_root", "proxy_tld", "core", "load_sandbox_yml",
                    "project_instance", "resolve_instance", "safe_json", "site_url",
                    "domain_service",
                ),
                "domains": ("domain_service",),
                "runtime": ("core", "project_instance", "runtime_service"),
                "jobs": ("job_service", "target_service", "workspace_service"),
                "hermes": ("hermes_service",),
                "resources": ("resource_service_factory",),
            },
        )
        self.assertTrue(all(spec.dependencies == ("app",) for spec in specs
                            if spec.group_id not in {"instances", "domains", "runtime", "jobs", "hermes", "resources"}))

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
            "domain_service": lambda: object(),
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
            "plugin_check", "remote", "hermes", "recovery", "resources",
        )
        for group in groups:
            self.assertNotIn("from app import *", (root / f"{group}.py").read_text())


if __name__ == "__main__":
    unittest.main()

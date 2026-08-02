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
    def test_native_proof_opt_in_cannot_enter_payload_or_persisted_models(self):
        opt_in = "SANDBOX_NATIVE_PROOF_CANDIDATE"
        launcher = (ROOT / "sandbox/isolation/launcher.py").read_text()
        helper = (ROOT / "tools/native-helper/native-helper.py").read_text()
        repository = (ROOT / "sandbox/runtimes/managed/repository.py").read_text()
        models = (ROOT / "sandbox/runtimes/base.py").read_text()
        self.assertNotIn(opt_in, launcher)
        self.assertNotIn(opt_in, helper)
        self.assertNotIn(opt_in, repository)
        self.assertNotIn(opt_in, models)

    def test_managed_native_project_payloads_cannot_bypass_execution_gateway(self):
        """Project argv is gated before legacy Compose, Herd, or host dispatch.

        This is deliberately a narrow allow-list rather than a repository-wide
        ban on ``subprocess``: fixed, reviewed control helpers (for example the
        native service and package helper verbs) remain valid mechanism code.
        The protected functions below are the only legacy entry points which
        accept project-controlled PHP/CLI payloads.
        """
        boundaries = (
            ("sandbox/core/_docker.py", "wpcli", "_managed_execution_gate", "compose("),
            ("sandbox/core/_tests.py", "_ensure_project_dependencies_docker", "_managed_execution_gate", "compose("),
            ("sandbox/core/_tests.py", "_run_tests", "_managed_execution_gate", "compose("),
            ("sandbox/core/_tests.py", "_run_tests_unit", "_managed_execution_gate", "compose("),
            ("sandbox/core/_provision.py", "_wire_project_plugins", "_managed_execution_gate", "wpcli("),
            ("sandbox/core/_provision.py", "_wire_project_themes", "_managed_execution_gate", "wpcli("),
            ("sandbox/jobs/supervisor.py", "run_descriptor", "_managed_native_job", "subprocess.Popen("),
            ("mcp/wp-server/tools/wp.py", "wp_cli", "_managed_execution_unavailable", "_wpcli("),
            ("mcp/wp-server/tools/wp.py", "wp_exec", "_managed_execution_unavailable", "_host_run("),
            ("mcp/wp-server/tools/wp.py", "run_tests", "_managed_execution_unavailable", "subprocess.run("),
            ("mcp/wp-server/tools/wp.py", "wp_cli_async", "_managed_execution_unavailable", "subprocess.run("),
        )

        def function_source(path, name):
            source = path.read_text()
            tree = ast.parse(source)
            node = next(
                candidate for candidate in ast.walk(tree)
                if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef))
                and candidate.name == name
            )
            lines = source.splitlines(keepends=True)
            return "".join(lines[node.lineno - 1:node.end_lineno])

        for relative, function, gate, dispatch in boundaries:
            section = function_source(ROOT / relative, function)
            self.assertIn(gate, section, f"{relative}:{function} has no managed-native gate")
            self.assertIn(dispatch, section, f"{relative}:{function} has no legacy dispatch")
            self.assertLess(
                section.index(gate), section.index(dispatch),
                f"{relative}:{function} dispatches a project payload before its managed-native gate",
            )

        command = (ROOT / "sandbox/commands/runtime.py").read_text()
        self.assertLess(command.index("ExecutionRequest("), command.index("execute_project(cfg, execution_request)"))
        self.assertLess(command.index("execute_project(cfg, execution_request)"),
                        command.index("runtime_service(cfg).invoke("))

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
        self.assertEqual(len(COMMANDS), 86)
        self.assertEqual(validate_builtin_command_coverage(), ())

        import sys
        mcp_root = ROOT / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        try:
            from tools.manifest import BUILTIN_TOOL_GROUPS, BUILTIN_TOOL_NAMES
            self.assertEqual(len(BUILTIN_TOOL_GROUPS), 22)
            tool_names = tuple(
                name for group_id in BUILTIN_TOOL_GROUPS
                for name in BUILTIN_TOOL_NAMES[group_id]
            )
            self.assertEqual(len(tool_names), 120)
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

    def test_resolver_state_has_one_repository_owner_and_no_transport_consumers(self):
        allowed = {"sandbox/application/context.py", "sandbox/network/repository.py"}
        violations = []
        for path in production_python_files():
            relative = str(path.relative_to(ROOT))
            if relative in allowed:
                continue
            text = path.read_text()
            if "resolver-state.json" in text or re.search(
                r"runtime[^\n]{0,80}(?:resolver|domain)[^\n]{0,80}\.json", text,
            ):
                violations.append(relative)
        self.assertEqual(violations, [])

    def test_ingress_state_has_one_repository_owner_and_no_transport_consumers(self):
        allowed = {"sandbox/application/context.py", "sandbox/ingress/repository.py"}
        violations = []
        for path in production_python_files():
            relative = str(path.relative_to(ROOT))
            if relative in allowed:
                continue
            text = path.read_text()
            if "ingress-state.json" in text or re.search(
                r"runtime[^\n]{0,80}ingress[^\n]{0,80}\.json", text,
            ):
                violations.append(relative)
        self.assertEqual(violations, [])

    def test_ingress_adapters_are_manifest_declared_and_do_not_import_compatibility_facades(self):
        manifest = (ROOT / "sandbox" / "ingress" / "manifest.py").read_text()
        declared = {
            "sandbox-caddy", "herd-valet", "system-nginx", "system-apache",
            "system-caddy", "traefik", "nginx-proxy-manager", "ddev", "local",
            "xampp", "laragon", "wamp", "unidentified",
        }
        for adapter_id in declared:
            self.assertIn(f'"{adapter_id}"', manifest)
        forbidden = re.compile(
            r"(?:from|import)\s+sandbox\.core(?:\._domains)?(?:\s+import|\s|$)",
        )
        violations = []
        for path in (ROOT / "sandbox" / "ingress").rglob("*.py"):
            if forbidden.search(path.read_text()):
                violations.append(str(path.relative_to(ROOT)))
        service = ROOT / "sandbox" / "application" / "ingress_service.py"
        if forbidden.search(service.read_text()):
            violations.append(str(service.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_unregistered_domain_result_cannot_reach_host_mutation(self):
        service = (ROOT / "sandbox" / "application" / "domain_service.py").read_text()
        context_index = service.index("def _context")
        prepare_index = service.index("def _prepare")
        section = service[context_index:prepare_index]
        self.assertIn("project_not_registered", service)
        self.assertIn("raise DomainContextError", section)

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

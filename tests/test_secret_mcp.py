from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent
MCP_ROOT = ROOT / "mcp/wp-server"


class FakeServer:
    def __init__(self):
        self.names = []

    def tool(self):
        def decorate(function):
            self.names.append(function.__name__)
            return function
        return decorate


class SecretMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(MCP_ROOT))

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(MCP_ROOT))

    def test_group_is_opt_in_and_not_in_runtime_defaults(self):
        manifest = importlib.import_module("tools.manifest")
        self.assertIn("secrets", manifest.BUILTIN_TOOL_GROUPS)
        self.assertNotIn("secrets", manifest.DEFAULT_MCP_GROUPS)
        self.assertNotIn("secrets", manifest.WORDPRESS_PROJECT_GROUPS)
        self.assertNotIn("secrets", manifest.COMPOSE_PROJECT_GROUPS)
        self.assertEqual(manifest.BUILTIN_TOOL_NAMES["secrets"],
                         ("secret_inspect", "secret_validate", "secret_use_profile"))

    def test_group_registers_only_three_non_reveal_tools(self):
        from dependencies import ToolDependencies
        module = importlib.import_module("tools.secrets")
        server = FakeServer()
        module.register(server, ToolDependencies({"secret_service_factory": lambda _: object()}))
        self.assertEqual(server.names, ["secret_inspect", "secret_validate", "secret_use_profile"])
        source = (MCP_ROOT / "tools/secrets.py").read_text()
        self.assertNotIn("reveal", source.lower())
        self.assertNotIn("candidate", source.lower())

    def test_adapter_returns_bounded_service_error(self):
        module = importlib.import_module("tools.secrets")
        class Service:
            def inspect(self, *args, **kwargs):
                from sandbox.secrets import SecretBrokerError
                raise SecretBrokerError("source_mode_denied", "mode denied")
        module._service_factory = lambda _: Service()
        result = module.secret_inspect("/fixture", "fixture")
        self.assertEqual(result["error"]["code"], "source_mode_denied")
        self.assertNotIn("value", repr(result).lower())


if __name__ == "__main__":
    unittest.main()

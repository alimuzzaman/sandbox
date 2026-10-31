from __future__ import annotations

import importlib
import inspect
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from sandbox.secrets.models import RunResult, SecretBrokerError, SecretOutcomeCode, UseProfile
from sandbox.secrets.providers.base import CapabilityStatus, ProviderError
from sandbox.secrets.service import SecretService


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

    def setUp(self):
        self.started = time.monotonic()
        module = importlib.import_module("tools.secrets")
        self.original_factory = module._service_factory

    def tearDown(self):
        importlib.import_module("tools.secrets")._service_factory = self.original_factory
        self.assertLess(time.monotonic() - self.started, 5)

    def test_group_is_opt_in_and_not_in_runtime_defaults(self):
        manifest = importlib.import_module("tools.manifest")
        self.assertIn("secrets", manifest.BUILTIN_TOOL_GROUPS)
        self.assertNotIn("secrets", manifest.DEFAULT_MCP_GROUPS)
        self.assertNotIn("secrets", manifest.WORDPRESS_PROJECT_GROUPS)
        self.assertNotIn("secrets", manifest.COMPOSE_PROJECT_GROUPS)
        self.assertEqual(manifest.BUILTIN_TOOL_NAMES["secrets"],
                         ("secret_source_info", "secret_inspect", "secret_validate",
                          "secret_use_profile"))

    def test_group_registers_only_four_non_reveal_tools(self):
        from dependencies import ToolDependencies
        module = importlib.import_module("tools.secrets")
        server = FakeServer()
        module.register(server, ToolDependencies({"secret_service_factory": lambda _: object()}))
        self.assertEqual(server.names, [
            "secret_source_info", "secret_inspect", "secret_validate", "secret_use_profile",
        ])
        source = (MCP_ROOT / "tools/secrets.py").read_text()
        self.assertNotIn("reveal", source.lower())
        self.assertNotIn("candidate", source.lower())

    def test_session_mode_is_not_exposed_over_mcp(self):
        """Spec 059 FR-002: session mode is local CLI only (regression guard)."""
        import inspect
        module = importlib.import_module("tools.secrets")
        parameters = set(inspect.signature(module.secret_use_profile).parameters)
        self.assertFalse({name for name in parameters
                          if "session" in name or "lifetime" in name})
        source = (MCP_ROOT / "tools/secrets.py").read_text()
        self.assertNotIn("run_session", source)
        self.assertNotIn("lifetime", source.lower())

    def test_source_info_adapter_never_accepts_exact_size(self):
        module = importlib.import_module("tools.secrets")
        class Service:
            def source_info(self, source, *, surface):
                return {"ok": True, "source": source, "surface": surface}
        module._service_factory = lambda _: Service()
        result = module.secret_source_info("/fixture", "fixture")
        self.assertEqual(result, {"ok": True, "source": "fixture", "surface": "mcp"})

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

    def test_adapter_discards_unknown_exception_detail_and_traceback(self):
        module = importlib.import_module("tools.secrets")
        canary = "SB_SYNTHETIC_SECRET_CANARY_7f34"
        class Service:
            def inspect(self, *args, **kwargs):
                raise RuntimeError(canary)
        module._service_factory = lambda _: Service()
        result = module.secret_inspect("/fixture", "fixture")
        self.assertEqual(result["error"]["code"], "operation_failed")
        self.assertNotIn(canary, repr(result))
        self.assertNotIn("Traceback", repr(result))


class FakeAudit:
    """Memory-only audit sink: no user files or runtime state are accessed."""

    def __init__(self):
        self.events = []

    def intent(self, operation, source, keys, **fields):
        self.events.append({"phase": "intent", "operation": operation, "source": source,
                            "keys": keys, **fields})
        return "synthetic-correlation"

    def outcome(self, correlation, operation, source, keys, **fields):
        self.events.append({"phase": "outcome", "operation": operation, "source": source,
                            "keys": keys, "correlation": correlation, **fields})


class ProviderMcpTests(unittest.TestCase):
    CANARY = "SB_SYNTHETIC_MCP_CANARY_067_Abc123"

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(MCP_ROOT))

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(MCP_ROOT))

    def setUp(self):
        self.started = time.monotonic()
        self.module = importlib.import_module("tools.secrets")
        self.policy = {
            "type": "infisical", "alias": "fixture", "endpoint": "https://provider.invalid",
            "projectId": "fixture-project", "environment": "development", "path": "/exact",
            "key": "API_TOKEN", "clientId": "controller-fixture", "timeoutSeconds": 10,
            "mcpModes": ["source_info", "keys", "metadata", "validate", "use"],
        }
        self.audit = FakeAudit()
        self.registry = mock.Mock()
        self.registry.policy.side_effect = self.policy_for
        self.provider = mock.Mock()
        self.provider.status.return_value = CapabilityStatus(state="available", endpoint_reachable=True)
        self.provider.resolve_exact.return_value = self.CANARY.encode()
        self.store = mock.Mock()
        self.store.status.return_value = "present"
        self.store.get.return_value = object()
        self.profile = UseProfile("fixture-use", "fixture", "API_TOKEN", ("fixture-client", "check"),
            "API_TOKEN", timeout_seconds=2, max_output_bytes=128, mcp=True)
        self.service = SecretService(self.registry, self.audit,
            revision_key_path=Path("/synthetic-never-read/revision.key"),
            provider_adapter=self.provider, credential_store=self.store,
            use_profiles={self.profile.name: self.profile})
        self.factory = mock.Mock(return_value=self.service)
        patcher = mock.patch.object(self.module, "_service_factory", self.factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        child_patcher = mock.patch("sandbox.secrets.service.run_with_secret",
            return_value=RunResult(0, "exited", "checked", False, 0.01))
        self.child = child_patcher.start()
        self.addCleanup(child_patcher.stop)

    def tearDown(self):
        self.assertLess(time.monotonic() - self.started, 5)

    def policy_for(self, source):
        if source != "fixture":
            raise SecretBrokerError("source_unknown", "secret source alias is not registered")
        return self.policy

    def assert_secret_free(self, result):
        encoded = json.dumps(result)
        for forbidden in (self.CANARY, "synthetic-bootstrap", "synthetic-access-token",
                          "https://provider.invalid", "fixture-project", "controller-fixture",
                          "Traceback", "Authorization", "secretValue", "clientSecret"):
            self.assertNotIn(forbidden, encoded)
        denied_names = {"value", "credential", "access_token", "provider_body", "body", "headers",
                        "endpoint", "environment", "argv", "client_id", "clientId", "locator"}
        def check_names(value):
            if isinstance(value, dict):
                self.assertFalse(denied_names.intersection(value))
                for item in value.values():
                    check_names(item)
            elif isinstance(value, list):
                for item in value:
                    check_names(item)
        check_names(result)

    def assert_denied(self, result, code):
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], code)
        self.assertFalse(result["error"]["retryable"])
        self.assert_secret_free(result)
        self.store.get.assert_not_called()
        self.provider.resolve_exact.assert_not_called()
        self.child.assert_not_called()

    def test_source_info_and_singleton_keys_do_not_resolve_values(self):
        result = self.module.secret_source_info("/synthetic-project", "fixture")
        self.assertTrue(result["ok"])
        self.assertEqual(result["source_type"], "infisical")
        self.assertEqual(result["sandbox_selection_policy"], "exact_item")
        self.assertEqual(result["provider_authorization_scope"], "configured_project")
        self.assertEqual(result["credential_state"], "present")
        self.assertEqual(result["reachability"], "reachable")
        self.assert_secret_free(result)
        self.store.status.assert_called_once_with(self.policy)
        self.provider.status.assert_called_once_with(self.policy)
        self.store.get.assert_not_called()
        self.provider.resolve_exact.assert_not_called()
        keys = self.module.secret_inspect("/synthetic-project", "fixture")
        self.assertEqual(keys["keys"], ["API_TOKEN"])
        self.assertEqual(keys["count"], 1)
        self.assert_secret_free(keys)
        self.store.get.assert_not_called()
        self.provider.resolve_exact.assert_not_called()

    def test_metadata_and_validate_resolve_only_registered_item_with_safe_envelopes(self):
        metadata = self.module.secret_inspect("/synthetic-project", "fixture", mode="metadata")
        self.assertTrue(metadata["ok"])
        self.assertEqual(metadata["entries"][0]["key"], "API_TOKEN")
        self.assertEqual(metadata["entries"][0]["state"], "present")
        self.assertNotIn("exact_length", metadata["entries"][0])
        validation = self.module.secret_validate("/synthetic-project", "fixture", "API_TOKEN", "opaque-token-v1")
        self.assertTrue(validation["ok"])
        self.assertFalse(validation["validation"]["live_checked"])
        for result in (metadata, validation):
            self.assert_secret_free(result)
        self.assertEqual(self.store.get.call_count, 2)
        self.assertEqual(self.provider.resolve_exact.call_count, 2)
        for call in self.provider.resolve_exact.call_args_list:
            self.assertEqual(call.args[0], self.policy)
            self.assertIs(call.args[1], self.store.get.return_value)
        self.assertEqual([event["phase"] for event in self.audit.events], ["intent", "outcome", "intent", "outcome"])
        self.assert_secret_free(self.audit.events)

    def test_each_mcp_mode_requires_explicit_source_authority_before_adapter_access(self):
        operations = [
            ("source_info", lambda: self.module.secret_source_info("/synthetic-project", "fixture")),
            ("keys", lambda: self.module.secret_inspect("/synthetic-project", "fixture")),
            ("metadata", lambda: self.module.secret_inspect("/synthetic-project", "fixture", mode="metadata")),
            ("validate", lambda: self.module.secret_validate("/synthetic-project", "fixture", "API_TOKEN", "opaque-token-v1")),
            ("use", lambda: self.module.secret_use_profile("/synthetic-project", "fixture-use")),
        ]
        for mode, operation in operations:
            with self.subTest(mode=mode):
                self.policy["mcpModes"] = [entry for entry in ("source_info", "keys", "metadata", "validate", "use") if entry != mode]
                self.assert_denied(operation(), "source_mode_denied")
                self.store.status.assert_not_called()
                self.provider.status.assert_not_called()
        self.assertEqual(len(self.audit.events), 10)
        self.assertTrue(all(event["surface"] == "mcp" for event in self.audit.events))

    def test_fixed_use_requires_both_profile_and_source_authority(self):
        self.service.use_profiles["fixture-use"] = UseProfile(
            "fixture-use", "fixture", "API_TOKEN", ("fixture-client",), "API_TOKEN", mcp=False)
        self.assert_denied(self.module.secret_use_profile("/synthetic-project", "fixture-use"), "profile_denied")
        self.service.use_profiles["fixture-use"] = self.profile
        self.policy["mcpModes"].remove("use")
        self.assert_denied(self.module.secret_use_profile("/synthetic-project", "fixture-use"), "source_mode_denied")

    def test_fixed_use_receives_exact_profile_and_returns_no_child_environment(self):
        result = self.module.secret_use_profile("/synthetic-project", "fixture-use")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "fixture-use")
        self.child.assert_called_once_with(self.profile.argv, destination="API_TOKEN", value=self.CANARY,
            timeout_seconds=2, max_output_bytes=128)
        self.assert_secret_free(result)
        self.assert_secret_free(self.audit.events)

    def test_selectors_and_wildcards_are_refused_before_value_access(self):
        for key in ("OTHER_KEY", "*", "API_*", "project/OTHER_KEY", "", "API_TOKEN,OTHER_KEY"):
            with self.subTest(key=key):
                self.assert_denied(self.module.secret_inspect("/synthetic-project", "fixture", keys=[key]), "selection_invalid")
                self.assert_denied(self.module.secret_validate("/synthetic-project", "fixture", key, "opaque-token-v1"), "selection_invalid")
        self.assertEqual(self.audit.events, [])

    def test_duplicate_selection_and_exact_length_cannot_expand_provider_disclosure(self):
        result = self.module.secret_inspect("/synthetic-project", "fixture", keys=["API_TOKEN", "API_TOKEN"], mode="metadata")
        self.assert_denied(result, "mode_requires_one_key")
        result = self.module.secret_inspect("/synthetic-project", "fixture", keys=["API_TOKEN"], mode="metadata", exact_length=True)
        self.assert_denied(result, "exact_length_denied")

    def test_provider_modes_reject_masking_reveal_listing_search_and_export(self):
        for mode in ("masked", "reveal", "session", "list", "search", "export", "delete", "rotate"):
            with self.subTest(mode=mode):
                self.assert_denied(self.module.secret_inspect("/synthetic-project", "fixture", keys=["API_TOKEN"], mode=mode), "mode_invalid")

    def test_mcp_signatures_accept_no_selector_mutation_or_arbitrary_child_arguments(self):
        expected = {
            "secret_source_info": {"project_dir", "source"},
            "secret_inspect": {"project_dir", "source", "keys", "mode", "exact_length"},
            "secret_validate": {"project_dir", "source", "key", "profile"},
            "secret_use_profile": {"project_dir", "profile"},
        }
        for name, names in expected.items():
            with self.subTest(tool=name):
                function = getattr(self.module, name)
                signature = inspect.signature(function)
                self.assertEqual(set(signature.parameters), names)
                self.assertFalse(any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()))
                arguments = {"project_dir": "/synthetic-project", "source": "fixture", "key": "API_TOKEN", "profile": "fixture-use"}
                required = {key: value for key, value in arguments.items() if key in names}
                for forbidden in ("endpoint", "projectId", "environment", "path", "clientId", "argv", "command", "destination", "value", "intent", "credential", "session"):
                    with self.assertRaises(TypeError):
                        function(**required, **{forbidden: "synthetic"})
        self.factory.assert_not_called()

    def test_provider_mutations_remain_denied_even_at_service_mcp_boundary(self):
        operations = [
            (lambda: self.service.set_provider_credential("fixture", self.CANARY, intent="create", surface="mcp"), "credential_write_denied"),
            (lambda: self.service.set("fixture", "API_TOKEN", self.CANARY, surface="mcp"), "update_denied"),
            (lambda: self.service.generate("fixture", "API_TOKEN", "random-base64url-32-v1", surface="mcp"), "update_denied"),
            (lambda: self.service.copy_reference("fixture", "API_TOKEN", "fixture", "OTHER_KEY", surface="mcp"), "update_denied"),
            (lambda: self.service.organize("fixture", apply=True, surface="mcp"), "organize_denied"),
            (lambda: self.service.reveal("fixture", "API_TOKEN", mock.Mock(), confirmed=True, surface="mcp"), "reveal_denied"),
        ]
        for operation, code in operations:
            with self.subTest(code=code):
                self.assert_denied(self.module._safe(operation), code)
        self.store.put.assert_not_called()
        self.assertEqual(self.audit.events, [])
        server = FakeServer()
        from dependencies import ToolDependencies
        self.module.register(server, ToolDependencies({"secret_service_factory": self.factory}))
        self.assertEqual(server.names, ["secret_source_info", "secret_inspect", "secret_validate", "secret_use_profile"])

    def test_arbitrary_child_and_sessions_are_denied_before_access(self):
        operations = [
            lambda: self.service.run("fixture", "API_TOKEN", ["fixture-client"], destination="API_TOKEN", surface="mcp"),
            lambda: self.service.run_many("fixture", [("API_TOKEN", "API_TOKEN")], ["fixture-client"], surface="mcp"),
            lambda: self.service.run_session("fixture", [("API_TOKEN", "API_TOKEN")], ["fixture-client"], lifetime_seconds=2, display=mock.Mock(), surface="mcp"),
        ]
        for operation in operations:
            self.assert_denied(self.module._safe(operation), "command_denied")
        self.assertEqual(self.audit.events, [])

    def test_privileged_fixed_consumer_and_changed_profile_selector_are_denied(self):
        self.service.use_profiles["fixture-use"] = UseProfile("fixture-use", "fixture", "API_TOKEN", ("sudo", "fixture-client"), "API_TOKEN", mcp=True)
        self.assert_denied(self.module.secret_use_profile("/synthetic-project", "fixture-use"), "escalation_unsupported")
        self.assertEqual(self.audit.events, [])
        self.service.use_profiles["fixture-use"] = UseProfile("fixture-use", "fixture", "OTHER_KEY", ("fixture-client",), "API_TOKEN", mcp=True)
        self.assert_denied(self.module.secret_use_profile("/synthetic-project", "fixture-use"), "selection_invalid")

    def test_all_stable_provider_failures_have_safe_remedies_and_no_retry(self):
        for outcome in SecretOutcomeCode:
            with self.subTest(code=outcome.value):
                self.store.get.reset_mock()
                self.provider.resolve_exact.reset_mock()
                error = ProviderError(outcome.value)
                error.__cause__ = RuntimeError(self.CANARY)
                self.provider.resolve_exact.side_effect = error
                result = self.module.secret_inspect("/synthetic-project", "fixture", mode="metadata")
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], error.as_dict())
                self.assertTrue(result["error"]["remedy"])
                self.assertFalse(result["error"]["retryable"])
                self.assert_secret_free(result)
                self.store.get.assert_called_once_with(self.policy)
                self.provider.resolve_exact.assert_called_once()
                self.child.assert_not_called()
        self.assert_secret_free(self.audit.events)

    def test_unknown_provider_exception_is_discarded_without_detail_or_retry(self):
        self.provider.resolve_exact.side_effect = RuntimeError(self.CANARY)
        result = self.module.secret_inspect("/synthetic-project", "fixture", mode="metadata")
        self.assertEqual(result, {"ok": False, "error": {"code": "operation_failed", "message": "secret operation failed", "retryable": False}})
        self.assert_secret_free(result)
        self.provider.resolve_exact.assert_called_once()

    def test_adapter_redacts_sensitive_structures_and_non_dict_results_fail_closed(self):
        service = mock.Mock()
        self.factory.return_value = service
        service.source_info.return_value = {"ok": True, "nested": {
            "password": self.CANARY, "authorization": "Bearer synthetic-access-token"}}
        result = self.module.secret_source_info("/synthetic-project", "fixture")
        self.assertEqual(result["nested"], {"password": "[REDACTED]", "authorization": "[REDACTED]"})
        self.assert_secret_free(result)
        for unexpected in ([self.CANARY], self.CANARY, None):
            service.source_info.return_value = unexpected
            result = self.module.secret_source_info("/synthetic-project", "fixture")
            self.assertEqual(result["error"]["code"], "redaction_failed")
            self.assert_secret_free(result)


if __name__ == "__main__":
    unittest.main()

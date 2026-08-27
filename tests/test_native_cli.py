from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock


class TestNativeCli(unittest.TestCase):
    def test_support_is_truthful_and_managed_is_not_advertised(self):
        from sandbox.commands.native import support
        result = support()
        managed = next(item for item in result["runtimes"]
                       if item["adapter_id"] == "ubuntu-nspawn")
        self.assertFalse(managed["adoptable"])
        self.assertEqual(managed["support_tier"], "implemented_unproven")
        self.assertIsNone(managed["evidence_id"])

    def test_preflight_cli_emits_one_nonmutating_json_object(self):
        from sandbox.commands.native import cmd_native
        service = SimpleNamespace(inspect=lambda: {
            "ok": False, "operation": "native_preflight", "state": "blocked",
            "mutated": False, "reason": {"code": "isolation_prerequisite_missing",
                                          "missing": ["nftables"]},
        })
        args = SimpleNamespace(action="preflight", json=True, project_dir=".",
                               label="default", web_server="nginx")
        output = io.StringIO()
        with mock.patch("sandbox.application.context.native_isolation_preflight",
                        return_value=service), redirect_stdout(output):
            cmd_native({}, args)
        result = json.loads(output.getvalue())
        self.assertFalse(result["mutated"]); self.assertEqual(result["state"], "blocked")

    def test_credential_status_is_secret_free_and_fail_closed(self):
        from sandbox.commands.native import cmd_native

        args = SimpleNamespace(action="credential-status", json=True, project_dir=".",
                               label="default", web_server="nginx")
        output = io.StringIO()
        with mock.patch("sandbox.commands.native._read_credential_repository", return_value=None), \
                redirect_stdout(output):
            cmd_native({}, args)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"])
        self.assertFalse(result["mutated"])
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["support_tier"], "implemented_unproven")
        self.assertFalse(result["adoptable"])
        self.assertEqual(result["binding_states"], [])
        self.assertIn("support_unproven", result["refusal_reasons"])
        self.assertNotIn("source_reference", output.getvalue())

    def test_credential_acceptance_routes_through_runtime_operation(self):
        from sandbox.commands.native import cmd_native
        from sandbox.runtimes.base import OperationResult

        request = {"action": "revoke", "binding_id": "binding-0001", "version": 1,
                   "machine_id": "sb-0123456789ab", "owner": "owner-0001"}
        calls = []
        service = SimpleNamespace(invoke=lambda value: (
            calls.append(value) or OperationResult(
                False, "credential_acceptance", value.project_root, "wordpress",
                {"state": "blocked", "mutated": False, "proof_candidate": True,
                 "adoptable": False, "reason": {"code": "credential_acceptance_unavailable"}},
            )))
        args = SimpleNamespace(action="credential-acceptance", json=True, project_dir=".",
                               label="default", web_server="nginx",
                               credential_request=json.dumps(request))
        output = io.StringIO()
        with mock.patch("sandbox.application.context.runtime_service", return_value=service), \
                redirect_stdout(output):
            cmd_native({}, args)
        self.assertEqual(calls[0].operation, "credential_acceptance")
        self.assertEqual(dict(calls[0].arguments["request"]), request)
        result = json.loads(output.getvalue())
        self.assertFalse(result["adoptable"])
        self.assertEqual(result["reason"]["code"], "credential_acceptance_unavailable")

    def test_credential_acceptance_rejects_secret_shaped_extra_before_dispatch(self):
        from sandbox.commands.native import cmd_native
        request = {"action": "revoke", "binding_id": "binding-0001", "version": 1,
                   "machine_id": "sb-0123456789ab", "owner": "owner-0001",
                   "body": "forbidden"}
        args = SimpleNamespace(action="credential-acceptance", json=True, project_dir=".",
                               label="default", web_server="nginx",
                               credential_request=json.dumps(request))
        output = io.StringIO()
        with mock.patch("sandbox.application.context.runtime_service") as runtime, \
                redirect_stdout(output):
            cmd_native({}, args)
        runtime.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason"]["code"], "credential_acceptance_invalid")

    def test_credential_acceptance_rejects_public_v1_or_unknown_protocol_tag(self):
        from sandbox.commands.native import cmd_native
        for protocol in ("credential-broker-controller-v1",
                         "credential-broker-controller-v3"):
            with self.subTest(protocol=protocol):
                request = {"action": "revoke", "binding_id": "binding-0001",
                           "version": 1, "machine_id": "sb-0123456789ab",
                           "owner": "owner-0001", "protocol": protocol}
                args = SimpleNamespace(
                    action="credential-acceptance", json=True, project_dir=".",
                    label="default", web_server="nginx",
                    credential_request=json.dumps(request),
                )
                output = io.StringIO()
                with mock.patch("sandbox.application.context.runtime_service") as runtime, \
                        redirect_stdout(output):
                    cmd_native({}, args)
                runtime.assert_not_called()
                result = json.loads(output.getvalue())
                self.assertEqual(result["reason"]["code"],
                                 "credential_acceptance_invalid")

    def test_credential_acceptance_preserves_indeterminate_without_retry(self):
        from sandbox.commands.native import cmd_native
        from sandbox.runtimes.base import OperationResult

        request = {"action": "request", "binding_id": "binding-0001", "version": 4,
                   "machine_id": "sb-0123456789ab", "owner": "owner-0001",
                   "content_type": "application/json", "deadline_seconds": 5,
                   "correlation_id": "correlation-0001"}
        calls = []

        def invoke(value):
            calls.append(dict(value.arguments["request"]))
            return OperationResult(
                False, "credential_acceptance", value.project_root, "wordpress",
                {"state": "blocked", "mutated": False, "proof_candidate": True,
                 "adoptable": False,
                 "reason": {"code": "credential_acceptance_indeterminate"}},
            )

        args = SimpleNamespace(
            action="credential-acceptance", json=True, project_dir=".",
            label="default", web_server="nginx",
            credential_request=json.dumps(request),
        )
        output = io.StringIO()
        with mock.patch("sandbox.application.context.runtime_service",
                        return_value=SimpleNamespace(invoke=invoke)), redirect_stdout(output):
            cmd_native({}, args)
        self.assertEqual(calls, [request])
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason"]["code"],
                         "credential_acceptance_indeterminate")
        self.assertNotIn("retry", result)


if __name__ == "__main__": unittest.main()

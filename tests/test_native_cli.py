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


if __name__ == "__main__": unittest.main()

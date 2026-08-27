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


    # --- Credential Vault acceptance seam (spec 045, T037) -------------------

    @staticmethod
    def _acceptance_args(action, **overrides):
        values = {"action": action, "json": True, "project_dir": ".", "label": "default",
                  "web_server": "nginx", "source_ref": "vault/acceptance-key",
                  "binding_id": "bind-acceptance", "binding_version": 1,
                  "instance_id": "sb-0123456789ab", "host": "api.example.com",
                  "path": "/v1/ping", "method": "get", "auth_form": "authorization_bearer",
                  "expires_at": "2030-01-01T00:00:00Z"}
        values.update(overrides)
        return SimpleNamespace(**values)

    def _run(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            from sandbox.commands.native import cmd_native
            cmd_native({}, args)
        return json.loads(output.getvalue()), output.getvalue()

    def test_acceptance_actions_refuse_without_live_evidence(self):
        for action in ("credential-bind", "credential-request", "credential-revoke"):
            with self.subTest(action=action):
                result, raw = self._run(self._acceptance_args(action))
                self.assertFalse(result["ok"])
                self.assertFalse(result["mutated"])
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(result["support_tier"], "implemented_unproven")
                self.assertFalse(result["adoptable"])
                self.assertIsNone(result["evidence_id"])
                self.assertIn("support_unproven", result["refusal_reasons"])
                self.assertEqual(result["reason"]["code"], "credential_acceptance_unproven")
                self.assertNotIn("acceptance-key", raw)

    def test_only_opaque_references_and_exact_metadata_are_accepted(self):
        result, raw = self._run(self._acceptance_args("credential-bind"))
        accepted = result["accepted"]
        self.assertEqual(set(accepted), {"binding_id", "instance_id",
                                         "credential_reference_digest", "scheme", "host",
                                         "port", "method", "path", "auth_form",
                                         "expires_at"})
        self.assertEqual((accepted["scheme"], accepted["port"]), ("https", 443))
        self.assertEqual(accepted["method"], "GET")
        self.assertEqual(len(accepted["credential_reference_digest"]), 64)
        self.assertNotIn("vault/acceptance-key", raw)
        self.assertNotIn("source_reference", raw)

    def test_non_opaque_or_inexact_metadata_is_refused_without_echo(self):
        cases = (
            ("credential-bind", {"source_ref": "AKIAEXAMPLESECRETVALUE=x y"}),
            ("credential-bind", {"source_ref": "../../etc/shadow"}),
            ("credential-bind", {"source_ref": "https://example.com/token"}),
            ("credential-bind", {"host": "http://api.example.com"}),
            ("credential-bind", {"host": "203.0.113.9"}),
            ("credential-bind", {"path": "/v1/../admin"}),
            ("credential-bind", {"method": "connect"}),
            ("credential-bind", {"auth_form": "guest_header"}),
            ("credential-bind", {"expires_at": "tomorrow"}),
            ("credential-bind", {"binding_id": "bind acceptance"}),
            ("credential-bind", {"source_ref": None}),
            ("credential-request", {"binding_version": 0}),
            ("credential-revoke", {"binding_version": None}),
        )
        for action, overrides in cases:
            with self.subTest(action=action, overrides=overrides):
                result, raw = self._run(self._acceptance_args(action, **overrides))
                self.assertFalse(result["ok"])
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(result["reason"]["code"], "credential_metadata_invalid")
                self.assertEqual(result["accepted"], {})
                for value in overrides.values():
                    if isinstance(value, str):
                        self.assertNotIn(value, raw)

    def test_the_public_seam_exposes_no_plaintext_credential_option(self):
        import argparse

        from sandbox.commands.native import configure_parser

        parser = argparse.ArgumentParser()
        configure_parser(parser)
        options = {option for action in parser._actions for option in action.option_strings}
        for forbidden in ("--secret", "--credential", "--token", "--api-key",
                          "--password", "--value", "--source-file", "--stdin"):
            self.assertNotIn(forbidden, options)
        self.assertIn("--source-ref", options)

    def test_acceptance_actions_need_no_project_runtime(self):
        from sandbox.commands.native import _predispatch
        for action in ("credential-status", "credential-bind", "credential-request",
                       "credential-revoke"):
            self.assertTrue(_predispatch(SimpleNamespace(action=action)))
        self.assertFalse(_predispatch(SimpleNamespace(action="status")))


if __name__ == "__main__": unittest.main()

"""Focused contract tests for the path-free remote workspace transport."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import unittest

from sandbox.transports.remote_workspaces import (
    RemoteWorkspaceError,
    RemoteWorkspaceTransport,
    WorkspaceCreateRequest,
)


@dataclass
class _Result:
    returncode: int = 0
    stdout: str = '{"ok":true,"workspaces":[]}'
    stderr: str = ""


class TestRemoteWorkspaceTransport(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.process_calls = []

        def run(remote, command, *, timeout):
            self.calls.append((remote, command, timeout))
            return _Result()

        self.transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {
                "provisioned": True,
                "capabilities": ["workspace.index"],
            },
            ssh_run=run,
            ssh_process=lambda remote, command, input_data, timeout: (
                self.process_calls.append((remote, command, input_data, timeout))
                or _Result(stdout='{"ok":true,"status":"accepted"}')
            ),
            remote_sb_path=lambda _remote: "/opt/sandbox/sb",
        )

    def _command(self):
        self.assertEqual(len(self.calls), 1)
        return self.calls[0][1]

    def test_list_uses_identity_and_never_project_path(self):
        result = self.transport.list(
            "remote-a", project_identity="project-identity", active_only=True,
        )
        self.assertEqual(result, {"ok": True, "workspaces": []})
        command = self._command()
        self.assertIn("workspace list", command)
        self.assertIn("--project-identity project-identity", command)
        self.assertIn("--active-only", command)
        self.assertNotIn("--project-dir", command)
        self.assertNotIn("/project", command)

    def test_list_forwards_bounded_size_measurement_opt_in(self):
        self.transport.list("remote-a", measure_sizes=True)
        command = self._command()
        self.assertIn("--measure-sizes", command)
        self.assertNotIn("--project-dir", command)
        self.calls.clear()
        self.transport.list("remote-a")
        self.assertNotIn("--measure-sizes", self._command())
        with self.assertRaises(RemoteWorkspaceError) as invalid:
            self.transport.list("remote-a", measure_sizes="yes")
        self.assertEqual(invalid.exception.code, "workspace_request_invalid")

    def test_status_migration_plan_and_apply_are_id_based(self):
        self.transport.status("remote-a", "ws-123", project_identity="project-identity")
        command = self._command()
        self.assertIn("--workspace-id ws-123", command)
        self.assertNotIn("--project-dir", command)

        self.calls.clear()
        self.transport.migration_plan(
            "remote-a", "project-identity", inventory_digest="digest-1", index_generation=4,
        )
        command = self._command()
        self.assertIn("workspace migrate", command)
        self.assertIn("--inventory-digest digest-1", command)
        self.assertIn("--index-generation 4", command)
        self.assertNotIn("--path", command)

        self.calls.clear()
        self.transport.migration_apply("remote-a", "plan-123", confirm=True)
        command = self._command()
        self.assertIn("--plan-id plan-123", command)
        self.assertIn("--confirm", command)
        self.assertNotIn("--project-dir", command)

    def test_sync_publication_is_path_free_and_bound_to_preflight_generation(self):
        self.transport.publish_sync(
            "remote-a", "ws-123", "project-identity", "gen-123",
            "a" * 64, "b" * 64, 2, 12, 7,
            b"archive",
        )
        self.assertEqual(len(self.process_calls), 1)
        command = self.process_calls[0][1]
        self.assertIn("workspace publish-sync", command)
        self.assertIn("--workspace-id ws-123", command)
        self.assertIn("--project-identity project-identity", command)
        self.assertIn("--generation-id gen-123", command)
        self.assertIn("--expected-index-generation 7", command)
        self.assertNotIn("--project-dir", command)
        self.assertNotIn("--path", command)
        self.assertEqual(self.process_calls[0][2], b"archive")

        self.transport.reconcile_sync(
            "remote-a", "ws-123", "project-identity", "gen-123",
            "a" * 64, 2, 12, 7,
        )
        self.assertEqual(len(self.calls), 1)
        reconcile = self.calls[0][1]
        self.assertIn("workspace reconcile-sync", reconcile)
        self.assertNotIn("--project-dir", reconcile)
        self.assertNotIn("--path", reconcile)

    def test_sync_publication_accepts_bytes_process_stdout(self):
        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True, "capabilities": ["workspace.index"]},
            ssh_run=lambda *args, **kwargs: _Result(),
            ssh_process=lambda remote, command, input_data, timeout: _Result(stdout=b'{"ok":true,"status":"accepted"}'),
            remote_sb_path=lambda _remote: "/opt/sandbox/sb",
        )
        result = transport.publish_sync(
            "remote-a", "ws-123", "project-identity", "gen-123",
            "a" * 64, "b" * 64, 2, 12, 7,
            b"archive",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "accepted")

    def test_reset_and_destroy_require_confirmation_and_workspace_id(self):
        for method in (self.transport.reset, self.transport.destroy):
            with self.subTest(method=method.__name__):
                with self.assertRaisesRegex(RemoteWorkspaceError, "confirmation_required") as caught:
                    method("remote-a", "ws-123")
                self.assertEqual(caught.exception.code, "confirmation_required")
                self.calls.clear()
                method("remote-a", "ws-123", confirm=True)
                command = self._command()
                self.assertIn("--workspace-id ws-123", command)
                self.assertIn("--confirm", command)
                self.assertNotIn("--workspace-path", command)
                self.calls.clear()

    def test_strict_top_level_envelope_rejects_logs_and_nested_shape(self):
        def noisy(_remote, _command, *, timeout):
            return _Result(stdout='warning\n{"ok":true,"workspaces":[]}')

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=noisy,
        )
        with self.assertRaisesRegex(RemoteWorkspaceError, "workspace_protocol_invalid"):
            transport.list("remote-a")

        def nested(_remote, _command, *, timeout):
            return _Result(stdout='{"ok":true,"data":{"workspaces":[]}}')

        # A top-level data field is still an envelope: the transport must not
        # unwrap it or reinterpret the payload as a list response.
        nested_result = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=nested,
        ).list("remote-a")
        self.assertEqual(nested_result["data"], {"workspaces": []})

        def numeric_ok(_remote, _command, *, timeout):
            return _Result(stdout='{"ok":1,"workspaces":[]}')

        with self.assertRaisesRegex(RemoteWorkspaceError, "workspace_protocol_invalid"):
            RemoteWorkspaceTransport(
                remote_lookup=lambda _name: {"provisioned": True}, ssh_run=numeric_ok,
            ).list("remote-a")

    def test_status_accepts_nullable_error_field(self):
        def status(_remote, _command, *, timeout):
            return _Result(stdout=json.dumps({
                "ok": True,
                "workspace_id": "ws-123",
                "status": "ready",
                "error": None,
            }))

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=status,
        )
        self.assertEqual(
            transport.status("remote-a", "ws-123")["workspace_id"],
            "ws-123",
        )

    def test_successful_envelope_rejects_populated_error_field(self):
        def malformed(_remote, _command, *, timeout):
            return _Result(stdout=json.dumps({
                "ok": True,
                "status": "ready",
                "error": {"code": "wrong_state"},
            }))

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=malformed,
        )
        with self.assertRaisesRegex(RemoteWorkspaceError, "workspace_protocol_invalid"):
            transport.status("remote-a", "ws-123")

    def test_remote_error_preserves_stable_code_without_secret(self):
        def failed(_remote, _command, *, timeout):
            return _Result(
                returncode=65,
                stdout=json.dumps({
                    "ok": False,
                    "code": "workspace_migration_plan_stale",
                    "message": "token=secret-value inventory changed",
                }),
            )

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=failed,
        )
        with self.assertRaises(RemoteWorkspaceError) as caught:
            transport.migration_apply("remote-a", "plan-123", confirm=True)
        self.assertEqual(caught.exception.code, "workspace_migration_plan_stale")
        self.assertNotIn("secret-value", str(caught.exception))

    def test_runner_stderr_structured_failure_and_provider_tokens_are_redacted(self):
        def failed(_remote, _command, *, timeout):
            return _Result(
                returncode=65,
                stdout="",
                stderr=json.dumps({
                    "ok": False,
                    "code": "workspace_index_unavailable",
                    "message": "github_pat_" + "a" * 30,
                }),
            )

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=failed,
        )
        with self.assertRaises(RemoteWorkspaceError) as caught:
            transport.list("remote-a")
        self.assertEqual(caught.exception.code, "workspace_index_unavailable")
        self.assertNotIn("github_pat_", str(caught.exception))

    def test_runner_plain_stderr_provider_token_is_redacted(self):
        def failed(_remote, _command, *, timeout):
            return _Result(
                returncode=65,
                stdout="",
                stderr="controller failed github_pat_" + "b" * 30,
            )

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_run=failed,
        )
        with self.assertRaises(RemoteWorkspaceError) as caught:
            transport.list("remote-a")
        self.assertNotIn("github_pat_", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_runner_redacts_generic_provider_assignments_and_key_material(self):
        canaries = (
            "CLOUDFLARE_API_TOKEN=cloudflare-canary-value",
            "AWS_SESSION_TOKEN=FwoGZXIvYXdz" + "A" * 28,
            "-----BEGIN PRIVATE KEY-----",
        )
        for canary in canaries:
            with self.subTest(canary=canary.split("=")[0]):
                def failed(_remote, _command, *, timeout, value=canary):
                    return _Result(returncode=65, stdout="", stderr=value)

                transport = RemoteWorkspaceTransport(
                    remote_lookup=lambda _name: {"provisioned": True},
                    ssh_run=failed,
                )
                with self.assertRaises(RemoteWorkspaceError) as caught:
                    transport.list("remote-a")
                self.assertNotIn(canary, str(caught.exception))
                self.assertIn("[REDACTED]", str(caught.exception))

    def test_create_uses_deployment_and_registration_seams_without_cli_path(self):
        seen = {}

        def deploy(request: WorkspaceCreateRequest):
            seen["deploy"] = request
            return {"checkout_locator": "remote-locator", "commit": "abc123"}

        def register(request: WorkspaceCreateRequest, prepared):
            seen["register"] = (request, prepared)
            return {"ok": True, "workspace": {"workspace_id": "ws-123"}}

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True},
            ssh_run=lambda *_args, **_kwargs: self.fail("registration should not invoke CLI"),
            deploy=deploy,
            register=register,
        )
        result = transport.create(
            "remote-a", "project-identity", "unit",
            checkout_locator="/local/project", registration={"source": "exact"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(seen["deploy"].checkout_locator, "/local/project")
        self.assertEqual(seen["register"][1]["commit"], "abc123")

    def test_create_uses_only_path_free_deployment_receipt_when_register_seam_is_absent(self):
        def deploy(_request):
            return {
                "checkout_locator": "/local/project",
                "deployment_receipt": "receipt-123",
            }

        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": True},
            ssh_run=self.transport.ssh_run,
            deploy=deploy,
        )
        transport.create(
            "remote-a", "project-identity", "unit", checkout_locator="/local/project",
        )
        command = self._command()
        self.assertIn("--deployment-receipt receipt-123", command)
        self.assertNotIn("/local/project", command)
        self.assertNotIn("--project-dir", command)

    def test_create_rejects_path_like_deployment_receipt(self):
        with self.assertRaisesRegex(RemoteWorkspaceError, "workspace_receipt_invalid"):
            self.transport.create(
                "remote-a", "project-identity", "unit", deployment_receipt="/tmp/receipt",
            )
        self.assertEqual(self.calls, [])

    def test_direct_deployment_receipt_is_sent_without_any_checkout_path(self):
        self.transport.create(
            "remote-a", "project-identity", "unit", deployment_receipt="receipt-123",
        )
        command = self._command()
        self.assertIn("--deployment-receipt receipt-123", command)
        self.assertNotIn("--project-dir", command)

    def test_create_rejects_checkout_path_without_registration_seam(self):
        with self.assertRaisesRegex(RemoteWorkspaceError, "workspace_registration_unavailable"):
            self.transport.create(
                "remote-a", "project-identity", "unit", checkout_locator="/local/project",
            )
        self.assertEqual(self.calls, [])

    def test_metadata_only_create_is_path_free(self):
        self.transport.create("remote-a", "project-identity", "unit")
        command = self._command()
        self.assertIn("workspace create", command)
        self.assertIn("--workspace-label unit", command)
        self.assertNotIn("--project-dir", command)
        self.assertNotIn("/local", command)

    def test_identifiers_reject_path_like_values_before_runner(self):
        with self.assertRaisesRegex(RemoteWorkspaceError, "workspace_identity_invalid"):
            self.transport.status("remote-a", "/tmp/workspace")
        self.assertEqual(self.calls, [])

    def test_unprovisioned_remote_is_stable_failure(self):
        transport = RemoteWorkspaceTransport(
            remote_lookup=lambda _name: {"provisioned": False},
            ssh_run=lambda *_args, **_kwargs: self.fail("runner must not be called"),
        )
        with self.assertRaises(RemoteWorkspaceError) as caught:
            transport.list("remote-a")
        self.assertEqual(caught.exception.code, "workspace_remote_unavailable")


if __name__ == "__main__":
    unittest.main()

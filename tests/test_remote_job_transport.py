import hashlib
import json
import unittest
from types import SimpleNamespace
from pathlib import Path

from sandbox.core import _remote
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.resources.network_capacity import evaluate_network_capacity
from sandbox.transports.remote_jobs import (
    RemoteJobAdmissionError,
    RemoteJobTransport,
    RemoteJobTransportError,
)


class RemoteJobTransportTests(unittest.TestCase):
    @staticmethod
    def _blocked_admission(remote_name="vps"):
        decision = evaluate_network_capacity({"status": "partial"}, remote_name=remote_name)
        # Exercise the evaluator's real exception source while ensuring the
        # transport boundary is tested against untrusted, noisy decision data.
        secret = "admission-fixture-private-value"
        decision["capacity"]["subnet"] = secret
        decision["evidence"]["ssh_output"] = secret
        decision["recovery"]["next_command"] = secret
        decision["target"]["path"] = secret
        return _remote.NetworkCapacityAdmissionError(decision), decision, secret

    def test_network_capacity_admission_blocks_submit_before_downstream_calls(self):
        admission, _decision, secret = self._blocked_admission()
        calls = []

        def deploy(*_args):
            calls.append("deploy")
            raise admission

        transport = RemoteJobTransport(
            deploy=deploy,
            ssh_run=lambda *_args, **_kwargs: calls.append("ssh"),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        submission = JobSubmission(
            "test", "/project", "project:remote", "remote", "unit",
            ("echo", "ok"), 60, SourceIdentity("caller"), remote_name="vps",
        )

        with self.assertRaises(RemoteJobAdmissionError) as raised:
            transport.submit(submission)

        error = raised.exception
        payload = error.to_payload()
        self.assertEqual(calls, ["deploy"])
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["code"], "docker_network_capacity_unavailable")
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["side_effects"], {
            "staging_started": False,
            "network_allocation_started": False,
        })
        self.assertEqual(payload["target"], {"kind": "remote", "remote": "vps"})
        self.assertNotIn("next_command", payload["recovery"])
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, repr(error))
        self.assertNotIn(secret, serialized)

    def test_network_capacity_admission_blocks_submit_many_before_matrix_calls(self):
        admission, _decision, secret = self._blocked_admission()
        calls = []

        def deploy(*_args):
            calls.append("deploy")
            raise admission

        transport = RemoteJobTransport(
            deploy=deploy,
            ssh_run=lambda *_args, **_kwargs: calls.append("ssh"),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        source = SourceIdentity("caller")
        submissions = [
            JobSubmission("test", "/project", "project:remote", "remote", label,
                          ("echo", label), 60, source, remote_name="vps",
                          workspace_mode="isolated")
            for label in ("one", "two")
        ]

        with self.assertRaises(RemoteJobAdmissionError) as raised:
            transport.submit_many(submissions)

        error = raised.exception
        payload = error.to_payload()
        self.assertEqual(calls, ["deploy"])
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["code"], "docker_network_capacity_unavailable")
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["side_effects"]["staging_started"], False)
        self.assertEqual(payload["side_effects"]["network_allocation_started"], False)
        self.assertNotIn("next_command", payload["recovery"])
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, repr(error))
        self.assertNotIn(secret, json.dumps(payload, sort_keys=True))

    def test_unrelated_deploy_value_error_propagates_unchanged(self):
        marker = ValueError("deploy-private-value")

        def deploy(*_args):
            raise marker

        transport = RemoteJobTransport(
            deploy=deploy,
            ssh_run=lambda *_args, **_kwargs: self.fail("SSH must not run"),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        submission = JobSubmission(
            "test", "/project", "project:remote", "remote", "unit",
            ("echo", "ok"), 60, SourceIdentity("caller"), remote_name="vps",
        )
        with self.assertRaises(ValueError) as raised:
            transport.submit(submission)
        self.assertIs(raised.exception, marker)

    def test_unrelated_deploy_value_error_propagates_unchanged_for_submit_many(self):
        marker = ValueError("matrix-deploy-private-value")

        def deploy(*_args):
            raise marker

        transport = RemoteJobTransport(
            deploy=deploy,
            ssh_run=lambda *_args, **_kwargs: self.fail("SSH must not run"),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        source = SourceIdentity("caller")
        submissions = [
            JobSubmission("test", "/project", "project:remote", "remote", label,
                          ("echo", label), 60, source, remote_name="vps",
                          workspace_mode="isolated")
            for label in ("one", "two")
        ]
        with self.assertRaises(ValueError) as raised:
            transport.submit_many(submissions)
        self.assertIs(raised.exception, marker)

    def test_admission_payload_whitelists_codes_and_fails_closed_for_unknown_codes(self):
        exhausted = evaluate_network_capacity({
            "status": "complete",
            "pools": [{
                "pool_id": "pool-" + "a" * 16,
                "capacity_subnets": 1,
                "allocated_subnets": 1,
                "usable_subnets": 0,
            }],
            "totals": {"total_subnets": 1, "allocated_subnets": 1, "usable_subnets": 0},
            "ownership": {
                "sandbox_allocated_subnets": 1,
                "foreign_allocated_subnets": 0,
                "unattributed_allocated_subnets": 0,
            },
        }, remote_name="vps")
        conflict = evaluate_network_capacity({
            "status": "partial",
            "collisions": [{"pool_id": "pool-" + "b" * 16}],
        }, remote_name="vps")
        secret = "payload-fixture-private-value"

        def noisy(decision, code):
            return {
                **decision,
                "code": code,
                "capacity": {**decision["capacity"], "subnet": secret},
                "evidence": {**decision["evidence"], "ssh_output": secret},
                "recovery": {**decision["recovery"], "next_command": secret},
                "target": {**decision["target"], "path": secret},
            }

        expected_keys = {
            "ok", "status", "code", "error", "resource_class", "resource_kind",
            "owner_classes", "target", "capacity", "evidence", "recovery",
            "retryable", "side_effects",
        }
        for expected_code, decision in (
            ("docker_network_subnet_exhausted", exhausted),
            ("network_allocation_conflict", conflict),
        ):
            with self.subTest(code=expected_code):
                error = RemoteJobAdmissionError(noisy(decision, expected_code))
                payload = error.to_payload()
                self.assertEqual(payload["code"], expected_code)
                self.assertEqual(set(payload), expected_keys)
                self.assertEqual(set(payload["target"]), {"kind", "remote"})
                self.assertNotIn("subnet", payload["capacity"])
                self.assertNotIn("ssh_output", payload["evidence"])
                self.assertNotIn("next_command", payload["recovery"])
                self.assertNotIn("path", payload["target"])
                self.assertNotIn(secret, str(error))
                self.assertNotIn(secret, repr(error))
                self.assertNotIn(secret, json.dumps(payload, sort_keys=True))

        for malformed_code in (None, {"raw": secret}, "unknown-capacity-code"):
            with self.subTest(code=malformed_code):
                error = RemoteJobAdmissionError(noisy(exhausted, malformed_code))
                payload = error.to_payload()
                self.assertEqual(payload["code"], "docker_network_capacity_unavailable")
                self.assertNotIn(secret, json.dumps(payload, sort_keys=True))

    def test_missing_execution_capability_rejects_before_deployment(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda *_args: calls.append("deploy"),
            ssh_run=lambda *_args, **_kwargs: calls.append("ssh"),
            remote_lookup=lambda _name: {"provisioned": True, "capabilities": ["status"]},
        )
        submission = JobSubmission("test", "/p", "p", "remote", "w", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r")
        with self.assertRaisesRegex(Exception, "does not support job.exec"):
            transport.submit(submission)
        self.assertEqual(calls, [])

    def test_deployment_precedes_bounded_json_acceptance(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: calls.append(("deploy", root)) or {"target_path": "/srv/p", "commit": "abc", "dirty": True, "dirty_digest": "d", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: calls.append(("ssh", command, timeout)) or SimpleNamespace(returncode=0, stdout='{"ok":true,"status":"accepted","job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
        )
        result = transport.submit(JobSubmission("test", "/p", "p", "remote", "w", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r", request_id="retry"))
        self.assertEqual(calls[0], ("deploy", "/p"))
        self.assertIn("workspace-", calls[1][1])
        self.assertIn("job-start", calls[2][1])
        self.assertIn("--request-id retry", calls[2][1])
        self.assertIn("workspace-", calls[2][1])
        self.assertEqual(result["source"]["identity"], "sha256:id")
        self.assertEqual(result["target"], {"kind": "remote", "remote": "r", "workspace": "w"})

    def test_343d1a5a_silent_job_start_acceptance_is_rejected(self):
        transport = RemoteJobTransport(
            deploy=lambda *_: {"target_path": "/srv/p", "identity": "sha256:deployed"},
            ssh_run=lambda _remote, command, timeout: SimpleNamespace(
                returncode=0, stdout='{"ok":true}\n'),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        submission = JobSubmission("test", "/p", "p", "remote", "w", ("echo", "ok"), 60,
            SourceIdentity("caller"), remote_name="r")
        with self.assertRaisesRegex(RemoteJobTransportError, "acceptance|job id|status"):
            transport.submit(submission)

    def test_3da039b4_empty_job_id_is_rejected_even_when_marked_accepted(self):
        transport = RemoteJobTransport(
            deploy=lambda *_: {"target_path": "/srv/p", "identity": "sha256:deployed"},
            ssh_run=lambda _remote, command, timeout: SimpleNamespace(
                returncode=0, stdout='{"ok":true,"status":"accepted","job_id":""}\n'),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        submission = JobSubmission("test", "/p", "p", "remote", "w", ("echo", "ok"), 60,
            SourceIdentity("caller"), remote_name="r")
        with self.assertRaisesRegex(RemoteJobTransportError, "job id"):
            transport.submit(submission)

    def test_b027d2ab_checkout_identity_and_resolved_cwd_reach_detached_submission(self):
        commands = []

        def run(_remote, command, timeout):
            commands.append(command)
            if "job-start" in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout='{"ok":true,"status":"accepted","job_id":"abc"}\n',
                )
            return SimpleNamespace(returncode=0, stdout="")

        transport = RemoteJobTransport(
            deploy=lambda *_: {
                "target_path": "/srv/proof-checkout",
                "identity": "sha256:deployed",
                "commit": "deployed-commit",
                "dirty": True,
                "dirty_digest": "deployed-dirty",
            },
            ssh_run=run,
            remote_lookup=lambda _name: {"provisioned": True},
        )
        submission = JobSubmission(
            "test", "/caller-checkout", "project:canonical", "remote", "proof",
            ("python", "-m", "tests"), 60,
            SourceIdentity("caller", "caller-commit", "caller-dirty"),
            remote_name="r", cwd_relative="tests/fixtures", execution_profile="ci",
            stall_seconds=45, cancel_on_stall=True,
        )
        result = transport.submit(submission)
        command = commands[-1]
        self.assertIn("--project-dir /srv/proof-checkout-workspace-", command)
        self.assertIn("--project-identity project:canonical", command)
        self.assertIn("--cwd-relative tests/fixtures", command)
        self.assertIn("--source-identity sha256:deployed", command)
        self.assertIn("--source-commit deployed-commit", command)
        self.assertIn("--source-dirty-digest deployed-dirty", command)
        self.assertIn("--profile ci", command)
        self.assertIn("--stall-seconds 45", command)
        self.assertIn("--cancel-on-stall", command)
        self.assertNotIn("caller-commit", command)
        self.assertEqual(result["source"]["identity"], "sha256:deployed")

    def test_status_list_cancel_and_metrics_use_bounded_json_control(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda remote, command, timeout: commands.append((command, timeout)) or SimpleNamespace(returncode=0, stdout='{"ok":true,"jobs":[]}\n'),
            remote_lookup=lambda name: {"provisioned": True},
        )
        self.assertEqual(transport.status("r", "abc")["ok"], True)
        self.assertEqual(transport.list("r")["jobs"], [])
        transport.cancel("r", "abc", force=True)
        transport.metrics("r", "abc")
        self.assertTrue(all("--json" in command for command, _ in commands))
        self.assertIn("job-cancel abc --force", commands[2][0])

    def test_remote_list_filters_by_canonical_project_identity_without_forwarding_local_path(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"jobs":[]}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        identity = "a" * 64

        transport.list("r", project_identity=identity, workspace="unit")

        self.assertEqual(commands, [
            f"/srv/sandbox/sb-src/sb job-list --limit 50 --project-identity {identity} "
            "--workspace unit --json",
        ])
        self.assertNotIn("--project-dir", commands[0])

    def test_control_uses_the_staged_remote_cli_not_path_lookup(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"jobs":[]}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        transport.list("r")
        self.assertTrue(commands[0].startswith("/srv/sandbox/sb-src/sb job-list"))

    def test_79d775b4_job_list_uses_canonical_identity_not_local_parser_flags(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda _remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"jobs":[]}\n'),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        transport.list("r", project_dir="/tmp/project", workspace="proof")
        expected = hashlib.sha256(
            str(Path("/tmp/project").expanduser().resolve()).encode()).hexdigest()
        self.assertIn(f"--project-identity {expected}", commands[0])
        self.assertNotIn(" --local ", commands[0])

    def test_6bc4c6d5_job_list_rejects_a_nested_data_envelope(self):
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda _remote, _command, **_kwargs: SimpleNamespace(
                returncode=0, stdout='{"ok":true,"data":{"jobs":[]}}\n'),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        with self.assertRaisesRegex(RemoteJobTransportError, "top-level jobs"):
            transport.list("r")

    def test_reconcile_uses_bounded_remote_control(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda remote, command, timeout: commands.append((command, timeout)) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"interrupted":[]}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        result = transport.control("r", ["job-reconcile", "--limit", "200"])
        self.assertTrue(result["ok"])
        self.assertEqual(commands, [("/srv/sandbox/sb-src/sb job-reconcile --limit 200 --json", 25)])

    def test_all_remote_control_operations_use_the_staged_cli_path(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"jobs":[]}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        transport.cancel("r", "abc", force=True)
        transport.metrics("r", "abc")
        transport.artifacts("r", "abc")
        transport.artifact_get("r", "abc", "artifact")
        transport.retry("r", "abc", request_id="retry")
        transport.cleanup("r", "abc")
        self.assertEqual(len(commands), 6)
        self.assertTrue(all(command.startswith("/srv/sandbox/sb-src/sb job-")
                            for command in commands))
        self.assertIn("job-retry abc --request-id retry --json", commands[4])

    def test_workspace_copy_path_is_slug_safe_for_remote_project_resolution(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: {"target_path": "/srv/sandbox/project", "commit": "abc", "dirty": False,
                                         "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"status":"accepted","job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
        )
        transport.submit(JobSubmission("test", "/p", "p", "remote", "workspace", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r"))
        self.assertIn("project-workspace-", commands[0])
        self.assertNotIn("project.workspace-", commands[0])

    def test_workspace_prepare_has_a_scoped_root_owned_file_recovery(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: {"target_path": "/srv/project", "commit": "abc", "dirty": False,
                                         "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"status":"accepted","job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
        )
        transport.submit(JobSubmission("test", "/p", "p", "remote", "workspace", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r"))
        prepare = commands[0]
        self.assertIn("docker run --rm --user 0:0", prepare)
        self.assertIn("/srv/project-workspace-", prepare)
        self.assertIn("find /workspace -mindepth 2 -maxdepth 2", prepare)
        self.assertIn('find "$item" -mindepth 1 -maxdepth 1', prepare)
        self.assertIn('rmdir -- "$item"', prepare)
        self.assertNotIn("rm -rf /srv/project-workspace-", prepare)

    def test_workspace_prepare_checks_for_root_owned_contents_after_unprivileged_cleanup(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout=""),
            remote_lookup=lambda name: {"provisioned": True},
        )
        transport._prepare_workspace({}, "/srv/project", "workspace")
        prepare = commands[0]
        self.assertIn("-print -quit", prepare)
        self.assertIn('if [ -n "$(find "$workspace" -mindepth 2', prepare)
        self.assertIn("remote workspace cleanup left contents", prepare)

    def test_workspace_prepare_retains_bounded_remote_error_detail(self):
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1, stdout="copy failed\n", stderr="permission denied\n"),
            remote_lookup=lambda name: {"provisioned": True},
        )
        with self.assertRaisesRegex(Exception, "(?s)permission denied.*copy failed"):
            transport._prepare_workspace({}, "/srv/project", "workspace")

    def test_remote_runtime_exec_ensures_and_executes_in_the_deployed_instance(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: {"target_path": "/srv/project", "commit": "abc", "dirty": False,
                                         "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: calls.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"status":"accepted","job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        transport.submit(JobSubmission("runtime-exec", "/p", "p", "remote", "workspace", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r"))
        controller = calls[-1]
        self.assertIn("/srv/sandbox/sb-src/sb ensure --local --json", controller)
        self.assertIn("/srv/sandbox/sb-src/sb exec --local --in-instance --timeout 60 -- npm test", controller)

    def test_remote_nested_cli_uses_the_staged_path(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: {"target_path": "/srv/project", "commit": "abc", "dirty": False,
                                         "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: calls.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"status":"accepted","job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        transport.submit(JobSubmission("test", "/p", "p", "remote", "workspace",
            ("sb", "test", "--local", "--project-dir", ".", "integration"), 60,
            SourceIdentity("ignored"), remote_name="r"))
        self.assertIn("/srv/sandbox/sb-src/sb ensure --local --json", calls[-1])
        self.assertIn("/srv/sandbox/sb-src/sb test --local --project-dir . integration", calls[-1])

    def test_status_reports_unreachable_without_inventing_terminal_success(self):
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
            remote_lookup=lambda name: {"provisioned": True},
        )
        result = transport.status("r", "a" * 32)
        self.assertFalse(result["ok"])
        self.assertEqual(result["health"], "unreachable")
        self.assertEqual(result["lifecycle"], "unknown")

    def test_output_controls_and_matrix_deploy_once(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: calls.append(("deploy", root)) or {"target_path": "/srv/p", "commit": "abc", "dirty": False, "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: calls.append(("ssh", command)) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"kind":"matrix","parent_job_id":"parent","children":[{"job_id":"a"},{"job_id":"b"}]}\n'),
            remote_lookup=lambda name: {"provisioned": True},
        )
        source = SourceIdentity("ignored")
        children = [JobSubmission("test", "/p", "p", "remote", label, ("npm", "test"), 60, source,
            remote_name="r", workspace_mode="isolated") for label in ("a", "b")]
        result = transport.submit_many(children)
        self.assertEqual(result["parent_job_id"], "parent")
        self.assertEqual(len(result["children"]), 2)
        self.assertEqual([item[0] for item in calls].count("deploy"), 1)
        workspace_commands = [item[1] for item in calls if item[0] == "ssh" and "workspace-" in item[1]]
        self.assertEqual(len(workspace_commands), 2)
        self.assertNotEqual(workspace_commands[0].split("workspace-")[1].split()[0],
                            workspace_commands[1].split("workspace-")[1].split()[0])
        calls.clear()
        transport.read_output("r", "abc", stream="stderr", cursor="cursor", tail_bytes=10,
                              offset=None, lines=None, since=None, max_bytes=12, wait_seconds=2, encoding="base64")
        command = calls[-1][1]
        self.assertIn("--stream stderr", command)
        self.assertIn("--tail-bytes 10", command)
        self.assertIn("--wait-seconds 2", command)
        self.assertIn("--encoding base64", command)

    def test_matrix_rejection_reports_structured_remote_reason(self):
        transport = RemoteJobTransport(
            deploy=lambda _remote, _root: {"target_path": "/srv/p", "commit": "abc", "dirty": False,
                                           "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=2, stdout='{"ok":false,"error":{"message":"matrix plan rejected"}}\n'),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        source = SourceIdentity("ignored")
        child = JobSubmission("test", "/p", "p", "remote", "a", ("npm", "test"), 60, source,
                              remote_name="r", workspace_mode="isolated")
        with self.assertRaisesRegex(RemoteJobTransportError, "matrix plan rejected"):
            transport.submit_many([child])

    def test_matrix_rejection_reports_bounded_stderr_when_json_is_absent(self):
        transport = RemoteJobTransport(
            deploy=lambda _remote, _root: {"target_path": "/srv/p", "commit": "abc", "dirty": False,
                                           "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="matrix parser failed"),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        source = SourceIdentity("ignored")
        child = JobSubmission("test", "/p", "p", "remote", "a", ("npm", "test"), 60, source,
                              remote_name="r", workspace_mode="isolated")
        with self.assertRaisesRegex(RemoteJobTransportError, "matrix parser failed"):
            transport.submit_many([child])

    def test_matrix_rejection_redacts_controller_credentials(self):
        transport = RemoteJobTransport(
            deploy=lambda _remote, _root: {"target_path": "/srv/p", "commit": "abc", "dirty": False,
                                           "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=2, stdout="", stderr="Bearer controller-token token=second-secret"),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        source = SourceIdentity("ignored")
        child = JobSubmission("test", "/p", "p", "remote", "a", ("npm", "test"), 60, source,
                              remote_name="r", workspace_mode="isolated")
        with self.assertRaises(RemoteJobTransportError) as raised:
            transport.submit_many([child])
        self.assertIn("[REDACTED]", str(raised.exception))
        self.assertNotIn("controller-token", str(raised.exception))
        self.assertNotIn("second-secret", str(raised.exception))

    def test_runner_exception_is_replaced_without_raw_cause_or_context(self):
        fixture = "runner-private-value"

        def fail(*_args, **_kwargs):
            raise RuntimeError(fixture)

        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=fail,
            remote_lookup=lambda _name: {"provisioned": True},
        )
        with self.assertRaises(RemoteJobTransportError) as raised:
            transport.control("r", ["job-status", "a" * 32])
        public = raised.exception
        self.assertEqual(str(public), "remote job transport runner failed")
        self.assertIsNone(public.__cause__)
        self.assertIsNone(public.__context__)
        self.assertFalse(fixture in repr(public))

import unittest
from types import SimpleNamespace

from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.transports.remote_jobs import RemoteJobTransport


class RemoteJobTransportTests(unittest.TestCase):
    def test_deployment_precedes_bounded_json_acceptance(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: calls.append(("deploy", root)) or {"target_path": "/srv/p", "commit": "abc", "dirty": True, "dirty_digest": "d", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: calls.append(("ssh", command, timeout)) or SimpleNamespace(returncode=0, stdout='{"ok":true,"job_id":"abc"}\n'),
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

    def test_workspace_copy_path_is_slug_safe_for_remote_project_resolution(self):
        commands = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: {"target_path": "/srv/sandbox/project", "commit": "abc", "dirty": False,
                                         "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: commands.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
        )
        transport.submit(JobSubmission("test", "/p", "p", "remote", "workspace", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r"))
        self.assertIn("project-workspace-", commands[0])
        self.assertNotIn("project.workspace-", commands[0])

    def test_remote_runtime_exec_ensures_and_executes_in_the_deployed_instance(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda remote, root: {"target_path": "/srv/project", "commit": "abc", "dirty": False,
                                         "dirty_digest": "", "identity": "sha256:id"},
            ssh_run=lambda remote, command, timeout: calls.append(command) or SimpleNamespace(
                returncode=0, stdout='{"ok":true,"job_id":"abc"}\n'),
            remote_lookup=lambda name: {"provisioned": True},
            remote_sb_path=lambda remote: "/srv/sandbox/sb-src/sb",
        )
        transport.submit(JobSubmission("runtime-exec", "/p", "p", "remote", "workspace", ("npm", "test"), 60,
            SourceIdentity("ignored"), remote_name="r"))
        controller = calls[-1]
        self.assertIn("/srv/sandbox/sb-src/sb ensure --local --json", controller)
        self.assertIn("/srv/sandbox/sb-src/sb exec --local -- npm test", controller)

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
                              max_bytes=12, wait_seconds=2, encoding="base64")
        command = calls[-1][1]
        self.assertIn("--stream stderr", command)
        self.assertIn("--tail-bytes 10", command)
        self.assertIn("--wait-seconds 2", command)
        self.assertIn("--encoding base64", command)

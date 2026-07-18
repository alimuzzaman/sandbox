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
        self.assertIn("job-start", calls[1][1])
        self.assertIn("--request-id retry", calls[1][1])
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
        calls.clear()
        transport.read_output("r", "abc", stream="stderr", cursor="cursor", tail_bytes=10,
                              max_bytes=12, wait_seconds=2)
        command = calls[-1][1]
        self.assertIn("--stream stderr", command)
        self.assertIn("--tail-bytes 10", command)
        self.assertIn("--wait-seconds 2", command)

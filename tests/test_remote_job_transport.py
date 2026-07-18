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

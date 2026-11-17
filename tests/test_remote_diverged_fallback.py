"""A remote job deploy from a rewritten local branch takes the immutable source ref.

Real temporary Git repositories stand in for the remote deploy repo; only the
SSH URL, capacity admission and target reset are synthetic.
"""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sandbox.core import _remote as sr
from sandbox.hosting import server_cli_dispatch as dispatch
from sandbox.transports.remote_jobs import RemoteJobTransport
from tests.subprocess_support import run_test_process


class DivergedFallbackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.client = self.root / "client"
        self.client.mkdir()
        self.git(self.client, "init", "-q", "-b", "topic")
        self.git(self.client, "config", "user.name", "Synthetic Fixture")
        self.git(self.client, "config", "user.email", "fixture@example.invalid")
        (self.client / "a.txt").write_text("one\n")
        self.git(self.client, "add", ".")
        self.git(self.client, "commit", "-qm", "one")
        self.target = self.root / "deploy.git"
        self.git(self.root, "init", "-q", "--bare", str(self.target))
        self.git(self.client, "push", "-q", str(self.target), "topic:refs/heads/topic")
        self.published = self.git(self.client, "rev-parse", "HEAD")
        # Rewrite local history so the remote branch can no longer fast-forward.
        (self.client / "a.txt").write_text("two\n")
        self.git(self.client, "commit", "-q", "--amend", "-am", "rewritten")
        self.head = self.git(self.client, "rev-parse", "HEAD")
        patches = [
            patch.object(sr, "git_ssh_url", lambda _remote, path: path),
            patch.object(sr, "git_ssh_command", lambda *_a, **_k: "ssh"),
            patch.object(sr, "_ensure_ssh_control_dir", lambda: self.root),
            patch.object(sr, "remote_network_capacity_admission",
                         lambda *_a, **_k: {"ok": True}),
            patch.object(sr, "ensure_deploy_repo", lambda *_a, **_k: str(self.target)),
            patch.object(sr, "update_target_to", self._update_target),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _update_target(self, _remote, _target, sha, **kwargs):
        kwargs["content_receipt"]["content_digest"] = "sha256:" + sha
        return []

    def git(self, root, *args):
        return run_test_process(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
            timeout=10, check=True, capture_output=True, text=True).stdout.strip()

    def ref(self, name):
        """The ref's commit, or "" when the remote has no such ref."""
        return run_test_process(["git", "-C", str(self.target), "rev-parse", "--verify", "-q", name],
            timeout=10, check=False, capture_output=True, text=True).stdout.strip()

    def test_strict_deploy_still_refuses_a_diverged_branch(self):
        with self.assertRaises(sr.RemoteBranchDiverged):
            sr.deploy_exact_working_tree({"ssh": "fixture"}, self.client, remote_name="fixture")
        self.assertEqual(self.ref("refs/heads/topic"), self.published)

    def test_job_deploy_publishes_the_exact_commit_to_the_immutable_ref(self):
        deployed = sr.deploy_exact_working_tree(
            {"ssh": "fixture"}, self.client, remote_name="fixture", diverged_fallback=True)
        self.assertEqual(deployed["commit"], self.head)
        self.assertEqual(self.ref("refs/heads/sandbox-source-" + self.head), self.head)
        # The named branch on the remote is untouched: no force push.
        self.assertEqual(self.ref("refs/heads/topic"), self.published)

    def test_fast_forward_branch_is_still_pushed_by_name(self):
        self.git(self.client, "reset", "-q", "--hard", self.published)
        (self.client / "a.txt").write_text("three\n")
        self.git(self.client, "commit", "-qam", "three")
        head = self.git(self.client, "rev-parse", "HEAD")
        deployed = sr.deploy_exact_working_tree(
            {"ssh": "fixture"}, self.client, remote_name="fixture", diverged_fallback=True)
        self.assertEqual(deployed["commit"], head)
        self.assertEqual(self.ref("refs/heads/topic"), head)
        self.assertEqual(self.ref("refs/heads/sandbox-source-" + head), "")

    def test_remote_job_transport_opts_into_the_fallback(self):
        seen = {}

        def deploy(remote, root, *, push_timeout=None, diverged_fallback=False):
            seen["diverged_fallback"] = diverged_fallback
            return {"ok": True}

        def strict(remote, root):
            seen["strict"] = True
            return {"ok": True}

        RemoteJobTransport(deploy=deploy, ssh_run=None, remote_lookup=None)._deploy(
            {}, self.client)
        RemoteJobTransport(deploy=strict, ssh_run=None, remote_lookup=None)._deploy(
            {}, self.client)
        self.assertIs(seen["diverged_fallback"], True)
        self.assertIs(seen["strict"], True)


class DivergedDispatchTests(unittest.TestCase):
    def run_dispatch(self, **values):
        args = Namespace(action="retire-delivery", json=True, request_id=None, **values)
        out = io.StringIO()
        with patch.object(dispatch, "_dispatch_host_if_selected",
                          side_effect=self._submitting_then_diverged(args)), \
                redirect_stdout(out), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(args)
        return json.loads(out.getvalue())

    @staticmethod
    def _submitting_then_diverged(args):
        def run(_args):
            args._server_cli_request_id = "host-cli-" + "a" * 24
            args._server_cli_submission_started = True
            raise sr.RemoteBranchDiverged()
        return run

    def test_a_refused_source_push_is_reported_not_accepted(self):
        payload = self.run_dispatch()
        self.assertEqual(payload["code"], "remote_branch_diverged")
        self.assertEqual(payload["acceptance"], "not_accepted")
        self.assertTrue(payload["error"].startswith("Not submitted: remote deploy branch diverged"))

    def test_a_caller_request_id_keeps_unknown_acceptance(self):
        args = Namespace(action="retire-delivery", json=True, request_id="nested-replay")
        out = io.StringIO()
        with patch.object(dispatch, "_dispatch_host_if_selected",
                          side_effect=self._submitting_then_diverged(args)), \
                redirect_stdout(out), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(args)
        self.assertEqual(json.loads(out.getvalue())["acceptance"], "unknown")


if __name__ == "__main__":
    unittest.main()

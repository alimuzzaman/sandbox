import argparse
import unittest
from unittest.mock import patch

from sandbox.commands.sync import configure_parser


class SyncCliTests(unittest.TestCase):
    def test_parser_requires_explicit_remote_workspace_and_request(self):
        parser = argparse.ArgumentParser()
        configure_parser(parser)
        args = parser.parse_args([
            "once", "--project-dir", "/tmp/project", "--remote", "remote",
            "--workspace-id", "workspace", "--request-id", "request", "--json",
        ])
        self.assertEqual(args.sync_action, "once")
        self.assertTrue(args.json)

    def test_status_dispatch_uses_shared_service_and_redacted_json(self):
        from sandbox.commands import sync
        args = argparse.Namespace(
            sync_action="status", project_dir="/tmp/project", remote="remote",
            workspace_id="workspace", json=True,
        )
        with patch.object(sync, "build_sync_service") as build:
            build.return_value.status.return_value = {
                "ok": True, "status": "stopped", "secret": "must-not-be-added",
            }
            with patch("builtins.print") as output:
                sync.cmd_sync({}, args)
        self.assertIn('"status":"stopped"', output.call_args.args[0])
        self.assertNotIn("secret", output.call_args.args[0])

    def test_parser_exposes_checkpoint_participant_and_resolve_confirmation(self):
        parser = argparse.ArgumentParser()
        configure_parser(parser)
        once = parser.parse_args([
            "once", "--project-dir", "/tmp/project", "--remote", "remote",
            "--workspace-id", "workspace", "--request-id", "request",
            "--checkpoint", "--participant-id", "participant",
        ])
        resolve = parser.parse_args([
            "resolve", "--project-dir", "/tmp/project", "--remote", "remote",
            "--workspace-id", "workspace", "--resolution", "keep-local",
            "--confirm",
        ])
        self.assertTrue(once.checkpoint)
        self.assertEqual(once.participant_id, "participant")
        self.assertTrue(resolve.confirm)


if __name__ == "__main__":
    unittest.main()

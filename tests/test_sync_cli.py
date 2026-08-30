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

    def test_ownership_conflict_is_bounded_and_redacted(self):
        from sandbox.commands import sync
        from sandbox.sync.service import SyncServiceError

        args = argparse.Namespace(
            sync_action="once", project_dir="/private/competing/project",
            remote="remote", workspace_id="workspace", request_id="request",
            include=[], checkpoint=False, participant_id=None, json=True,
        )
        with patch.object(sync, "build_sync_service") as build:
            build.return_value.once.side_effect = SyncServiceError(
                "workspace owned by /private/other token=synthetic", "ownership_conflict",
            )
            with patch("builtins.print") as output, self.assertRaises(SystemExit):
                sync.cmd_sync({}, args)
        rendered = output.call_args.args[0]
        self.assertIn('"code":"ownership_conflict"', rendered)
        self.assertNotIn("/private", rendered)
        self.assertNotIn("synthetic", rendered)


if __name__ == "__main__":
    unittest.main()

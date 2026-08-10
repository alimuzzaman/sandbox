"""Local contract tests for Hermes state synchronization (spec 017)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.hermes import cmd_hermes
from sandbox.hermes.state import state_restore_command, state_sync_command


PATHS = {
    "sandbox_home": "/home/u/sandbox",
    "state": "/home/u/sandbox/runtime/hermes.json",
    "locks": "/home/u/sandbox/runtime/hermes-locks",
}


class TestHermesStateSync(unittest.TestCase):
    def test_restore_validates_revision_symlinks_secrets_and_rolls_back(self):
        command = state_restore_command(PATHS, "https://github.com/acme/state.git")
        self.assertIn("<state-validator>", command)
        self.assertIn("committed=0", command)
        self.assertIn("trap rollback EXIT", command)
        self.assertIn("else rm -rf \"$HOME/.hermes\"", command)
        self.assertIn("state-old", command)

    def test_sync_locks_owned_paths_and_emits_a_stable_manifest(self):
        command = state_sync_command(PATHS, "https://github.com/acme/state.git")
        self.assertIn("flock -w 30", command)
        self.assertIn("git add -- manifest.json hermes sandbox", command)
        self.assertIn('"schema_version":1,"revision"', command)
        self.assertIn("pushed:%s", command)
        self.assertIn("--no-dereference", command)
        self.assertNotIn("git add -A", command)

    def test_state_setup_keeps_the_legacy_repo_option_as_a_scoped_alias(self):
        args = SimpleNamespace(action="state", subaction="setup", state_repo=None,
                               repo="https://github.com/acme/state.git", remote="fixture", json=True)
        with patch("sandbox.commands.hermes.hermes.state_setup", return_value={
            "ok": True, "action": "state_setup", "status": "configured", "data": {}, "remote": "fixture",
        }) as setup:
            cmd_hermes(None, args)
        setup.assert_called_once_with("fixture", "https://github.com/acme/state.git")


if __name__ == "__main__":
    unittest.main()

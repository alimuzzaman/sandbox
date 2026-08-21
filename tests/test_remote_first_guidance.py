"""Regression checks for the remote-first CLI and MCP guidance surfaces."""

import unittest
from pathlib import Path

from sandbox.commands.runtime import _GUIDES


ROOT = Path(__file__).resolve().parent.parent


class RemoteFirstGuidanceTests(unittest.TestCase):
    def test_cli_catalog_recommends_configured_remote_and_durable_recovery(self):
        for kind in ("compose", "wordpress"):
            entries = {name: (command, purpose) for name, command, purpose in _GUIDES[kind]}
            self.assertIn("--local", entries["test"][0])
            self.assertIn("configured remote", entries["test"][1])
            self.assertIn("job-status", entries["jobs"][0])
            self.assertIn("retained output", entries["jobs"][1])
        exec_recipe = dict((name, command) for name, command, _ in _GUIDES["compose"])["exec"]
        self.assertIn("--workspace", exec_recipe)
        self.assertIn("--detach", exec_recipe)
        self.assertIn("--request-id", exec_recipe)

    def test_cli_skill_covers_deploy_deadlines_workspace_isolation_and_remote_mcp(self):
        content = (ROOT / "skills" / "sandbox-cli" / "SKILL.md").read_text()
        for phrase in ("--timeout", "exact local working tree", "isolated labels",
                       "co-located remote MCP", "--local"):
            self.assertIn(phrase, content)

    def test_mcp_baseline_instructions_cover_remote_durable_recovery(self):
        content = (ROOT / "mcp" / "wp-server" / "app.py").read_text()
        for phrase in ("co-located remote MCP", "durable job", "job_status",
                       "job_output", "explicit local target"):
            self.assertIn(phrase, content)
        context = (ROOT / "mcp" / "wp-server" / "tools" / "context.py").read_text()
        self.assertIn("co-located remote MCP", context)
        self.assertIn("durable status/output", context)

    def test_public_docs_state_the_docker_trust_boundary(self):
        required = (
            "trusted project, plugin, and agent-generated code",
            "share the host kernel and docker daemon",
            "hostile-code or multi-tenant security boundary",
            "per-instance deny-by-default egress policy",
        )
        for relative_path in ("README.md", "docs/remote-hosting.md"):
            content = " ".join(
                (ROOT / relative_path).read_text().lower().replace(">", " ").split()
            )
            with self.subTest(path=relative_path):
                for phrase in required:
                    self.assertIn(phrase, content)


if __name__ == "__main__":
    unittest.main()

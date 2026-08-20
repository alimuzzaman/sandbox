import json
import hashlib
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.config.compose import ComposeSchemaProvider


class ComposeDeclaredTests(unittest.TestCase):
    def test_declared_mode_is_preserved_without_script_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.yml").write_text("services: {test: {image: node}}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose", "compose": {"file": "compose.yml", "service": "test", "internal_port": 3000, "health_path": "/"},
                "tests": {"modes": {"fast": {"argv": ["pnpm", "test:fast"]}}},
            }))
            result = ComposeSchemaProvider().resolve(root)
        self.assertEqual(result["tests"]["modes"]["fast"]["argv"], ["pnpm", "test:fast"])

    def test_rejects_empty_declared_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.yml").touch()
            (root / "sandbox.config.json").write_text(json.dumps({
                "compose": {"file": "compose.yml", "service": "test", "internal_port": 3000, "health_path": "/"},
                "tests": {"modes": {"fast": {"argv": []}}},
            }))
            with self.assertRaisesRegex(ValueError, "non-empty argv"):
                ComposeSchemaProvider().resolve(root)

    def test_declared_mode_submits_exact_argv_to_remote_workspace(self):
        import sandbox.commands.debug as debug

        captured = {}
        args = SimpleNamespace(project_dir="/fixture", label=None, mode="fast",
                               provision_only=False, local=False, remote="scaleway-sandbox",
                               workspace=["lenzora-test"], timeout=1200,
                               output_profile="smart", json=True, passthrough=[])

        class RegistryFacade:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_path, label=None):
                return {"kind": "compose", "root": "/fixture",
                        "tests": {"modes": {"fast": {"argv": ["pnpm", "test:fast"]}}}}

        target = SimpleNamespace(kind="remote", project_root="/fixture",
                                 workspace_label="lenzora-test", remote_name="scaleway-sandbox",
                                 sources={"identity": "project:compose"})

        def resolve(request):
            captured["request"] = request
            return target

        def accept(submission):
            captured["submission"] = submission
            return {"ok": True, "job_id": "job-1"}

        output = StringIO()
        with patch.object(debug, "_core", return_value=RegistryFacade()), \
                patch("sandbox.application.context.durable_job_dependencies", return_value={
                    "target_service": SimpleNamespace(resolve=resolve),
                }), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport.submit", side_effect=accept):
            with redirect_stdout(output):
                debug.cmd_test({}, args)

        self.assertEqual(captured["request"].required_capability, "job.exec")
        self.assertEqual(captured["submission"].argv, ("pnpm", "test:fast"))
        self.assertEqual(captured["submission"].workspace_label, "lenzora-test")
        self.assertEqual(captured["submission"].project_identity, "project:compose")
        self.assertEqual(
            captured["submission"].source.identity,
            "sha256:" + hashlib.sha256("/fixture".encode()).hexdigest(),
        )
        self.assertEqual(json.loads(output.getvalue())["job_id"], "job-1")

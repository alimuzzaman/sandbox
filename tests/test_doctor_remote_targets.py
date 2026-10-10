"""Doctor's "Remote targets" covers the declared remote with readiness rows (spec 063 T020a)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.commands import lifecycle  # noqa: E402
from sandbox.readiness import check  # noqa: E402
from tests.test_readiness import _probes  # noqa: E402


def _dependencies(declared):
    service = SimpleNamespace(declared_remote=lambda _root: declared)
    return lambda: {"target_service": service}


class DoctorRemoteTargetsTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def rows_for(self, declared, **probe_overrides):
        probes = _probes(self.home, **probe_overrides)
        with patch("sandbox.application.context.durable_job_dependencies",
                   _dependencies(declared)), \
                patch.object(check, "default_probes", return_value=probes):
            return lifecycle._remote_readiness_doctor_rows("/work/project")

    def test_registered_declared_remote_reports_every_row(self):
        found = self.rows_for("vps")
        self.assertEqual(len(found), 6)
        self.assertTrue(all(label.startswith("vps (declared): ") for label, _ok, _h in found))
        self.assertTrue(all(ok for _label, ok, _hint in found))

    def test_unregistered_declared_remote_fails_registration(self):
        class Refusal(Exception):
            code = "unknown_remote"

        def resolve(_project, _remote):
            raise Refusal("not registered")
        found = self.rows_for("ghost", resolve=resolve)
        label, ok, hint = found[0]
        self.assertIn("ghost (declared): registration not_ready (unknown_remote)", label)
        self.assertFalse(ok)
        self.assertEqual(hint, "./sb remote list")
        self.assertTrue(all(ok for _label, ok, _hint in found[1:]))

    def test_no_declared_remote_adds_nothing(self):
        self.assertEqual(self.rows_for(None), [])


class FullDoctorTests(unittest.TestCase):
    """The whole `sb doctor --json` command carries the declared remote's rows
    in its "Remote targets" section, registered or not."""

    def doctor_rows(self, declared, **probe_overrides):
        import contextlib
        import io
        import json
        home = tempfile.mkdtemp()
        project = tempfile.mkdtemp()
        (Path(project) / "sandbox.config.json").write_text("{}\n")
        process = SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(row) for row in (
            {"Service": "wp", "State": "running"}, {"Service": "db", "State": "running"},
            {"Service": "mailpit", "State": "running"})))
        venv = Path(home) / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").touch()
        output = io.StringIO()
        with patch.object(lifecycle, "preflight_instance_capability", return_value=None), \
                patch.object(lifecycle, "resolve_instances", return_value={
                    "fixture": {"admin": {}, "wordpress_port": 8188}}), \
                patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                    registry_find_instance=lambda _name: {"root": project})), \
                patch.object(lifecycle, "php_extension_status", return_value=None), \
                patch.object(lifecycle, "runtime_service", return_value=SimpleNamespace(
                    invoke=lambda _request: SimpleNamespace(ok=True, data={"status": "running"}))), \
                patch.object(lifecycle, "compose", return_value=process), \
                patch.object(lifecycle, "wpcli", return_value=SimpleNamespace(returncode=0)), \
                patch.object(lifecycle, "_probe_mcp_server", return_value=(True, "")), \
                patch.object(lifecycle, "MCP_VENV", venv), \
                patch.object(lifecycle, "focus_file", return_value=Path(home) / "focus"), \
                patch.object(lifecycle, "plugins_dir", return_value=Path(home) / "plugins"), \
                patch.object(lifecycle, "_local_yaml", return_value={}), \
                patch.object(lifecycle, "SECRETS_ENV", Path(home) / "missing-env"), \
                patch("sandbox.core._domains.proxy_health_checks", return_value=[]), \
                patch("sandbox.core._remote.list_remotes", return_value={}), \
                patch("sandbox.resources.monitor.storage_doctor_checks", return_value=[]), \
                patch("sandbox.application.context.durable_job_dependencies",
                      _dependencies(declared)), \
                patch.object(check, "default_probes",
                             return_value=_probes(home, **probe_overrides)), \
                contextlib.redirect_stdout(output), contextlib.suppress(SystemExit):
            lifecycle.cmd_doctor({"instances": {"fixture": {}}},
                                 SimpleNamespace(resolved_instance="fixture", json=True))
        payload = json.loads(output.getvalue())
        return [row for row in payload["checks"] if row["section"] == "Remote targets"]

    def test_cmd_doctor_reports_declared_registered_and_unregistered_remotes(self):
        registered = self.doctor_rows("vps")
        self.assertEqual(len(registered), 6)
        self.assertTrue(all(row["label"].startswith("vps (declared): ") and row["ok"]
                            for row in registered))

        class Refusal(Exception):
            code = "unknown_remote"

        def resolve(_project, _remote):
            raise Refusal("not registered")
        unregistered = self.doctor_rows("ghost", resolve=resolve)
        self.assertIn("ghost (declared): registration not_ready (unknown_remote)",
                      unregistered[0]["label"])
        self.assertFalse(unregistered[0]["ok"])
        self.assertEqual(unregistered[0]["hint"], "./sb remote list")


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

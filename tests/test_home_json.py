import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import migrate


class TestHomeJson(unittest.TestCase):
    def test_read_only_home_json_includes_feedback_root(self):
        output = io.StringIO()
        base = Path("/tmp/sandbox-home")
        runtime = base / "runtime"
        config = base / "sandbox.local.yml"
        with patch.object(migrate, "BASE", base), \
             patch.object(migrate, "RUNTIME_DIR", runtime), \
             patch.object(migrate, "CONFIG_FILE", config), \
             patch.object(migrate, "_state_present", return_value=True), \
             patch.object(Path, "exists", return_value=True), \
             contextlib.redirect_stdout(output):
            migrate.cmd_home({}, SimpleNamespace(dir=None, json=True))

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["base"], str(base))
        self.assertEqual(payload["runtime"], str(runtime))
        self.assertEqual(payload["feedback"], str(runtime / "feedback"))
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()

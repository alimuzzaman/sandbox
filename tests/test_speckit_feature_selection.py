from __future__ import annotations

import json
import os
import shutil
import subprocess
from tests.subprocess_support import synthetic_environment
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SpeckitFeatureSelectionTests(unittest.TestCase):
    def _project(self, directory: str) -> tuple[Path, Path]:
        project = Path(directory)
        scripts = project / ".specify/scripts/bash"
        scripts.mkdir(parents=True)
        for name in ("common.sh", "check-prerequisites.sh"):
            shutil.copy2(ROOT / ".specify/scripts/bash" / name, scripts / name)
        active = project / "specs/033-agent-aware-remote-sync"
        active.mkdir(parents=True)
        (active / "plan.md").write_text("active plan\n")
        selected = project / "specs/009-runtime-user-dir"
        selected.mkdir(parents=True)
        (selected / "plan.md").write_text("selected plan\n")
        (project / ".specify/feature.json").write_text(
            json.dumps({"feature_directory": "specs/033-agent-aware-remote-sync"})
            + "\n"
        )
        return project, selected

    def _run(self, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = synthetic_environment()
        env["SPECIFY_INIT_DIR"] = str(project)
        return subprocess.run(
            [str(project / ".specify/scripts/bash/check-prerequisites.sh"), *args],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_feature_dir_selects_existing_feature_without_pointer_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            project, selected = self._project(directory)
            pointer_before = (project / ".specify/feature.json").read_bytes()
            result = self._run(
                project, "--feature-dir", "specs/009-runtime-user-dir",
                "--json", "--paths-only",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["FEATURE_DIR"], str(selected))
            self.assertEqual((project / ".specify/feature.json").read_bytes(), pointer_before)

    def test_feature_dir_keeps_normal_plan_and_task_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            project, _selected = self._project(directory)
            result = self._run(
                project, "--feature-dir", "specs/009-runtime-user-dir", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["FEATURE_DIR"],
                             str(project / "specs/009-runtime-user-dir"))

    def test_feature_dir_rejects_missing_and_outside_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            project, _selected = self._project(directory)
            for value, expected in (
                ("specs/missing", "does not point to an existing directory"),
                ("..", "must stay inside the Spec Kit project"),
            ):
                with self.subTest(value=value):
                    result = self._run(project, "--feature-dir", value, "--json")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)

    def test_feature_dir_requires_a_value(self):
        with tempfile.TemporaryDirectory() as directory:
            project, _selected = self._project(directory)
            result = self._run(project, "--feature-dir")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires a feature directory path", result.stderr)


if __name__ == "__main__":
    unittest.main()

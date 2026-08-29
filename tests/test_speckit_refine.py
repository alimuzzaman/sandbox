from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SpeckitRefineArtifactTests(unittest.TestCase):
    def test_prd_template_is_first_class_and_phase_bounded(self):
        template = (ROOT / ".specify/templates/prd-template.md").read_text()
        self.assertIn("# Product Requirements Draft:", template)
        self.assertIn("**Artifact Owner**: `speckit-refine`", template)
        self.assertIn("## Goals", template)
        self.assertIn("## Non-Goals", template)
        self.assertIn("## Open Questions", template)
        self.assertIn("## Readiness for Specification", template)
        self.assertIn("**Drafting Model**: `gpt-5.6-terra` Medium", template)
        self.assertIn("**Final Validation**: `PENDING`", template)
        self.assertIn("Sol High validation verdict is `PASS`", template)
        self.assertIn("**Readiness**: `NOT READY`", template)
        self.assertNotIn("### Functional Requirements", template)

    def test_refine_skill_is_mirrored_and_forbids_downstream_artifacts(self):
        canonical = (ROOT / "skills/speckit-refine/SKILL.md").read_text()
        mirrored = (ROOT / ".agents/skills/speckit-refine/SKILL.md").read_text()
        self.assertEqual(canonical, mirrored)
        self.assertIn("`gpt-5.6-terra` at Medium effort", canonical)
        self.assertIn("`gpt-5.6-sol` at High effort", canonical)
        self.assertIn("## Final Sol validation", canonical)
        self.assertNotIn("gpt-5.6-luna", canonical)
        self.assertIn("Run at most five passes", canonical)
        self.assertIn("MUST NOT create or modify", canonical)
        for artifact in ("`spec.md`", "`plan.md`", "`tasks.md`"):
            self.assertIn(artifact, canonical)
        self.assertIn("Do not invoke\n   it automatically", canonical)

    def test_specify_consumes_ready_prd_without_mutating_it(self):
        canonical = (ROOT / "skills/speckit-specify/SKILL.md").read_text()
        mirrored = (ROOT / ".agents/skills/speckit-specify/SKILL.md").read_text()
        self.assertEqual(canonical, mirrored)
        self.assertIn("## PRD handoff", canonical)
        self.assertIn("READY FOR SPECKIT", canonical)
        self.assertIn("Never modify, rename, or delete `prd.md`", canonical)
        self.assertIn("reuse the PRD's existing feature directory", canonical)
        self.assertIn("`gpt-5.6-sol` at Medium effort", canonical)

    def test_implementation_model_routing_is_mirrored(self):
        canonical = (ROOT / "skills/speckit-implement/SKILL.md").read_text()
        mirrored = (ROOT / ".agents/skills/speckit-implement/SKILL.md").read_text()
        for content in (canonical, mirrored):
            self.assertIn("`gpt-5.6-terra` at High effort", content)
            self.assertIn("`gpt-5.6-sol` at Medium", content)

    def test_workflow_places_refinement_before_specification(self):
        workflow = (ROOT / ".specify/workflows/speckit/workflow.yml").read_text()
        refine = workflow.index("  - id: refine")
        review = workflow.index("  - id: review-prd")
        specify = workflow.index("  - id: specify")
        self.assertLess(refine, review)
        self.assertLess(review, specify)
        self.assertIn("command: speckit.refine", workflow)
        self.assertIn("Sol High validation", workflow)
        self.assertIn("command: speckit.clarify", workflow)
        self.assertIn("command: speckit.analyze", workflow)
        self.assertLess(workflow.index("  - id: clarify"), workflow.index("  - id: plan"))
        self.assertLess(workflow.index("  - id: analyze"), workflow.index("  - id: implement"))
        self.assertIn('args: ""', workflow)

    def test_existing_remote_sync_prd_handoff_has_downstream_artifacts(self):
        feature = ROOT / "specs/033-agent-aware-remote-sync"
        self.assertTrue((feature / "prd.md").is_file())
        self.assertTrue((feature / "spec.md").is_file())
        self.assertTrue((feature / "plan.md").is_file())
        self.assertTrue((feature / "tasks.md").is_file())
        self.assertTrue((feature / "checklists/requirements.md").is_file())
        self.assertIn("**Readiness**: `READY FOR SPECKIT`", (feature / "prd.md").read_text())

    def test_integration_manifests_track_custom_skill_and_template(self):
        claude = json.loads((ROOT / ".specify/integrations/claude.manifest.json").read_text())
        speckit = json.loads((ROOT / ".specify/integrations/speckit.manifest.json").read_text())
        expected = {
            "skills/speckit-refine/SKILL.md": claude["files"]["skills/speckit-refine/SKILL.md"],
            ".specify/templates/prd-template.md": speckit["files"][".specify/templates/prd-template.md"],
        }
        for relative, digest in expected.items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, digest)


class SpeckitRefinePathTests(unittest.TestCase):
    def _feature_project(self, directory: str, *, include_prd: bool = False) -> tuple[Path, Path]:
        project = Path(directory)
        scripts = project / ".specify/scripts/bash"
        templates = project / ".specify/templates"
        scripts.mkdir(parents=True)
        templates.mkdir(parents=True)
        for name in ("common.sh", "create-new-feature.sh"):
            shutil.copy2(ROOT / ".specify/scripts/bash" / name, scripts / name)
        if include_prd:
            shutil.copy2(ROOT / ".specify/templates/prd-template.md", templates / "prd-template.md")
        else:
            shutil.copy2(ROOT / ".specify/templates/spec-template.md", templates / "spec-template.md")
        return project, scripts

    def test_feature_creation_script_supports_prd_without_creating_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            project, scripts = self._feature_project(directory, include_prd=True)
            env = os.environ.copy()
            env["SPECIFY_INIT_DIR"] = str(project)
            result = subprocess.run(
                [
                    str(scripts / "create-new-feature.sh"), "--prd", "--json",
                    "--short-name", "product-refine", "Refine a product idea",
                ],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            prd = Path(payload["PRD_FILE"])
            self.assertTrue(payload["PRD_MODE"])
            self.assertEqual(payload["ARTIFACT_FILE"], payload["PRD_FILE"])
            self.assertTrue(prd.is_file())
            self.assertFalse((prd.parent / "spec.md").exists())
            active = json.loads((project / ".specify/feature.json").read_text())
            self.assertEqual(active["feature_directory"], "specs/001-product-refine")

    def test_default_feature_creation_still_returns_spec_file(self):
        with tempfile.TemporaryDirectory() as directory:
            project, scripts = self._feature_project(directory)
            env = os.environ.copy()
            env["SPECIFY_INIT_DIR"] = str(project)
            result = subprocess.run(
                [
                    str(scripts / "create-new-feature.sh"), "--json",
                    "--short-name", "normal-spec", "Create a normal specification",
                ],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            spec = Path(payload["SPEC_FILE"])
            self.assertFalse(payload["PRD_MODE"])
            self.assertEqual(payload["ARTIFACT_FILE"], payload["SPEC_FILE"])
            self.assertTrue(spec.is_file())
            self.assertFalse((spec.parent / "prd.md").exists())

    def test_common_and_prerequisite_json_expose_feature_prd(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            scripts = project / ".specify/scripts/bash"
            scripts.mkdir(parents=True)
            for name in ("common.sh", "check-prerequisites.sh"):
                shutil.copy2(ROOT / ".specify/scripts/bash" / name, scripts / name)
            feature = project / "specs/001-example"
            feature.mkdir(parents=True)
            (project / ".specify/feature.json").write_text(
                json.dumps({"feature_directory": "specs/001-example"})
            )
            env = os.environ.copy()
            env["SPECIFY_INIT_DIR"] = str(project)
            result = subprocess.run(
                [str(scripts / "check-prerequisites.sh"), "--json", "--paths-only"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["FEATURE_PRD"], str(feature / "prd.md"))


if __name__ == "__main__":
    unittest.main()

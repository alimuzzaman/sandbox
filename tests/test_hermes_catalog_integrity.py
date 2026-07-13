"""Regression coverage for secret-safe Hermes catalog reconciliation."""
from __future__ import annotations

from pathlib import Path
import unittest

from sandbox.hermes.scheduler import (
    _sha256,
    guarded_prompt,
    load_catalog,
    reconciliation_plan,
    scheduled_route,
    scripts_path,
)


PATHS = {
    "repo_root": "/srv/hermes-repos",
    "sandbox_home": "/srv/sandbox",
    "worktrees": "/srv/sandbox/runtime/hermes-worktrees",
}


def _observed(entry, *, script_root: Path, **changes):
    # Build a safe pinned-schema observation; prompts and script bodies stay absent.
    fields = {
        "id": f"id-{entry.name}",
        "name": entry.name,
        "schedule": entry.schedule,
        "enabled": True,
        "deliver": entry.deliver,
        "workdir": entry.workdir_template.format(**PATHS) if entry.workdir_template else None,
        "no_agent": entry.kind == "script",
    }
    if entry.kind == "agent":
        route = scheduled_route(entry.profile or "")
        fields.update({
            "provider_snapshot": route.provider,
            "model_snapshot": route.model,
            "reasoning_effort_snapshot": route.effort,
            "prompt_sha256": _sha256(guarded_prompt(entry.prompt)),
        })
    else:
        fields.update({
            "script": entry.script,
            "script_sha256": _sha256((script_root / entry.script).read_bytes()),
        })
    fields.update(changes)
    return fields


class TestHermesCatalogIntegrity(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.script_root = scripts_path()

    def _observed_catalog(self):
        return [_observed(entry, script_root=self.script_root) for entry in self.catalog["jobs"]]

    def assertConverged(self, observed):
        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)
        self.assertFalse(plan["changes"])
        self.assertEqual(plan["blocked_by"], [])

    def test_guarded_prompt_drift_requires_reconciliation_without_disclosing_prompt(self):
        observed = self._observed_catalog()
        self.assertConverged(observed)
        worker = next(job for job in observed if job["name"] == "sandbox-approved-spec-task")
        worker["prompt_sha256"] = "0" * 64

        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["retain"], [])
        self.assertNotIn("prompt", str(plan).lower())

    def test_delivery_drift_requires_reconciliation(self):
        observed = self._observed_catalog()
        self.assertConverged(observed)
        observed[0]["deliver"] = "remote"

        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["retain"], [])

    def test_installed_script_content_drift_requires_reconciliation(self):
        observed = self._observed_catalog()
        self.assertConverged(observed)
        script_job = next(job for job in observed if job["no_agent"])
        script_job["script_sha256"] = "f" * 64

        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["retain"], [])

    def test_incomplete_safe_observation_is_explicitly_blocked(self):
        observed = self._observed_catalog()
        worker = next(job for job in observed if job["name"] == "sandbox-approved-spec-task")
        del worker["prompt_sha256"]

        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["blocked_by"], [{
            "name": "sandbox-approved-spec-task",
            "reason": "controlled-state fingerprint unavailable",
        }])
        self.assertNotIn("prompt", str(plan).lower())


if __name__ == "__main__":
    unittest.main()

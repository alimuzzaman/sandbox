"""Regression coverage for secret-safe Hermes catalog reconciliation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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

    def _observed_catalog(self, catalog=None):
        catalog = catalog or self.catalog
        return [_observed(entry, script_root=self.script_root)
                for entry in catalog["jobs"] if entry.enabled]

    def _catalog_with_worker_enabled(self):
        catalog = deepcopy(self.catalog)
        worker = next(entry for entry in catalog["jobs"] if entry.name == "lenzora-todo-task")
        return {"schema_version": catalog["schema_version"], "jobs": [
            replace(entry, enabled=entry.name == "lenzora-todo-task")
            for entry in catalog["jobs"]
        ]}

    def assertConverged(self, observed):
        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)
        self.assertFalse(plan["changes"])
        self.assertEqual(plan["blocked_by"], [])

    def test_enabled_agents_are_reconciled(self):
        observed = self._observed_catalog()
        self.assertConverged(observed)
        plan = reconciliation_plan(self.catalog, observed, paths=PATHS)
        self.assertEqual(plan["retain"], [
            "codex-quota-requeue", "authorization-expiry", "sandbox-approved-spec-task",
            "sandbox-remaining-spec-tasks",
        ])

    def test_delivery_drift_requires_reconciliation(self):
        catalog = self._catalog_with_worker_enabled()
        observed = self._observed_catalog(catalog)
        plan = reconciliation_plan(catalog, observed, paths=PATHS)
        self.assertFalse(plan["changes"])
        observed[0]["deliver"] = "remote"

        plan = reconciliation_plan(catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["retain"], [])

    def test_active_agent_prompt_hash_drift_requires_reconciliation(self):
        catalog = self._catalog_with_worker_enabled()
        observed = self._observed_catalog(catalog)
        plan = reconciliation_plan(catalog, observed, paths=PATHS)
        self.assertFalse(plan["changes"])
        worker = next(job for job in observed if not job["no_agent"])
        worker["prompt_sha256"] = "f" * 64

        plan = reconciliation_plan(catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["retain"], [])

    def test_lenzora_worker_uses_only_the_fixed_authorization_template(self):
        worker = next(entry for entry in self.catalog["jobs"] if entry.name == "lenzora-todo-task")
        self.assertIn("request.py --template lenzora-preview-overlay", worker.prompt)
        self.assertIn("cannot approve it, alter scope/origin, or access lenzora.app", worker.prompt)
        self.assertIn("existing unexpired SANDBOX AUTHORIZATION", worker.prompt)

    def test_incomplete_safe_observation_is_explicitly_blocked(self):
        catalog = self._catalog_with_worker_enabled()
        observed = self._observed_catalog(catalog)
        worker = next(job for job in observed if job["name"] == "lenzora-todo-task")
        del worker["prompt_sha256"]

        plan = reconciliation_plan(catalog, observed, paths=PATHS)

        self.assertTrue(plan["changes"])
        self.assertEqual(plan["blocked_by"], [{
            "name": "lenzora-todo-task",
            "reason": "controlled-state fingerprint unavailable",
        }])
        self.assertNotIn("prompt", str(plan).lower())


if __name__ == "__main__":
    unittest.main()

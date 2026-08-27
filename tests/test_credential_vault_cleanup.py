"""Offline cleanup verification from injected observations only.

Nothing here inspects or removes a real unit, socket, interface, or file. The
verifier's contract is that it never deletes anything at all: uncertainty
becomes a retained item and `cleanup_incomplete`.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    cleanup as cleanup_module, fixtures, manifest as manifest_module,
)


class TestCleanupVerifier(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest_module.validate_manifest(fixtures.manifest())

    def observations(self, **overrides):
        return list(fixtures.cleanup_observations(self.manifest, **overrides))

    def test_expected_resources_cover_every_declared_kind(self):
        expected = cleanup_module.expected_resources(self.manifest)
        kinds = {item["kind"] for item in expected}
        self.assertEqual(kinds, {
            "unit", "socket", "interface", "cgroup", "nftables_object", "path",
            "route", "process", "descriptor", "epoch_state",
        })

    def test_every_resource_proven_absent_is_complete(self):
        result = cleanup_module.verify(self.manifest, self.observations())
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["retained"], ())
        self.assertEqual(len(result["removed"]),
                         len(cleanup_module.expected_resources(self.manifest)))

    def test_a_missing_observation_is_never_absence(self):
        observations = self.observations()
        dropped = observations.pop()
        result = cleanup_module.verify(self.manifest, observations)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "incomplete")
        reasons = {item["reason_code"] for item in result["retained"]}
        self.assertEqual(reasons, {"observation_missing"})
        self.assertIn(dropped["identity"],
                      {item["identity"] for item in result["retained"]})

    def test_an_unreadable_observation_is_incomplete_not_absent(self):
        observations = self.observations()
        observations[0] = {**observations[0], "state": "unavailable"}
        result = cleanup_module.verify(self.manifest, observations)
        self.assertEqual(result["state"], "incomplete")
        self.assertIn("observation_unavailable",
                      {item["reason_code"] for item in result["retained"]})

    def test_a_foreign_resource_is_retained_and_never_removed(self):
        observations = self.observations()
        observations[0] = {**observations[0], "state": "foreign", "owned": False}
        result = cleanup_module.verify(self.manifest, observations)
        self.assertEqual(result["state"], "incomplete")
        retained = [item for item in result["retained"]
                    if item["reason_code"] == "foreign_resource"]
        self.assertEqual(len(retained), 1)
        self.assertNotIn(observations[0]["identity"], result["removed"])

    def test_a_present_owned_resource_is_still_incomplete(self):
        observations = self.observations()
        observations[1] = {**observations[1], "state": "present", "owned": True}
        result = cleanup_module.verify(self.manifest, observations)
        self.assertEqual(result["state"], "incomplete")
        self.assertIn("resource_present",
                      {item["reason_code"] for item in result["retained"]})

    def test_an_unexpected_leftover_resource_blocks_completion(self):
        observations = self.observations()
        observations.append({
            "kind": "unit", "identity": "sandbox-stray@sb-0123456789ab.service",
            "state": "present", "owned": False,
        })
        result = cleanup_module.verify(self.manifest, observations)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(len(result["unexpected"]), 1)

    def test_contradictory_observations_are_refused(self):
        observations = self.observations()
        observations.append({**observations[0], "state": "present"})
        with self.assertRaises(cleanup_module.CleanupError) as raised:
            cleanup_module.verify(self.manifest, observations)
        self.assertEqual(raised.exception.code, "observation_contradiction")

        owned_foreign = self.observations()
        owned_foreign[0] = {**owned_foreign[0], "state": "foreign", "owned": True}
        with self.assertRaises(cleanup_module.CleanupError) as raised:
            cleanup_module.verify(self.manifest, owned_foreign)
        self.assertEqual(raised.exception.code, "observation_contradiction")

    def test_malformed_observations_are_refused(self):
        for observation, code in (
            ({"kind": "unit"}, "observation_schema_invalid"),
            ({"kind": "wat", "identity": "x", "state": "absent", "owned": True},
             "observation_kind_invalid"),
            ({"kind": "unit", "identity": "a b", "state": "absent", "owned": True},
             "observation_identity_invalid"),
            ({"kind": "unit", "identity": "u.service", "state": "gone", "owned": True},
             "observation_state_invalid"),
            ({"kind": "unit", "identity": "u.service", "state": "absent", "owned": 1},
             "observation_ownership_invalid"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(cleanup_module.CleanupError) as raised:
                    cleanup_module.verify(self.manifest, [observation])
                self.assertEqual(raised.exception.code, code)
        with self.assertRaises(cleanup_module.CleanupError) as raised:
            cleanup_module.verify(self.manifest, "not a list")
        self.assertEqual(raised.exception.code, "observations_invalid")

    def test_the_verifier_exposes_no_removal_capability(self):
        # The module deliberately has no delete/stop/remove entry point.
        for name in dir(cleanup_module):
            self.assertNotIn(name.lower().split("_")[0],
                             {"delete", "remove", "stop", "kill", "purge"})


if __name__ == "__main__":
    unittest.main()

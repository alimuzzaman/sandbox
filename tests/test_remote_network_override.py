"""Compose override for development ranges (spec 063 T009)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sandbox.remote_network import override  # noqa: E402

CONFIG = {
    "name": "proj",
    "services": {"wp": {"networks": {"default": None, "backend": None}}},
    "networks": {
        "default": {"name": "proj_default"},
        "backend": {"name": "proj_backend", "driver": "bridge"},
        "shared": {"name": "edge", "external": True},
    },
}


class NetworkSelectionTests(unittest.TestCase):
    def test_created_and_external_networks_are_split(self):
        created, external = override.compose_networks(CONFIG)
        self.assertEqual(created, ["backend", "default"])
        self.assertEqual(external, ["shared"])

    def test_missing_or_malformed_networks_are_empty(self):
        self.assertEqual(override.compose_networks({}), ([], []))
        self.assertEqual(override.compose_networks({"networks": []}), ([], []))
        with self.assertRaises(override.OverrideError):
            override.compose_networks({"networks": {"bad name!": {}}})

    def test_network_with_its_own_ipam_is_left_alone(self):
        config = {"networks": {"pinned": {"ipam": {"config": [{"subnet": "10.9.0.0/24"}]}}}}
        self.assertEqual(override.compose_networks(config), ([], ["pinned"]))


class DockerNameTests(unittest.TestCase):
    def test_allocation_uses_project_scoped_docker_names(self):
        self.assertEqual(override.docker_names(CONFIG, ["backend", "default"]),
                         {"backend": "proj_backend", "default": "proj_default"})

    def test_two_stacks_declaring_default_get_distinct_names(self):
        one = override.docker_names({"name": "sandbox-a", "networks": {"default": {}}}, ["default"])
        two = override.docker_names({"name": "sandbox-b", "networks": {"default": {}}}, ["default"])
        self.assertNotEqual(one["default"], two["default"])

    def test_unusable_names_are_refused(self):
        with self.assertRaises(override.OverrideError):
            override.docker_names({"networks": {"default": {}}}, ["default"])
        with self.assertRaises(override.OverrideError):
            override.docker_names({"name": "p", "networks": {
                "a": {"name": "same"}, "b": {"name": "same"}}}, ["a", "b"])


class RenderTests(unittest.TestCase):
    def test_one_ipam_subnet_per_created_network(self):
        plan = override.plan(CONFIG, {"default": "10.200.0.0/26", "backend": "10.200.0.64/26"})
        self.assertEqual(plan["covered"], ["backend", "default"])
        self.assertEqual(plan["outside_range"], ["shared"])
        self.assertEqual(plan["text"], (
            "networks:\n"
            "  backend:\n    ipam:\n      config:\n        - subnet: 10.200.0.64/26\n"
            "  default:\n    ipam:\n      config:\n        - subnet: 10.200.0.0/26\n"))

    def test_a_created_network_without_a_grant_is_refused(self):
        with self.assertRaises(override.OverrideError) as caught:
            override.plan(CONFIG, {"default": "10.200.0.0/26"})
        self.assertEqual(caught.exception.code, "range_allocation_incomplete")

    def test_grants_must_be_ipv4_networks(self):
        with self.assertRaises(override.OverrideError):
            override.plan(CONFIG, {"default": "10.200.0.0/26", "backend": "x; rm -rf /"})

    def test_no_created_networks_renders_nothing(self):
        plan = override.plan({"networks": {"e": {"external": True}}}, {})
        self.assertEqual((plan["text"], plan["outside_range"]), ("", ["e"]))


class OwnerTests(unittest.TestCase):
    def test_owner_kind_mapping(self):
        for run in ("workspace", "job", "preview", "exec", "ci_cell"):
            self.assertEqual(override.owner(run, workspace_id="ws_1", job_id="j1"),
                             ("workspace", "ws_1"))
        self.assertEqual(override.owner("job_stack", workspace_id="ws_1", job_id="j1"),
                         ("job", "j1"))
        with self.assertRaises(override.OverrideError):
            override.owner("other", workspace_id="ws_1", job_id=None)


class CollisionTests(unittest.TestCase):
    def test_subnet_collision_is_typed_and_not_retryable(self):
        for stderr in ("Error response from daemon: Pool overlaps with other one on this address space",
                       "failed to create network proj_default: invalid pool request: Pool overlaps"):
            refusal = override.classify_create_failure(stderr)
            self.assertEqual(refusal["code"], "range_network_collision")
            self.assertFalse(refusal["retryable"])
            self.assertNotIn("10.", refusal["message"])

    def test_other_failures_are_not_classified(self):
        self.assertIsNone(override.classify_create_failure("no space left on device"))


if __name__ == "__main__":
    unittest.main()

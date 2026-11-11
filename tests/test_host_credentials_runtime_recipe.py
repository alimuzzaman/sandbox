"""Pure committed-template rendering and fixed recipient refusal boundaries."""
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from sandbox.hosting.shared_credentials.application import RuntimeAction, RuntimeEnvironmentConsumer
from sandbox.hosting.shared_credentials.profiles import registered_profiles
from sandbox.hosting.shared_credentials.recipe_registration import render_named_recipe


class NamedRecipeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="credential-recipe-")))
        self.template = b'''stack:
  slug: "[a-z][a-z0-9-]+"
  ports: []
project: hub-{slug}
environments:
  fixture:
    compose:
      service: hub
      files: [compose.yml]
    host:
      primary: hub-{slug}.example.test
    secrets:
      required: {TYPESAFE_API_KEY: XSPEED_JEV_API_KEY}
      generated: {POSTGRES_PASSWORD: POSTGRES_PASSWORD}
'''
        mapping = dict(kind="provider", source_key="XSPEED_JEV_API_KEY", destination="TYPESAFE_API_KEY",
            consumer_id="runtime-hub", consumer_revision="runtime-env-v1", purpose="runtime-environment")
        self.profile = registered_profiles({"hub-profile": dict(source="fixture-source", action="stack_up",
            environment="fixture", sourceCommit="a" * 40, artifactDigest="b" * 64,
            manifestDigest=hashlib.sha256(self.template).hexdigest(), mappings=[mapping],
            recipeTemplate="template.yml")})["hub-profile"]

    def render(self, name):
        return render_named_recipe(root=self.root, checkout=self.root, name=name, profile=self.profile,
            template_content=self.template, render=dict(name=name, ports={}, ref_branch=""))

    def test_new_name_uses_distinct_real_template_project_and_hosts(self):
        first, one = self.render("fresh-one")
        second, two = self.render("fresh-two")
        self.assertEqual(first["project"], "hub-fresh-one")
        self.assertEqual(second["project"], "hub-fresh-two")
        self.assertNotEqual(first["environments"]["fixture"]["host"],
                            second["environments"]["fixture"]["host"])
        self.assertNotEqual(hashlib.sha256(one).digest(), hashlib.sha256(two).digest())
        self.assertEqual(one, self.render("fresh-one")[1])

    def test_inline_values_refuse_before_template_or_secret_delivery(self):
        with self.assertRaises(ValueError):
            render_named_recipe(root=self.root, checkout=self.root, name="fresh-one", profile=self.profile,
                template_content=self.template + b"      values: {TOKEN: fixture-only}\n",
                render=dict(name="fresh-one", ports={}, ref_branch=""))


class RuntimeRecipientAuthorityTests(unittest.TestCase):
    def test_typed_action_without_actual_claimed_effect_cannot_start_runtime(self):
        action = RuntimeAction(Path("/nonexistent/compose.json"), "a" * 64, "fixture-project", "hub",
            "example/hub@sha256:" + "b" * 64, Path("/nonexistent/engine.sock"),
            ("TYPESAFE_API_KEY",), 0, time.time() + 30)
        consumer = RuntimeEnvironmentConsumer("c" * 64, action, "runtime-hub")
        with patch("sandbox.hosting.shared_credentials.application.BoundedProcessRunner") as runner:
            self.assertEqual(consumer.consume({"TYPESAFE_API_KEY": "fixture-only"}), "failed")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()

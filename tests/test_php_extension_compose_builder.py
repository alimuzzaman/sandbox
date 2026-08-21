"""Pure PHP extension child-image planner contracts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.php_extensions.compose_builder import (
    UnsupportedExtensionError,
    UnsupportedParentImageError,
    extension_cache_status,
    materialize_compose_extension_context,
    plan_compose_extension_images,
)


class TestComposeExtensionBuilder(unittest.TestCase):
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64

    def _plan(self, **kwargs):
        args = {
            "requirements": {"profile": "wordpress@1", "extensions": {"gd": True}},
            "parent_image": "wordpress:php8.3-fpm",
            "wpcli_image": "wordpress:cli-php8.3",
            "parent_digest": self.digest_a,
            "wpcli_parent_digest": self.digest_b,
            "server": "nginx",
            "platform": "linux",
            "architecture": "amd64",
        }
        args.update(kwargs)
        return plan_compose_extension_images(**args)

    def test_fingerprint_contains_every_independent_fr055_input(self):
        """Every trusted resolution input gets its own cache identity.

        The Apache case uses its compatible official parent because the
        allowlisted planner rejects an Apache/FPM mismatch before it can
        produce a digest.  The other rows vary exactly one planner input.
        """
        base = self._plan()
        self.assertEqual(base.digest, self._plan().digest)

        cases = {
            "web parent digest": {
                "parent_digest": "sha256:" + "c" * 64,
            },
            "wpcli parent digest": {
                "wpcli_parent_digest": "sha256:" + "d" * 64,
            },
            "web parent image": {
                "parent_image": "wordpress:php8.4-fpm",
            },
            "wpcli parent image": {
                "wpcli_image": "wordpress:cli-php8.4",
            },
            "php version": {"php_version": "8.4"},
            "platform": {"platform": "darwin"},
            "architecture": {"architecture": "arm64"},
            "requirements": {
                "requirements": {
                    "profile": "wordpress@1",
                    "extensions": {"gd": "2.3.3"},
                },
            },
            "profile": {
                "requirements": {"profile": None, "extensions": {"gd": True}},
            },
        }
        for label, kwargs in cases.items():
            with self.subTest(label=label):
                candidate = self._plan(**kwargs)
                self.assertNotEqual(base.digest, candidate.digest)

        apache = self._plan(
            server="apache",
            parent_image="wordpress:php8.3-apache",
        )
        self.assertNotEqual(base.digest, apache.digest)
        self.assertEqual(apache.web.provenance["server"], "apache")

        import sandbox.php_extensions.compose_builder as builder
        with patch.object(
            builder,
            "_recipe_catalog_digest",
            return_value="sha256:" + "e" * 64,
        ):
            revised_catalog = self._plan()
        self.assertNotEqual(base.digest, revised_catalog.digest)
        self.assertEqual(
            revised_catalog.web.provenance["recipe_catalog_digest"],
            "sha256:" + "e" * 64,
        )

    def test_official_and_litespeed_boundaries_are_fail_closed(self):
        with self.assertRaises(UnsupportedParentImageError):
            self._plan(parent_image="example.invalid/wordpress:php8.3-fpm")
        with self.assertRaises(UnsupportedParentImageError):
            self._plan(server="litespeed", parent_image="wordpress:php8.3")
        with self.assertRaises(UnsupportedParentImageError):
            self._plan(wpcli_image="example.invalid/wpcli:latest")

    def test_v1_has_no_unreviewed_source_or_pecl_path(self):
        for name in ("imagick", "xdebug"):
            with self.subTest(name=name):
                with self.assertRaises(UnsupportedExtensionError) as ctx:
                    self._plan(requirements={"extensions": {name: True}})
                self.assertEqual(ctx.exception.code, "unsupported_provisioning")
        with self.assertRaises(UnsupportedExtensionError) as ctx:
            self._plan(requirements={"extensions": {"gd": False}})
        self.assertEqual(ctx.exception.code, "unsupported_disable")
        import sandbox.php_extensions.compose_builder as builder
        source = Path(builder.__file__).read_text()
        self.assertNotIn("pecl install", source.lower())
        self.assertNotIn("http://", source.lower())
        self.assertNotIn("https://", source.lower())

    def test_context_is_explicit_and_tamper_invalidates_cache(self):
        with tempfile.TemporaryDirectory(prefix="sb-ext-context-") as home:
            old = os.environ.get("SANDBOX_HOME")
            os.environ["SANDBOX_HOME"] = home
            try:
                plan = self._plan()
                expected = Path(home).resolve() / "runtime" / "build" / "php-extensions" / plan.digest
                self.assertEqual(plan.context_dir, expected)
                self.assertFalse(plan.context_dir.exists())
                materialize_compose_extension_context(plan)
                dockerfile = plan.context_dir / "Dockerfile.web"
                self.assertTrue(dockerfile.is_file())
                self.assertIn(
                    "FROM wordpress:php8.3-fpm@sha256:" + "a" * 64,
                    dockerfile.read_text(),
                )
                wpcli_dockerfile = (plan.context_dir / "Dockerfile.wpcli").read_text()
                self.assertIn("USER root", wpcli_dockerfile)
                self.assertIn(
                    "RUN apk add --no-cache freetype-dev libjpeg-turbo-dev "
                    "libpng-dev libwebp-dev",
                    wpcli_dockerfile,
                )
                self.assertIn("USER 33:33", wpcli_dockerfile)
                self.assertIn(
                    "LABEL org.sandbox.php-extensions.digest=" + plan.digest,
                    dockerfile.read_text(),
                )
                self.assertIn(
                    "LABEL org.sandbox.php-extensions.role=web",
                    dockerfile.read_text(),
                )
                self.assertIn(
                    "LABEL org.sandbox.php-extensions.provenance="
                    + plan.web.provenance["recipe_catalog_digest"],
                    dockerfile.read_text(),
                )
                self.assertNotIn("docker-php-ext-install mbstring", dockerfile.read_text())
                self.assertNotIn("docker-php-ext-install mysqli", dockerfile.read_text())
                provenance = json.loads((plan.context_dir / "provenance.json").read_text())
                self.assertEqual(provenance["digest"], plan.digest)
                self.assertRegex(provenance["recipe_catalog_digest"], r"^sha256:[0-9a-f]{64}$")
                self.assertEqual(self._plan().cache_state, "hit")
                dockerfile.write_text("tampered\n")
                self.assertEqual(self._plan().cache_state, "invalidated")
            finally:
                if old is None:
                    os.environ.pop("SANDBOX_HOME", None)
                else:
                    os.environ["SANDBOX_HOME"] = old

    def test_cache_status_distinguishes_missing_ready_stale_and_discarded_without_private_paths(self):
        with tempfile.TemporaryDirectory(prefix="sb-ext-status-") as home:
            old = os.environ.get("SANDBOX_HOME")
            os.environ["SANDBOX_HOME"] = home
            try:
                plan = self._plan()
                missing = extension_cache_status(plan.digest)
                self.assertEqual(missing["state"], "missing")
                self.assertIsNone(missing["provenance"])

                materialize_compose_extension_context(plan)
                ready = extension_cache_status(plan.digest)
                self.assertEqual(ready["state"], "ready")
                self.assertEqual(ready["cache_state"], "hit")
                serialized = json.dumps(ready, sort_keys=True)
                self.assertNotIn(str(Path(home).resolve()), serialized)
                self.assertNotIn("password", serialized.lower())
                self.assertNotIn("token", serialized.lower())

                (plan.context_dir / "Dockerfile.web").write_text("tampered\n")
                stale = extension_cache_status(plan.digest)
                self.assertEqual(stale["state"], "stale")
                self.assertEqual(stale["cache_state"], "invalidated")

                (plan.context_dir / ".discarded").write_text("operator discard\n")
                discarded = extension_cache_status(plan.digest)
                self.assertEqual(discarded["state"], "discarded")
                self.assertEqual(discarded["cache_state"], "invalidated")
            finally:
                if old is None:
                    os.environ.pop("SANDBOX_HOME", None)
                else:
                    os.environ["SANDBOX_HOME"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)

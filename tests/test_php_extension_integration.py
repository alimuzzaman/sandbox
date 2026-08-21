"""Focused integration seams for the WordPress PHP extension contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _probe_document(*, gd=True, version="2.3.3", php="8.3.10"):
    return json.dumps({
        "schema_version": 1,
        "php_version": php,
        "sapi": "cli",
        "extensions": {
            "gd": {"enabled": gd, "version": version},
        },
    })


class PhpExtensionIntegrationTests(unittest.TestCase):
    def _assert_named_db_volume_contract(self, docker, runtime_root, project_root):
        """Prove the rendered Compose DB tier retains its named volume."""
        with patch.object(docker, "RUNTIME_DIR", runtime_root):
            compose_text = docker.render_compose(
                "fixture",
                {
                    "server": "nginx",
                    "wordpress_port": 8188,
                    "db_port": 3318,
                    "mailpit_port": 8125,
                    "wordpress_image": "wordpress:php8.3-fpm",
                    "wpcli_image": "wordpress:cli-php8.3",
                    "mariadb_image": "mariadb:latest",
                    "php_version": "8.3",
                    "wp_version": None,
                },
                project_root / "plugins",
            )
        self.assertIn("\n  db:\n", compose_text)
        self.assertIn(f"{runtime_root}/wp-fixture:/var/www/html", compose_text)
        self.assertIn("      - db_data:/var/lib/mysql\n", compose_text)
        self.assertIn("\nvolumes:\n  db_data:\n", compose_text)

    def test_instance_block_detaches_normalized_config_and_omission_is_legacy(self):
        import sandbox.core._instances as instances
        from sandbox.config.php_extensions import normalize_php_extensions

        normalized = normalize_php_extensions({
            "profile": "wordpress@1",
            "extensions": {"gd": "2.3.*"},
        })
        pconf = {"root": "/tmp/project", "phpExtensions": normalized}
        with patch.object(instances, "_local_yaml", return_value={}):
            block = instances._build_instance_block(
                {}, "project", "/tmp/project", pconf,
                {"wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125},
                "nginx",
            )
            legacy = instances._build_instance_block(
                {}, "project", "/tmp/project", {"root": "/tmp/project"},
                {"wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125},
                "nginx",
            )

        self.assertEqual(block["php_extensions"]["profile"], "wordpress@1")
        self.assertEqual(block["php_extensions"]["extensions"]["gd"],
                         {"state": "enabled", "version": "2.3.*"})
        self.assertNotIn("php_extensions", legacy)
        json.dumps(block["php_extensions"])

    def test_profile_preflight_uses_catalogued_gd_plan_without_faking_digests(self):
        from sandbox.config.php_extensions import normalize_php_extensions
        from sandbox.core._docker import php_extension_preflight

        config = normalize_php_extensions({"profile": "wordpress@1"}).to_dict()
        digests = {"web": "sha256:" + "a" * 64,
                   "wpcli": "sha256:" + "b" * 64}
        result = php_extension_preflight({
            "server": "nginx", "php_version": "8.3",
            "platform": "linux", "architecture": "amd64",
            "php_extensions": config,
            "php_extension_parent_digests": digests,
        })
        self.assertEqual(result["readiness"], "ready")
        self.assertEqual(result["requirements"]["extensions"]["gd"], True)
        self.assertEqual(result["plan"]["parent_digests"], digests)

    def test_new_profile_resolves_parent_digests_materializes_and_builds_both_images(self):
        import sandbox.core._docker as docker

        old_home = os.environ.get("SANDBOX_HOME")
        with tempfile.TemporaryDirectory(prefix="sb-php-ext-home-") as home:
            os.environ["SANDBOX_HOME"] = home
            web_digest = "sha256:" + "a" * 64
            cli_digest = "sha256:" + "b" * 64
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((tuple(argv), dict(kwargs)))
                command = tuple(argv)
                if command[:4] == ("docker", "image", "inspect", "--format"):
                    image = command[-1]
                    if image.startswith("sandbox/"):
                        # Child tags are absent before build, then available
                        # after their respective bounded build call.
                        build_call = next((item[0] for item in calls
                                           if item[0][:3] == ("docker", "build", "--quiet")
                                           and item[0][item[0].index("--tag") + 1] == image), None)
                        built = build_call is not None
                        if built:
                            provenance = json.loads(
                                (Path(build_call[-1]) / "provenance.json").read_text()
                            )
                            role = "wpcli" if "wpcli" in image else "web"
                            labels = {
                                "org.sandbox.php-extensions.digest": provenance["digest"],
                                "org.sandbox.php-extensions.role": role,
                                "org.sandbox.php-extensions.provenance": provenance["recipe_catalog_digest"],
                            }
                            stdout = json.dumps({
                                "Id": "sha256:" + "c" * 64,
                                "Config": {"Labels": labels},
                            })
                        else:
                            stdout = ""
                        return SimpleNamespace(
                            returncode=0 if built else 1,
                            stdout=stdout,
                            stderr="",
                        )
                    digest = web_digest if image.startswith("wordpress:php") else cli_digest
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(["wordpress@" + digest]),
                        stderr="",
                    )
                if command[:3] == ("docker", "build", "--quiet"):
                    return SimpleNamespace(returncode=0, stdout="sha256:" + "c" * 64,
                                           stderr="")
                raise AssertionError(command)

            config = {"profile": "wordpress@1"}
            with patch.object(docker, "run", side_effect=fake_run):
                prepared = docker.prepare_php_extension_runtime({
                    "server": "nginx", "php_version": "8.3",
                    "wordpress_image": "wordpress:php8.3-fpm",
                    "wpcli_image": "wordpress:cli-php8.3",
                    "php_extensions": config,
                    "platform": "linux", "architecture": "amd64",
                }, timeout=10)
            self.assertEqual(prepared["parent_digests"], {"web": web_digest, "wpcli": cli_digest})
            self.assertTrue((prepared["plan"].context_dir / "Dockerfile.web").is_file())
            self.assertTrue((prepared["plan"].context_dir / "Dockerfile.wpcli").is_file())
            builds = [call for call in calls if call[0][:3] == ("docker", "build", "--quiet")]
            self.assertEqual(len(builds), 2)
            self.assertTrue(all(call[1]["timeout"] == 10 for call in builds))
        if old_home is None:
            os.environ.pop("SANDBOX_HOME", None)
        else:
            os.environ["SANDBOX_HOME"] = old_home

    def test_observation_only_extensions_fail_before_any_docker_or_context_side_effect(self):
        import sandbox.core._docker as docker

        for name in ("imagick", "xdebug"):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="sb-php-ext-invalid-") as home:
                old_home = os.environ.get("SANDBOX_HOME")
                os.environ["SANDBOX_HOME"] = home
                try:
                    with patch.object(docker, "run") as run:
                        with self.assertRaisesRegex(ValueError, "no allowlisted v1 provisioning recipe"):
                            docker.prepare_php_extension_runtime({
                                "server": "nginx", "php_version": "8.3",
                                "wordpress_image": "wordpress:php8.3-fpm",
                                "wpcli_image": "wordpress:cli-php8.3",
                                "php_extensions": {"extensions": {name: True}},
                            }, timeout=3)
                        run.assert_not_called()
                    self.assertFalse((Path(home) / "runtime" / "build" / "php-extensions").exists())
                finally:
                    if old_home is None:
                        os.environ.pop("SANDBOX_HOME", None)
                    else:
                        os.environ["SANDBOX_HOME"] = old_home

    def test_retagged_child_is_rebuilt_and_requires_verified_receipt_labels(self):
        import sandbox.core._docker as docker
        from sandbox.php_extensions.compose_builder import (
            materialize_compose_extension_context,
            plan_compose_extension_images,
        )

        with tempfile.TemporaryDirectory(prefix="sb-php-ext-retag-") as home:
            old_home = os.environ.get("SANDBOX_HOME")
            os.environ["SANDBOX_HOME"] = home
            try:
                plan = plan_compose_extension_images(
                    {"extensions": {"gd": True}},
                    parent_image="wordpress:php8.3-fpm",
                    wpcli_image="wordpress:cli-php8.3",
                    parent_digest="sha256:" + "a" * 64,
                    wpcli_parent_digest="sha256:" + "b" * 64,
                    server="nginx", platform="linux", architecture="amd64",
                )
                context = materialize_compose_extension_context(plan)
                calls = []

                def fake_run(argv, **kwargs):
                    calls.append((tuple(argv), dict(kwargs)))
                    command = tuple(argv)
                    if command[:4] == ("docker", "image", "inspect", "--format"):
                        image = command[-1]
                        if image.startswith("sandbox/"):
                            role = "wpcli" if "wpcli" in image else "web"
                            built = any(item[0][:3] == ("docker", "build", "--quiet")
                                        and item[0][item[0].index("--tag") + 1] == image
                                        for item in calls)
                            if not built:
                                # A valid image ID alone is not a cache receipt;
                                # this simulates a manually retagged child.
                                return SimpleNamespace(
                                    returncode=0,
                                    stdout=json.dumps({"Id": "sha256:" + "d" * 64,
                                                       "Config": {"Labels": {
                                                           "org.sandbox.php-extensions.digest": "sha256:" + "e" * 64,
                                                           "org.sandbox.php-extensions.role": role,
                                                           "org.sandbox.php-extensions.provenance": "sha256:" + "f" * 64,
                                                       }}}),
                                    stderr="",
                                )
                            provenance = json.loads((context / "provenance.json").read_text())
                            return SimpleNamespace(
                                returncode=0,
                                stdout=json.dumps({"Id": "sha256:" + "c" * 64,
                                                   "Config": {"Labels": {
                                                       "org.sandbox.php-extensions.digest": plan.digest,
                                                       "org.sandbox.php-extensions.role": role,
                                                       "org.sandbox.php-extensions.provenance": provenance["recipe_catalog_digest"],
                                                   }}}),
                                stderr="",
                            )
                    if command[:3] == ("docker", "build", "--quiet"):
                        return SimpleNamespace(returncode=0, stdout="sha256:" + "c" * 64,
                                               stderr="")
                    raise AssertionError(command)

                with patch.object(docker, "run", side_effect=fake_run):
                    built = docker._build_php_extension_images(plan, timeout=4)
                self.assertEqual(set(built), {"web", "wpcli"})
                builds = [item for item in calls if item[0][:3] == ("docker", "build", "--quiet")]
                self.assertEqual(len(builds), 2)
                self.assertTrue(all(item[1]["timeout"] == 4 for item in builds))
            finally:
                if old_home is None:
                    os.environ.pop("SANDBOX_HOME", None)
                else:
                    os.environ["SANDBOX_HOME"] = old_home

    def test_missing_parent_digest_or_unsupported_parent_fails_before_build(self):
        import sandbox.core._docker as docker

        with patch.object(docker, "run") as run:
            run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="not found")
            with self.assertRaisesRegex(ValueError, "could not be pulled"):
                docker.prepare_php_extension_runtime({
                    "server": "nginx", "php_version": "8.3",
                    "wordpress_image": "wordpress:php8.3-fpm",
                    "wpcli_image": "wordpress:cli-php8.3",
                    "php_extensions": {"extensions": {"gd": True}},
                }, timeout=3)
            self.assertFalse(any(
                args and args[0][:3] == ("docker", "build", "--quiet")
                for args, _kwargs in run.call_args_list
            ))
        with patch.object(docker, "run") as run:
            with self.assertRaisesRegex(ValueError, "official wordpress"):
                docker.prepare_php_extension_runtime({
                    "server": "nginx", "php_version": "8.3",
                    "wordpress_image": "example.invalid/custom:latest",
                    "wpcli_image": "wordpress:cli-php8.3",
                    "php_extensions": {"extensions": {"gd": True}},
                }, timeout=3)
            run.assert_not_called()

    def test_apply_recreates_only_web_planes_and_preserves_db_uploads_contract(self):
        import sandbox.core._instances as instances
        import sandbox.core._docker as docker

        home = Path(tempfile.mkdtemp(prefix="sb-php-ext-apply-home-"))
        root = home / "project"
        root.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        runtime_root = home / "runtime"
        sentinel_paths = {
            root / "project-sentinel.txt": b"project stays mounted\n",
            runtime_root / "wp-fixture" / "wp-content" / "uploads" / "sentinel.txt": b"uploads stay mounted\n",
            runtime_root / "snapshots" / "fixture" / "install-baseline" / "db.sql": b"snapshot stays\n",
        }
        for path, value in sentinel_paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        sentinel_bytes = {path: path.read_bytes() for path in sentinel_paths}
        existing = {
            "instance": "fixture", "label": "default", "status": "ready",
            "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
            "server": "nginx",
        }
        pconf = {"root": str(root), "server": "nginx", "phpExtensions": {
            "profile": "wordpress@1", "extensions": {"gd": True},
        }}
        block = {"server": "nginx", "php_extensions": pconf["phpExtensions"]}
        compose_calls = []
        persisted = {}
        registry_calls = []
        extension_identity = {
            "php_extension_parent_digests": {
                "web": "sha256:" + "a" * 64,
                "wpcli": "sha256:" + "b" * 64,
            },
            "php_extension_parent_images": {
                "web": "wordpress:php8.3-fpm",
                "wpcli": "wordpress:cli-php8.3",
            },
            "php_extension_digest": "sha256:" + "c" * 64,
            "platform": "linux",
            "architecture": "amd64",
        }

        class FakeCore:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_project, label=None):
                return pconf

            @staticmethod
            def registry_get(_root, label="default"):
                return existing

            @staticmethod
            def registry_list_for_root(_root):
                return [existing]

            @staticmethod
            def registry_put(_root, **fields):
                registry_calls.append(dict(fields))
                return {**existing, **fields}

            @staticmethod
            @contextmanager
            def project_lock(_root):
                yield

        resolved = {"fixture": {
            "server": "nginx", "php_extensions": block["php_extensions"],
            "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
        }}
        fake_prepared = {"plan": SimpleNamespace(
            web=SimpleNamespace(parent_image="wordpress:php8.3-fpm"),
            wpcli=SimpleNamespace(parent_image="wordpress:cli-php8.3"),
            digest="sha256:" + "c" * 64,
            platform="linux", architecture="amd64",
        ), "parent_digests": {
            "web": "sha256:" + "a" * 64,
            "wpcli": "sha256:" + "b" * 64,
        }}

        def persist_extensions(target, _prepared):
            target.update(extension_identity)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"SANDBOX_HOME": str(home)}))
            stack.enter_context(patch.object(instances, "RUNTIME_DIR", runtime_root))
            stack.enter_context(patch.object(docker, "RUNTIME_DIR", runtime_root))
            stack.enter_context(patch.object(instances, "_core", return_value=FakeCore()))
            stack.enter_context(patch.object(
                instances, "_local_yaml", return_value={"instances": {"fixture": {}}}))
            stack.enter_context(patch.object(
                instances, "_write_local_yaml",
                side_effect=lambda value: persisted.update(value),
            ))
            stack.enter_context(patch.object(instances, "_build_instance_block", return_value=block))
            stack.enter_context(patch.object(
                instances, "prepare_php_extension_runtime", return_value=fake_prepared))
            stack.enter_context(patch.object(
                instances, "_persist_php_extension_runtime",
                side_effect=persist_extensions,
            ))
            stack.enter_context(patch.object(instances, "load_config", return_value={}))
            stack.enter_context(patch.object(instances, "write_compose_files"))
            stack.enter_context(patch.object(instances, "resolve_instances", return_value=resolved))
            stack.enter_context(patch.object(
                instances, "compose",
                side_effect=lambda *args, **kwargs: compose_calls.append((args, kwargs)),
            ))
            wait_reachable = stack.enter_context(
                patch.object(instances, "_wait_reachable", return_value=True))
            stack.enter_context(patch.object(
                instances, "php_extension_status", return_value={"drift": {"state": "ready"}}))
            stack.enter_context(patch.object(instances, "_wire_project_plugins"))
            stack.enter_context(patch.object(instances, "_wire_project_themes"))
            stack.enter_context(patch.object(instances, "_reconcile_wp_core", return_value={}))
            stack.enter_context(patch.object(
                instances, "site_url", return_value="http://localhost:8188"))
            for helper in (
                "_write_mail_muplugin", "_write_dl_cache_muplugin",
                "_write_ondemand_muplugin", "_write_host_runtime_muplugins",
                "_write_licensing_muplugin", "_remove_obsolete_builder_authoring_assets",
            ):
                stack.enter_context(patch.object(instances, helper))
            result = instances.apply_config({}, str(root))

        self._assert_named_db_volume_contract(docker, runtime_root, root)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(compose_calls)
        argv = compose_calls[0][0]
        self.assertEqual(argv[:4], ("up", "-d", "--no-deps", "--force-recreate"))
        self.assertIn("wp", argv)
        self.assertIn("nginx", argv)
        self.assertNotIn("db", argv)
        self.assertNotIn("mailpit", argv)
        for args, _kwargs in compose_calls:
            self.assertNotIn("down", args)
            self.assertNotIn("-v", args)
        # The in-place apply command owns only web services; DB and uploads
        # are never passed to a destructive volume-removal command.
        self.assertEqual(persisted["instances"]["fixture"], block)
        self.assertEqual(
            persisted["instances"]["fixture"]["php_extension_digest"],
            extension_identity["php_extension_digest"],
        )
        self.assertEqual(result["instance"], existing["instance"])
        self.assertEqual(result["label"], existing["label"])
        self.assertEqual(registry_calls[-1]["instance"], existing["instance"])
        self.assertEqual(registry_calls[-1]["label"], existing["label"])
        for path, expected in sentinel_bytes.items():
            self.assertTrue(path.exists(), path)
            self.assertEqual(path.read_bytes(), expected, path)
        wait_reachable.assert_called_once_with(resolved["fixture"])

    def test_herd_apply_reconciles_host_runtime_muplugins_without_compose(self):
        import sandbox.core._instances as instances

        root = Path(tempfile.mkdtemp(prefix="sb-herd-apply-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        existing = {
            "instance": "fixture", "label": "default", "status": "ready",
            "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
            "server": "herd",
        }
        pconf = {"root": str(root), "server": "herd"}
        block = {"server": "herd"}

        class FakeCore:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_project, label=None):
                return pconf

            @staticmethod
            def registry_get(_root, label="default"):
                return existing

            @staticmethod
            def registry_list_for_root(_root):
                return [existing]

            @staticmethod
            def registry_put(_root, **fields):
                return {**existing, **fields}

            @staticmethod
            @contextmanager
            def project_lock(_root):
                yield

        resolved = {"fixture": {
            "server": "herd", "wordpress_port": 8188,
            "db_port": 3318, "mailpit_port": 8125,
        }}
        with patch.object(instances, "_core", return_value=FakeCore()), \
                patch.object(instances, "_capture_apply_rollback_state", return_value={}), \
                patch.object(instances, "_local_yaml", return_value={"instances": {"fixture": {}}}), \
                patch.object(instances, "_write_local_yaml"), \
                patch.object(instances, "_build_instance_block", return_value=block), \
                patch.object(instances, "prepare_php_extension_runtime", return_value=None), \
                patch.object(instances, "load_config", return_value={}), \
                patch.object(instances, "write_compose_files"), \
                patch.object(instances, "resolve_instances", return_value=resolved), \
                patch.object(instances, "compose") as compose, \
                patch.object(instances, "_pin_wp_constants_in_config"), \
                patch.object(instances, "wp_dir", return_value=root), \
                patch.object(instances, "_write_host_runtime_muplugins") as host_plugins, \
                patch.object(instances, "_remove_obsolete_builder_authoring_assets"), \
                patch.object(instances, "_wire_project_plugins"), \
                patch.object(instances, "_wire_project_themes"), \
                patch.object(instances, "_reconcile_wp_core", return_value={}), \
                patch.object(instances, "site_url", return_value="https://fixture.test"):
            result = instances.apply_config({}, str(root))

        self.assertEqual(result["status"], "ready")
        host_plugins.assert_called_once_with("fixture")
        compose.assert_not_called()

    def test_ensure_prepares_child_images_before_compose_up_and_verifies_planes(self):
        import sandbox.core._instances as instances
        import sandbox.commands.data as data_commands
        import sandbox.commands.lifecycle as lifecycle

        root = Path(tempfile.mkdtemp(prefix="sb-php-ext-ensure-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        pconf = {"root": str(root), "server": "nginx", "phpExtensions": {
            "profile": "wordpress@1", "extensions": {"gd": True},
        }}
        block = {"server": "nginx", "php_extensions": pconf["phpExtensions"]}
        events = []

        class FakeCore:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_project, label=None):
                return pconf

            @staticmethod
            def registry_get(_root, label="default"):
                return None

            @staticmethod
            def registry_all():
                return {}

            @staticmethod
            @contextmanager
            def project_lock(_root):
                yield

            @staticmethod
            def registry_put(_root, **fields):
                return {"instance": fields.get("instance", "fixture"), **fields}

        resolved = {"fixture": {
            "server": "nginx", "php_extensions": block["php_extensions"],
            "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
            "domain": None,
        }}
        fake_prepared = {"plan": SimpleNamespace(
            web=SimpleNamespace(parent_image="wordpress:php8.3-fpm"),
            wpcli=SimpleNamespace(parent_image="wordpress:cli-php8.3"),
            digest="sha256:" + "c" * 64,
            platform="linux", architecture="amd64",
        ), "parent_digests": {
            "web": "sha256:" + "a" * 64,
            "wpcli": "sha256:" + "b" * 64,
        }}
        local = {"instances": {"fixture": {"autologin_token": "opaque"}}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(instances, "_core", return_value=FakeCore()))
            stack.enter_context(patch.object(instances, "_local_yaml", return_value=local))
            stack.enter_context(patch.object(instances, "_write_local_yaml"))
            stack.enter_context(patch.object(instances, "_resolve_port_conflicts", return_value={}))
            stack.enter_context(patch.object(instances, "resolve_instances", return_value=resolved))
            stack.enter_context(patch.object(instances, "_derive_instance_name", return_value="fixture"))
            stack.enter_context(patch.object(instances, "_pick_instance_ports", return_value={
                "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125}))
            stack.enter_context(patch.object(instances, "_build_instance_block", return_value=block))
            stack.enter_context(patch.object(instances, "prepare_php_extension_runtime", side_effect=lambda *_a, **_k: events.append("prepare") or fake_prepared))
            stack.enter_context(patch.object(instances, "_persist_php_extension_runtime", side_effect=lambda *_a, **_k: events.append("persist")))
            stack.enter_context(patch.object(instances, "load_config", return_value={}))
            stack.enter_context(patch.object(instances, "write_compose_files", side_effect=lambda *_a, **_k: events.append("compose-file")))
            stack.enter_context(patch.object(lifecycle, "cmd_up", side_effect=lambda *_a, **_k: events.append("up")))
            stack.enter_context(patch.object(lifecycle, "cmd_install", side_effect=lambda *_a, **_k: events.append("install")))
            stack.enter_context(patch.object(instances, "_wait_http"))
            stack.enter_context(patch.object(instances, "php_extension_status", return_value={"drift": {"state": "ready"}}))
            stack.enter_context(patch.object(
                instances, "_wire_project_plugins",
                side_effect=lambda *_a, **_k: events.append("plugins"),
            ))
            stack.enter_context(patch.object(
                instances, "_wire_project_themes",
                side_effect=lambda *_a, **_k: events.append("themes"),
            ))
            stack.enter_context(patch.object(instances, "_multisite_mode", return_value=False))
            stack.enter_context(patch.object(instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(patch.object(instances, "site_url", return_value="http://localhost:8188"))
            capture = stack.enter_context(patch.object(
                data_commands, "capture_install_snapshots",
                side_effect=lambda *_a, **_k: events.append("snapshots"),
            ))
            stack.enter_context(patch.object(instances, "info"))
            # A few legacy helpers invoke the CLI entrypoint when their
            # dependency discovery is not stubbed. Keep the harness argv
            # neutral so this regression remains about ensure ordering rather
            # than unittest's module selector being parsed as an sb command.
            stack.enter_context(patch.object(sys, "argv", ["sb-test"]))
            result = instances.ensure_instance({}, str(root))

        self.assertEqual(result["status"], "ready")
        self.assertLess(events.index("prepare"), events.index("up"))
        self.assertLess(events.index("persist"), events.index("compose-file"))
        self.assertLess(events.index("compose-file"), events.index("up"))
        self.assertLess(events.index("install"), events.index("plugins"))
        self.assertLess(events.index("plugins"), events.index("themes"))
        self.assertLess(events.index("themes"), events.index("snapshots"))
        capture.assert_called_once_with("fixture")

    def test_ready_ensure_does_not_recapture_install_snapshots(self):
        """The ready fast path must preserve existing install restore points."""
        import sandbox.core._instances as instances
        import sandbox.commands.data as data_commands

        root = Path(tempfile.mkdtemp(prefix="sb-install-snapshot-idempotency-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        existing = {
            "instance": "fixture", "label": "default", "status": "ready",
            "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
            "server": "nginx", "url": "https://fixture.tst",
        }

        class FakeCore:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_project, label=None):
                return {"root": str(root), "server": "nginx"}

            @staticmethod
            def registry_get(_root, label="default"):
                return existing

            @staticmethod
            @contextmanager
            def project_lock(_root):
                yield

        with patch.object(instances, "_core", return_value=FakeCore()), \
                patch.object(instances, "_resolve_port_conflicts", return_value={}), \
                patch.object(instances, "resolve_instances", return_value={"fixture": existing}), \
                patch.object(instances, "_desired_source_mounts", return_value=["/plugins"]), \
                patch.object(instances, "attest_source_mounts", return_value={"ok": True}), \
                patch.object(instances, "_instance_reachable", return_value=True), \
                patch.object(instances, "_wp_core_install_state",
                             return_value=instances._WP_INSTALL_STATE_INSTALLED), \
                patch.object(instances, "_warn_version_drift"), \
                patch.object(instances, "_auto_heal_wp_url"), \
                patch.object(instances, "_refresh_registered_url", return_value=existing), \
                patch.object(data_commands, "capture_install_snapshots") as capture:
            result = instances.ensure_instance({}, str(root))

        self.assertEqual(result, existing)
        capture.assert_not_called()

    def test_apply_failed_verification_restores_state_compose_and_web_runtime(self):
        """A failed four-plane gate rolls back only the web tier.

        The second subcase also proves the operator-facing error is explicit
        when the rollback itself cannot reconcile the prior web services.
        """
        import sandbox.core._instances as instances
        import sandbox.core._docker as docker

        def run_case(*, rollback_ok, fail_before_runtime=False):
            home = Path(tempfile.mkdtemp(prefix="sb-php-ext-rollback-home-"))
            root = home / "project"
            root.mkdir()
            runtime_root = home / "runtime"
            sentinel_paths = {
                root / "project-sentinel.txt": b"project rollback marker\n",
                runtime_root / "wp-fixture" / "wp-content" / "uploads" / "sentinel.txt": b"uploads rollback marker\n",
                runtime_root / "snapshots" / "fixture" / "named" / "db.sql": b"snapshot rollback marker\n",
            }
            for path, value in sentinel_paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            sentinel_bytes = {path: path.read_bytes() for path in sentinel_paths}
            old_compose = b"old compose artifact\n"
            compose_path = root / "fixture.yml"
            compose_path.write_bytes(old_compose)
            existing = {
                "instance": "fixture", "label": "default", "status": "ready",
                "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
                "server": "nginx",
            }
            old_extension_identity = {
                "php_extension_parent_digests": {
                    "web": "sha256:" + "1" * 64,
                    "wpcli": "sha256:" + "2" * 64,
                },
                "php_extension_parent_images": {
                    "web": "wordpress:php8.2-fpm",
                    "wpcli": "wordpress:cli-php8.2",
                },
                "php_extension_digest": "sha256:" + "3" * 64,
                "platform": "linux",
                "architecture": "amd64",
            }
            new_extension_identity = {
                "php_extension_parent_digests": {
                    "web": "sha256:" + "a" * 64,
                    "wpcli": "sha256:" + "b" * 64,
                },
                "php_extension_parent_images": {
                    "web": "wordpress:php8.3-fpm",
                    "wpcli": "wordpress:cli-php8.3",
                },
                "php_extension_digest": "sha256:" + "c" * 64,
                "platform": "linux",
                "architecture": "arm64",
            }
            old_local = {"instances": {"fixture": {
                "server": "nginx", "wordpress_port": 8188,
                "db_port": 3318, "mailpit_port": 8125,
                "php_extensions": {"extensions": {"gd": True}},
                "keep": "prior",
            }}}
            old_local["instances"]["fixture"].update(old_extension_identity)
            current_local = {"value": old_local}
            writes = []
            compose_calls = []
            pconf = {"root": str(root), "server": "nginx", "phpExtensions": {
                "profile": "wordpress@1", "extensions": {"gd": True},
            }}
            block = {"server": "nginx", "php_extensions": pconf["phpExtensions"],
                     "keep": "new"}
            expected_runtime = {
                "server": "nginx", "wordpress_port": 8188,
                "db_port": 3318, "mailpit_port": 8125,
                "php_extensions": block["php_extensions"],
                **old_extension_identity,
            }

            class FakeCore:
                ConfigError = ValueError

                @staticmethod
                def load_project_config(_project, label=None):
                    return pconf

                @staticmethod
                def registry_get(_root, label="default"):
                    return existing

                @staticmethod
                def registry_list_for_root(_root):
                    return [existing]

                @staticmethod
                def registry_put(_root, **fields):
                    return {**existing, **fields}

                @staticmethod
                @contextmanager
                def project_lock(_root):
                    yield

            def read_local():
                return __import__("copy").deepcopy(current_local["value"])

            def write_local(value):
                writes.append(__import__("copy").deepcopy(value))
                current_local["value"] = __import__("copy").deepcopy(value)

            def write_compose(*_args, **_kwargs):
                compose_path.write_bytes(b"new compose artifact\n")
                if fail_before_runtime:
                    raise RuntimeError("compose generation failed")

            def fake_compose(*args, **kwargs):
                compose_calls.append((args, kwargs))
                if len(compose_calls) == 1:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if rollback_ok:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="rollback unavailable")

            fake_prepared = {"plan": SimpleNamespace(
                web=SimpleNamespace(parent_image="wordpress:php8.3-fpm"),
                wpcli=SimpleNamespace(parent_image="wordpress:cli-php8.3"),
                digest="sha256:" + "c" * 64,
                platform="linux", architecture="amd64",
            ), "parent_digests": {
                "web": "sha256:" + "a" * 64,
                "wpcli": "sha256:" + "b" * 64,
            }}

            def persist_extensions(target, _prepared):
                target.update(new_extension_identity)
            try:
                with ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, {"SANDBOX_HOME": str(home)}))
                    stack.enter_context(patch.object(instances, "RUNTIME_DIR", runtime_root))
                    stack.enter_context(patch.object(docker, "RUNTIME_DIR", runtime_root))
                    stack.enter_context(patch.object(instances, "_core", return_value=FakeCore()))
                    stack.enter_context(patch.object(instances, "_local_yaml", side_effect=read_local))
                    stack.enter_context(patch.object(instances, "_write_local_yaml", side_effect=write_local))
                    stack.enter_context(patch.object(instances, "compose_file", return_value=compose_path))
                    stack.enter_context(patch.object(instances, "load_config", return_value={}))
                    stack.enter_context(patch.object(instances, "resolve_instances", return_value={
                        "fixture": {"server": "nginx", "wordpress_port": 8188,
                                    "db_port": 3318, "mailpit_port": 8125,
                                    "php_extensions": block["php_extensions"],
                                    **old_extension_identity}}))
                    stack.enter_context(patch.object(instances, "_build_instance_block", return_value=block))
                    stack.enter_context(patch.object(instances, "prepare_php_extension_runtime", return_value=fake_prepared))
                    stack.enter_context(patch.object(
                        instances, "_persist_php_extension_runtime",
                        side_effect=persist_extensions,
                    ))
                    stack.enter_context(patch.object(instances, "write_compose_files", side_effect=write_compose))
                    stack.enter_context(patch.object(instances, "compose", side_effect=fake_compose))
                    stack.enter_context(patch.object(instances, "_wait_http",
                                                      side_effect=AssertionError(
                                                          "apply/rollback must use canonical reachability")))
                    wait_reachable = stack.enter_context(
                        patch.object(instances, "_wait_reachable", return_value=True))
                    stack.enter_context(patch.object(instances, "php_extension_status", return_value={
                        "drift": {"state": "drift", "issues": [{"message": "GD plane mismatch"}]}}))
                    stack.enter_context(patch.object(instances, "_wire_project_plugins"))
                    stack.enter_context(patch.object(instances, "_wire_project_themes"))
                    stack.enter_context(patch.object(instances, "_reconcile_wp_core", return_value={}))
                    stack.enter_context(patch.object(instances, "site_url", return_value="http://localhost:8188"))
                    stack.enter_context(patch.object(instances, "_write_mail_muplugin"))
                    stack.enter_context(patch.object(instances, "_write_dl_cache_muplugin"))
                    stack.enter_context(patch.object(instances, "_write_ondemand_muplugin"))
                    stack.enter_context(patch.object(instances, "_write_host_runtime_muplugins"))
                    stack.enter_context(patch.object(instances, "_write_licensing_muplugin"))
                    stack.enter_context(patch.object(instances, "_remove_obsolete_builder_authoring_assets"))
                    with self.assertRaisesRegex(ValueError, "rollback=(succeeded|failed)") as raised:
                        instances.apply_config({}, str(root))
                self._assert_named_db_volume_contract(docker, runtime_root, root)
                self.assertEqual(current_local["value"], old_local)
                self.assertEqual(compose_path.read_bytes(), old_compose)
                self.assertTrue(writes)
                self.assertEqual(
                    writes[0]["instances"]["fixture"]["php_extension_digest"],
                    new_extension_identity["php_extension_digest"],
                )
                for key, expected in old_extension_identity.items():
                    self.assertEqual(
                        current_local["value"]["instances"]["fixture"][key],
                        expected,
                    )
                for path, expected in sentinel_bytes.items():
                    self.assertTrue(path.exists(), path)
                    self.assertEqual(path.read_bytes(), expected, path)
                if fail_before_runtime:
                    self.assertEqual(compose_calls, [])
                    self.assertIn("before web reconcile", str(raised.exception))
                    self.assertIn("rollback=succeeded", str(raised.exception))
                else:
                    self.assertGreaterEqual(len(compose_calls), 2)
                    for args, kwargs in compose_calls:
                        self.assertEqual(args[:4], ("up", "-d", "--no-deps", "--force-recreate"))
                        self.assertIn("wp", args)
                        self.assertIn("nginx", args)
                        self.assertNotIn("db", args)
                        self.assertNotIn("mailpit", args)
                        self.assertNotIn("down", args)
                        self.assertNotIn("-v", args)
                    expected = "rollback=succeeded" if rollback_ok else "rollback=failed"
                    self.assertIn(expected, str(raised.exception))
                    self.assertEqual(len(wait_reachable.call_args_list),
                                     2 if rollback_ok else 1)
                    self.assertEqual(wait_reachable.call_args_list[0].args[0],
                                     expected_runtime)
                    if rollback_ok:
                        self.assertEqual(wait_reachable.call_args_list[1].args[0],
                                         wait_reachable.call_args_list[0].args[0])
                if fail_before_runtime:
                    self.assertEqual(wait_reachable.call_args_list, [])
            finally:
                __import__("shutil").rmtree(home, ignore_errors=True)

        run_case(rollback_ok=True)
        run_case(rollback_ok=False)
        run_case(rollback_ok=True, fail_before_runtime=True)

    def test_status_is_secret_free_and_explicitly_marks_unprobed_planes(self):
        from sandbox.core._docker import php_extension_status

        result = php_extension_status({
            "php_extensions": {"extensions": {"gd": True}},
        })
        self.assertEqual(set(result["observed"]), {"web", "cli", "exec", "phpunit"})
        self.assertTrue(all(row["state"] == "unavailable"
                            for row in result["observed"].values()))
        self.assertEqual(result["staleness"]["state"], "stale")
        self.assertNotIn("password", json.dumps(result).lower())

    def test_status_preserves_missing_and_discarded_cache_states_without_paths(self):
        import sandbox.core._docker as docker
        from sandbox.php_extensions.compose_builder import extension_build_root

        digest = "sha256:" + "a" * 64
        old_home = os.environ.get("SANDBOX_HOME")
        with tempfile.TemporaryDirectory(prefix="sb-php-ext-status-") as home:
            os.environ["SANDBOX_HOME"] = home
            config = {
                "php_extensions": {"extensions": {"gd": True}},
                "php_extension_digest": digest,
            }
            try:
                missing = docker.php_extension_status(config)
                self.assertEqual(missing["provenance"], {"state": "missing"})
                context = extension_build_root() / digest
                context.mkdir(parents=True, exist_ok=True)
                (context / ".discarded").write_text("operator discard\n")
                discarded = docker.php_extension_status(config)
                self.assertEqual(discarded["provenance"], {"state": "discarded"})
                serialized = json.dumps(discarded, sort_keys=True)
                self.assertNotIn(str(Path(home).resolve()), serialized)
                self.assertNotIn("operator discard", serialized)
            finally:
                if old_home is None:
                    os.environ.pop("SANDBOX_HOME", None)
                else:
                    os.environ["SANDBOX_HOME"] = old_home

    def test_running_status_executes_four_standalone_planes_with_bounded_argv(self):
        import sandbox.core._docker as docker

        calls = []

        def fake_compose(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=_probe_document(), stderr="")

        with patch.object(docker, "_is_herd_instance", return_value=False), \
                patch.object(docker, "compose", side_effect=fake_compose), \
                patch("sandbox.core._tests.compose", side_effect=fake_compose), \
                patch("sandbox.core._tests._managed_execution_gate", return_value=None):
            result = docker.php_extension_status(
                {"php_extensions": {"extensions": {"gd": True}}},
                instance="fixture", timeout=2,
            )

        self.assertEqual(len(calls), 4)
        commands = [call[0] for call in calls]
        self.assertTrue(any(command[:3] == ("exec", "-T", "wp") for command in commands))
        self.assertTrue(any("wpcli" in command and "--entrypoint" in command
                            for command in commands))
        self.assertTrue(any(command[command.index("--entrypoint") + 2] == "wp"
                            for command in commands if "--entrypoint" in command))
        self.assertEqual({call[1]["timeout"] for call in calls}, {2})
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["desired"]["catalog"]["revision"], 1)
        self.assertIn("resolution_digest", result["desired"])
        self.assertEqual(result["staleness"]["state"], "fresh")
        self.assertEqual(result["drift"]["state"], "ready")
        # The payload is fixed PHP source and does not load WordPress/project
        # code; container service names are not shell commands.
        from sandbox.php_extensions.probe import STANDALONE_PROBE_PAYLOAD
        self.assertNotIn("wp-load", STANDALONE_PROBE_PAYLOAD)
        self.assertNotIn("require", STANDALONE_PROBE_PAYLOAD.lower())
        self.assertFalse(any("WP_TESTS_DIR" in command for command in commands))
        self.assertFalse(any(any("-v" == item or item.startswith("-v")
                                 for item in command) for command in commands))

    def test_status_reports_genuine_unavailability_as_stale_without_faking_observation(self):
        import sandbox.core._docker as docker

        def fake_compose(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="Error: container is not running")

        with patch.object(docker, "_is_herd_instance", return_value=False), \
                patch.object(docker, "compose", side_effect=fake_compose), \
                patch("sandbox.core._tests.compose", side_effect=fake_compose), \
                patch("sandbox.core._tests._managed_execution_gate", return_value=None):
            result = docker.php_extension_status(
                {"php_extensions": {"extensions": {"gd": True}}},
                instance="fixture", timeout=1,
            )

        self.assertEqual(result["staleness"]["state"], "stale")
        self.assertTrue(all(row["state"] == "unavailable"
                            for row in result["observed"].values()))
        self.assertEqual(result["drift"]["state"], "unknown")

    def test_status_does_not_forward_untrusted_probe_dimensions(self):
        import sandbox.core._docker as docker

        document = json.loads(_probe_document())
        document["php_version"] = "https://user:pass@example.invalid/private"
        document["sapi"] = "cli; sh -c secret"
        document["extensions"]["gd"]["version"] = "/private/context/path"

        def fake_compose(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(document), stderr="")

        with patch.object(docker, "_is_herd_instance", return_value=False), \
                patch.object(docker, "compose", side_effect=fake_compose), \
                patch("sandbox.core._tests.compose", side_effect=fake_compose), \
                patch("sandbox.core._tests._managed_execution_gate", return_value=None):
            report = docker.php_extension_status(
                {"php_extensions": {"extensions": {"gd": True}}}, instance="fixture")

        output = json.dumps(report)
        self.assertNotIn("example.invalid", output)
        self.assertNotIn("sh -c", output)
        self.assertNotIn("/private/", output)
        for row in report["observed"].values():
            self.assertIsNone(row["php_version"])
            self.assertIsNone(row["sapi"])
            self.assertIsNone(row["extensions"]["gd"]["version"])

    def test_status_redacts_provider_corpus_across_every_public_string_boundary(self):
        import sandbox.core._docker as docker
        from tests.redaction_corpus import AWS, GITHUB, GOOGLE, OPENAI, SLACK
        from sandbox.php_extensions.probe import (
            ExtensionObservation, PlaneObservation, ProbeError, ProbeResult,
        )

        probes = {}
        for plane in ("web", "cli", "exec", "phpunit"):
            observation = PlaneObservation(
                plane, GITHUB,
                (ExtensionObservation("gd", True, SLACK, plane),),
                sapi=OPENAI,
            )
            probes[plane] = ProbeResult(
                False, plane, observation=observation,
                errors=(ProbeError("version_mismatch", "raw provider failure",
                                   plane=plane, extension="gd",
                                   expected=AWS, observed=GOOGLE),),
            )
        with patch.object(docker, "php_extension_probe", return_value=probes):
            report = docker.php_extension_status(
                {"php_extensions": {"extensions": {"gd": True}}}, instance="fixture")

        output = json.dumps(report)
        for forbidden in (AWS, GITHUB, GOOGLE, OPENAI, SLACK):
            self.assertNotIn(forbidden, output)
        self.assertEqual(report["desired"]["requirements"][0]["name"], "gd")
        self.assertEqual(report["issues"][0]["code"], "version_mismatch")

        profile = docker.php_extension_status({
            "php_extensions": {"profile": GITHUB, "extensions": {"gd": True}},
        })
        self.assertNotIn(GITHUB, json.dumps(profile))

    def test_status_omits_cache_recipe_ids_not_present_in_immutable_catalog(self):
        import sandbox.core._docker as docker
        from tests.redaction_corpus import GITHUB

        digest = "sha256:" + "a" * 64
        receipt = {
            "state": "ready",
            "provenance": {
                "digest": digest,
                "recipe_catalog_digest": "sha256:" + "b" * 64,
                "parent_digests": {"web": "sha256:" + "c" * 64,
                                   "wpcli": "sha256:" + "d" * 64},
                "recipe_ids": [GITHUB],
            },
        }
        with patch("sandbox.php_extensions.compose_builder.extension_cache_status",
                   return_value=receipt):
            report = docker.php_extension_status({
                "php_extensions": {"extensions": {"gd": True}},
                "php_extension_digest": digest,
            })
        self.assertNotIn("build_digest", report["desired"])
        self.assertEqual(report["provenance"], {"state": "stale"})
        self.assertNotIn(GITHUB, json.dumps(report))

    def test_status_blocks_version_mismatch_and_plane_drift(self):
        import sandbox.core._docker as docker

        index = {"value": 0}

        def fake_compose(*args, **kwargs):
            index["value"] += 1
            if index["value"] == 1:
                stdout = _probe_document(version="3.0.0")
            elif index["value"] == 2:
                stdout = _probe_document(php="8.2.9")
            else:
                stdout = _probe_document()
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        with patch.object(docker, "_is_herd_instance", return_value=False), \
                patch.object(docker, "compose", side_effect=fake_compose), \
                patch("sandbox.core._tests.compose", side_effect=fake_compose), \
                patch("sandbox.core._tests._managed_execution_gate", return_value=None):
            result = docker.php_extension_status(
                {"php_extensions": {"extensions": {"gd": "2.3.*"}}},
                instance="fixture", timeout=1,
            )

        codes = {item["code"] for item in result["issues"]}
        self.assertIn("version_mismatch", codes)
        self.assertIn("plane_drift", codes)
        self.assertEqual(result["staleness"]["state"], "fresh")
        self.assertEqual(result["drift"]["state"], "drift")

    def test_status_constructor_emits_only_stable_failure_codes_and_safe_fields(self):
        import sandbox.core._docker as docker
        from sandbox.php_extensions.probe import ProbeError, ProbeResult

        cases = {
            "missing": ProbeError("missing", "raw missing output", plane="web",
                                  extension="gd"),
            "version_mismatch": ProbeError("version_mismatch", "raw mismatch", plane="cli",
                                           extension="gd", expected="2.3.*", observed="3.0.0"),
            "version_unobservable": ProbeError("version_unobservable", "raw unavailable", plane="exec",
                                               extension="gd", expected="2.3.*"),
            "unsupported_provisioning": ProbeError("unsupported_provisioning", "raw package detail",
                                                   plane="phpunit", extension="xdebug"),
            "unsupported_disable": ProbeError("unsupported_disable", "raw ini detail",
                                              plane="web", extension="gd"),
            "plane_drift": ProbeError("plane_drift", "raw plane output", plane="cli",
                                      extension="gd"),
        }
        for expected, issue in cases.items():
            probes = {plane: ProbeResult(False, plane, errors=(
                issue if plane == issue.plane else ProbeError(
                    "probe_unavailable", "raw stderr", plane=plane),
            ), stderr="secret://context/path --shell") for plane in
                ("web", "cli", "exec", "phpunit")}
            with self.subTest(code=expected), \
                    patch.object(docker, "php_extension_probe", return_value=probes):
                report = docker.php_extension_status(
                    {"php_extensions": {"extensions": {"gd": True}}}, instance="fixture")
                codes = {row["code"] for row in report["issues"]}
                self.assertIn(expected, codes)
                output = json.dumps(report)
                self.assertNotIn("raw ", output)
                self.assertNotIn("secret://", output)
                self.assertNotIn("--shell", output)
                self.assertEqual(report["exit_code"], 1)

    def test_status_exposes_build_digest_only_for_complete_safe_read_only_receipt(self):
        import sandbox.core._docker as docker

        digest = "sha256:" + "a" * 64
        receipt = {
            "state": "ready",
            "provenance": {
                "digest": digest,
                "recipe_catalog_digest": "sha256:" + "b" * 64,
                "parent_digests": {"web": "sha256:" + "c" * 64,
                                   "wpcli": "sha256:" + "d" * 64},
                "recipe_ids": ["php-gd"],
                "parent_images": {"web": "https://user:pass@example.invalid/private"},
                "context_path": "/private/project/path",
                "commands": ["sh", "-c", "secret"],
            },
        }
        with patch("sandbox.php_extensions.compose_builder.extension_cache_status",
                   return_value=receipt):
            report = docker.php_extension_status({
                "php_extensions": {"extensions": {"gd": True}},
                "php_extension_digest": digest,
            })
        self.assertEqual(report["desired"]["build_digest"], digest)
        output = json.dumps(report)
        self.assertNotIn("example.invalid", output)
        self.assertNotIn("context_path", output)
        self.assertNotIn("commands", output)

        receipt["provenance"].pop("recipe_catalog_digest")
        with patch("sandbox.php_extensions.compose_builder.extension_cache_status",
                   return_value=receipt):
            incomplete = docker.php_extension_status({
                "php_extensions": {"extensions": {"gd": True}},
                "php_extension_digest": digest,
            })
        self.assertNotIn("build_digest", incomplete["desired"])

    def test_new_wordpress_init_emits_explicit_profile(self):
        import sandbox.commands.instances_cmd as command

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            captured = {}
            class _FakeCore:
                CONFIG_BASENAMES = ("sandbox.config.json",)
                DEFAULTS = {"slug": None, "plugins": {}, "themes": [],
                            "mappings": {}, "mappings_inactive": {},
                            "phpVersion": None, "wpVersion": None,
                            "multisite": False, "server": "nginx", "tld": "tst",
                            "config": {}, "port": None,
                            "tests": {"suite": "auto"}, "pluginCheck": {}}

                @staticmethod
                def load_project_config(_path):
                    return {"kind": "wordpress", "root": str(root),
                            "source": "defaults", "slug": "fixture"}

            entry = {"instance": "fixture", "url": "http://localhost:8188",
                     "root": str(root), "wordpress_port": 8188,
                     "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}
            with patch.object(command, "_core", return_value=_FakeCore()), \
                 patch.object(command, "ensure_instance", side_effect=lambda *_a, **_k: captured.update(entry) or entry), \
                 patch.object(command, "_provision_test_harness"):
                command.cmd_init({}, SimpleNamespace(
                    project_dir=str(root), type=None, force=False,
                    no_test_harness=True,
                ))
            document = json.loads((root / "sandbox.config.json").read_text())
            self.assertEqual(document["phpExtensions"], {"profile": "wordpress@1"})


if __name__ == "__main__":
    unittest.main()

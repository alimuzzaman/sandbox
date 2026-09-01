"""Final feature-022 compatibility matrix for the shipped config facade."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


FIXTURES = Path(__file__).parent / "fixtures" / "modularity" / "config"


class TestProjectConfigCompatibilityMatrix(unittest.TestCase):
    def test_global_project_override_and_label_precedence_uses_public_facade(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            global_path = root / "global.json"
            global_path.write_text((FIXTURES / "global.json").read_text())
            (root / "sandbox.config.json").write_text((FIXTURES / "project.json").read_text())
            (root / "sandbox.config.override.json").write_text((FIXTURES / "override.json").read_text())
            (root / "sandbox.config.qa.json").write_text((FIXTURES / "label-qa.json").read_text())

            with patch.dict(os.environ, {"SANDBOX_USER_CONFIG": str(global_path)}):
                result = sandbox_core.load_project_config(root, label="qa")

            self.assertEqual(result["kind"], "wordpress")
            self.assertEqual(result["phpVersion"], "8.3")
            self.assertEqual(result["wpVersion"], "6.7")
            self.assertEqual(result["config"]["GLOBAL_VALUE"], "global")
            self.assertEqual(result["config"]["PROJECT_VALUE"], "project")
            self.assertEqual(result["config"]["OVERRIDDEN_VALUE"], "override")
            self.assertEqual(result["config"]["INSTANCE_LABEL"], "qa")
            self.assertIn("query-monitor", result["plugins_resolved"])
            self.assertIn("fixture-plugin", result["plugins_resolved"])

    def test_legacy_wordpress_fixture_keeps_legacy_observables(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            document = json.loads((FIXTURES / "legacy-wordpress.json").read_text())
            (root / "sandbox.config.json").write_text(json.dumps(document))
            result = sandbox_core.load_project_config(root)

        self.assertEqual(result["kind"], "wordpress")
        self.assertEqual(result["slug"], "fixture-plugin")
        self.assertEqual(result["phpVersion"], "8.2")
        self.assertEqual(result["wpVersion"], "6.8")
        self.assertTrue(result["plugins_resolved"]["fixture-plugin"]["active"])
        self.assertTrue(result["plugins_resolved"]["query-monitor"]["active"])




import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestProjectConfig(unittest.TestCase):
    def test_explicit_nested_config_owns_family_and_beats_automatic_homes(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            selected = root / "plugin-config"
            selected.mkdir()
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "automatic"}))
            (selected / "sandbox.config.json").write_text(json.dumps({
                "slug": "explicit", "phpVersion": "8.1",
                "plugins": {"fixture": {"path": "plugins/fixture"}},
            }))
            (selected / "sandbox.config.override.json").write_text(json.dumps({
                "phpVersion": "8.2",
            }))
            (selected / "sandbox.config.qa.json").write_text(json.dumps({
                "wpVersion": "6.8",
            }))

            result = sandbox_core.load_project_config(
                root, label="qa", config_file="plugin-config/sandbox.config.json",
            )

        self.assertEqual(result["slug"], "explicit")
        self.assertEqual(result["phpVersion"], "8.2")
        self.assertEqual(result["wpVersion"], "6.8")
        self.assertEqual(result["root"], str(root))
        self.assertEqual(
            result["plugins_resolved"]["fixture"]["source"]["value"],
            "plugins/fixture",
        )

    def test_explicit_config_never_falls_back_or_mixes_families(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            selected = root / "config"
            selected.mkdir()
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "root"}))
            (root / "sandbox.config.override.json").write_text(json.dumps({"phpVersion": "8.4"}))
            (selected / "sandbox.config.json").write_text(json.dumps({"slug": "selected"}))
            result = sandbox_core.load_project_config(
                root, config_file=selected / "sandbox.config.json",
            )

        self.assertEqual(result["slug"], "selected")
        self.assertNotEqual(result["phpVersion"], "8.4")

    def test_explicit_config_rejects_override_and_label_symlink_escape(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp) / "project"
            selected = root / "config"
            selected.mkdir(parents=True)
            primary = selected / "sandbox.config.json"
            primary.write_text("{}")
            outside = Path(tmp) / "outside.json"
            outside.write_text("{}")
            for name in ("sandbox.config.override.json", "sandbox.config.qa.json"):
                sibling = selected / name
                sibling.symlink_to(outside)
                with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "explicit config family",
                ):
                    sandbox_core.load_project_config(
                        root, label="qa", config_file=primary,
                    )
                sibling.unlink()

    def test_explicit_config_rejects_symlinked_selected_parent(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp) / "project"
            real = root / "real-config"
            real.mkdir(parents=True)
            (real / "sandbox.config.json").write_text("{}")
            alias = root / "config"
            alias.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "directory must not be a symbolic link"):
                sandbox_core.load_project_config(
                    root, config_file=alias / "sandbox.config.json",
                )

    def test_explicit_config_rejects_escape_symlink_directory_and_wrong_basename(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "sandbox.config.json"
            outside.write_text("{}")
            linked = root / "sandbox.config.json"
            linked.symlink_to(outside)
            for value, message in (
                ("../sandbox.config.json", "inside the project root"),
                ("sandbox.config.json", "inside the project root"),
                ("config.json", "basename"),
            ):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                    sandbox_core.load_project_config(root, config_file=value)
            linked.unlink()
            linked.mkdir()
            with self.assertRaisesRegex(ValueError, "regular non-symbolic-link"):
                sandbox_core.load_project_config(root, config_file=linked)
            linked.rmdir()
            real = root / "real" / "sandbox.config.json"
            real.parent.mkdir()
            real.write_text("{}")
            linked.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "regular non-symbolic-link"):
                sandbox_core.load_project_config(root, config_file=linked)

    def test_shared_git_config_home_loads_complete_family_without_tree_writes(self):
        import sandbox_core
        from sandbox.config.descriptors import project_config_key

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            base = Path(tmp)
            root = base / "fresh-worktree"
            home = base / "sandbox-home"
            root.mkdir()
            (root / ".git").write_text("gitdir: /fixture/repo/.git/worktrees/fresh\n")
            with mock.patch("sandbox.config.descriptors._git_output") as git_output:
                git_output.side_effect = lambda _root, *args: (
                    "git@example.test:team/plugin.git"
                    if args[:2] == ("config", "--get") else None
                )
                key = project_config_key(root)
                shared = home / "projects" / key
                shared.mkdir(parents=True)
                (shared / "sandbox.config.json").write_text(json.dumps({
                    "slug": "shared-plugin", "phpVersion": "8.2",
                }))
                (shared / "sandbox.config.override.json").write_text(json.dumps({
                    "phpVersion": "8.3",
                }))
                (shared / "sandbox.config.qa.json").write_text(json.dumps({
                    "wpVersion": "6.8",
                }))
                before = set(root.iterdir())
                with mock.patch.dict(os.environ, {
                    "SANDBOX_HOME": str(home),
                    "SANDBOX_USER_CONFIG": str(base / "missing-global.json"),
                }):
                    result = sandbox_core.load_project_config(root, label="qa")

            self.assertEqual(result["slug"], "shared-plugin")
            self.assertEqual(result["phpVersion"], "8.3")
            self.assertEqual(result["wpVersion"], "6.8")
            self.assertEqual(set(root.iterdir()), before)
            self.assertEqual(result["root"], str(root))

    def test_in_tree_primary_takes_priority_over_shared_git_config(self):
        import sandbox_core
        from sandbox.config.descriptors import project_config_key

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            base = Path(tmp)
            root = base / "worktree"
            home = base / "sandbox-home"
            root.mkdir()
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "in-tree"}))
            with mock.patch("sandbox.config.descriptors._git_output") as git_output:
                git_output.return_value = "git@example.test:team/plugin.git"
                shared = home / "projects" / project_config_key(root)
                shared.mkdir(parents=True)
                (shared / "sandbox.config.json").write_text(json.dumps({"slug": "shared"}))
                with mock.patch.dict(os.environ, {
                    "SANDBOX_HOME": str(home),
                    "SANDBOX_USER_CONFIG": str(base / "missing-global.json"),
                }):
                    result = sandbox_core.load_project_config(root)

            self.assertEqual(result["slug"], "in-tree")

    def test_project_config_key_falls_back_to_git_common_dir(self):
        from sandbox.config.descriptors import project_config_key

        roots = (Path("/tmp/repo"), Path("/tmp/worktree"))
        with mock.patch("sandbox.config.descriptors._git_output") as git_output:
            git_output.side_effect = lambda _root, *args: (
                None if args[:2] == ("config", "--get") else "/srv/source/plugin/.git"
            )
            self.assertEqual(project_config_key(roots[0]), project_config_key(roots[1]))

    def test_relative_origins_use_distinct_git_common_directory_keys(self):
        from sandbox.config.descriptors import project_config_key

        roots = (Path("/tmp/one/repo"), Path("/tmp/two/repo"))
        with mock.patch("sandbox.config.descriptors._git_output") as git_output:
            def output(root, *args):
                if args[:2] == ("config", "--get"):
                    return "../upstream.git"
                return str(root / ".git")

            git_output.side_effect = output
            self.assertNotEqual(project_config_key(roots[0]), project_config_key(roots[1]))

    def test_shared_config_rejects_symlinked_home_and_family_files(self):
        from sandbox.config.descriptors import config_home, project_config_key

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            base = Path(tmp)
            root = base / "worktree"
            home = base / "sandbox-home"
            root.mkdir()
            (root / ".git").mkdir()
            with mock.patch("sandbox.config.descriptors._git_output") as git_output:
                git_output.return_value = "git@example.test:team/plugin.git"
                shared = home / "projects" / project_config_key(root)
                sibling = home / "projects" / "other-repo"
                sibling.mkdir(parents=True)
                (sibling / "sandbox.config.json").write_text("{}")
                shared.symlink_to(sibling, target_is_directory=True)
                with mock.patch.dict(os.environ, {"SANDBOX_HOME": str(home)}):
                    with self.assertRaisesRegex(ValueError, "symbolic links"):
                        config_home(root)

                shared.unlink()
                shared.mkdir()
                outside = base / "outside.json"
                outside.write_text("{}")
                for name in (
                    "sandbox.config.json",
                    "sandbox.config.override.json",
                    "sandbox.config.qa.json",
                ):
                    for existing in shared.iterdir():
                        existing.unlink()
                    # A real primary selects the shared family when the layer
                    # under test is an override or label file.
                    (shared / "sandbox.config.json").write_text("{}")
                    if name != "sandbox.config.json":
                        (shared / name).symlink_to(outside)
                    else:
                        (shared / name).unlink()
                        (shared / name).symlink_to(outside)
                    with self.subTest(name=name), mock.patch.dict(
                        os.environ, {"SANDBOX_HOME": str(home)}
                    ):
                        with self.assertRaisesRegex(ValueError, "symbolic link"):
                            config_home(root)

    def test_bare_wordpress_plugin_root_gets_header_slug_self_mapping(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "worktree-entry.php").write_text(
                "<?php\n/**\n * Plugin Name: Fixture\n * Text Domain: fixture-plugin\n */\n",
            )
            result = sandbox_core.load_project_config(root)

        self.assertTrue(result["source"].endswith("defaults"))
        self.assertEqual(result["slug"], "fixture-plugin")
        self.assertEqual(
            result["plugins_resolved"]["fixture-plugin"]["source"],
            {"kind": "path", "value": "."},
        )
        self.assertTrue(result["plugins_resolved"]["fixture-plugin"]["active"])

    def test_bare_non_plugin_root_does_not_get_a_synthetic_plugin_mapping(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "application.php").write_text("<?php echo 'not a plugin';\n")
            result = sandbox_core.load_project_config(root)

        self.assertIsNone(result["slug"])
        self.assertNotIn(root.name, result["plugins_resolved"])

    def test_conventional_config_home_loads_complete_compose_family(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            home = root / ".config" / "sandbox"
            home.mkdir(parents=True)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            (home / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml", "service": "web",
                    "internal_port": 80, "health_path": "/primary",
                },
            }))
            (home / "sandbox.config.override.json").write_text(json.dumps({
                "compose": {"health_path": "/override"},
            }))
            (home / "sandbox.config.qa.json").write_text(json.dumps({
                "compose": {"health_path": "/qa"},
            }))

            from sandbox.config.facade import resolve_project_config
            result = resolve_project_config(root, label="qa", legacy_loader=Mock())

        self.assertEqual(result["kind"], "compose")
        self.assertEqual(result["health_path"], "/qa")

    def test_duplicate_primary_config_homes_fail_closed(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            home = root / ".config" / "sandbox"
            home.mkdir(parents=True)
            descriptor = {
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml", "service": "web",
                    "internal_port": 80, "health_path": "/",
                },
            }
            (root / "sandbox.config.json").write_text(json.dumps(descriptor))
            (home / "sandbox.config.json").write_text(json.dumps(descriptor))

            with self.assertRaisesRegex(ValueError, "ambiguous Sandbox project configuration"):
                resolve_project_config(root, legacy_loader=Mock())

    def test_nested_invocation_from_conventional_home_keeps_project_root(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            home = root / ".config" / "sandbox"
            nested = home / "nested"
            nested.mkdir(parents=True)
            (home / "sandbox.config.json").write_text(json.dumps({"slug": "nested-plugin"}))
            self.assertEqual(sandbox_core.find_project_root(nested), root)

    def test_wordpress_test_suite_config_is_validated(self):
        from sandbox.config.facade import resolve_project_config

        for suite in ("auto", "unit", "integration"):
            with self.subTest(suite=suite):
                legacy = mock.Mock(return_value={"tests": {"suite": suite}})
                result = resolve_project_config(Path("/tmp/project"), legacy_loader=legacy)
                self.assertEqual(result["tests"], {"suite": suite})

    def test_wordpress_test_suite_config_rejects_malformed_values(self):
        from sandbox.config.facade import resolve_project_config

        for tests in (None, [], {"suite": "coverage"}, {"suite": 1}):
            with self.subTest(tests=tests), self.assertRaises(ValueError):
                resolve_project_config(
                    Path("/tmp/project"),
                    legacy_loader=mock.Mock(return_value={"tests": tests}),
                )

    def test_legacy_config_defaults_to_wordpress_without_generic_normalization(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "legacy-plugin"}))
            legacy = mock.Mock(return_value={"slug": "legacy-plugin", "wordpress_port": 8192})

            result = resolve_project_config(root, legacy_loader=legacy)

            self.assertEqual(result["kind"], "wordpress")
            self.assertEqual(result["wordpress_port"], 8192)
            legacy.assert_called_once_with(root, label=None)

    def test_explicit_compose_descriptor_uses_declared_service_and_health_probe(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory(suffix=".project") as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: { web: { image: nginx:alpine } }\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml",
                    "service": "web",
                    "internal_port": 80,
                    "health_path": "/healthz",
                },
            }))
            legacy = mock.Mock(side_effect=AssertionError("legacy WordPress loader called"))

            result = resolve_project_config(root, legacy_loader=legacy)

            self.assertEqual(result["kind"], "compose")
            self.assertEqual(result["compose_file"], str(root / "compose.yaml"))
            self.assertEqual(result["service"], "web")
            self.assertEqual(result["internal_port"], 80)
            self.assertEqual(result["health_path"], "/healthz")
            legacy.assert_not_called()

    def test_compose_descriptor_rejects_path_outside_project_root(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "../outside.yaml",
                    "service": "web",
                    "internal_port": 80,
                    "health_path": "/",
                },
            }))

            with self.assertRaisesRegex(ValueError, "compose file.*project root"):
                resolve_project_config(root, legacy_loader=mock.Mock())

    def test_compose_descriptor_rejects_unsafe_route_fields(self):
        from sandbox.config.facade import resolve_project_config

        cases = (
            {"service": "web\nbad"},
            {"health_path": "/health\x00z"},
            {"http_port": True},
            {"http_port": 70000},
        )
        for changes in cases:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                descriptor = {
                    "kind": "compose", "compose": {
                        "file": "compose.yaml", "service": "web", "internal_port": 80,
                        "health_path": "/healthz",
                    },
                }
                descriptor["compose"].update(changes)
                (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
                (root / "sandbox.config.json").write_text(json.dumps(descriptor))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    resolve_project_config(root, legacy_loader=mock.Mock())

    def test_compose_descriptor_rejects_unsafe_label_override(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose", "compose": {
                    "file": "compose.yaml", "service": "web", "internal_port": 80,
                    "health_path": "/",
                },
            }))
            with self.assertRaisesRegex(ValueError, "label"):
                resolve_project_config(root, label="../escape", legacy_loader=mock.Mock())

    def test_dot_named_project_and_label_override_preserve_display_name(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory(suffix=".site") as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: { web: { image: nginx:alpine } }\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml",
                    "service": "web",
                    "internal_port": 80,
                    "health_path": "/",
                },
            }))
            (root / "sandbox.config.preview.json").write_text(json.dumps({
                "compose": {"health_path": "/preview-health"},
            }))

            result = resolve_project_config(root, label="preview", legacy_loader=mock.Mock())

            self.assertEqual(result["display_name"], root.name)
            self.assertEqual(result["label"], "preview")
            self.assertEqual(result["health_path"], "/preview-health")




if __name__ == "__main__":
    unittest.main()

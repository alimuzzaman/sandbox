"""Focused coverage for WordPress bootstrap lifecycle helpers."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.lifecycle as lifecycle  # noqa: E402


class TestWordPressCoreDownload(unittest.TestCase):
    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=False)
    def test_download_repairs_docroot_ownership_first(self, _is_herd, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]

        lifecycle._download_wordpress_core("preview-demo", args)

        compose.assert_called_once_with(
            "exec", "-T", "wp", "chown", "-R", "www-data:www-data",
            "/var/www/html", instance="preview-demo", check=True,
        )
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )

    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=True)
    def test_download_keeps_herd_on_its_host_path(self, _is_herd, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]

        lifecycle._download_wordpress_core("preview-demo", args)

        compose.assert_not_called()
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )


class TestMuPluginDirectoryPreparation(unittest.TestCase):
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=False)
    def test_docker_prepares_only_the_mu_plugin_directory(self, _is_herd, compose):
        lifecycle._prepare_mu_plugin_directory("preview-demo")

        compose.assert_called_once_with(
            "exec", "-T", "wp", "sh", "-c",
            "mkdir -p /var/www/html/wp-content/mu-plugins && "
            "chown -R www-data:www-data /var/www/html/wp-content/mu-plugins && "
            "chmod -R a+rwX /var/www/html/wp-content/mu-plugins",
            instance="preview-demo", check=True,
        )

    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=True)
    def test_herd_does_not_need_container_permission_repair(self, _is_herd, compose):
        lifecycle._prepare_mu_plugin_directory("preview-demo")

        compose.assert_not_called()

"""render_compose tests — the per-instance docker-compose generator.

Builds a real merged instance config via resolve_instances against an isolated
SANDBOX_RUNTIME registry (so merged() fills every default), then asserts the
rendered YAML carries the right image/ports/volumes and the host-gateway
extra_hosts (the spec-002 container→host reachability fix).
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core as core  # noqa: E402
import sandbox_core  # noqa: E402


class TestRenderCompose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-compose-")
        self.old = os.environ.get("SANDBOX_RUNTIME")
        os.environ["SANDBOX_RUNTIME"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SANDBOX_RUNTIME", None)
        else:
            os.environ["SANDBOX_RUNTIME"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, server):
        root = str(Path(self.tmp) / f"proj-{server}")
        sandbox_core.registry_put(root, instance="ti", wordpress_port=8300,
                                  db_port=3500, mailpit_port=8300, server=server)
        return core.resolve_instances({})["ti"]

    def _render(self, server, extra_mount=None):
        cfg = self._cfg(server)
        if extra_mount:
            cfg["extra_mounts"] = [extra_mount]
        return core.render_compose("ti", cfg, Path("/tmp/plugins-host"))

    def _document_with_source(self, server):
        source = f"/tmp/local-source-{server}"
        return yaml.safe_load(self._render(server, source)), source

    def test_apache_compose(self):
        out = self._render("apache")
        self.assertIn("8300:80", out)                       # published WP port
        self.assertIn("wordpress:", out)                    # WP image
        self.assertIn("runtime/wp-ti", out)                 # per-instance WP dir
        self.assertIn("host.docker.internal:host-gateway", out)  # spec-002 reachability
        self.assertIn("mariadb", out.lower() + "")          # db service image family
        self.assertIn("wp-content/plugins", out)             # wp.org installs need a writable destination
        self.assertIn("chown www-data:www-data /var/www/html/wp-content", out)
        self.assertIn("chmod 0777 /var/www/html/wp-content", out)
        for svc in ("wp:", "db:", "mailpit:"):
            self.assertIn(svc, out, f"missing service {svc}")

    def test_nginx_compose_has_fpm_and_nginx(self):
        out = self._render("nginx")
        self.assertIn("nginx:", out)                        # nginx sidecar service
        self.assertIn("-fpm", out)                          # php-fpm image flavor
        self.assertIn("host.docker.internal:host-gateway", out)
        self.assertIn("wp-content/plugins", out)             # wp.org installs need a writable destination
        self.assertIn("chown www-data:www-data /var/www/html/wp-content", out)
        self.assertIn("chmod 0777 /var/www/html/wp-content", out)
        self.assertIn("docker-entrypoint.sh php-fpm", out)  # FPM must repair permissions before serving

    def test_litespeed_compose(self):
        out = self._render("litespeed")
        self.assertIn("openlitespeed", out)
        self.assertIn("host.docker.internal:host-gateway", out)

    def test_local_source_mounts_are_read_only_for_all_wordpress_services(self):
        """Every generated local source bind is RO in each execution plane."""
        plugins_host = "/tmp/plugins-host"
        for server in ("apache", "nginx", "litespeed"):
            document, extra_source = self._document_with_source(server)
            services = document["services"]
            applicable = ["wp", "wpcli"]
            if server == "nginx":
                applicable.append("nginx")
            for service in applicable:
                volumes = services[service]["volumes"]
                for source in (plugins_host, extra_source):
                    self.assertIn(f"{source}:{source}:ro", volumes,
                                  f"{server}/{service} missing RO source")
                    self.assertNotIn(f"{source}:{source}", volumes,
                                     f"{server}/{service} has RW source")

    def test_runtime_state_and_cache_mount_modes_are_unchanged(self):
        """Source hardening does not make WordPress state or caches RO."""
        runtime = str(core.RUNTIME_DIR)
        for server in ("apache", "nginx", "litespeed"):
            document, _ = self._document_with_source(server)
            services = document["services"]
            wp = services["wp"]["volumes"]
            wpcli = services["wpcli"]["volumes"]
            docroot = core._server_runtime(server)["docroot"]
            self.assertIn(f"{runtime}/wp-ti:{docroot}", wp)
            self.assertNotIn(f"{runtime}/wp-ti:{docroot}:ro", wp)
            self.assertIn(f"{runtime}/seeds:/seeds", wp)
            self.assertIn(f"{runtime}/dl-cache/wp-http:/sandbox-dl-cache", wp)
            self.assertIn(f"{runtime}/wp-ti:{docroot}", wpcli)
            self.assertNotIn(f"{runtime}/wp-ti:{docroot}:ro", wpcli)
            self.assertIn(f"{runtime}/seeds:/seeds", wpcli)
            self.assertIn(f"{runtime}/dl-cache/wp-cli:/tmp/.wp-cli/cache", wpcli)
            self.assertEqual(services["db"]["volumes"], ["db_data:/var/lib/mysql"])
            if server == "nginx":
                self.assertIn(f"{runtime}/wp-ti:/var/www/html:ro",
                              services["nginx"]["volumes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

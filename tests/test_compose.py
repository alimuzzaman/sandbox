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

    def _render(self, server):
        return core.render_compose("ti", self._cfg(server), Path("/tmp/plugins-host"))

    def test_apache_compose(self):
        out = self._render("apache")
        self.assertIn("8300:80", out)                       # published WP port
        self.assertIn("wordpress:", out)                    # WP image
        self.assertIn("runtime/wp-ti", out)                 # per-instance WP dir
        self.assertIn("host.docker.internal:host-gateway", out)  # spec-002 reachability
        self.assertIn("mariadb", out.lower() + "")          # db service image family
        for svc in ("wp:", "db:", "mailpit:"):
            self.assertIn(svc, out, f"missing service {svc}")

    def test_nginx_compose_has_fpm_and_nginx(self):
        out = self._render("nginx")
        self.assertIn("nginx:", out)                        # nginx sidecar service
        self.assertIn("-fpm", out)                          # php-fpm image flavor
        self.assertIn("host.docker.internal:host-gateway", out)

    def test_litespeed_compose(self):
        out = self._render("litespeed")
        self.assertIn("openlitespeed", out)
        self.assertIn("host.docker.internal:host-gateway", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.hosted import HostedRecoveryMaterializer
from sandbox.recovery.planner import build_plan
from sandbox.transports.remote_recovery import (
    RegisteredRemoteRecoveryController,
)


def _inventory():
    return {
        "host_projects": ["amarsonar-bangla"],
        "runtime_environments": {"amarsonar-bangla": ["production"]},
        "managed_containers": [
            "sandbox-host-amarsonar-bangla-production-wordpress-1",
            "sandbox-host-amarsonar-bangla-production-db-1",
        ],
        "mounts": {
            "sandbox-host-amarsonar-bangla-production-wordpress-1": [
                {"type": "volume", "name": "sandbox-host-amarsonar-bangla-production_wordpress-root",
                 "destination": "/var/www/html", "rw": True},
                {"type": "volume", "name": "sandbox-host-amarsonar-bangla-production_wordpress-uploads",
                 "destination": "/var/www/html/wp-content/uploads", "rw": True},
            ],
            "sandbox-host-amarsonar-bangla-production-db-1": [
                {"type": "volume", "name": "sandbox-host-amarsonar-bangla-production_wordpress-db",
                 "destination": "/var/lib/mysql", "rw": True},
            ],
        },
        "repositories": {"amarsonar-bangla": {"head": "a" * 40, "branch": "master",
                                                "dirty_count": 0, "untracked_count": 0}},
    }


def _combined_archive() -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "database.sql"
        database.write_text("CREATE TABLE wp_test (id int);\n")
        wordpress = root / "wordpress.tar"
        with tarfile.open(wordpress, "w") as archive:
            payload = b"<?php echo 'ok';\n"
            member = tarfile.TarInfo("index.php")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        combined = root / "combined.tar"
        with tarfile.open(combined, "w") as archive:
            archive.add(database, arcname="database.sql", recursive=False)
            archive.add(wordpress, arcname="wordpress.tar", recursive=False)
        return combined.read_bytes()


class TestRegisteredRemoteRecoveryController(unittest.TestCase):
    def _controller(self, *, inventory=None, process=None):
        calls = []

        def lookup(name):
            return {"provisioned": True, "name": name}

        def ssh_run(_entry, command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess([], 0, "recovery-host\n", "")

        def ssh_process(_entry, command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess([], 0, process or _combined_archive(), b"")

        controller = RegisteredRemoteRecoveryController(
            remote_lookup=lookup, ssh_run=ssh_run, ssh_process=ssh_process,
            resolve_home=lambda _entry: "/home/recovery/sandbox",
            service_status=lambda _entry: {
                "installed_runtime_revision": "b" * 40,
                "runtime_revision_state": "match",
            },
            inventory=lambda _remote: inventory or _inventory(),
            environment={"SANDBOX_RECOVERY_DB_PASSWORD": "fixture-secret"},
        )
        return controller, calls

    def test_observation_is_bound_to_remote_revision_and_exact_sources(self):
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        plan = build_plan(catalog, ("amarsonar-bangla-prod",))
        controller, _calls = self._controller()
        observation = controller.observe("scaleway-sandbox", plan)
        observation.validate("scaleway-sandbox", plan)
        self.assertEqual(observation.binding.revision, "b" * 40)
        self.assertEqual(set(observation.covered_sources), set(plan.profiles))

    def test_missing_production_mount_fails_closed(self):
        inventory = _inventory()
        inventory["mounts"]["sandbox-host-amarsonar-bangla-production-db-1"] = []
        controller, _calls = self._controller(inventory=inventory)
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        with self.assertRaises(Exception) as caught:
            controller.observe("scaleway-sandbox", build_plan(catalog, ("amarsonar-bangla-prod",)))
        self.assertEqual(caught.exception.code, "remote_source_unavailable")

    def test_control_plane_capture_contains_only_nonsecret_declarations(self):
        controller, _calls = self._controller()
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        plan = build_plan(catalog, ("amarsonar-bangla-prod",))
        observation = controller.observe("scaleway-sandbox", plan)
        artifact = plan.artifacts[0]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            receipt = controller.capture("scaleway-sandbox", artifact, destination,
                                         observation.binding, "recovery-" + "0" * 64)
            payload = json.loads(receipt.files[0].read_text())
            self.assertNotIn("fixture-secret", receipt.files[0].read_text())
            self.assertEqual(payload["sources"], list(artifact.sources))
            self.assertEqual(receipt.native_format, "declarations")

    def test_wordpress_capture_uses_brokered_stdin_and_validates_native_archive(self):
        controller, calls = self._controller()
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        plan = build_plan(catalog, ("amarsonar-bangla-prod",))
        observation = controller.observe("scaleway-sandbox", plan)
        artifact = plan.artifacts[-1]
        with tempfile.TemporaryDirectory() as directory:
            receipt = controller.capture("scaleway-sandbox", artifact, Path(directory),
                                         observation.binding, "recovery-" + "1" * 64)
            self.assertEqual(receipt.native_format, "tar")
            self.assertTrue(receipt.files[0].is_file())
            self.assertNotIn("fixture-secret", calls[-1])
            self.assertIn("sandbox-host-amarsonar-bangla-production-db-1", calls[-1])
            self.assertIn("recovery-" + "1" * 64, calls[-1])

    def test_materializer_request_is_stable_for_controller(self):
        controller, _calls = self._controller()
        materializer = HostedRecoveryMaterializer(controller)
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        plan = build_plan(catalog, ("amarsonar-bangla-prod",))
        observation = controller.observe("scaleway-sandbox", plan)
        first = materializer._request_id("scaleway-sandbox", plan.artifacts[-1], observation.binding)
        second = materializer._request_id("scaleway-sandbox", plan.artifacts[-1], observation.binding)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

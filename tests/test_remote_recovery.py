import io
import hashlib
import json
import shlex
import sys
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
from tests.subprocess_support import run_test_process


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
        payload = process or _combined_archive()
        receipt = {}

        def lookup(name):
            return {"provisioned": True, "name": name}

        def ssh_run(_entry, command, **_kwargs):
            calls.append(command)
            if '.receipt.json' in command:
                return subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
            return subprocess.CompletedProcess([], 0, "recovery-host\n", "")

        def ssh_process(_entry, command, **_kwargs):
            calls.append(command)
            argv = shlex.split(command)
            receipt.update(schema_version=2, request_id=argv[5], backup_operation_id=argv[6],
                           source_digest=argv[8], artifact_sha256=hashlib.sha256(payload).hexdigest(),
                           started_at=1.0, completed_at=2.0)
            return subprocess.CompletedProcess([], 0, payload, b"")

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
                observation.binding, HostedRecoveryMaterializer._request_id(
                    "scaleway-sandbox", artifact, observation.binding, "set-a"),
                backup_operation_id="set-a")
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
        request_id = HostedRecoveryMaterializer._request_id(
            "scaleway-sandbox", artifact, observation.binding, "set-a")
        with tempfile.TemporaryDirectory() as directory:
            receipt = controller.capture("scaleway-sandbox", artifact, Path(directory),
                                         observation.binding, request_id, backup_operation_id="set-a")
            self.assertEqual(receipt.native_format, "tar")
            self.assertTrue(receipt.files[0].is_file())
            self.assertNotIn("fixture-secret", str(calls))
            self.assertIn("sandbox-host-amarsonar-bangla-production-db-1", calls[-2])
            self.assertIn(request_id, calls[-2])
            receipt.validate(artifact, request_id, "set-a")

    def test_materializer_request_is_stable_for_controller(self):
        controller, _calls = self._controller()
        materializer = HostedRecoveryMaterializer(controller)
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        plan = build_plan(catalog, ("amarsonar-bangla-prod",))
        observation = controller.observe("scaleway-sandbox", plan)
        first = materializer._request_id("scaleway-sandbox", plan.artifacts[-1], observation.binding, "set-a")
        second = materializer._request_id("scaleway-sandbox", plan.artifacts[-1], observation.binding, "set-a")
        self.assertEqual(first, second)

    def test_generated_capture_keeps_same_set_and_refreshes_distinct_sets(self):
        # Use the actual generated capture program and real private files/tars.
        # Only the container boundary is synthetic; no Docker or SSH is invoked.
        controller, calls = self._controller()
        plan = build_plan(load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json"),
                          ("amarsonar-bangla-prod",))
        observation = controller.observe("scaleway-sandbox", plan)
        artifact = plan.artifacts[-1]
        request = HostedRecoveryMaterializer._request_id("scaleway-sandbox", artifact,
                                                       observation.binding, "set-a")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller.capture("scaleway-sandbox", artifact, root, observation.binding,
                               request, backup_operation_id="set-a")
            program = shlex.split(calls[-2])[2]
            capture_root = root / "remote"
            counter = root / "count"
            counter.write_text("0")
            wordpress = io.BytesIO()
            with tarfile.open(fileobj=wordpress, mode="w") as archive:
                member = tarfile.TarInfo("./index.php")
                member.size = 2
                archive.addfile(member, io.BytesIO(b"ok"))
            wrapper = root / "capture.py"
            wrapper.write_text(
                "import pathlib,sys,types\nfrom unittest.mock import patch\n"
                f"counter=pathlib.Path({str(counter)!r})\n"
                "generation=sys.argv.pop()\n"
                "def native(argv, **kw):\n"
                " if 'mariadb-dump' in argv:\n"
                "  counter.write_text(str(int(counter.read_text())+1))\n"
                "  kw['stdout'].write(('CREATE TABLE generation_'+generation+' (id int);\\n').encode())\n"
                f" else: kw['stdout'].write({wordpress.getvalue()!r})\n"
                " return types.SimpleNamespace(returncode=0)\n"
                f"with patch('subprocess.run', side_effect=native): exec({program!r})\n"
            )

            def run(set_id, generation, *, source="source", request_suffix=""):
                return run_test_process([sys.executable, str(wrapper), str(capture_root),
                    str(capture_root / (set_id + request_suffix + ".tar")),
                    "request-" + set_id + request_suffix, set_id, "operation-" + set_id,
                    source, generation], input=b"synthetic-password\n", capture_output=True)

            first = run("set-a", "one")
            retry = run("set-a", "two")
            second = run("set-b", "two")
            self.assertEqual((first.returncode, retry.returncode, second.returncode), (0, 0, 0))
            self.assertEqual(first.stdout, retry.stdout)
            self.assertNotEqual(first.stdout, second.stdout)
            self.assertEqual(counter.read_text(), "2")
            self.assertEqual(run("set-a", "three", source="changed", request_suffix="-drift").returncode, 8)
            self.assertEqual(counter.read_text(), "2")
            receipt = capture_root / "set-a.receipt.json"
            receipt.unlink()
            self.assertEqual(run("set-a", "three").returncode, 7)
            self.assertEqual(counter.read_text(), "2")
            (capture_root / "set-b.tar").write_bytes(b"tampered")
            self.assertEqual(run("set-b", "three").returncode, 7)
            self.assertEqual(counter.read_text(), "2")


if __name__ == "__main__":
    unittest.main()

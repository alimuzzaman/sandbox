import subprocess
import tempfile
import tarfile
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from sandbox.sync.capture import capture_manifest
from sandbox.sync.models import SourceGeneration, SynchronizationRelationship
from sandbox.transports.remote_sync import (
    HostSourceSyncTransport,
    RemoteSyncTransport,
    RemoteSyncTransportError,
)


class RemoteSyncTransportTests(unittest.TestCase):
    @staticmethod
    def ready_workspace(relationship):
        checkout = "sha256:" + "1" * 64
        return {
            "ok": True,
            "workspace_id": relationship.workspace_id,
            "project_identity": relationship.project_identity,
            "lifecycle": "ready",
            "state": "ready",
            "status": "ready",
            "index": {"generation": 4, "complete": True},
            "checkout": {"present": True, "identity": checkout},
            "locator_digests": {
                "metadata": "sha256:" + "2" * 64,
                "checkout": checkout,
                "source_checkout": "sha256:" + "3" * 64,
            },
            "deployment_proof": {
                "checkout_locator_digest": checkout,
                "source_identity": "sha256:" + "4" * 64,
                "source_commit": "a" * 40,
            },
            "source_binding": {
                "checkout_present": True,
                "source_present": True,
                "healthy": True,
            },
            "error": None,
        }

    def test_reconcile_returns_typed_original_generation_without_retransfer(self):
        relationship = SynchronizationRelationship(
            "rel_fixture", "project:fixture", "remote", "workspace",
        )
        generation = SourceGeneration(
            "gen_fixture", "rel_fixture", 1, "a" * 64, 2, 10,
            "transferring", "request_fixture",
        )
        calls = []

        def ssh_run(_remote, command, timeout=30):
            calls.append(command)
            return SimpleNamespace(
                returncode=0, stdout='{"status":"accepted"}\n', stderr="",
            )

        transport = RemoteSyncTransport(
            remote_lookup=lambda name: {"provisioned": True, "name": name},
            ssh_run=ssh_run,
            ssh_process=lambda *_args, **_kwargs: self.fail("must not upload"),
            resolve_home=lambda _remote: "/srv/sandbox",
            workspace_preflight=self.ready_workspace,
        )
        result = transport.reconcile(relationship, generation)
        self.assertEqual(result["accepted_generation"], "gen_fixture")
        self.assertEqual(result["request_id"], "request_fixture")
        self.assertEqual(len(calls), 1)
    def test_transfer_stages_archive_and_publishes_only_after_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=root, check=True)
            (root / "source.txt").write_text("safe\n")
            subprocess.run(["git", "add", "source.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            manifest = capture_manifest(root)
            relationship = SynchronizationRelationship(
                "rel_fixture", "project:fixture", "remote", "workspace",
            )
            generation = SourceGeneration(
                "gen_fixture", "rel_fixture", 1, manifest.manifest_digest,
                manifest.file_count, manifest.byte_count, "pending", "request",
            )
            commands = []
            uploads = []

            def ssh_run(_remote, command, timeout=30):
                commands.append(command)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def ssh_process(_remote, command, input_data=None, timeout=120):
                uploads.append((command, input_data))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            transport = RemoteSyncTransport(
                remote_lookup=lambda name: {"provisioned": True, "name": name},
                ssh_run=ssh_run, ssh_process=ssh_process,
                resolve_home=lambda _remote: "/srv/sandbox",
                workspace_preflight=self.ready_workspace,
                workspace_publish=lambda *_args: {"ok": True},
            )
            result = transport.transfer(root, manifest, relationship, generation)
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(len(uploads), 1)
            self.assertIn("tar -xzf -", uploads[0][0])
            self.assertGreater(len(uploads[0][1]), 0)
            self.assertEqual(len(commands), 1)

    def test_transfer_publication_rechecks_workspace_after_staging_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=root, check=True)
            (root / "source.txt").write_text("safe\n")
            subprocess.run(["git", "add", "source.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            manifest = capture_manifest(root)
            relationship = SynchronizationRelationship(
                "rel_fixture", "project:fixture", "remote", "workspace",
            )
            generation = SourceGeneration(
                "gen_fixture", "rel_fixture", 1, manifest.manifest_digest,
                manifest.file_count, manifest.byte_count, "pending", "request",
            )
            events = []

            def publish(_relationship, _generation, _manifest, _archive_digest, evidence):
                events.append(("publish", evidence["index"]["generation"]))
                raise RuntimeError("workspace_ownership_drift")

            transport = RemoteSyncTransport(
                remote_lookup=lambda name: {"provisioned": True, "name": name},
                ssh_run=lambda *_args, **_kwargs: SimpleNamespace(
                    returncode=0, stdout="", stderr=""),
                ssh_process=lambda *_args, **_kwargs: events.append(("upload", 4)) or SimpleNamespace(
                    returncode=0, stdout="", stderr=""),
                resolve_home=lambda _remote: "/srv/sandbox",
                workspace_preflight=self.ready_workspace,
                workspace_publish=publish,
            )
            with self.assertRaises(RemoteSyncTransportError) as caught:
                transport.transfer(root, manifest, relationship, generation)
            self.assertEqual(caught.exception.code, "ownership_conflict")
            self.assertEqual(events, [("upload", 4), ("publish", 4)])

    def test_unprovisioned_remote_fails_before_runner(self):
        calls = []
        transport = RemoteSyncTransport(
            remote_lookup=lambda _name: {"provisioned": False},
            ssh_run=lambda *_args, **_kwargs: calls.append("run"),
            ssh_process=lambda *_args, **_kwargs: calls.append("process"),
            resolve_home=lambda _remote: "/srv/sandbox",
            workspace_preflight=self.ready_workspace,
        )
        with self.assertRaisesRegex(Exception, "not provisioned"):
            transport.transfer(Path("/tmp"), SimpleNamespace(entries=(), git_root=Path("/tmp")),
                               SynchronizationRelationship("rel", "project", "remote", "workspace"),
                               SourceGeneration("gen", "rel", 1, "a" * 64, 0, 0, "pending", "request"))
        self.assertEqual(calls, [])

    def test_workspace_owner_conflict_refuses_before_remote_source_mutation(self):
        calls = []
        transport = RemoteSyncTransport(
            remote_lookup=lambda name: {"provisioned": True, "name": name},
            ssh_run=lambda *_args, **_kwargs: calls.append("run"),
            ssh_process=lambda *_args, **_kwargs: calls.append("process"),
            resolve_home=lambda _remote: "/srv/sandbox",
            workspace_preflight=lambda _relationship: {
                "workspace_id": "workspace",
                "project_identity": "project:competing",
            },
        )
        relationship = SynchronizationRelationship(
            "rel_fixture", "project:fixture", "remote", "workspace",
        )
        generation = SourceGeneration(
            "gen_fixture", "rel_fixture", 1, "a" * 64, 0, 0,
            "pending", "request",
        )
        with self.assertRaisesRegex(Exception, "ownership") as caught:
            transport.transfer(
                Path("/tmp"), SimpleNamespace(entries=(), git_root=Path("/tmp")),
                relationship, generation,
            )
        self.assertEqual(caught.exception.code, "ownership_conflict")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(calls, [])

    def test_workspace_preflight_refuses_non_ready_or_unbound_canonical_status(self):
        relationship = SynchronizationRelationship(
            "rel_fixture", "project:fixture", "remote", "workspace",
        )
        baseline = self.ready_workspace(relationship)
        cases = {
            "destroyed": {**baseline, "lifecycle": "destroyed", "state": "destroyed",
                          "status": "destroyed"},
            "tombstoned": {**baseline, "lifecycle": "tombstoned",
                           "state": "tombstoned", "status": "tombstoned"},
            "unhealthy": {**baseline, "error": "runtime_unhealthy"},
            "incomplete": {**baseline, "index": {"generation": 4, "complete": False}},
            "missing_checkout": {key: value for key, value in baseline.items()
                                 if key != "checkout"},
            "ambiguous_state": {**baseline, "state": "provisioning"},
            "missing_source_binding": {**baseline, "deployment_proof": None},
            "mismatched_checkout_locator_digest": {
                **baseline,
                "locator_digests": {
                    **baseline["locator_digests"],
                    "checkout": "sha256:" + "9" * 64,
                },
            },
            "mismatched_receipt_checkout_digest": {
                **baseline,
                "deployment_proof": {
                    **baseline["deployment_proof"],
                    "checkout_locator_digest": "sha256:" + "8" * 64,
                },
            },
            "missing_locator_map": {key: value for key, value in baseline.items()
                                    if key != "locator_digests"},
            "missing_source_commit": {
                **baseline,
                "deployment_proof": {
                    "source_identity": "sha256:" + "4" * 64,
                },
            },
            "malformed_source_commit": {
                **baseline,
                "deployment_proof": {
                    **baseline["deployment_proof"],
                    "source_commit": "A" * 40,
                },
            },
            "missing_source_identity": {
                **baseline,
                "deployment_proof": {
                    "checkout_locator_digest": baseline["checkout"]["identity"],
                    "source_commit": "a" * 40,
                },
            },
            "stale_live_checkout": {
                **baseline,
                "source_binding": {
                    "checkout_present": False,
                    "source_present": True,
                    "healthy": False,
                },
            },
            "missing_live_source": {
                **baseline,
                "source_binding": {
                    "checkout_present": True,
                    "source_present": False,
                    "healthy": False,
                },
            },
            "missing_live_attestation": {
                key: value for key, value in baseline.items()
                if key != "source_binding"
            },
        }
        for label, evidence in cases.items():
            with self.subTest(label=label):
                calls = []
                transport = RemoteSyncTransport(
                    remote_lookup=lambda name: {"provisioned": True, "name": name},
                    ssh_run=lambda *_args, **_kwargs: calls.append("run"),
                    ssh_process=lambda *_args, **_kwargs: calls.append("process"),
                    resolve_home=lambda _remote: "/srv/sandbox",
                    workspace_preflight=lambda _relationship, value=evidence: value,
                )
                with self.assertRaises(RemoteSyncTransportError) as caught:
                    transport._verify_workspace_owner(relationship)
                self.assertEqual(caught.exception.code, "remote_unavailable")
                self.assertEqual(calls, [])

    def test_host_source_transfer_uses_project_relative_manifest_without_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            outer = Path(temp)
            root = outer / "site"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
            subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=outer, check=True)
            subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=outer, check=True)
            (root / "compose.yml").write_text("services: {}\n")
            subprocess.run(["git", "add", "site/compose.yml"], cwd=outer, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=outer, check=True)
            manifest = capture_manifest(root)
            relationship = SynchronizationRelationship(
                "rel_fixture", "project:fixture", "remote", "workspace",
            )
            generation = SourceGeneration(
                "gen_fixture", "rel_fixture", 1, manifest.manifest_digest,
                manifest.file_count, manifest.byte_count, "pending", "request",
            )
            commands = []
            uploads = []

            def ssh_run(_remote, command, timeout=30):
                commands.append(command)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def ssh_process(_remote, command, input_data=None, timeout=120):
                uploads.append((command, input_data))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            transport = HostSourceSyncTransport(
                remote_lookup=lambda name: {"provisioned": True, "name": name},
                ssh_run=ssh_run, ssh_process=ssh_process,
                resolve_home=lambda _remote: "/srv/sandbox",
                project_slug="demo-site",
            )
            result = transport.transfer(root, manifest, relationship, generation)

            self.assertEqual(result["status"], "accepted")
            self.assertFalse(result["restarted"])
            self.assertEqual(len(uploads), 1)
            self.assertIn("deploy-src/hosts/demo-site", commands[0])
            self.assertIn("python3 -c", commands[-1])
            self.assertNotIn("compose", commands[-1].lower())
            with tarfile.open(fileobj=io.BytesIO(uploads[0][1]), mode="r:gz") as archive:
                metadata = json.loads(
                    archive.extractfile(".sandbox-sync-manifest.json").read()
                )
            self.assertEqual([item["path"] for item in metadata["entries"]], ["compose.yml"])


if __name__ == "__main__":
    unittest.main()

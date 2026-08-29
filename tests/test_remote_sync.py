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
from sandbox.transports.remote_sync import HostSourceSyncTransport, RemoteSyncTransport


class RemoteSyncTransportTests(unittest.TestCase):
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
            )
            result = transport.transfer(root, manifest, relationship, generation)
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(len(uploads), 1)
            self.assertIn("tar -xzf -", uploads[0][0])
            self.assertGreater(len(uploads[0][1]), 0)
            self.assertIn("python3 -c", commands[-1])
            self.assertIn("manifest digest invalid", commands[-1])
            self.assertIn("os.replace(staging, published)", commands[-1])
            self.assertIn("os.symlink", commands[-1])

    def test_unprovisioned_remote_fails_before_runner(self):
        calls = []
        transport = RemoteSyncTransport(
            remote_lookup=lambda _name: {"provisioned": False},
            ssh_run=lambda *_args, **_kwargs: calls.append("run"),
            ssh_process=lambda *_args, **_kwargs: calls.append("process"),
            resolve_home=lambda _remote: "/srv/sandbox",
        )
        with self.assertRaisesRegex(Exception, "not provisioned"):
            transport.transfer(Path("/tmp"), SimpleNamespace(entries=(), git_root=Path("/tmp")),
                               SynchronizationRelationship("rel", "project", "remote", "workspace"),
                               SourceGeneration("gen", "rel", 1, "a" * 64, 0, 0, "pending", "request"))
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

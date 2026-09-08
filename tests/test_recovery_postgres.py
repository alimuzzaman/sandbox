import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.postgres import PostgresRecovery, _load_json_bytes
from sandbox.recovery.postgres_contract import PostgresSource


def source(**changes):
    value = {
        "schema_version": 1,
        "profile": "lenzora-dev",
        "remote": "scaleway-sandbox",
        "compose_project": "lenzora-dev",
        "container_id": "a" * 64,
        "volume": "lenzora-dev_postgres-data",
        "database": "lenzora",
        "role": "postgres",
        "image_id": "sha256:" + "b" * 64,
        "client_image_id": "sha256:" + "b" * 64,
        "credential_reference": None,
        "target_password_reference": None,
    }
    value.update(changes)
    return value


def capture_archive(source_digest, *, dump=b"PGDMP\x00synthetic", major=16):
    evidence = {
        "major": major,
        "database_identity": "database-identity",
        "table_counts": [],
        "migration_checksum": "absent",
        "schema_digest": "sha256:" + "a" * 64,
        "constraints_valid": True,
        "dump_digest": "sha256:" + hashlib.sha256(dump).hexdigest(),
        "source_digest": source_digest,
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("database.dump"); info.size = len(dump)
        archive.addfile(info, io.BytesIO(dump))
        raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        info = tarfile.TarInfo("evidence.json"); info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    return stream.getvalue(), evidence


class FakeCapture:
    def __init__(self, root: Path):
        self.materialization_root = root / "materialized"
        self.materialization_root.mkdir(mode=0o700)
        self.drive = MemoryDrive()
        self.publish_calls = []

    def publish_files(self, backup_id, artifacts, *, profiles, provenance, profile_bindings):
        self.publish_calls.append((backup_id, artifacts, profiles, provenance, profile_bindings))
        ciphertext = b"ciphertext-for-" + backup_id.encode()
        object_key = f"sets/{backup_id}/archive.tar.gpg"
        self.drive.put(object_key, ciphertext)
        artifact_path = Path(next(iter(artifacts.values())))
        manifest = {
            "schema_version": 1, "id": backup_id, "status": "complete",
            "profiles": list(profiles),
            "artifacts": [{"name": "postgres-capture.tar", "sha256": hashlib.sha256(
                artifact_path.read_bytes()).hexdigest(), "size": artifact_path.stat().st_size}],
            "profile_bindings": profile_bindings,
            "provenance": provenance,
            "ciphertext_object": object_key,
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_size": len(ciphertext),
            "plaintext_sha256": "a" * 64,
            "restore_compatibility": "sandbox-recovery-v1",
        }
        self.drive.put(f"sets/{backup_id}/manifest.json", json.dumps(manifest).encode())
        return manifest


class FakeTransport:
    def __init__(self, archive):
        self.archive = archive
        self.calls = []

    def invoke(self, source_value, operation, request_id, *, archive=b""):
        self.calls.append((source_value, operation, request_id, archive))
        if operation == "capture":
            return self.archive
        return json.dumps({"ok": True, "code": "observed", "observation": {
            "major": 16}}).encode()


class PostgresRecoveryTests(unittest.TestCase):
    def test_evidence_accepts_realistic_table_inventory(self):
        value = {'table_counts': [{'name': f'table_{i}', 'count': i} for i in range(307)]}
        self.assertEqual(_load_json_bytes(json.dumps(value).encode()), value)

    def test_evidence_rejects_ambiguous_or_unbounded_json(self):
        for payload in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.5}',
                        b'[]', b' ' * (1024 * 1024 + 1),
                        b'{"x":' + b'[' * 34 + b'0' + b']' * 34 + b'}'):
            with self.subTest(payload_size=len(payload)), self.assertRaises(RecoveryError):
                _load_json_bytes(payload)

    def _recovery(self, root: Path, transport: FakeTransport):
        root = Path(os.path.realpath(root))
        capture = FakeCapture(root)
        recovery = PostgresRecovery(root / "postgres", transport, capture, catalog=None)
        return recovery, capture

    def test_registration_is_planned_without_confirmation_and_installed_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recovery, _capture = self._recovery(root, FakeTransport(b""))
            planned = recovery.register(source(), confirm=False)
            self.assertEqual(planned["code"], "registration_planned")
            self.assertFalse((root / "postgres" / "sources").exists())
            installed = recovery.register(source(), confirm=True)
            self.assertEqual(installed["code"], "installed")
            replayed = recovery.register(source(), confirm=True)
            self.assertEqual(replayed["code"], "replayed")
            self.assertEqual(installed["source_digest"], replayed["source_digest"])

    def test_capture_requires_confirmation_and_exact_source_evidence_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_value = PostgresSource.from_mapping(source())
            archive, _evidence = capture_archive(source_value.source_digest)
            transport = FakeTransport(archive)
            recovery, capture = self._recovery(root, transport)
            recovery.register(source(), confirm=True)
            with self.assertRaisesRegex(RecoveryError, "confirmation"):
                recovery.create("scaleway-sandbox", "lenzora-dev", "capture-a", "backup-a")
            self.assertEqual(capture.publish_calls, [])

            result = recovery.create("scaleway-sandbox", "lenzora-dev", "capture-a", "backup-a", confirm=True)
            self.assertEqual(result["code"], "captured")
            self.assertEqual(result["source_digest"], source_value.source_digest)
            self.assertEqual(len(capture.publish_calls), 1)
            self.assertEqual(len(transport.calls), 1)
            replay = recovery.create("scaleway-sandbox", "lenzora-dev", "capture-a", "backup-a", confirm=True)
            self.assertEqual(replay, result)
            self.assertEqual(len(capture.publish_calls), 1)
            self.assertEqual(len(transport.calls), 1)

    def test_capture_source_mismatch_refuses_before_encrypted_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_value = PostgresSource.from_mapping(source())
            archive, _evidence = capture_archive("sha256:" + "f" * 64)
            transport = FakeTransport(archive)
            recovery, capture = self._recovery(root, transport)
            recovery.register(source(), confirm=True)
            with self.assertRaisesRegex(RecoveryError, "capture source changed"):
                recovery.create("scaleway-sandbox", "lenzora-dev", "capture-a", "backup-a", confirm=True)
            self.assertEqual(capture.publish_calls, [])
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(source_value.source_digest, recovery.source(
                "scaleway-sandbox", "lenzora-dev").source_digest)

    def test_restore_plan_is_bound_to_backup_profile_and_source_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_value = PostgresSource.from_mapping(source())
            archive, _evidence = capture_archive(source_value.source_digest)
            recovery, _capture = self._recovery(root, FakeTransport(archive))
            recovery.register(source(), confirm=True)
            recovery.create("scaleway-sandbox", "lenzora-dev", "capture-a", "backup-a", confirm=True)
            plan = recovery.restore_plan("scaleway-sandbox", "lenzora-dev", "restore-a", "backup-a")
            self.assertEqual(plan["profile"], "lenzora-dev")
            self.assertFalse(plan["active_database_overwrite"])
            self.assertEqual(plan["source_digest"], source_value.source_digest)
            changed = dict(plan, target="sandbox-recovery-restore-forged")
            with self.assertRaisesRegex(RecoveryError, "restore plan changed"):
                recovery.restore(changed, confirm=True)


if __name__ == "__main__":
    unittest.main()

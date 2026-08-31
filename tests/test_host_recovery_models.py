import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os
import hashlib
import time

from sandbox.core._secrets import (
    MAX_HOSTING_BINDING_REVISION, _secret_source_lock,
    hosting_binding_broker_lock, hosting_binding_key,
    prospective_hosting_binding_reference,
    read_hosting_binding_metadata,
    write_hosting_binding_metadata,
)
from sandbox.hosting.recovery.models import (
    RecoveryAction, RecoveryRequest, TargetIdentity, canonical_digest,
    secret_binding_identities, validate_observation,
)


class HostRecoveryModelsTests(unittest.TestCase):
    def request(self):
        return RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
            "apply-1", TargetIdentity("remote", "project", "development"), 2,
        )

    def test_request_digest_is_canonical_and_binds_target(self):
        request = self.request()
        self.assertEqual(request.digest, canonical_digest(request.identity_dict()))
        self.assertNotEqual(request.digest, RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
            "apply-1", TargetIdentity("remote", "project", "production"), 2,
        ).digest)

    def test_observation_rejects_duplicates_and_bounds(self):
        observation = {
            "schema_version": 1, "complete": True, "bounded": True,
            "phases": [{"phase": "observe", "state": "complete"}],
            "services": [{"service": "web", "state": "ready"}],
            "images": [{"name": "web", "id": "sha256:1"}],
        }
        self.assertTrue(validate_observation(observation)["evidence_id"].startswith("sha256:"))
        observation["services"].append({"service": "web", "state": "ready"})
        with self.assertRaisesRegex(ValueError, "duplicated"):
            validate_observation(observation)

    def test_secret_bindings_never_include_values(self):
        result = secret_binding_identities(
            {"DATABASE_PASSWORD": "guessable"}, key=b"k" * 32,
            key_version="v1-test",
        )
        encoded = json.dumps(result)
        self.assertNotIn("guessable", encoded)
        self.assertIn("DATABASE_PASSWORD", encoded)

    def test_binding_key_is_owner_only_and_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "key"
            with patch("sandbox.core._secrets.os.fsync", wraps=os.fsync) as fsync:
                first = hosting_binding_key(path)
            self.assertGreaterEqual(fsync.call_count, 2)
            second = hosting_binding_key(path)
            self.assertEqual(first, second)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_bytes(b"z" * 32)
            self.assertNotEqual(hosting_binding_key(path)[1], first[1])
            path.unlink()
            with self.assertRaisesRegex(ValueError, "unavailable"):
                hosting_binding_key(path, create=False)

    def test_missing_read_only_binding_key_does_not_create_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing" / "key"
            with self.assertRaisesRegex(ValueError, "unavailable"):
                hosting_binding_key(path, create=False)
            self.assertFalse(path.parent.exists())

    def test_edge_request_identity_binds_confirmation(self):
        common = dict(
            action=RecoveryAction.CONTINUE_EDGE, request_id="edge-1",
            job_id="a" * 32, original_request_id="apply-1",
            target=TargetIdentity("remote", "project", "development"),
            expected_generation=1, observation_request_id="recover-1",
            evidence_id="sha256:" + "1" * 64,
        )
        self.assertNotEqual(
            RecoveryRequest(confirmed=False, **common).digest,
            RecoveryRequest(confirmed=True, **common).digest,
        )

    def test_opaque_broker_metadata_never_stores_values_and_file_change_invalidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_path = root / "secrets"
            key_path = root / "binding.key"
            key_path.write_bytes(b"k" * 32)
            key_path.chmod(0o600)
            name = "SYNTHETIC_RECOVERY_BINDING_TOKEN_048"
            secret_path.write_text(f"{name}=first\n")
            secret_path.chmod(0o600)
            with patch("sandbox.core._secrets.os.fsync", wraps=os.fsync) as fsync:
                result = write_hosting_binding_metadata(
                    "remote/project/development", {name: "first"},
                    key=b"k" * 32, key_version="v1-test",
                    path=root / "metadata", secret_path=secret_path,
                    key_path=key_path)
            self.assertGreaterEqual(fsync.call_count, 2)
            encoded = next((root / "metadata").glob("*.json")).read_text()
            self.assertNotIn("first", encoded)
            self.assertEqual(read_hosting_binding_metadata(
                "remote/project/development", path=root / "metadata",
                secret_path=secret_path, key_path=key_path), result)
            updated = write_hosting_binding_metadata(
                "remote/project/development", {name: "first"},
                key=b"k" * 32, key_version="v1-test",
                path=root / "metadata", secret_path=secret_path,
                key_path=key_path)
            self.assertEqual(updated["revision"], result["revision"] + 1)
            secret_path.write_text(f"{name}=second\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                read_hosting_binding_metadata(
                    "remote/project/development", path=root / "metadata",
                    secret_path=secret_path, key_path=key_path)

    def test_broker_revision_is_exact_bounded_and_prepared_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_path = root / "secrets"
            secret_path.write_text("TOKEN=first\n")
            secret_path.chmod(0o600)
            key_path = root / "binding.key"
            key_path.write_bytes(b"k" * 32)
            key_path.chmod(0o600)
            metadata_root = root / "metadata"
            prepared = prospective_hosting_binding_reference(
                "remote/project/development", {"TOKEN": "first"},
                key=b"k" * 32, key_version="v1-test", path=metadata_root,
                secret_path=secret_path, key_path=key_path)
            self.assertFalse(metadata_root.exists())
            published = write_hosting_binding_metadata(
                "remote/project/development", {"TOKEN": "first"},
                key=b"k" * 32, key_version="v1-test", path=metadata_root,
                secret_path=secret_path, key_path=key_path, prepared=prepared)
            self.assertEqual(published, {
                "metadata_id": prepared["metadata_id"],
                "key_version": prepared["key_version"],
                "revision": prepared["revision"],
            })

            destination = next(metadata_root.glob("*.json"))
            valid = json.loads(destination.read_text())
            invalid_revisions = (True, -1, MAX_HOSTING_BINDING_REVISION, 10 ** 100)
            for revision in invalid_revisions:
                with self.subTest(revision=revision):
                    corrupt = dict(valid)
                    corrupt["revision"] = revision
                    destination.write_text(json.dumps(corrupt, sort_keys=True))
                    before = destination.read_bytes()
                    with patch("sandbox.core._secrets._ensure_directory_durable",
                               side_effect=AssertionError("directory mutated")), \
                         patch("sandbox.core._secrets.tempfile.mkstemp",
                               side_effect=AssertionError("metadata written")):
                        with self.assertRaisesRegex(ValueError, "revision is invalid"):
                            write_hosting_binding_metadata(
                                "remote/project/development", {"TOKEN": "first"},
                                key=b"k" * 32, key_version="v1-test",
                                path=metadata_root, secret_path=secret_path,
                                key_path=key_path)
                    self.assertEqual(destination.read_bytes(), before)

            destination.write_text('{"revision":' + "9" * 5000 + "}")
            before = destination.read_bytes()
            with self.assertRaisesRegex(ValueError, "revision is invalid"):
                prospective_hosting_binding_reference(
                    "remote/project/development", {"TOKEN": "first"},
                    key=b"k" * 32, key_version="v1-test", path=metadata_root,
                    secret_path=secret_path, key_path=key_path)
            self.assertEqual(destination.read_bytes(), before)

    def test_symlinked_secret_source_cannot_reuse_binding_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = root / "secrets"
            secret.write_text("TOKEN=first\n")
            secret.chmod(0o600)
            key_path = root / "binding.key"
            key_path.write_bytes(b"k" * 32)
            key_path.chmod(0o600)
            metadata = root / "metadata"
            write_hosting_binding_metadata(
                "remote/project/development", {"TOKEN": "first"},
                key=b"k" * 32, key_version="v1-test", path=metadata,
                secret_path=secret, key_path=key_path)
            replacement = root / "replacement"
            replacement.write_text("TOKEN=first\n")
            replacement.chmod(0o600)
            secret.unlink()
            secret.symlink_to(replacement)
            with self.assertRaisesRegex(ValueError, "stale"):
                read_hosting_binding_metadata(
                    "remote/project/development", path=metadata,
                    secret_path=secret, key_path=key_path)

    def test_binding_metadata_inode_and_bounded_shape_are_fail_closed(self):
        target = "remote/project/development"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            def prepared(label):
                root = base / label
                root.mkdir(mode=0o700)
                secret = root / "source"
                secret.write_text("TOKEN=synthetic\n")
                secret.chmod(0o600)
                key = root / "key"
                key.write_bytes(b"k" * 32)
                key.chmod(0o600)
                metadata = root / "metadata"
                write_hosting_binding_metadata(
                    target, {"TOKEN": "synthetic"}, key=b"k" * 32,
                    key_version="v1-test", path=metadata,
                    secret_path=secret, key_path=key)
                destination = next(metadata.glob("*.json"))
                return metadata, destination, secret, key

            metadata, destination, secret, key = prepared("mode")
            destination.chmod(0o640)
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=metadata,
                                              secret_path=secret, key_path=key)

            metadata, destination, secret, key = prepared("hardlink")
            os.link(destination, destination.with_suffix(".alias"))
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=metadata,
                                              secret_path=secret, key_path=key)

            metadata, destination, secret, key = prepared("shape")
            document = json.loads(destination.read_text())
            document["unknown"] = "field"
            destination.write_text(json.dumps(document))
            destination.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=metadata,
                                              secret_path=secret, key_path=key)

            metadata, destination, secret, key = prepared("oversize")
            destination.write_bytes(b"{" + b" " * (64 * 1024) + b"}")
            destination.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=metadata,
                                              secret_path=secret, key_path=key)

            metadata, destination, secret, key = prepared("symlink")
            victim = metadata.parent / "victim.json"
            victim.write_bytes(destination.read_bytes())
            victim.chmod(0o600)
            destination.unlink()
            destination.symlink_to(victim)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                read_hosting_binding_metadata(target, path=metadata,
                                              secret_path=secret, key_path=key)

            metadata, _destination, secret, key = prepared("root-mode")
            metadata.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=metadata,
                                              secret_path=secret, key_path=key)

            metadata, _destination, secret, key = prepared("root-link-target")
            linked_root = base / "root-link"
            linked_root.symlink_to(metadata, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=linked_root,
                                              secret_path=secret, key_path=key)

    def test_binding_metadata_fifo_refuses_without_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "metadata"
            root.mkdir(mode=0o700)
            target = "remote/project/development"
            destination = root / (hashlib.sha256(target.encode()).hexdigest() + ".json")
            os.mkfifo(destination, mode=0o600)
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "invalid"):
                read_hosting_binding_metadata(target, path=root)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_legacy_writer_reuses_the_guarded_source_lock(self):
        from sandbox.core._secrets import write_secret
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secrets"
            path.write_text("TOKEN=old\n")
            with _secret_source_lock(path, timeout_seconds=0.05):
                write_secret("TOKEN", "new", path)
            from sandbox.core._secrets import read_secret_file
            self.assertEqual(read_secret_file(path)["TOKEN"], "new")

    def test_broker_lock_has_a_finite_deadline(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = root / "secrets"
            secret.write_text("TOKEN=value\n")
            broker = root / "broker.lock"
            descriptor = os.open(broker, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                with patch("sandbox.core._secrets.secret_file", return_value=secret):
                    with self.assertRaisesRegex(ValueError, "busy"):
                        with hosting_binding_broker_lock(
                                broker, timeout_seconds=0.02):
                            self.fail("busy broker lock must time out")
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


if __name__ == "__main__":
    unittest.main()

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
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
        self.crypto = FakeCrypto()
        self.publish_calls = []

    def publish_files(self, backup_id, artifacts, *, profiles, provenance, profile_bindings):
        self.publish_calls.append((backup_id, artifacts, profiles, provenance, profile_bindings))
        # The real restore path decrypts a wrapper containing native-capture.tar.
        # Keep that boundary in the fixture so reopen tests prove the exact bytes
        # handed to the transport instead of bypassing retained-archive loading.
        native = Path(next(iter(artifacts.values()))).read_bytes()
        wrapped = io.BytesIO()
        with tarfile.open(fileobj=wrapped, mode="w") as archive:
            info = tarfile.TarInfo("native-capture.tar")
            info.size = len(native)
            archive.addfile(info, io.BytesIO(native))
        ciphertext = wrapped.getvalue()
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
            "plaintext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "restore_compatibility": "sandbox-recovery-v1",
        }
        self.drive.put(f"sets/{backup_id}/manifest.json", json.dumps(manifest).encode())
        return manifest


class FakeCrypto:
    def decrypt_file(self, ciphertext: Path, plaintext: Path):
        plaintext.write_bytes(ciphertext.read_bytes())


class FakeTransport:
    def __init__(self, archive):
        self.archive = archive
        self.calls = []
        self.responses = {}

    def invoke(self, source_value, operation, request_id, *, archive=b"", target_volume=None,
               reopen_plan=None):
        self.calls.append((source_value, operation, request_id, archive, target_volume, reopen_plan))
        if operation == "capture":
            return self.archive
        if operation in self.responses:
            response = self.responses[operation]
            return response() if callable(response) else response
        return json.dumps({"ok": True, "code": "observed", "observation": {
            "major": 16}}).encode()


class PostgresRecoveryTests(unittest.TestCase):
    def test_retained_verification_requires_confirmation_and_unambiguous_action(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _ = self._recovery(Path(directory), FakeTransport(b''))
            for options in ({'verify': True}, {'verify': True, 'inspect': True}):
                with self.assertRaises(RecoveryError): recovery.restore({}, **options)

    def test_readiness_uses_verified_ciphertext_channel_without_passphrase(self):
        from sandbox.hosting.images.provisioning import install_owner_only_json
        with tempfile.TemporaryDirectory() as directory:
            value = PostgresSource.from_mapping(source())
            archive, evidence = capture_archive(value.source_digest)
            recovery, capture = self._recovery(Path(directory), FakeTransport(archive))
            capture.drive.destination = 'gdrive:synthetic-recovery'
            recovery.register(source(), confirm=True)
            recovery.create('scaleway-sandbox', 'lenzora-dev', 'capture-a', 'backup-a', confirm=True)
            install_owner_only_json(recovery.root / 'restores' / 'receipt.json', {
                'source_digest': value.source_digest, 'backup_id': 'backup-a',
                'dump_digest': evidence['dump_digest'], 'plan_digest': 'sha256:' + 'c' * 64})
            readonly = PostgresRecovery(recovery.root, None, None, None)
            with patch('sandbox.recovery.drive.RcloneDrive', return_value=capture.drive) as configured:
                result = readonly.readiness('scaleway-sandbox', 'lenzora-dev', value.volume)
            self.assertEqual(result['code'], 'data_ready')
            self.assertEqual(configured.call_args.args[1], 'gdrive:synthetic-recovery')
            capture.drive.objects.clear()
            with patch('sandbox.recovery.drive.RcloneDrive', return_value=capture.drive), self.assertRaises(RecoveryError):
                readonly.readiness('scaleway-sandbox', 'lenzora-dev', value.volume)

    def test_capture_resume_inspects_original_identity_and_refuses_other_states(self):
        for code in ('retained_without_result', 'terminal_available'):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                value = PostgresSource.from_mapping(source())
                archive, _ = capture_archive(value.source_digest)
                transport = Mock()
                transport.invoke.side_effect = [json.dumps({'ok': True, 'code': code,
                    'operation': 'capture', 'source_digest': value.source_digest}).encode(), archive]
                recovery, capture = self._recovery(Path(directory), transport)
                recovery.register(source(), confirm=True)
                if code == 'terminal_available':
                    with self.assertRaises(RecoveryError):
                        recovery.create('scaleway-sandbox', 'lenzora-dev', 'capture-a', 'backup-a', confirm=True, resume=True)
                    self.assertEqual(len(capture.publish_calls), 0)
                else:
                    result = recovery.create('scaleway-sandbox', 'lenzora-dev', 'capture-a', 'backup-a', confirm=True, resume=True)
                    self.assertEqual(result['code'], 'captured')
                    first, second = transport.invoke.call_args_list
                    self.assertEqual(first.args[1], 'status')
                    self.assertEqual(first.args[2], second.args[2])
                    self.assertEqual(second.kwargs, {'resume_capture': True})

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

    def test_stopped_inspection_is_closed_and_does_not_install_a_restore_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
            reopen_plan = self._reopen_plan(plan)
            transport.responses["inspect-restore"] = json.dumps({
                "schema_version": 1,
                "ok": True,
                "code": "restore_target_stopped",
                "target": plan["target"],
                "container_id": reopen_plan["container_id"],
                "database_available": False,
                "all_match": False,
                "reopen_plan": reopen_plan,
            }).encode()

            result = recovery.restore(plan, inspect=True)

            self.assertEqual(result["code"], "restore_target_stopped")
            self.assertFalse(result["database_available"])
            self.assertFalse(result["all_match"])
            self.assertEqual(result["reopen_plan"], reopen_plan)
            self.assertFalse((recovery.root / "restores" / (plan["native_request_id"] + ".json")).exists())
            self.assertEqual(transport.calls[-1][1:3], ("inspect-restore", plan["native_request_id"]))

    def test_stopped_inspection_requires_closed_fields_and_bound_reopen_plan(self):
        cases = (
            ("database_available", True),
            ("all_match", True),
            ("reopen_plan", None),
            ("native_request_id", "f" * 64),
            ("source_digest", "sha256:" + "f" * 64),
            ("target", "sandbox-recovery-restore-forged"),
            ("container_id", "short"),
            ("plan_digest", "forged"),
            ("generation", "zero"),
        )
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
                reopen_plan = self._reopen_plan(plan)
                response = {
                    "schema_version": 1,
                    "ok": True,
                    "code": "restore_target_stopped",
                    "target": plan["target"],
                    "database_available": False,
                    "all_match": False,
                    "reopen_plan": reopen_plan,
                }
                if field in {"database_available", "all_match", "reopen_plan"}:
                    response[field] = value
                else:
                    response["reopen_plan"] = {**reopen_plan, field: value}
                transport.responses["inspect-restore"] = json.dumps(response).encode()
                with self.assertRaises(RecoveryError) as raised:
                    recovery.restore(plan, inspect=True)
                self.assertEqual(raised.exception.code, "restore_verification_failed")
                self.assertFalse((recovery.root / "restores" / (plan["native_request_id"] + ".json")).exists())

    def test_reopen_requires_confirmation_one_mode_and_a_nonempty_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
            reopen_plan = {"schema_version": 1, "generation": 1, "target": plan["target"]}
            cases = (
                ({"reopen": True, "reopen_plan": reopen_plan}, "confirmation_required"),
                ({"confirm": True, "inspect": True, "reopen": True, "reopen_plan": reopen_plan}, "request_invalid"),
                ({"confirm": True, "verify": True, "reopen": True, "reopen_plan": reopen_plan}, "request_invalid"),
                ({"confirm": True, "reopen": True, "reopen_plan": {}}, "request_invalid"),
                ({"confirm": True, "reopen": True, "reopen_plan": None}, "request_invalid"),
                ({"confirm": True, "reopen": True, "reopen_plan": "forged"}, "request_invalid"),
                ({"confirm": True, "reopen_plan": reopen_plan}, "request_invalid"),
            )
            for options, code in cases:
                with self.subTest(options=options), self.assertRaises(RecoveryError) as raised:
                    recovery.restore(plan, **options)
                self.assertEqual(raised.exception.code, code)
            self.assertEqual([call[1] for call in transport.calls], ["capture"])

    def test_reopen_rejects_a_changed_original_restore_plan_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
            changed = dict(plan, target="sandbox-recovery-restore-forged")
            with self.assertRaises(RecoveryError) as raised:
                recovery.restore(changed, confirm=True, reopen=True,
                                 reopen_plan={"schema_version": 1, "generation": 1})
            self.assertEqual(raised.exception.code, "restore_plan_changed")
            self.assertEqual([call[1] for call in transport.calls], ["capture"])

    def test_reopen_is_limited_to_isolated_development_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
            reopen_plan = {"schema_version": 1, "generation": 1, "target": plan["target"]}
            for changed in (
                dict(plan, profile="lenzora-prod"),
                dict(plan, target_volume="lenzora-dev_postgres-data"),
            ):
                with self.subTest(changed=changed), self.assertRaises(RecoveryError) as raised:
                    recovery.restore(changed, confirm=True, reopen=True, reopen_plan=reopen_plan)
                self.assertEqual(raised.exception.code, "request_invalid")
            self.assertEqual([call[1] for call in transport.calls], ["capture"])

        with tempfile.TemporaryDirectory() as directory:
            mapping = source(profile="lenzora-prod-legacy", credential_reference="personal/PGPASSWORD")
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory), source_mapping=mapping)
            with self.assertRaises(RecoveryError) as raised:
                recovery.restore(plan, confirm=True, reopen=True,
                                 reopen_plan={"schema_version": 1, "generation": 1})
            self.assertEqual(raised.exception.code, "request_invalid")
            self.assertEqual([call[1] for call in transport.calls], ["capture"])

    def test_reopen_preserves_original_identity_archive_target_and_plan_without_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, archive = self._prepared_plan(Path(directory))
            reopen_plan = self._reopen_plan(plan)
            transport.responses["reopen-restore"] = json.dumps({
                "schema_version": 1,
                "ok": True,
                "code": "restore_reopened",
                "target": plan["target"],
                "container_id": reopen_plan["container_id"],
                "volume": plan["target"] + "-data",
                "reopen_generation": reopen_plan["generation"],
                "reopen_plan_digest": reopen_plan["plan_digest"],
                "database_available": True,
                "all_match": False,
            }).encode()

            result = recovery.restore(plan, confirm=True, reopen=True, reopen_plan=reopen_plan)

            self.assertEqual(result["code"], "restore_reopened")
            self.assertFalse((recovery.root / "restores" / (plan["native_request_id"] + ".json")).exists())
            call = transport.calls[-1]
            self.assertEqual(call[1], "reopen-restore")
            self.assertEqual(call[2], plan["native_request_id"])
            self.assertEqual(call[3], archive)
            self.assertIsNone(call[4])
            self.assertEqual(call[5], reopen_plan)

    def test_reopen_rejects_returned_identity_or_plan_digest_without_a_receipt(self):
        for field, value in (("container_id", "f" * 64),
                             ("reopen_plan_digest", "sha256:" + "f" * 64)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
                reopen_plan = self._reopen_plan(plan)
                result = {
                    "schema_version": 1,
                    "ok": True,
                    "code": "restore_reopened",
                    "target": plan["target"],
                    "container_id": reopen_plan["container_id"],
                    "volume": plan["target"] + "-data",
                    "reopen_generation": reopen_plan["generation"],
                    "reopen_plan_digest": reopen_plan["plan_digest"],
                    "database_available": True,
                }
                result[field] = value
                transport.responses["reopen-restore"] = json.dumps(result).encode()
                with self.assertRaises(RecoveryError) as raised:
                    recovery.restore(plan, confirm=True, reopen=True, reopen_plan=reopen_plan)
                self.assertEqual(raised.exception.code, "restore_verification_failed")
                self.assertFalse((recovery.root / "restores" / (plan["native_request_id"] + ".json")).exists())

    def test_reopen_rejects_an_existing_verified_receipt_without_transport(self):
        from sandbox.hosting.images.provisioning import install_owner_only_json
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
            receipt = recovery.root / "restores" / (plan["native_request_id"] + ".json")
            install_owner_only_json(receipt, {
                "schema_version": 1,
                "ok": True,
                "code": "restore_verified",
                "target": plan["target"],
                "plan_digest": plan["plan_digest"],
            })
            with self.assertRaises(RecoveryError) as raised:
                recovery.restore(plan, confirm=True, reopen=True, reopen_plan=self._reopen_plan(plan))
            self.assertEqual(raised.exception.code, "request_invalid")
            self.assertEqual([call[1] for call in transport.calls], ["capture"])

    def test_reopen_failure_is_closed_and_does_not_write_a_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _capture, transport, plan, _archive = self._prepared_plan(Path(directory))
            reopen_plan = self._reopen_plan(plan)
            transport.responses["reopen-restore"] = json.dumps({
                "ok": False,
                "code": "restore_reopen_failed",
                "reason": "startup_failed",
                "stderr": "private-password-canary",
            }).encode()
            with self.assertRaises(RecoveryError) as raised:
                recovery.restore(plan, confirm=True, reopen=True,
                                 reopen_plan=reopen_plan)
            self.assertEqual(raised.exception.code, "acceptance_unknown")
            self.assertNotIn("private-password-canary", str(raised.exception))
            self.assertFalse((recovery.root / "restores" / (plan["native_request_id"] + ".json")).exists())
            self.assertEqual(transport.calls[-1][1:3], ("reopen-restore", plan["native_request_id"]))

    def _prepared_plan(self, root: Path, *, source_mapping=None, backup_id="backup-a"):
        source_mapping = source_mapping or source()
        source_value = PostgresSource.from_mapping(source_mapping)
        archive, _evidence = capture_archive(source_value.source_digest)
        transport = FakeTransport(archive)
        recovery, capture = self._recovery(root, transport)
        recovery.register(source_mapping, confirm=True)
        recovery.create(source_value.remote, source_value.profile, "capture-a", backup_id, confirm=True)
        plan = recovery.restore_plan(source_value.remote, source_value.profile, "restore-a", backup_id)
        return recovery, capture, transport, plan, archive

    @staticmethod
    def _reopen_plan(plan):
        return {
            "schema_version": 1,
            "generation": 0,
            "native_request_id": plan["native_request_id"],
            "source_digest": plan["source_digest"],
            "target": plan["target"],
            "container_id": "e" * 64,
            "volume": plan["target"] + "-data",
            "plan_digest": "sha256:" + "d" * 64,
        }


if __name__ == "__main__":
    unittest.main()

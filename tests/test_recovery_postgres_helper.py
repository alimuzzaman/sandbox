import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.recovery import postgres_helper as helper


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


def capture_archive(dump=b"PGDMP\x00synthetic"):
    evidence = {
        "major": 16,
        "database_identity": "database-identity",
        "table_counts": [],
        "migration_checksum": "absent",
        "schema_digest": "sha256:" + "a" * 64,
        "constraints_valid": True,
        "dump_digest": "sha256:" + hashlib.sha256(dump).hexdigest(),
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        dump_info = tarfile.TarInfo("database.dump")
        dump_info.size = len(dump)
        archive.addfile(dump_info, io.BytesIO(dump))
        evidence_bytes = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        evidence_info = tarfile.TarInfo("evidence.json")
        evidence_info.size = len(evidence_bytes)
        archive.addfile(evidence_info, io.BytesIO(evidence_bytes))
    return stream.getvalue(), evidence


class PostgresHelperTests(unittest.TestCase):
    def test_legacy_database_source_binds_stopped_application_storage(self):
        value = source(profile="lenzora-prod-legacy", credential_reference="personal/DATABASE_URL")
        row = {"Id": value["container_id"], "Image": value["image_id"],
            "Config": {"Labels": {"com.docker.compose.project": value["compose_project"]}},
            "State": {"Running": False}, "Mounts": [{"Type": "volume", "Name": value["volume"],
                "Destination": "/app/storage"}]}
        with patch.object(helper, "run", return_value=json.dumps([row]).encode()):
            self.assertEqual(helper.inspect_source(value)["container_id"], value["container_id"])
        row["Mounts"][0]["Destination"] = "/var/lib/postgresql/data"
        with patch.object(helper, "run", return_value=json.dumps([row]).encode()):
            with self.assertRaisesRegex(ValueError, "source_changed"): helper.inspect_source(value)
            with self.assertRaisesRegex(ValueError, "source_changed"): helper.inspect_source(source())

    def test_final_transfer_uses_fixed_lenzora_database_and_role_for_legacy_source(self):
        source_value = source(profile="lenzora-prod-legacy", database="legacy_db",
                              role="legacy_owner", credential_reference="personal/PGPASSWORD",
                              target_password_reference="personal/TARGET_PASSWORD")
        archive, evidence = capture_archive()
        evidence["captured_quiescent"] = True
        raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r") as existing:
            dump = existing.extractfile("database.dump").read()
        rebuilt = io.BytesIO()
        with tarfile.open(fileobj=rebuilt, mode="w") as output:
            info = tarfile.TarInfo("database.dump"); info.size = len(dump)
            output.addfile(info, io.BytesIO(dump))
            info = tarfile.TarInfo("evidence.json"); info.size = len(raw)
            output.addfile(info, io.BytesIO(raw))
        archive = rebuilt.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(os.path.realpath(directory)) / "work"
            work.mkdir(mode=0o700)
            archive_path = work / "input.tar"
            archive_path.write_bytes(archive)
            calls = []

            def invoke(argv, **_kwargs):
                calls.append(argv)
                if argv[:2] == ["docker", "ps"] or "volume" in argv and "ls" in argv:
                    return b""
                if "psql" in argv:
                    return b"1"
                return b""

            actual = {**evidence, "database_identity": "target-database-identity"}
            with patch.object(helper, "run", side_effect=invoke), \
                    patch.object(helper, "observation", return_value=actual), \
                    patch.object(helper.subprocess, "run",
                                 return_value=SimpleNamespace(returncode=0)) as restore_process:
                result = helper.restore(source_value, archive_path, work,
                                        "sandbox-recovery-restore-" + "a" * 24,
                                        target_volume="sandbox-host-lenzora-production_lenzora-postgres-data",
                                        target_password=b"target-password")

        create = next(argv for argv in calls if argv[:2] == ["docker", "create"]
                      and "POSTGRES_DB=lenzora" in argv)
        self.assertIn("POSTGRES_USER=lenzora", create)
        restore_argv = restore_process.call_args.args[0]
        self.assertEqual(restore_argv[restore_argv.index("--dbname") + 1], "lenzora")
        self.assertEqual(result["target_database"], "lenzora")
        self.assertEqual(result["target_role"], "lenzora")
        self.assertEqual(result["source_database_identity"], evidence["database_identity"])
        self.assertEqual(source_value["database"], "legacy_db")

    def test_legacy_capture_marks_non_quiescent_when_source_starts_running_then_stops(self):
        source_value = source(profile="lenzora-prod-legacy",
                              credential_reference="personal/PGPASSWORD")

        class Hold:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"ABC-1\n")
                self.done = False

            def poll(self):
                return 0 if self.done else None

            def terminate(self):
                self.done = True

            def wait(self, timeout=None):
                self.done = True

        hold = Hold()
        project_states = iter((b"source-container\n", b""))

        def invoke(argv, **kwargs):
            if argv[:2] == ["docker", "ps"]:
                return next(project_states)
            if "pg_dump" in argv:
                kwargs["output"].write(b"PGDMP\x00synthetic")
                return b""
            raise AssertionError(f"unexpected command: {argv}")

        with tempfile.TemporaryDirectory() as directory, patch.object(
                helper.subprocess, "Popen", return_value=hold), \
                patch("select.select", return_value=([hold.stdout], [], [])), \
                patch.object(helper, "observation", return_value={
                    "major": 16, "database_identity": "db", "table_counts": [],
                    "migration_checksum": "absent", "schema_digest": "sha256:" + "a" * 64,
                    "constraints_valid": True}), \
                patch.object(helper, "run", side_effect=invoke):
            work = Path(os.path.realpath(directory)) / "work"
            work.mkdir(mode=0o700)
            archive = helper.capture(source_value, ["docker", "exec", "reader"], work)
            with tarfile.open(archive, "r") as bundle:
                evidence = json.loads(bundle.extractfile("evidence.json").read())
        self.assertFalse(evidence["captured_quiescent"])

    def test_inspect_source_requires_exact_running_container_volume_and_image_binding(self):
        expected = source()
        row = [{
            "Id": expected["container_id"],
            "Image": expected["image_id"],
            "Config": {"Labels": {"com.docker.compose.project": expected["compose_project"]}},
            "State": {"Running": True},
            "Mounts": [{"Type": "volume", "Name": expected["volume"],
                        "Destination": "/var/lib/postgresql/data"}],
        }]
        calls = []

        def invoke(argv, **kwargs):
            calls.append((argv, kwargs))
            return json.dumps(row).encode()

        with patch.object(helper, "run", side_effect=invoke):
            self.assertEqual(helper.inspect_source(expected), {
                "container_id": expected["container_id"],
                "image_id": expected["image_id"],
                "volume": expected["volume"],
            })
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ["docker", "inspect", expected["container_id"]])

        for mutation in (
            lambda item: item[0].update(Image="sha256:" + "c" * 64),
            lambda item: item[0]["State"].update(Running=False),
            lambda item: item[0]["Mounts"].clear(),
            lambda item: item[0]["Config"]["Labels"].update(
                {"com.docker.compose.project": "foreign"}),
        ):
            changed = json.loads(json.dumps(row))
            mutation(changed)
            with self.subTest(mutation=mutation), patch.object(
                    helper, "run", return_value=json.dumps(changed).encode()):
                with self.assertRaisesRegex(ValueError, "source_changed"):
                    helper.inspect_source(expected)

    def test_external_client_keeps_credential_bytes_out_of_argv_and_returns_bounded_client(self):
        calls = []
        secret = "private-password-canary"

        def invoke(argv, **kwargs):
            calls.append((argv, kwargs))
            return b""

        with tempfile.TemporaryDirectory() as directory, patch.object(
                helper, "run", side_effect=invoke):
            client = helper.external_client(
                source(profile="lenzora-prod-legacy", credential_reference="personal/PGPASSWORD",
                       database="app", role="app_role"),
                b"postgresql://app_role:" + secret.encode() + b"@db.example.test:5432/app?sslmode=require",
                Path(directory), "sandbox-recovery-client-" + "a" * 24)
        self.assertEqual(client[:4], ["docker", "exec", "-i", "-e"])
        rendered_argv = json.dumps([argv for argv, _kwargs in calls])
        self.assertNotIn(secret, rendered_argv)
        self.assertEqual(calls[0][0][0:3], ["docker", "create", "--name"])
        credential_calls = [kwargs for argv, kwargs in calls
                            if argv[:3] == ["docker", "exec", "-i"] and "sh" in argv]
        self.assertEqual(len(credential_calls), 1)
        self.assertIn(secret.encode(), credential_calls[0]["data"])

    def test_restore_refuses_existing_isolated_volume_before_create_or_delete(self):
        archive, _evidence = capture_archive()
        calls = []

        def invoke(argv, **_kwargs):
            calls.append(argv)
            if argv[:4] == ["docker", "volume", "ls", "--filter"]:
                return b"sandbox-recovery-restore-aaaaaaaaaaaaaaaaaaaaaaaa-data\n"
            raise AssertionError(f"unexpected command after existing-volume refusal: {argv}")

        with tempfile.TemporaryDirectory() as directory, patch.object(helper, "run", side_effect=invoke):
            work = Path(os.path.realpath(directory)) / "work"
            work.mkdir(mode=0o700)
            archive_path = work / "input.tar"
            archive_path.write_bytes(archive)
            with self.assertRaisesRegex(ValueError, "restore_target_exists"):
                helper.restore(source(), archive_path, work, "sandbox-recovery-restore-" + "a" * 24)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(argv[1:3] in (["volume", "rm"], ["rm", "-f"])
                             for argv in calls))
        self.assertNotIn("create", calls[0])

    def test_restore_rejects_changed_backup_integrity_before_docker_effects(self):
        archive, evidence = capture_archive()
        evidence["dump_digest"] = "sha256:" + "f" * 64
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as output:
            dump = b"PGDMP\x00synthetic"
            info = tarfile.TarInfo("database.dump"); info.size = len(dump)
            output.addfile(info, io.BytesIO(dump))
            raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            info = tarfile.TarInfo("evidence.json"); info.size = len(raw)
            output.addfile(info, io.BytesIO(raw))
        with tempfile.TemporaryDirectory() as directory, patch.object(
                helper, "run", side_effect=AssertionError("docker must not run")):
            work = Path(os.path.realpath(directory)) / "work"; work.mkdir(mode=0o700)
            archive_path = work / "input.tar"
            archive_path.write_bytes(stream.getvalue())
            with self.assertRaisesRegex(ValueError, "dump_changed"):
                helper.restore(source(), archive_path, work,
                               "sandbox-recovery-restore-" + "b" * 24)


if __name__ == "__main__":
    unittest.main()

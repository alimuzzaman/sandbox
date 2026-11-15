"""The infisical PostgreSQL profile beside the unchanged Lenzora profiles."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandbox.recovery import postgres_contract as contract
from sandbox.recovery import postgres_helper as helper
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.postgres import PostgresRecovery
from sandbox.recovery.postgres_contract import PostgresSource, valid_backup_id
from sandbox.transports.remote_postgres_recovery import RegisteredPostgresRecoveryTransport
from tests.test_recovery_postgres import FakeCapture, FakeTransport, capture_archive, inspection_diagnostic

BACKUP_ID = "infisical-20261116T0300Z"


def infisical(**changes):
    value = {
        "schema_version": 1,
        "profile": "infisical",
        "remote": "xcloud-london",
        "compose_project": "sandbox-infisical",
        "container_id": "a" * 64,
        "volume": "sandbox-infisical_pg_data",
        "database": "infisical",
        "role": "infisical",
        "image_id": "sha256:" + "b" * 64,
        "client_image_id": "sha256:" + "b" * 64,
        "credential_reference": None,
        "target_password_reference": None,
    }
    value.update(changes)
    return value


def lenzora_dev(**changes):
    return infisical(profile="lenzora-dev", compose_project="lenzora-dev", container_id="c" * 64,
                     volume="lenzora-dev_postgres-data", database="lenzora", role="postgres", **changes)


def recovery_for(root, transport):
    root = Path(os.path.realpath(root))
    capture = FakeCapture(root)
    return PostgresRecovery(root / "postgres", transport, capture, catalog=None), capture


class InfisicalContractTests(unittest.TestCase):
    def test_infisical_descriptor_is_accepted_exactly(self):
        value = infisical()
        self.assertEqual(PostgresSource.from_mapping(value).as_mapping(), value)

    def test_infisical_refuses_external_credentials_and_a_different_client_image(self):
        for changes in ({"credential_reference": "personal/PGPASSWORD"},
                        {"target_password_reference": "personal/PGPASSWORD"},
                        {"client_image_id": "sha256:" + "d" * 64}):
            with self.subTest(changes=changes), self.assertRaises(RecoveryError) as raised:
                PostgresSource.from_mapping(infisical(**changes))
            self.assertEqual(raised.exception.code, "source_binding_invalid")

    def test_lenzora_profiles_are_unchanged(self):
        self.assertEqual(contract.DATABASE_PROFILES - {"infisical"},
                         {"lenzora-dev", "lenzora-prod", "lenzora-prod-legacy"})
        self.assertEqual(contract.SOURCE_PROFILES - contract.DATABASE_PROFILES, {"lenzora-prod-storage"})
        self.assertEqual(contract.LOCAL_DATABASE_PROFILES, {"lenzora-dev", "infisical"})
        self.assertEqual(PostgresSource.from_mapping(lenzora_dev()).profile, "lenzora-dev")
        self.assertEqual(PostgresSource.from_mapping(infisical(profile="lenzora-prod")).profile, "lenzora-prod")
        with self.assertRaises(RecoveryError):
            # Legacy still requires its broker reference.
            PostgresSource.from_mapping(infisical(profile="lenzora-prod-legacy"))

    def test_unknown_profiles_are_refused_by_contract_and_source_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _ = recovery_for(directory, FakeTransport(b""))
            for profile in ("compose-postgres", "Infisical", "infisical-dev", ""):
                with self.subTest(profile=profile):
                    with self.assertRaises(RecoveryError) as raised:
                        PostgresSource.from_mapping(infisical(profile=profile))
                    self.assertEqual(raised.exception.code, "source_binding_invalid")
                    with self.assertRaises(RecoveryError) as raised:
                        recovery.source("xcloud-london", profile)
                    self.assertEqual(raised.exception.code, "source_binding_invalid")

    def test_standalone_helper_profiles_match_the_contract(self):
        self.assertEqual(helper.LOCAL_DATABASE_PROFILES, contract.LOCAL_DATABASE_PROFILES)

    def test_infisical_backup_ids_are_utc_slot_names_and_other_profiles_are_unconstrained(self):
        self.assertTrue(valid_backup_id("infisical", BACKUP_ID))
        for value in ("backup-a", "infisical-2026-11-16", "infisical-20261116T0300",
                      "infisical-20261116T0300Z-retry", "lenzora-20261116T0300Z", None):
            with self.subTest(value=value):
                self.assertFalse(valid_backup_id("infisical", value))
        for profile in ("lenzora-dev", "lenzora-prod", "lenzora-prod-legacy", "lenzora-prod-storage"):
            self.assertTrue(valid_backup_id(profile, "backup-a"))


class InfisicalRecoveryTests(unittest.TestCase):
    def test_infisical_registers_beside_lenzora_dev_on_the_same_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _ = recovery_for(directory, FakeTransport(b""))
            self.assertEqual(recovery.register(lenzora_dev(), confirm=True)["code"], "installed")
            self.assertEqual(recovery.register(infisical(), confirm=True)["code"], "installed")
            sources = recovery.root / "sources"
            self.assertEqual(sorted(path.name for path in sources.iterdir()),
                             ["xcloud-london-infisical.json", "xcloud-london-lenzora-dev.json"])
            self.assertEqual(recovery.source("xcloud-london", "lenzora-dev").as_mapping(), lenzora_dev())
            self.assertEqual(recovery.source("xcloud-london", "infisical").as_mapping(), infisical())

    def test_capture_refuses_an_unnamed_backup_id_before_any_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(b"")
            recovery, capture = recovery_for(directory, transport)
            recovery.register(infisical(), confirm=True)
            with self.assertRaises(RecoveryError) as raised:
                recovery.create("xcloud-london", "infisical", "capture-a", "backup-a", confirm=True)
            self.assertEqual(raised.exception.code, "invalid_set_id")
            self.assertEqual(transport.calls, [])
            self.assertEqual(capture.publish_calls, [])
            self.assertFalse((recovery.root / "requests").exists())

    def test_capture_publishes_an_infisical_set_and_plans_an_isolated_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            source_value = PostgresSource.from_mapping(infisical())
            archive, _ = capture_archive(source_value.source_digest)
            recovery, capture = recovery_for(directory, FakeTransport(archive))
            recovery.register(infisical(), confirm=True)
            result = recovery.create("xcloud-london", "infisical", "capture-a", BACKUP_ID, confirm=True)
            self.assertEqual(result["code"], "captured")
            backup_id, _artifacts, profiles, _provenance, bindings = capture.publish_calls[0]
            self.assertEqual((backup_id, profiles), (BACKUP_ID, ("infisical",)))
            self.assertEqual(bindings["infisical"]["restore_target"], "isolated-postgresql-volume")
            plan = recovery.restore_plan("xcloud-london", "infisical", "restore-a", BACKUP_ID)
            self.assertEqual((plan["target_volume"], plan["target_database"], plan["target_role"]),
                             (None, "infisical", "infisical"))
            self.assertFalse(plan["active_database_overwrite"])
            with self.assertRaises(RecoveryError) as raised:
                recovery.restore_plan("xcloud-london", "infisical", "restore-b", BACKUP_ID,
                                      target_volume="sandbox-infisical_pg_data")
            self.assertEqual(raised.exception.code, "restore_plan_changed")

    def test_inspection_of_an_infisical_drill_reaches_the_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            source_value = PostgresSource.from_mapping(infisical())
            archive, _ = capture_archive(source_value.source_digest)
            transport = FakeTransport(archive)
            recovery, _ = recovery_for(directory, transport)
            recovery.register(infisical(), confirm=True)
            recovery.create("xcloud-london", "infisical", "capture-a", BACKUP_ID, confirm=True)
            plan = recovery.restore_plan("xcloud-london", "infisical", "restore-a", BACKUP_ID)
            transport.responses["inspect-restore"] = json.dumps({
                "schema_version": 1, "ok": True, "code": "restore_inspected", "target": plan["target"],
                "all_match": True, "inspection_diagnostic": inspection_diagnostic(
                    plan, archive, code="restore_inspected", phase="complete", status="complete"),
            }).encode()
            result = recovery.restore(plan, inspect=True)
            self.assertEqual(result["code"], "restore_inspected")
            self.assertEqual(transport.calls[-1][1:3], ("inspect-restore", plan["native_request_id"]))
            with self.assertRaises(RecoveryError) as raised:
                recovery.restore(dict(plan, target_volume="sandbox-infisical_pg_data"), inspect=True)
            self.assertEqual(raised.exception.code, "request_invalid")

    def test_incomplete_infisical_capture_can_resume_under_its_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source_value = PostgresSource.from_mapping(infisical())
            archive, _ = capture_archive(source_value.source_digest)
            transport = Mock()
            transport.invoke.side_effect = [json.dumps({
                "ok": True, "code": "retained_without_result", "operation": "capture",
                "source_digest": source_value.source_digest}).encode(), archive]
            recovery, _ = recovery_for(directory, transport)
            recovery.register(infisical(), confirm=True)
            result = recovery.create("xcloud-london", "infisical", "capture-a", BACKUP_ID,
                                     confirm=True, resume=True)
            self.assertEqual(result["code"], "captured")
            status, capture_call = transport.invoke.call_args_list
            self.assertEqual(status.args[1], "status")
            self.assertEqual(capture_call.kwargs, {"resume_capture": True})

    def test_restore_plan_prints_an_envelope_whose_data_is_the_plan_file(self):
        from sandbox.commands.recovery import _postgres
        plan = {"schema_version": 1, "remote": "xcloud-london", "profile": "infisical",
                "request_id": "restore-a", "native_request_id": "e" * 64, "backup_id": BACKUP_ID,
                "source_digest": "sha256:" + "f" * 64, "ciphertext_digest": "1" * 64,
                "target": "sandbox-recovery-restore-" + "e" * 24, "source_major": 16, "target_volume": None,
                "target_database": "infisical", "target_role": "infisical",
                "active_database_overwrite": False, "published_ports": [], "plan_digest": "sha256:" + "2" * 64}
        recovery = Mock()
        recovery.restore_plan.return_value = plan
        recovery.restore.return_value = {"code": "restore_verified"}

        def args(operation, restore_plan=None):
            return SimpleNamespace(postgres_operation=operation, reopen_plan=None, project_dir=None,
                resume_capture=False, source_binding=None, restore_plan=restore_plan, remote="xcloud-london",
                profile=["infisical"], request_id="restore-a", backup_id=BACKUP_ID, target_volume=None,
                confirm=True)

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"SANDBOX_HOME": directory}), \
                patch("sandbox.recovery.postgres.PostgresRecovery", return_value=recovery), \
                patch("sandbox.transports.remote_postgres_recovery.RegisteredPostgresRecoveryTransport"):
            service = SimpleNamespace(capture=None, catalog=None)
            envelope = _postgres({}, args("restore-plan"), service)
            self.assertEqual((envelope["ok"], envelope["action"], envelope["status"]), (True, "postgres", "planned"))
            self.assertEqual(envelope["data"], plan)
            for name, body, ok in (("plan.json", envelope["data"], True), ("envelope.json", envelope, False)):
                path = Path(os.path.realpath(directory)) / name
                path.write_text(json.dumps(body))
                os.chmod(path, 0o600)
                with self.subTest(file=name):
                    payload = _postgres({}, args("restore", str(path)), service)
                    self.assertIs(payload["ok"], ok)
                    if not ok:
                        self.assertEqual(payload["error"]["code"], "restore_plan_changed")
            recovery.restore.assert_called_once()
            self.assertEqual(recovery.restore.call_args.args[0], plan)


class InfisicalHelperTests(unittest.TestCase):
    def test_inspect_source_binds_the_compose_instance_database(self):
        expected = infisical()
        row = [{"Id": expected["container_id"], "Image": expected["image_id"],
                "Config": {"Labels": {"com.docker.compose.project": "sandbox-infisical"}},
                "State": {"Running": True},
                "Mounts": [{"Type": "volume", "Name": expected["volume"],
                            "Destination": "/var/lib/postgresql/data"}]}]
        with patch.object(helper, "run", return_value=json.dumps(row).encode()):
            self.assertEqual(helper.inspect_source(expected)["volume"], "sandbox-infisical_pg_data")

    def test_local_only_schema_baseline_accepts_infisical_and_refuses_production(self):
        evidence = {"schema_digest": "sha256:" + "a" * 64}
        with self.assertRaisesRegex(ValueError, "schema_reference_source_unavailable"):
            helper._captured_structure(infisical(profile="lenzora-prod"), evidence)
        with patch.object(helper, "inspect_source", side_effect=ValueError("gate_passed")):
            with self.assertRaisesRegex(ValueError, "gate_passed"):
                helper._captured_structure(infisical(), evidence)

    def test_resume_gate_accepts_infisical_and_refuses_production(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            identity = "a" * 64
            def request(value):
                return {"operation": "capture", "source": value, "request_id": identity, "root": str(root),
                        "credential_size": 0, "credential_revision": None, "archive_size": 0,
                        "archive_digest": "sha256:" + "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "target_volume": None, "resume_capture": True}
            def invoke(value):
                output = io.BytesIO()
                with patch.object(helper.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(helper.canonical(value) + b"\n"))), \
                        patch.object(helper.sys, "stdout", SimpleNamespace(buffer=output)):
                    helper.main()
                return output.getvalue()
            with self.assertRaisesRegex(ValueError, "request_invalid"):
                invoke(request(infisical(profile="lenzora-prod")))
            slot = root / identity; slot.mkdir(mode=0o700)
            retained = request(infisical()); retained.pop("resume_capture")
            helper.private_write(slot / "request.json", helper.canonical(retained))
            def capture(_source, _client, work):
                path = work / "capture.tar"; helper.private_write(path, b"synthetic archive"); return path
            with patch.object(helper, "inspect_source"), patch.object(helper, "capture", side_effect=capture):
                self.assertEqual(invoke(request(infisical())), b"synthetic archive")


class InfisicalTransportTests(unittest.TestCase):
    def _transport(self, process):
        return RegisteredPostgresRecoveryTransport(
            cfg={}, project_root=Path("/synthetic/project"), state_root=Path("/synthetic/state"),
            lookup=lambda _remote: {"provisioned": True},
            status=lambda _entry: {"runtime_revision_state": "match", "local_runtime_revision": "a" * 24,
                                   "installed_runtime_revision": "a" * 24, "active": True, "authenticated": True},
            resolve_home=lambda _entry: "/srv/sandbox", process=process,
            broker=lambda *_args, **_kwargs: self.fail("infisical never uses a credential broker"))

    def test_inspect_restore_and_resume_reach_the_helper_for_infisical(self):
        frames = []
        response = {"schema_version": 1, "ok": True, "code": "restore_database_unavailable",
                    "inspection_diagnostic": {"phase": "database_probe", "status": "unavailable",
                                              "code": "restore_database_unavailable"}}
        def process(_entry, _command, **kwargs):
            frames.append(json.loads(kwargs["input_data"].split(b"\n", 1)[0]))
            return SimpleNamespace(returncode=0, stdout=json.dumps(response).encode())
        transport = self._transport(process)
        source_value = PostgresSource.from_mapping(infisical())
        result = json.loads(transport.invoke(source_value, "inspect-restore", "9" * 64, archive=b"archive"))
        self.assertEqual(result["code"], "restore_database_unavailable")
        transport.invoke(source_value, "capture", "8" * 64, resume_capture=True)
        self.assertEqual([(frame["operation"], frame["source"]["profile"]) for frame in frames],
                         [("inspect-restore", "infisical"), ("capture", "infisical")])
        self.assertTrue(frames[1]["resume_capture"])

    def test_production_inspection_is_still_refused_before_the_helper(self):
        transport = self._transport(lambda *_args, **_kwargs: self.fail("helper must not run"))
        with self.assertRaises(RecoveryError) as raised:
            transport.invoke(PostgresSource.from_mapping(infisical(profile="lenzora-prod")),
                             "inspect-restore", "9" * 64, archive=b"archive")
        self.assertEqual(raised.exception.code, "request_invalid")


if __name__ == "__main__":
    unittest.main()

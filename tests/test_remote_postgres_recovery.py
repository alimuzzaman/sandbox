import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sandbox.recovery.postgres_contract import PostgresSource, recovery_source
from sandbox.transports.remote_postgres_recovery import RegisteredPostgresRecoveryTransport


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
    return PostgresSource.from_mapping(value)


def storage_source(**changes):
    value = {
        "schema_version": 1,
        "profile": "lenzora-prod-storage",
        "remote": "scaleway-sandbox",
        "compose_project": "lenzora",
        "container_id": "a" * 64,
        "volume": "lenzora-storage",
        "image_id": "sha256:" + "b" * 64,
        "mount_path": "/app/storage",
        "credential_reference": None,
    }
    value.update(changes)
    return recovery_source(value)


class RemotePostgresRecoveryTests(unittest.TestCase):
    def test_status_does_not_consume_external_credentials(self):
        def broker(*_args):
            self.fail('status must not consume credentials')
        self._transport(broker=broker).invoke(source(profile='lenzora-prod-legacy',
            credential_reference='personal/PGPASSWORD'), 'status', 'a' * 64)

    def _transport(self, *, lookup=None, status=None, home=None, process=None, broker=None):
        return RegisteredPostgresRecoveryTransport(
            cfg={}, project_root=Path("/synthetic/project"), state_root=Path("/synthetic/state"),
            lookup=lookup or (lambda _remote: {"provisioned": True}),
            status=status or (lambda _entry: {
                "runtime_revision_state": "match", "active": True, "authenticated": True}),
            resolve_home=home or (lambda _entry: "/srv/sandbox"),
            process=process or (lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=b'{"ok":true,"code":"observed"}')),
            broker=broker,
        )

    def test_remote_and_runtime_authority_are_checked_before_helper_execution(self):
        process_calls = []
        process = lambda *args, **kwargs: process_calls.append((args, kwargs))
        for lookup, status in (
            (lambda _remote: None, lambda _entry: {}),
            (lambda _remote: {"provisioned": False}, lambda _entry: {}),
            (lambda _remote: {"provisioned": True}, lambda _entry: {
                "runtime_revision_state": "mismatch", "active": True, "authenticated": True}),
            (lambda _remote: {"provisioned": True}, lambda _entry: {
                "runtime_revision_state": "match", "active": False, "authenticated": True}),
        ):
            transport = self._transport(lookup=lookup, status=status, process=process)
            with self.subTest(lookup=lookup, status=status), self.assertRaisesRegex(
                    Exception, "remote|runtime"):
                transport.invoke(source(), "observe", "a" * 64)
        self.assertEqual(process_calls, [])

    def test_request_identity_and_helper_frame_are_bounded_and_secret_free_at_command_boundary(self):
        calls = []

        def process(entry, command, **kwargs):
            calls.append((entry, command, kwargs))
            self.assertNotIn("private-password-canary", command)
            self.assertNotIn("postgresql://", command)
            frame, material = kwargs["input_data"].split(b"\n", 1)[0], kwargs["input_data"].split(b"\n", 1)[1]
            request = json.loads(frame)
            self.assertEqual(request["operation"], "observe")
            self.assertEqual(request["request_id"], "a" * 64)
            self.assertEqual(request["credential_size"], 0)
            self.assertEqual(material, b"")
            return SimpleNamespace(returncode=0, stdout='{"ok":true,"code":"observed"}')

        transport = self._transport(process=process)
        result = transport.invoke(source(), "observe", "a" * 64)
        self.assertEqual(result, b'{"ok":true,"code":"observed"}')
        self.assertEqual(len(calls), 1)

    def test_external_credential_is_broker_scoped_and_never_printed_or_embedded_in_command(self):
        calls = []
        secret = b"private-password-canary"

        def process(_entry, command, **kwargs):
            calls.append((command, kwargs))
            self.assertNotIn(secret.decode(), command)
            return SimpleNamespace(returncode=0, stdout=b'{"ok":true,"code":"observed"}')

        broker_calls = []

        def broker(value, consumer):
            broker_calls.append(value)
            return consumer(secret, "sha256:" + hashlib.sha256(secret).hexdigest())

        transport = self._transport(process=process, broker=broker)
        result = transport.invoke(
            source(profile="lenzora-prod-legacy", credential_reference="personal/PGPASSWORD",
                   client_image_id="sha256:" + "c" * 64),
            "observe", "b" * 64)
        self.assertEqual(result, b'{"ok":true,"code":"observed"}')
        self.assertEqual(broker_calls, [source(profile="lenzora-prod-legacy",
            credential_reference="personal/PGPASSWORD", client_image_id="sha256:" + "c" * 64)])
        self.assertEqual(len(calls), 1)

    def test_invalid_request_id_and_unsafe_home_refuse_before_process(self):
        process_calls = []
        transport = self._transport(home=lambda _entry: "/srv/sandbox\nunsafe",
                                    process=lambda *args, **kwargs: process_calls.append(args))
        with self.assertRaisesRegex(Exception, "invalid"):
            transport.invoke(source(), "observe", "not-a-digest")
        with self.assertRaisesRegex(Exception, "root"):
            transport.invoke(source(), "observe", "c" * 64)
        self.assertEqual(process_calls, [])

    def test_reopen_requires_a_nonempty_plan_and_rejects_other_operations_before_remote_lookup(self):
        lookups = []
        brokers = []
        transport = self._transport(
            lookup=lambda remote: lookups.append(remote) or {"provisioned": True},
            broker=lambda *args: brokers.append(args),
        )
        cases = (
            (source(), "reopen-restore", None),
            (source(), "reopen-restore", {}),
            (source(profile="lenzora-prod"), "reopen-restore", {"generation": 1}),
            (source(profile="lenzora-prod-legacy", credential_reference="personal/PGPASSWORD"),
             "reopen-restore", {"generation": 1}),
            (storage_source(), "reopen-restore", {"generation": 1}),
            (source(), "observe", {"generation": 1}),
            (source(), "inspect-restore", {"generation": 1}),
        )
        for source_value, operation, plan in cases:
            with self.subTest(profile=source_value["profile"] if isinstance(source_value, dict) else source_value.profile,
                              operation=operation, plan=plan), self.assertRaises(Exception) as raised:
                transport.invoke(source_value, operation, "a" * 64, reopen_plan=plan)
            self.assertRegex(str(raised.exception), "invalid")
        self.assertEqual(lookups, [])
        self.assertEqual(brokers, [])

    def test_local_development_reopen_has_no_secret_broker_and_preserves_frame_identity_archive_and_plan(self):
        calls = []
        brokers = []
        archive = b"retained-decrypted-archive"
        reopen_plan = {
            "schema_version": 1,
            "generation": 3,
            "target": "sandbox-recovery-restore-" + "c" * 24,
            "plan_digest": "sha256:" + "d" * 64,
        }

        def process(entry, command, **kwargs):
            calls.append((entry, command, kwargs))
            frame, material = kwargs["input_data"].split(b"\n", 1)
            request = json.loads(frame)
            self.assertEqual(request["operation"], "reopen-restore")
            self.assertEqual(request["request_id"], "a" * 64)
            self.assertEqual(request["target_volume"], None)
            self.assertEqual(request["reopen_plan"], reopen_plan)
            self.assertEqual(request["archive_size"], len(archive))
            self.assertEqual(request["archive_digest"], "sha256:" + hashlib.sha256(archive).hexdigest())
            self.assertEqual(request["credential_size"], 0)
            self.assertEqual(material, archive)
            return SimpleNamespace(returncode=0, stdout=b'{"ok":true,"code":"restore_reopened"}')

        transport = self._transport(process=process, broker=lambda *args: brokers.append(args))
        result = transport.invoke(source(), "reopen-restore", "a" * 64, archive=archive,
                                  target_volume=None, reopen_plan=reopen_plan)
        self.assertEqual(result, b'{"ok":true,"code":"restore_reopened"}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(brokers, [])

    def test_reopen_remote_failure_is_a_closed_acceptance_result(self):
        secret = "private-password-canary"
        transport = self._transport(process=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=("{" + "\"stderr\":\"" + secret + "\"}").encode()))
        with self.assertRaises(Exception) as raised:
            transport.invoke(source(), "reopen-restore", "a" * 64,
                             archive=b"archive", reopen_plan={"generation": 1})
        self.assertRegex(str(raised.exception), "inspection")
        self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()

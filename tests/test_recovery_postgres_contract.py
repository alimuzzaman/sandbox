import copy
import unittest

from sandbox.recovery.errors import RecoveryError
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


class PostgresSourceContractTests(unittest.TestCase):
    def test_local_source_round_trips_and_digest_is_canonical(self):
        value = source()
        parsed = PostgresSource.from_mapping(value)
        self.assertEqual(parsed.as_mapping(), value)
        self.assertEqual(parsed.source_digest, PostgresSource.from_mapping(
            copy.deepcopy(value)).source_digest)
        self.assertRegex(parsed.source_digest, r"^sha256:[0-9a-f]{64}$")

    def test_closed_fields_and_bindings_fail_before_any_source_can_be_used(self):
        for label, change in (
            ("unknown field", lambda value: value.update(extra=True)),
            ("bool schema", lambda value: value.update(schema_version=True)),
            ("bad container", lambda value: value.update(container_id="not-a-container")),
            ("bad image", lambda value: value.update(image_id="sha256:" + "z" * 64)),
            ("different local client", lambda value: value.update(client_image_id="sha256:" + "c" * 64)),
            ("bad database", lambda value: value.update(database="db/name")),
            ("bad remote", lambda value: value.update(remote="../remote")),
        ):
            value = source()
            change(value)
            with self.subTest(label=label), self.assertRaisesRegex(RecoveryError, "source binding is invalid"):
                PostgresSource.from_mapping(value)

    def test_external_credentials_require_legacy_profile_and_registered_source_form(self):
        value = source(profile="lenzora-prod-legacy", credential_reference="personal/PGPASSWORD")
        self.assertEqual(PostgresSource.from_mapping(value).credential_reference,
                         "personal/PGPASSWORD")
        for label, changes in (
            ("credential on local profile", {"credential_reference": "personal/PGPASSWORD"}),
            ("credential on nonlegacy production", {
                "profile": "lenzora-prod", "credential_reference": "personal/PGPASSWORD"}),
            ("unregistered reference", {
                "profile": "lenzora-prod-legacy", "credential_reference": "personal"}),
        ):
            invalid = source(**changes)
            with self.subTest(label=label), self.assertRaises(RecoveryError):
                PostgresSource.from_mapping(invalid)
        external = source(profile="lenzora-prod-legacy", credential_reference="personal/PGPASSWORD",
                          client_image_id="sha256:" + "c" * 64)
        self.assertEqual(PostgresSource.from_mapping(external).client_image_id,
                         "sha256:" + "c" * 64)


if __name__ == "__main__":
    unittest.main()

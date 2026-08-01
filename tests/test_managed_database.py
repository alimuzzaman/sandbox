import unittest


class TestManagedDatabase(unittest.TestCase):
    def test_database_names_are_unique_owned_and_secret_free(self):
        from sandbox.runtimes.managed.database import ManagedDatabase
        database = ManagedDatabase()
        first = database.plan(owner="/tmp/a::default", machine_id="sb-0123456789ab")
        second = database.plan(owner="/tmp/b::default", machine_id="sb-fedcba987654")
        self.assertNotEqual(first["production"], second["production"])
        self.assertFalse(first["network_exposed"])
        self.assertFalse(any("=" in value for value in first["credential_refs"]))
        self.assertTrue(database.validate_observed(first, dict(first)))


if __name__ == "__main__": unittest.main()

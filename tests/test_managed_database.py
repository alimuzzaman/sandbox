import unittest


class Process:
    def __init__(self, *, returncode=0):
        self.returncode = returncode
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return type("Result", (), {"returncode": self.returncode, "stdout": "password=not-output"})()


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

    def test_bootstrap_and_status_use_only_digest_bound_helper_without_credentials(self):
        from sandbox.runtimes.managed.database import ManagedDatabase
        process = Process()
        plan = ManagedDatabase().plan(owner="/tmp/a::default", machine_id="sb-0123456789ab")
        plan["policy_digest"] = "a" * 64
        database = ManagedDatabase(process=process, helper="/fixed/native-helper")
        initialized = database.initialize(plan)
        status = database.status(plan)
        self.assertTrue(initialized["ok"]); self.assertTrue(status["ok"])
        self.assertEqual([call[0][3] for call in process.calls], [
            "database-bootstrap", "database-status",
        ])
        for argv, kwargs in process.calls:
            self.assertEqual(argv, ("sudo", "-n", "/fixed/native-helper", argv[3],
                                    "sb-0123456789ab", "a" * 64))
            self.assertNotIn("credential", repr(argv)); self.assertNotIn("password", repr(argv))
            self.assertEqual(kwargs["timeout"], 120)

    def test_tampered_or_networked_database_plan_fails_before_helper_invocation(self):
        from sandbox.runtimes.managed.database import ManagedDatabase
        process = Process()
        plan = ManagedDatabase().plan(owner="/tmp/a::default", machine_id="sb-0123456789ab")
        plan.update({"policy_digest": "a" * 64, "network_exposed": True})
        with self.assertRaises(ValueError):
            ManagedDatabase(process=process, helper="/fixed/native-helper").initialize(plan)
        self.assertEqual(process.calls, [])


if __name__ == "__main__": unittest.main()

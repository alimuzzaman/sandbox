import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.database import DatabaseCapture
from sandbox.recovery.errors import RecoveryError
from sandbox.services.process import ProcessResult


class DumpRunner:
    def __init__(self, payload=b"-- MariaDB dump\nCREATE TABLE app (id int);\n"):
        self.payload, self.calls = payload, []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        target = Path(argv[argv.index("--file") + 1] if "--file" in argv else argv[argv.index("--result-file") + 1])
        target.write_bytes(self.payload)
        return ProcessResult(tuple(argv), 0, "", "")


class TestDatabaseCapture(unittest.TestCase):
    def test_postgresql_uses_native_consistent_dump_without_credentials(self):
        runner = DumpRunner(b"PGDMP\x00custom dump")
        with tempfile.TemporaryDirectory() as directory:
            outcome = DatabaseCapture(runner).capture("postgresql", "app", Path(directory) / "app.dump")
        command = runner.calls[0][0]
        self.assertEqual(command[:2], ("pg_dump", "--format=custom"))
        self.assertNotIn("password", " ".join(command).lower())
        self.assertEqual(outcome["warnings"], ())
        self.assertTrue(outcome["format_validated"])

    def test_mariadb_uses_single_transaction_and_warns_on_ddl(self):
        runner = DumpRunner()
        with tempfile.TemporaryDirectory() as directory:
            outcome = DatabaseCapture(runner).capture("mariadb", "app", Path(directory) / "app.sql", ddl_risk=True)
        self.assertIn("--single-transaction", runner.calls[0][0])
        self.assertTrue(outcome["warnings"])

    def test_rejects_nontransactional_tables_and_empty_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RecoveryError, "non-transactional"):
                DatabaseCapture(DumpRunner()).capture("mariadb", "app", Path(directory) / "app.sql", nontransactional=True)
            with self.assertRaisesRegex(RecoveryError, "empty"):
                DatabaseCapture(DumpRunner(b"")).capture("postgresql", "app", Path(directory) / "app.dump")

    def test_rejects_option_like_database_names_and_invalid_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = DatabaseCapture(DumpRunner())
            with self.assertRaisesRegex(RecoveryError, "database name"):
                capture.capture("mariadb", "--all", Path(directory) / "app.sql")
            with self.assertRaisesRegex(RecoveryError, "timeout"):
                capture.capture("mariadb", "app", Path(directory) / "app.sql", timeout=0)

    def test_rejects_symlink_dump_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "target.sql"; target.write_bytes(b"keep")
            link = root / "dump.sql"; link.symlink_to(target)
            with self.assertRaisesRegex(RecoveryError, "destination"):
                DatabaseCapture(DumpRunner()).capture("mariadb", "app", link)
            self.assertEqual(target.read_bytes(), b"keep")

    def test_rejects_nonempty_dump_with_invalid_engine_format(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RecoveryError, "format"):
                DatabaseCapture(DumpRunner(b"not a dump")).capture(
                    "postgresql", "app", Path(directory) / "app.dump")

import json
import os
import tempfile
import unittest
from pathlib import Path


class ContractMixin:
    def make_repository(self):
        raise NotImplementedError

    def test_put_get_list_remove_and_unknown_fields(self):
        repo = self.make_repository()
        record = repo.put(
            "/tmp/project", "default", instance="fixture", unknown={"preserve": True}
        )
        self.assertEqual(record["instance"], "fixture")
        self.assertEqual(repo.get("/tmp/project")["unknown"], {"preserve": True})
        self.assertEqual(len(repo.list_for_root("/tmp/project")), 1)
        self.assertTrue(repo.remove("/tmp/project", "default"))
        self.assertIsNone(repo.get("/tmp/project"))


class TestMemoryRepository(ContractMixin, unittest.TestCase):
    def make_repository(self):
        from sandbox.project_registry.memory import MemoryRegistryRepository

        return MemoryRegistryRepository()


class TestRepositoryContractShape(unittest.TestCase):
    def test_protocol_lists_required_operations(self):
        from sandbox.project_registry.base import RegistryRepository

        for name in ("all", "get", "list_for_root", "put", "remove"):
            self.assertTrue(hasattr(RegistryRepository, name), name)

    def test_fixture_directory_contains_supported_and_future_versions(self):
        root = Path(__file__).parent / "fixtures" / "modularity" / "registry"
        self.assertTrue((root / "v1.json").exists())
        self.assertTrue((root / "v2.json").exists())
        self.assertTrue((root / "future-version.json").exists())
        self.assertTrue((root / "corrupt.json").exists())


class TestJsonRepository(ContractMixin, unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.path = Path(self._tmp.name) / "registry.json"

    def tearDown(self):
        self._tmp.cleanup()

    def make_repository(self):
        from sandbox.project_registry.json import JsonRegistryRepository

        return JsonRegistryRepository(self.path)

    def fixture(self, name):
        return Path(__file__).parent / "fixtures" / "modularity" / "registry" / name

    def test_v1_migrates_under_lock_and_preserves_unknown_fields(self):
        from sandbox.project_registry.json import JsonRegistryRepository

        self.path.write_text(self.fixture("v1.json").read_text())
        repo = JsonRegistryRepository(self.path)
        records = repo.all()
        self.assertIn("/tmp/sandbox-fixture/project::default", records)
        self.assertEqual(records["/tmp/sandbox-fixture/project::default"]["future_field"],
                         {"preserve": True})
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["version"], 2)

    def test_future_version_fails_without_rewrite(self):
        from sandbox.project_registry.base import UnsupportedRegistryVersion
        from sandbox.project_registry.json import JsonRegistryRepository

        original = self.fixture("future-version.json").read_text()
        self.path.write_text(original)
        with self.assertRaises(UnsupportedRegistryVersion):
            JsonRegistryRepository(self.path).all()
        self.assertEqual(self.path.read_text(), original)

    def test_corrupt_file_reports_without_rewrite(self):
        from sandbox.project_registry.base import RegistryCorruption
        from sandbox.project_registry.json import JsonRegistryRepository

        original = self.fixture("corrupt.json").read_text()
        self.path.write_text(original)
        with self.assertRaises(RegistryCorruption):
            JsonRegistryRepository(self.path).all()
        self.assertEqual(self.path.read_text(), original)

    def test_failed_atomic_replace_keeps_previous_file(self):
        from sandbox.project_registry.json import JsonRegistryRepository

        self.path.write_text(json.dumps({"version": 2, "instances": {}}) + "\n")
        original = self.path.read_text()

        def fail_replace(_src, _dst):
            raise OSError("injected replace failure")

        repo = JsonRegistryRepository(self.path, replace=fail_replace)
        with self.assertRaisesRegex(OSError, "injected"):
            repo.put("/tmp/project", instance="fixture")
        self.assertEqual(self.path.read_text(), original)

    def test_lock_file_is_created_outside_registry_document(self):
        repo = self.make_repository()
        with repo.transaction():
            self.assertTrue(self.path.with_name("registry.lock").exists())
        self.assertFalse(self.path.exists())

    def test_json_repository_rejects_invalid_persisted_identity(self):
        from sandbox.project_registry.base import RegistryCorruption
        from sandbox.project_registry.json import JsonRegistryRepository

        self.path.write_text(json.dumps({"version": 2, "instances": {
            "/tmp/project::bad label": {
                "root": "/tmp/project", "label": "bad label", "is_default": True,
            },
        }}))
        with self.assertRaises(RegistryCorruption):
            JsonRegistryRepository(self.path).all()

    def test_json_repository_backfills_identity_omitted_by_older_v2_writer(self):
        from sandbox.project_registry.json import JsonRegistryRepository

        key = "/tmp/project"
        self.path.write_text(json.dumps({"version": 2, "instances": {
            key: {"root": "/tmp/project", "instance": "fixture"},
        }}))
        record = JsonRegistryRepository(self.path).all()[key]
        self.assertEqual(record["label"], "default")
        self.assertTrue(record["is_default"])
        stored = json.loads(self.path.read_text())["instances"][key]
        self.assertEqual(stored["label"], "default")

    def test_repositories_reject_broad_roots_and_unsafe_labels(self):
        from sandbox.project_registry.json import JsonRegistryRepository
        from sandbox.project_registry.memory import MemoryRegistryRepository

        for repo in (JsonRegistryRepository(self.path), MemoryRegistryRepository()):
            with self.subTest(repository=type(repo).__name__), self.assertRaises(ValueError):
                repo.put("/", "default")
            with self.subTest(repository=type(repo).__name__), self.assertRaises(ValueError):
                repo.put("/tmp/project", "bad label")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest


class MemoryRegistry:
    def __init__(self, record):
        self.record = dict(record)
        self.puts = []

    def registry_get(self, _root, label=None):
        return dict(self.record) if self.record else None

    def registry_put(self, root, label="default", **fields):
        self.puts.append((root, label, fields))
        self.record.update(fields)
        return dict(self.record)


class TestDomainIdentityLifecycle(unittest.TestCase):
    def test_persistence_does_not_overwrite_existing_identity(self):
        from sandbox.application.instance_service import persist_hostname_intent

        registry = MemoryRegistry({"domain": "existing.tst", "instance": "demo"})
        result = persist_hostname_intent(
            registry, "/tmp/project", "default", "new.test", "default",
        )
        self.assertEqual(result["domain"], "existing.tst")
        self.assertEqual(registry.puts, [])

    def test_persistence_records_new_identity_and_source_once(self):
        from sandbox.application.instance_service import persist_hostname_intent

        registry = MemoryRegistry({"instance": "demo"})
        result = persist_hostname_intent(
            registry, "/tmp/project", "default", "demo.test", "default",
        )
        self.assertEqual(result["domain"], "demo.test")
        self.assertEqual(registry.puts[0][2]["domain_source"], "default")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class TestDomainWildcardLifecycle(unittest.TestCase):
    def test_binding_identity_is_stable_across_owners(self):
        from sandbox.network.models import ResolutionBinding

        first = ResolutionBinding.create(
            kind="zone", name="*.site.test", target="127.0.0.77",
            adapter_id="resolved", owners=("/tmp/one::default",), desired={"zone": "site.test"},
        )
        second = ResolutionBinding.create(
            kind="zone", name="*.site.test", target="127.0.0.77",
            adapter_id="resolved", owners=("/tmp/two::default",), desired={"zone": "site.test"},
        )
        self.assertEqual(first.binding_id, second.binding_id)

    def test_repository_merges_owners_and_retains_until_last_release(self):
        from sandbox.network.models import ResolutionBinding
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = DomainRepository(Path(tmp) / "state.json")
            first = ResolutionBinding.create(
                kind="zone", name="*.site.test", target="127.0.0.77",
                adapter_id="resolved", owners=("/tmp/one::default",), desired={"zone": "site.test"},
            ).with_applied({"route": "owned"})
            second = ResolutionBinding.create(
                kind="zone", name="*.site.test", target="127.0.0.77",
                adapter_id="resolved", owners=("/tmp/two::default",), desired={"zone": "site.test"},
            )
            repository.put_binding(first)
            repository.put_binding(second)
            shared = repository.binding(first.binding_id)
            self.assertEqual(shared.owners, ("/tmp/one::default", "/tmp/two::default"))
            self.assertEqual(repository.release_binding_owner(
                first.binding_id, "/tmp/one::default",
            ), "retained")
            self.assertEqual(repository.binding(first.binding_id).owners,
                             ("/tmp/two::default",))
            self.assertEqual(repository.release_binding_owner(
                first.binding_id, "/tmp/two::default",
            ), "last")
            self.assertIsNotNone(repository.binding(first.binding_id))


if __name__ == "__main__":
    unittest.main()

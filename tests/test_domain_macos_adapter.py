from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class TestMacosDomainAdapter(unittest.TestCase):
    def test_plan_uses_only_exact_owned_resolver_fragment(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = MacosResolverAdapter(
                helper="/fixed/helper", process=object(), staging_root=Path(tmp),
            )
            plan = adapter.plan("test", "127.0.0.54", 5300)
        self.assertEqual(plan["destination"], "/etc/resolver/test")
        self.assertNotIn("/etc/resolv.conf", str(plan))


if __name__ == "__main__":
    unittest.main()

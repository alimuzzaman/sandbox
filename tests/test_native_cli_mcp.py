import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


class TestNativeCliMcp(unittest.TestCase):
    def module(self):
        root = Path(__file__).parent.parent / "mcp/wp-server"
        if str(root) not in sys.path: sys.path.insert(0, str(root))
        spec = importlib.util.spec_from_file_location("native_runtime_tools_test",
                                                      root / "tools/runtime.py")
        value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value

    def test_mcp_support_and_preflight_are_nonmutating(self):
        runtime = self.module()
        runtime._native_preflight = lambda: SimpleNamespace(inspect=lambda: {
            "ok": False, "state": "blocked", "mutated": False,
            "reason": {"code": "isolation_prerequisite_missing"},
        })
        self.assertFalse(runtime.native_support()["mutated"])
        self.assertFalse(runtime.native_preflight()["mutated"])

    def test_mcp_install_plan_never_applies_or_prompts(self):
        from tests.test_managed_package_plan import TestManagedPackagePlan
        runtime = self.module(); plan = TestManagedPackagePlan().planner().plan()
        runtime._managed_package_planner = lambda: SimpleNamespace(
            plan=lambda **kwargs: plan)
        result = runtime.native_install_plan("nginx")
        self.assertTrue(result["ok"]); self.assertFalse(result["mutated"])
        self.assertEqual(result["simulation_digest"], plan.simulation_digest)


if __name__ == "__main__": unittest.main()

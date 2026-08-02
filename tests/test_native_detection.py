import unittest


class TestNativeDetection(unittest.TestCase):
    def test_local_xampp_laragon_wamp_are_detect_only_and_platform_scoped(self):
        from sandbox.runtimes.manifest import detect_only_runtime_declarations
        linux = {item["adapter_id"]: item for item in detect_only_runtime_declarations("linux")}
        windows = {item["adapter_id"]: item for item in detect_only_runtime_declarations("windows")}
        self.assertTrue(linux["local"]["available"]); self.assertTrue(linux["xampp"]["available"])
        self.assertFalse(linux["laragon"]["available"]); self.assertFalse(linux["wamp"]["available"])
        self.assertTrue(windows["laragon"]["available"]); self.assertTrue(windows["wamp"]["available"])
        self.assertTrue(all(item["mode"] == "detect_only" and not item["adoptable"] for item in linux.values()))

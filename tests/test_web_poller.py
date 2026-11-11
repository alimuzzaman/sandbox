"""Run the dashboard poller tests and check the vendored bundle carries the fix."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebPollerTests(unittest.TestCase):
    def test_poller_node_suite(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
        match = re.match(r"v(\d+)", version)
        if not match or int(match.group(1)) < 24:
            self.skipTest("node 24 or newer runs TypeScript tests directly")
        result = subprocess.run(
            [node, "--test", "src/web/tests/poller.test.ts", "src/web/tests/hostlabels.test.ts",
             "src/web/tests/sharelabels.test.ts"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-1000:])

    def test_vendored_bundle_contains_the_polling_fix(self):
        bundle = (ROOT / "config" / "sandbox-web.js").read_text(encoding="utf-8")
        self.assertIn("Lost contact with this job", bundle)
        self.assertIn("Lost contact with this command", bundle)
        self.assertIn("is busy:", bundle)

    def test_vendored_bundle_contains_the_lifetime_follow_up(self):
        bundle = (ROOT / "config" / "sandbox-web.js").read_text(encoding="utf-8")
        for text in ("unclassified, protected", "applied before tracking", "delivery unverified",
                     "Hosted apps (sb host apply / stacks)", "Sandbox dev instances", "point-in-time"):
            self.assertIn(text, bundle)


if __name__ == "__main__":
    unittest.main()

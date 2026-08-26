import json
import subprocess
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestGuideBootstrapHint(unittest.TestCase):
    def test_wordpress_guide_labels_status_as_post_bootstrap(self):
        result = subprocess.run(
            [str(ROOT / "sb"), "guide", "--project-dir", ".", "--json"],
            cwd=str(ROOT), capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        status = next(item for item in payload["commands"] if item["name"] == "status")
        self.assertIn("after init/ensure", status["purpose"])
        self.assertIn("bootstrap hint", status["purpose"])


if __name__ == "__main__":
    unittest.main()

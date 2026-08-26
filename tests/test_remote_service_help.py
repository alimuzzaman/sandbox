from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestRemoteServiceHelp(unittest.TestCase):
    def test_remote_help_lists_nested_service_forms(self):
        result = subprocess.run(
            [str(ROOT / "sb"), "remote", "--help"],
            cwd=str(ROOT), capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("./sb remote service status NAME [--json]", result.stdout)
        self.assertIn("./sb remote service migrate NAME --plan|--confirm [--json]",
                      result.stdout)


if __name__ == "__main__":
    unittest.main()

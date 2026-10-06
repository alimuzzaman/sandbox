"""Deploy warns when a Composer plugin's ignored vendor/ would not be staged."""

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from sandbox.commands import deploy


class DeployVendorWarningTests(unittest.TestCase):
    def _repo(self, temp, *, ignore_vendor=True):
        root = Path(temp)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "composer.json").write_text("{}")
        if ignore_vendor:
            (root / ".gitignore").write_text("vendor/\n")
        return root

    def _warning(self, root, include=()):
        err = io.StringIO()
        with redirect_stderr(err):
            deploy._warn_unstaged_composer_vendor(root, list(include), as_json=False)
        return err.getvalue()

    def test_warns_when_vendor_is_ignored_and_not_included(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn("--include vendor", self._warning(self._repo(temp)))

    def test_silent_when_vendor_included_or_tracked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self._repo(temp)
            self.assertEqual(self._warning(root, ["vendor/autoload.php"]), "")
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self._warning(self._repo(temp, ignore_vendor=False)), "")


if __name__ == "__main__":
    unittest.main()

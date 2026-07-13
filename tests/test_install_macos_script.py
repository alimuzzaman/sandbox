"""Regression checks for the macOS bootstrap's optional documentation reader."""

from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "install-macos.sh"


class InstallMacosScriptTests(unittest.TestCase):
    def test_reader_md_is_defaulted_and_can_be_skipped(self):
        script = SCRIPT.read_text()

        self.assertIn('step "4/4  Reader.md"', script)
        self.assertIn('SANDBOX_SKIP_READER_MD:-', script)
        self.assertIn('brew tap jnahian/reader.md https://github.com/jnahian/reader.md', script)
        self.assertIn('brew trust --cask jnahian/reader.md/reader-md', script)
        self.assertIn('brew install --cask reader-md', script)

    def test_reader_failure_does_not_block_sandbox_setup(self):
        script = SCRIPT.read_text()

        self.assertIn('warn "Reader.md could not be installed; continuing without it."', script)
        self.assertIn('# --- Hand off to install.sh', script)


if __name__ == "__main__":
    unittest.main()

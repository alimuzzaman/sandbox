"""Static contract checks for the locally completed Spec 003 interface convergence."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "specs/003-wp-abilities-adapter/quickstart.md"
CLI_CONTRACT = ROOT / "specs/003-wp-abilities-adapter/contracts/cli-contract.md"
ABILITIES_CONTRACT = ROOT / "specs/003-wp-abilities-adapter/contracts/abilities.md"
COMMAND = ROOT / "sandbox/commands/abilities.py"


class Spec003InterfaceDocumentationTests(unittest.TestCase):
    def test_documented_connect_command_matches_the_registered_command(self):
        quickstart = QUICKSTART.read_text()
        contract = CLI_CONTRACT.read_text()
        command = COMMAND.read_text()

        self.assertIn("./sb abilities connect", quickstart)
        self.assertNotIn("./sb connect --client", quickstart)
        self.assertIn("<on|off|status|connect>", contract)
        self.assertIn('"connect"', command)

    def test_documented_file_surfaces_match_registered_tools(self):
        quickstart = QUICKSTART.read_text()
        contract = CLI_CONTRACT.read_text()
        abilities = ABILITIES_CONTRACT.read_text()

        self.assertIn("sandbox/write-file", quickstart)
        self.assertIn("fs_read", quickstart)
        self.assertNotIn("wp_file_write", quickstart)
        self.assertIn("no separate `wp_file_*` proxy tools are registered", contract)
        self.assertNotIn("mcp-adapter/discover-abilities` (override)", abilities)


if __name__ == "__main__":
    unittest.main()

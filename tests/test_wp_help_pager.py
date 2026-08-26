import unittest

from sandbox.commands import wp


class TestWpHelpPager(unittest.TestCase):
    def test_help_gets_no_pager_switch(self):
        self.assertEqual(
            wp._disable_help_pager(["help", "w3-total-cache", "option", "set"]),
            ["help", "w3-total-cache", "option", "set", "--no-pager"],
        )

    def test_global_option_before_help_is_supported(self):
        self.assertEqual(
            wp._disable_help_pager(["--require=fixture.php", "help", "plugin"])[-1],
            "--no-pager",
        )

    def test_explicit_pager_choice_is_preserved(self):
        self.assertEqual(
            wp._disable_help_pager(["help", "plugin", "--pager"]),
            ["help", "plugin", "--pager"],
        )
        self.assertEqual(
            wp._disable_help_pager(["help", "plugin", "--no-pager"]),
            ["help", "plugin", "--no-pager"],
        )

    def test_unrelated_commands_are_unchanged(self):
        self.assertEqual(wp._disable_help_pager(["plugin", "list"]), ["plugin", "list"])


if __name__ == "__main__":
    unittest.main()

import unittest

from sandbox.commands import wp


class TestWpEvalDiagnostic(unittest.TestCase):
    def test_eval_parse_error_drops_only_generic_wrapper(self):
        stdout, stderr = wp._clean_eval_parse_diagnostic(
            ["eval", "echo \\\\Broken;"],
            "Parse error: unexpected token\nThere has been a critical error on this website\n",
            "",
        )

        self.assertIn("Parse error: unexpected token", stdout)
        self.assertNotIn("critical error on this website", stdout.lower())

    def test_eval_runtime_fatal_wrapper_is_preserved(self):
        stdout, stderr = wp._clean_eval_parse_diagnostic(
            ["eval", "throw new Exception();"],
            "Fatal error: uncaught Exception\nThere has been a critical error on this website\n",
            "",
        )

        self.assertIn("Fatal error", stdout)
        self.assertIn("critical error on this website", stdout)

    def test_non_eval_parse_error_is_unchanged(self):
        value = "Parse error: bad plugin\nThere has been a critical error on this website\n"
        self.assertEqual(wp._clean_eval_parse_diagnostic(["plugin", "list"], value, ""), (value, ""))


if __name__ == "__main__":
    unittest.main()

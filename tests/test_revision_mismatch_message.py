"""Revision mismatch errors name both revisions so a matching checkout is findable."""

import unittest

from sandbox.resources.context import _revision_mismatch_message


class RevisionMismatchMessageTests(unittest.TestCase):
    def test_names_short_revisions_and_inspection_command(self):
        message = _revision_mismatch_message("a" * 40, "b" * 40)
        self.assertIn("a" * 12, message)
        self.assertIn("b" * 12, message)
        self.assertNotIn("a" * 13, message)
        self.assertIn("sb remote service status", message)

    def test_non_hex_values_are_not_echoed(self):
        message = _revision_mismatch_message("token=secret", None)
        self.assertNotIn("secret", message)
        self.assertIn("unknown", message)


if __name__ == "__main__":
    unittest.main()

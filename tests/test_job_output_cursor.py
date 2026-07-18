import unittest

from sandbox.jobs.models import OutputQuery


class OutputCursorModelTests(unittest.TestCase):
    def test_only_one_position_selector_is_allowed(self):
        with self.assertRaises(ValueError):
            OutputQuery(cursor="abc", offset=0)
        self.assertEqual(OutputQuery(wait_seconds=20).wait_seconds, 20)

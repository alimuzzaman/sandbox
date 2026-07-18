import unittest

from sandbox.jobs.models import OutputProfile
from sandbox.jobs.output import present_output


class OutputProfileTests(unittest.TestCase):
    def test_sampled_errors_quiet_and_smart_are_display_only(self):
        page = {"encoding": "utf8", "data": "one\nerror two\none\nthree\n", "events": []}
        self.assertEqual(present_output(page, OutputProfile("s", mode="sampled", every_lines=2))["data"], "error two\nthree\n")
        self.assertEqual(present_output(page, OutputProfile("e", mode="errors"))["data"], "error two\n")
        self.assertEqual(present_output(page, OutputProfile("q", mode="quiet"))["data"], "")
        self.assertEqual(present_output(page, OutputProfile("smart", deduplicate=True))["data"], "one\nerror two\nthree\n")

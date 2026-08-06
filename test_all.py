"""Bridge Python's default unittest invocation to the repository test directory."""

import unittest


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str,
) -> unittest.TestSuite:
    """Discover the non-package ``tests/`` directory for ``python -m unittest``."""
    return loader.discover(start_dir="tests", pattern=pattern, top_level_dir="tests")

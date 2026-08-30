"""Privacy assertions shared by Feature 046 tests."""

from __future__ import annotations

import json

FORBIDDEN = ("stdout", "stderr", "environment", "command_line", "argv", "pid",
             "process_name", "container_id", "/var/", "/etc/", "bearer_token")


def assert_privacy_bounded(testcase, payload, *, maximum=1024 * 1024):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    testcase.assertLessEqual(len(encoded.encode()), maximum)
    lowered = encoded.lower()
    for value in FORBIDDEN:
        testcase.assertNotIn(value, lowered)

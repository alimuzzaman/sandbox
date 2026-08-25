import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


class VisitWrapperTests(unittest.TestCase):
    def test_json_selector_after_url_is_consumed_by_sandbox_wrapper(self):
        from sandbox.commands import wp

        with patch.object(wp, "ensure_tools_venv", return_value=Path("/venv/bin/python")), \
                patch.object(wp, "TOOLS_DIR", Path("/sandbox/tools")), \
                patch.object(wp.os, "execv") as execv:
            wp.cmd_visit({}, SimpleNamespace(
                passthrough=["http://example.test", "--json", "--timeout", "15"],
            ))

        execv.assert_called_once_with(
            "/venv/bin/python",
            ["/venv/bin/python", "/sandbox/tools/visit/visit.py",
             "http://example.test", "--timeout", "15"],
        )


if __name__ == "__main__":
    unittest.main()

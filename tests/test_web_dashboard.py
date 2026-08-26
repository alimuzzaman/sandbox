import unittest
from unittest.mock import patch


class TestWebDashboardRemoteSummaries(unittest.TestCase):
    def test_remote_summary_rows_are_lightweight_and_secret_free(self):
        from sandbox.commands.ui_dash import _remote_summary_rows

        with patch(
            "sandbox.core._remote.list_remotes",
            return_value={
                "scaleway-sandbox": {
                    "provisioned": True,
                    "control_url": "https://control.example.test",
                    "bearer_token": "token-value-must-not-escape",
                    "ssh": "ssh://operator@example.test",
                },
                "offline": {
                    "provisioned": False,
                    "control_url": "",
                    "bearer_token": "",
                },
            },
        ):
            rows = _remote_summary_rows()

        self.assertEqual(
            rows,
            [
                {"name": "offline", "provisioned": False, "control_ready": False},
                {"name": "scaleway-sandbox", "provisioned": True, "control_ready": True},
            ],
        )
        self.assertNotIn("bearer_token", str(rows))
        self.assertNotIn("ssh", str(rows))


if __name__ == "__main__":
    unittest.main()

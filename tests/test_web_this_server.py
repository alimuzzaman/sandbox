import unittest
from unittest import mock

import sandbox.core._remote as remote_core
from sandbox.commands import ui_dash

SNAPSHOT = {"ok": True, "inventory_schema": 1, "transport": "control",
            "instances": {"total": 0, "running": 0, "stopped": 0, "rows": []}}


class ThisServerListTest(unittest.TestCase):
    def rows(self, hosts_control, registered=None):
        with mock.patch.object(ui_dash, "_hosts_control_service", return_value=hosts_control), \
                mock.patch.object(remote_core, "list_remotes", return_value=registered or {}):
            return ui_dash._remote_summary_rows()

    def test_a_server_running_the_control_service_lists_itself_first(self):
        rows = self.rows(True, {"other": {"provisioned": True}})
        self.assertEqual(rows[0], {"name": "this-server", "provisioned": True,
                                   "control_ready": True, "local": True})
        self.assertEqual([row["name"] for row in rows], ["this-server", "other"])

    def test_a_controller_without_the_service_does_not(self):
        self.assertEqual(self.rows(False), [])

    def test_a_registered_remote_with_that_name_wins(self):
        rows = self.rows(True, {"this-server": {"provisioned": False}})
        self.assertEqual(rows, [{"name": "this-server", "provisioned": False, "control_ready": False}])


class ThisServerInventoryTest(unittest.TestCase):
    def view(self, name, *, hosts_control=True, registered=None, deep=False):
        with mock.patch.object(ui_dash, "_hosts_control_service", return_value=hosts_control), \
                mock.patch.object(remote_core, "get_remote", side_effect=(registered or {}).get), \
                mock.patch.object(remote_core, "remote_inventory", return_value=dict(SNAPSHOT)) as remote, \
                mock.patch("sandbox.remote_runtime.host_inventory._hosted_inventory_snapshot",
                           return_value=dict(SNAPSHOT, secret_url="https://u:p@example.test/")) as local:
            code, payload = ui_dash._remote_view(name, deep)
        return code, payload, remote, local

    def test_this_server_is_built_locally_without_a_token_or_http(self):
        code, payload, remote, local = self.view("this-server", deep=True)
        self.assertEqual((code, payload["name"], payload["inventory_schema"]), (200, "this-server", 1))
        local.assert_called_once_with(deep=True)
        remote.assert_not_called()

    def test_its_inventory_is_redacted_like_a_remote_one(self):
        _, payload, _, _ = self.view("this-server")
        self.assertNotIn("u:p@", str(payload))

    def test_it_is_unknown_where_no_control_service_runs(self):
        code, _, _, local = self.view("this-server", hosts_control=False)
        self.assertEqual(code, 404)
        local.assert_not_called()

    def test_registered_remotes_still_go_through_their_control_service(self):
        code, payload, remote, local = self.view("box", registered={"box": {"control_url": "x"}})
        self.assertEqual((code, payload["name"]), (200, "box"))
        remote.assert_called_once()
        local.assert_not_called()

    def test_a_failed_local_build_is_reported_unavailable(self):
        with mock.patch.object(ui_dash, "_hosts_control_service", return_value=True), \
                mock.patch.object(remote_core, "get_remote", return_value=None), \
                mock.patch("sandbox.remote_runtime.host_inventory._hosted_inventory_snapshot",
                           side_effect=RuntimeError("boom")):
            code, payload = ui_dash._remote_view("this-server", False)
        self.assertEqual((code, payload["evidence_status"]), (503, "unavailable"))


if __name__ == "__main__":
    unittest.main()

"""Stack page metadata never mints or probes a remote."""
from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace

from sandbox.hosting import panel_access
from sandbox.hosting import panel_view as pv
from tests.test_panel_view import summary, server, COMMIT


class StackPageMetadataTests(unittest.TestCase):
    def view(self):
        return pv.build_view([server([summary(remote="r")])])

    def test_shared_names_are_direct_and_inbox_comes_from_recorded_port(self):
        view = self.view()
        view["stacks"][0]["source"] = "controller"
        records = {"stacks": {"r/demo": {"uses": ["dev"]}, "r/dev": {"uses": ["demo"]}}}
        with patch.object(panel_access, "inbox_names", return_value={18142: "mail-demo.example.test"}):
            panel_access.enrich(view, records, {})
        access = view["stacks"][0]["page_access"]
        self.assertEqual(access["uses"], ["dev"])
        self.assertEqual(access["inbox"], {"hostname": "mail-demo.example.test",
                                           "url": "https://mail-demo.example.test"})

    def test_missing_inbox_has_guidance_and_no_broken_url(self):
        view = self.view()
        with patch.object(panel_access, "inbox_names", return_value={}):
            panel_access.enrich(view, {}, {})
        access = view["stacks"][0]["page_access"]
        self.assertIsNone(access["inbox"])
        self.assertIn("18142", access["inbox_reason"])
        self.assertIn("sb host tailnet-name add", access["inbox_reason"])

    def test_controller_revision_needs_runtime_evidence(self):
        for observed, expected in ((None, "unverified"), (COMMIT, "verified")):
            view = self.view()
            view["stacks"][0]["source"] = "controller"
            with patch.object(panel_access, "inbox_names", return_value={}):
                panel_access.enrich(view, {}, {"hosts": {
                    "r/xspeed-hub-demo/staging": {"observed_runtime_revision": observed}}})
            self.assertEqual(view["stacks"][0]["page_access"]["revision_status"], expected)

    def test_inbox_discovery_ignores_unpublished_and_non_loopback_names(self):
        records = {
            "mail.example.test": SimpleNamespace(state="published", upstream="127.0.0.1:18142"),
            "pending.example.test": SimpleNamespace(state="publishing", upstream="127.0.0.1:18143"),
            "private.example.test": SimpleNamespace(state="published", upstream="10.0.0.2:18144"),
        }
        with patch.object(panel_access.StateStore, "load", return_value=records):
            self.assertEqual(panel_access.inbox_names("r"), {18142: "mail.example.test"})

    def test_no_commit_is_not_delivered(self):
        view = self.view()
        view["stacks"][0]["commit"] = ""
        with patch.object(panel_access, "inbox_names", return_value={}):
            panel_access.enrich(view, {}, {})
        self.assertEqual(view["stacks"][0]["page_access"]["revision_status"], "not_delivered")

    def test_controller_uses_job_refined_delivered_commit_for_own_routes(self):
        old, requested = "a" * 40, "b" * 40
        record = {"remote": "r", "slug": "demo", "project": "app", "environment": "test",
                  "commit": old, "hostnames": ["app.example.test"], "ports": {},
                  "last_apply": {"commit": requested, "status": "submitted", "submitted_at": 1}}
        source = pv.controller_records({"stacks": {"r/demo": record}}, {},
                                       lambda row: {**row["last_apply"], "status": "succeeded"})
        stack = pv.build_view([source])["stacks"][0]
        self.assertEqual(stack["commit"], requested)
        self.assertEqual(stack["servers"][0]["ref"]["commit"], requested)


class RecordedSignInTests(unittest.TestCase):
    def test_private_target_and_mismatched_manifest_never_mint(self):
        from sandbox.commands import hosting as command
        from sandbox.hosting import target_resolution, login_service
        import sandbox.core._hosting as hosting
        resolved = SimpleNamespace(project_dir='/recorded', environment='test', project='app')
        validated = {'project': 'app', 'environment': 'test', 'autologin': {'user': 'admin'},
                     'routes': [{'primary': True, 'hostname': 'wp.example.test'}],
                     'login_service': {}}
        with patch.object(target_resolution, 'resolve', return_value=resolved), \
                patch.object(hosting, 'validate_manifest', return_value=validated), \
                patch.object(login_service, 'is_private', return_value=True), \
                patch.object(command, '_issue_host_autologin') as mint:
            with self.assertRaises(panel_access.SignInUnavailable):
                panel_access.sign_in('r', 'demo')
            validated['project'] = 'other'
            with self.assertRaises(panel_access.SignInUnavailable):
                panel_access.sign_in('r', 'demo')
        mint.assert_not_called()

    def test_sign_in_uses_recorded_manifest_and_locked_state(self):
        from sandbox.commands import hosting as command
        from sandbox.hosting import target_resolution
        import sandbox.core._hosting as hosting
        import sandbox.core._remote as remote
        resolved = SimpleNamespace(project_dir='/recorded', environment='test', project='app')
        validated = {'project': 'app', 'environment': 'test', 'autologin': {'user': 'admin'},
                     'routes': [{'primary': True, 'hostname': 'wp.example.test'}]}
        state, save = {}, lambda value: None
        def locked(manifest, name, callback):
            return callback(state, save)
        with patch.object(target_resolution, 'resolve', return_value=resolved) as resolve, \
                patch.object(hosting, 'validate_manifest', return_value=validated) as validate, \
                patch.object(remote, 'get_remote', return_value={'provisioned': True}), \
                patch.object(command, '_with_host_writer_lock', side_effect=locked), \
                patch.object(command, '_issue_host_autologin', return_value={'url': 'once'}) as mint:
            self.assertEqual(panel_access.sign_in('r', 'demo'), {'url': 'once'})
        resolve.assert_called_once_with('r', name='demo')
        validate.assert_called_once_with('/recorded', 'test')
        self.assertIs(mint.call_args.args[3], state)
        self.assertIs(mint.call_args.kwargs['save_state'], save)

    def test_availability_explains_server_missing_and_undeclared_states(self):
        from sandbox.hosting import target_resolution
        import sandbox.core._hosting as hosting
        stack = {'source': 'this-server', 'remote': 'r', 'slug': 'demo'}
        status = panel_access.sign_in_availability(stack, {}, {})
        self.assertFalse(status['available'])
        self.assertEqual(status['code'], 'sign_in_controller_required')
        stack['source'] = 'controller'
        with patch.object(target_resolution, 'resolve', side_effect=OSError('private path')):
            status = panel_access.sign_in_availability(stack, {}, {})
        self.assertEqual(status['code'], 'sign_in_manifest_unavailable')
        self.assertNotIn('private path', status['message'])
        resolved = SimpleNamespace(project_dir='/recorded', environment='test', project='app')
        with patch.object(target_resolution, 'resolve', return_value=resolved), \
                patch.object(hosting, 'validate_manifest', return_value={'project': 'app', 'environment': 'test'}):
            status = panel_access.sign_in_availability(stack, {}, {})
        self.assertEqual(status['code'], 'sign_in_not_declared')

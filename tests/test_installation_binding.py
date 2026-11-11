"""Observable identity lifecycle, read purity and drift refusal without host access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sandbox.installation import binding, extensions
from sandbox.commands import installation


class InstallationBindingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.patches = [mock.patch.object(binding, 'machine_identity', return_value='a' * 24),
                        mock.patch.object(binding, 'runtime_revision', return_value='b' * 24),
                        mock.patch.dict(os.environ, {'SANDBOX_REMOTE_MCP_MARKER': 'c' * 24}),
                        mock.patch.object(extensions, '_PROVIDERS', {})]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def initialize(self):
        binding.initialize_installation(home=self.home, ownership_marker='c' * 24, confirm=True)
        return binding.read_installation_binding(home=self.home)

    def test_missing_read_is_side_effect_free(self):
        before = list(self.home.iterdir())
        with self.assertRaises(binding.InstallationBindingError) as failure:
            binding.read_installation_binding(home=self.home)
        self.assertEqual(failure.exception.code, 'installation_binding_uninitialized')
        self.assertEqual(list(self.home.iterdir()), before)

    def test_explicit_initialize_is_stable_and_discovery_read_only(self):
        first = self.initialize()
        self.assertEqual(first, self.initialize())
        files = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(first, binding.read_installation_binding(home=self.home))
        self.assertEqual(files, {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in files})
        self.assertEqual(first['layout_kind'], 'owner')
        self.assertEqual(first['execution_capabilities'], {})
        self.assertRegex(first['installation_id'], r'^[0-9a-f]{32}$')
        self.assertRegex(first['incarnation'], r'^[0-9a-f]{32}$')
        self.assertNotEqual(first['installation_id'], first['incarnation'])

    def test_confirmation_and_foreign_reinitialization_refuse(self):
        with self.assertRaises(binding.InstallationBindingError):
            binding.initialize_installation(home=self.home, ownership_marker='c' * 24)
        first = self.initialize()
        with self.assertRaises(binding.InstallationBindingError):
            binding.initialize_installation(home=self.home, ownership_marker='d' * 24, confirm=True)
        self.assertEqual(first, binding.read_installation_binding(home=self.home))

    def test_policy_and_machine_drift_refuse_current_binding(self):
        first = self.initialize()
        path = self.home / 'runtime/installation/policy.json'
        policy = json.loads(path.read_text())
        policy['revision'] = 'admitted-owner-v2'
        path.write_text(json.dumps(policy))
        with self.assertRaises(binding.InstallationBindingError) as failure:
            binding.validate_current(first, home=self.home)
        self.assertEqual(failure.exception.code, 'installation_binding_drift')
        with mock.patch.object(binding, 'machine_identity', return_value='d' * 24):
            with self.assertRaises(binding.InstallationBindingError) as failure:
                binding.read_installation_binding(home=self.home)
        self.assertEqual(failure.exception.code, 'installation_machine_mismatch')

    def test_unsafe_identity_file_or_service_marker_refuses(self):
        self.initialize()
        path = self.home / 'runtime/installation/identity.json'
        path.chmod(0o644)
        with self.assertRaises(binding.InstallationBindingError):
            binding.read_installation_binding(home=self.home)
        path.chmod(0o600)
        with mock.patch.dict(os.environ, {'SANDBOX_REMOTE_MCP_MARKER': 'd' * 24}):
            with self.assertRaises(binding.InstallationBindingError):
                binding.read_installation_binding(home=self.home)
        saved = path.with_name('saved.json')
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(binding.InstallationBindingError):
            binding.read_installation_binding(home=self.home)

    def test_capabilities_require_installed_verifier_and_changes_invalidate_binding(self):
        first = self.initialize()
        extensions.register_capability('fixture.worker', 1, lambda _: False)
        self.assertEqual(binding.read_installation_binding(home=self.home)['execution_capabilities'], {})
        extensions.register_capability('fixture.ready', 2, lambda _: True)
        fresh = binding.read_installation_binding(home=self.home)
        self.assertEqual(fresh['execution_capabilities'], {'fixture.ready': 2})
        with self.assertRaises(binding.InstallationBindingError):
            binding.validate_current(first, home=self.home)

    def test_personal_layout_is_not_treated_as_managed(self):
        self.assertFalse(binding.is_managed_layout(home=self.home))
        self.initialize()
        self.assertFalse(binding.is_managed_layout(home=self.home))

    def test_initialization_never_substitutes_persisted_for_missing_live_marker(self):
        first = self.initialize()
        with mock.patch.dict(os.environ, {}, clear=True):
            initialized = binding.initialize_installation(home=self.home,
                ownership_marker='c' * 24, confirm=True)
            self.assertEqual(initialized['installation_id'], first['installation_id'])
            self.assertNotIn('runtime_revision', initialized)
            with self.assertRaises(binding.InstallationBindingError) as failure:
                binding.read_installation_binding(home=self.home)
            self.assertEqual(failure.exception.code, 'installation_service_unavailable')
            with self.assertRaises(binding.InstallationBindingError):
                binding.validate_current(first, home=self.home)

    def test_boolean_schema_cannot_equal_integer_schema_in_bound_input(self):
        first = self.initialize()
        with self.assertRaises(binding.InstallationBindingError):
            binding.validate_current({**first, 'schema_version': True}, home=self.home)

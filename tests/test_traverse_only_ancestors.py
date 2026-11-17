"""Descriptor walks pass a traverse-only ancestor, as a host's /home at 0711.

Each site walks from / with one descriptor per component. A directory the
walker may enter but not list refuses O_RDONLY, so the walk falls back to
O_PATH for it, as sandbox/hosting/images/activation/private_inputs.py does.
The fixture is <tmp>/locked (0311: owner may enter, not read) /home (0700).
"""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "O_PATH"),
                     "O_PATH fallback is Linux-only")
@unittest.skipIf(os.geteuid() == 0, "root reads a traverse-only directory anyway")
class TraverseOnlyAncestorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        locked = Path(temporary.name) / "locked"
        self.home = locked / "home"
        self.home.mkdir(parents=True, mode=0o700)
        locked.chmod(0o311)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(PermissionError):  # the fixture really is traverse-only
            os.listdir(locked)

    def test_image_provisioning_owned_directory(self):
        from sandbox.hosting.images.provisioning import _owned_directory
        target = self.home / "authority"
        _owned_directory(target, create=True)
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o700)

    def test_image_staging_repository_owned_directory(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        repository = StageRepository(self.home / "image-staging")
        repository._ensure_owned_dir(repository.lock_dir)
        self.assertTrue(repository.lock_dir.is_dir())

    def test_workspace_service_absolute_directory_walk(self):
        from sandbox.application.workspace_service import _open_absolute_directory_nofollow
        descriptor = _open_absolute_directory_nofollow(self.home)
        try:
            self.assertEqual(os.fstat(descriptor).st_ino, os.stat(self.home).st_ino)
        finally:
            os.close(descriptor)

    def test_workspace_service_never_returns_a_traverse_only_final_directory(self):
        # Callers fsync and fchmod under the returned descriptor (Opus re-check).
        from sandbox.application.workspace_service import _open_absolute_directory_nofollow
        with self.assertRaises(PermissionError):
            _open_absolute_directory_nofollow(self.home.parent)

    def test_tailnet_name_remote_program_directory_walk(self):
        source = ROOT / "sandbox" / "hosting" / "tailnet_names" / "_remote_main.py"
        spec = importlib.util.spec_from_file_location("tailnet_remote_main_under_test", source)
        program = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(program)
        descriptor = program._open_dir(str(self.home), {"trusted_uids": {0, os.getuid()}})
        try:
            self.assertEqual(os.fstat(descriptor).st_ino, os.stat(self.home).st_ino)
        finally:
            os.close(descriptor)


if __name__ == "__main__":
    unittest.main()

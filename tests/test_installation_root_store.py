"""Actual lock inode replacement is rejected; roots remain private and non-linking."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sandbox.installation.root_store import RootDirectory
from sandbox.installation.binding import InstallationBindingError


def as_root(value):
    fields = list(value)
    fields[4] = 0
    return os.stat_result(fields)


class RootAnchorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = RootDirectory.__new__(RootDirectory)
        self.store.path, self.store.private = self.root, True
        self.store.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.store.fd)
        # This fixture tests opened-file/lookup identity, not host root ownership.
        # Parent authority is deliberately left to separate integration acceptance.
        self.store.assert_current = lambda: None
        original_stat, original_fstat = os.stat, os.fstat
        patches = (mock.patch('sandbox.installation.root_store.os.stat',
                    side_effect=lambda *a, **kw: as_root(original_stat(*a, **kw))),
                   mock.patch('sandbox.installation.root_store.os.fstat',
                    side_effect=lambda *a, **kw: as_root(original_fstat(*a, **kw))))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_replaced_lock_lookup_cannot_become_parallel_authority(self):
        anchor = self.root / 'anchor.lock'
        anchor.touch(mode=0o644)
        replacement = self.root / 'replacement'
        replacement.touch(mode=0o644)
        with self.assertRaises(InstallationBindingError):
            with self.store.lock('anchor.lock', exclusive=False):
                os.replace(replacement, anchor)

    def test_symlink_record_is_not_followed(self):
        target = self.root / 'target.json'
        target.write_text('{"scope":"fixture"}')
        (self.root / 'record.json').symlink_to(target)
        with self.assertRaises(OSError):
            self.store.read('record.json')

    def test_record_name_cannot_escape_root(self):
        with self.assertRaises(InstallationBindingError):
            self.store.read('../record.json')

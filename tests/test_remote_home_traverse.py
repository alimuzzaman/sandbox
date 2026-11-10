"""Remote programs that walk to the Sandbox home must cross traverse-only
ancestors (a host may set /home to 0711), opening only the home itself and
what is under it for reading."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.core._remote import _REMOTE_HOME_NORMALIZE_PROGRAM
from sandbox.transports.remote_hosting_images import _INODE_EXEC

FAKE_O_PATH = 0x40000000  # a bit no real open flag uses on the test host


class TraverseOnlyAncestors:
    """Patch os.open so every directory above `home` refuses a read open.

    A real traverse-only directory refuses O_RDONLY but allows O_PATH; the
    test host may lack O_PATH, so it is faked and stripped before the real
    open.
    """

    def __init__(self, home: Path):
        self.home = str(home)
        self.paths: dict[int, str] = {}
        self.read_opens: list[str] = []
        self.real_open = os.open

    def full(self, path, dir_fd):
        if isinstance(path, str) and path.startswith("/"):
            return path
        base = self.paths.get(dir_fd, "")
        return (base.rstrip("/") + "/" + path) if base else path

    def __call__(self, path, flags, *args, dir_fd=None, **kwargs):
        full = self.full(path, dir_fd)
        inside = full == self.home or full.startswith(self.home + "/")
        if not flags & FAKE_O_PATH:
            if not inside:
                raise PermissionError(13, "Permission denied", path)
            self.read_opens.append(full)
        if dir_fd is not None:
            kwargs["dir_fd"] = dir_fd
        descriptor = self.real_open(path, flags & ~FAKE_O_PATH, *args, **kwargs)
        self.paths[descriptor] = full
        return descriptor


def run_program(source, argv):
    old_argv = sys.argv
    sys.argv = argv
    try:
        exec(compile(source, "<remote-program>", "exec"), {})
    finally:
        sys.argv = old_argv


class HomeNormalizeTests(unittest.TestCase):
    def test_crosses_traverse_only_ancestors_and_tightens_home(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as directory:
            home = Path(directory).resolve() / "home" / "alim" / "sandbox"
            (home / "runtime").mkdir(parents=True)
            for path in (Path(directory).resolve(), home.parent.parent, home.parent):
                path.chmod(0o711)
            home.chmod(0o775)
            (home / "runtime").chmod(0o775)
            guard = TraverseOnlyAncestors(home)
            with patch.object(os, "O_PATH", FAKE_O_PATH, create=True), \
                 patch.object(os, "open", side_effect=guard):
                run_program(_REMOTE_HOME_NORMALIZE_PROGRAM, ["normalize", str(home)])
            self.assertEqual(oct(home.stat().st_mode & 0o777), oct(0o755))
            self.assertEqual(oct((home / "runtime").stat().st_mode & 0o777), oct(0o755))
            self.assertEqual(guard.read_opens, [str(home), str(home / "runtime")])

    def test_still_refuses_a_group_writable_ancestor(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as directory:
            home = Path(directory).resolve() / "home" / "alim" / "sandbox"
            home.mkdir(parents=True)
            home.parent.chmod(0o771)
            guard = TraverseOnlyAncestors(home)
            with patch.object(os, "O_PATH", FAKE_O_PATH, create=True), \
                 patch.object(os, "open", side_effect=guard), \
                 self.assertRaises(SystemExit) as raised:
                run_program(_REMOTE_HOME_NORMALIZE_PROGRAM, ["normalize", str(home)])
            self.assertEqual(raised.exception.code, 69)


class ImageStageTraverseTests(unittest.TestCase):
    def test_inode_exec_crosses_traverse_only_ancestors(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as directory:
            home = Path(directory).resolve() / "home" / "alim" / "sandbox"
            revision = "c" * 40
            helper_bytes = b"print('helper')\n"
            digest = hashlib.sha256(helper_bytes).hexdigest()
            root = (home / "runtime" / "helpers" / "image-stage"
                    / f"sha256-{digest}-revision-{revision}")
            root.mkdir(parents=True)
            for path in (Path(directory).resolve(), home.parent.parent, home.parent):
                path.chmod(0o711)
            for path in (home, home / "runtime", home / "runtime" / "helpers",
                         home / "runtime" / "helpers" / "image-stage", root):
                path.chmod(0o700)
            helper = root / "staging_helper.py"
            helper.write_bytes(helper_bytes); helper.chmod(0o500)
            manifest = {"schema_version": 2, "artifact_digest": f"sha256:{digest}",
                        "entry": "sandbox-image-stage-helper-v2",
                        "runtime_revision": revision,
                        "capability_revision": "systemd-cgroup-v2-batch-stage-v2"}
            (root / "manifest-v2.json").write_text(json.dumps(manifest))
            (root / "manifest-v2.json").chmod(0o600)

            class Executed(Exception):
                pass

            guard = TraverseOnlyAncestors(home)
            with patch.object(os, "O_PATH", FAKE_O_PATH, create=True), \
                 patch.object(os, "open", side_effect=guard), \
                 patch.object(os, "execv", side_effect=Executed), \
                 self.assertRaises(Executed):
                run_program(_INODE_EXEC, ["inode-exec", str(root), str(home),
                                          json.dumps(manifest), manifest["entry"],
                                          "manifest-v2.json"])
            self.assertTrue(all(p == str(home) or p.startswith(str(home) + "/")
                                for p in guard.read_opens))


if __name__ == "__main__":
    unittest.main()

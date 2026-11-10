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
REAL_O_PATH = getattr(os, "O_PATH", 0)  # what the fake becomes for the real open
# Both programs refuse a group- or world-writable ancestor, so the tree goes
# under the user's home rather than the checkout (a umask-0002 host leaves the
# checkout's parents at 0775). On a host whose /home is 0711 this also crosses
# a real traverse-only ancestor.
TEST_ROOT = Path.home()


class TraverseOnlyAncestors:
    """Patch os.open so every directory between / and `home` refuses a read open.

    A real traverse-only directory refuses O_RDONLY but allows O_PATH. The
    flag is faked so the test also runs where O_PATH does not exist; the real
    open gets the host's O_PATH back, since a real ancestor (such as a 0711
    /home) may itself be traverse-only.
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
        if not flags & FAKE_O_PATH and full != "/":
            if not inside:
                raise PermissionError(13, "Permission denied", path)
            self.read_opens.append(full)
        if dir_fd is not None:
            kwargs["dir_fd"] = dir_fd
        if flags & FAKE_O_PATH:
            flags = (flags & ~FAKE_O_PATH) | REAL_O_PATH
        descriptor = self.real_open(path, flags, *args, **kwargs)
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
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
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
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
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
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
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



def load_stage_helper():
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "scripts" / "provision_image_stage_helper.py"
    spec = importlib.util.spec_from_file_location("provision_image_stage_helper_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DescriptorWalkTraverseTests(unittest.TestCase):
    def tree(self, directory):
        home = Path(directory).resolve() / "home" / "alim" / "sandbox"
        home.mkdir(parents=True)
        for path in (Path(directory).resolve(), home.parent.parent, home.parent):
            path.chmod(0o711)
        return home

    def test_stage_helper_creates_owner_directories_past_traverse_only_ancestors(self):
        helper = load_stage_helper()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            home = self.tree(directory)
            target = home / "runtime" / "helpers" / "image-stage"
            guard = TraverseOnlyAncestors(home)
            with patch.object(os, "O_PATH", FAKE_O_PATH, create=True), \
                 patch.object(os, "open", side_effect=guard):
                helper._ensure_owner_directories(home, target, os.geteuid())
            self.assertTrue(target.is_dir())
            self.assertEqual(oct(target.stat().st_mode & 0o777), oct(0o700))

    def test_private_inputs_walk_reaches_a_directory_past_traverse_only_ancestors(self):
        from sandbox.hosting.images.activation import private_inputs
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            home = self.tree(directory)
            guard = TraverseOnlyAncestors(home)
            with patch.object(os, "O_PATH", FAKE_O_PATH, create=True), \
                 patch.object(os, "open", side_effect=guard):
                descriptor = private_inputs._directory_fd(home)
            try:
                self.assertEqual(os.fstat(descriptor).st_ino, home.stat().st_ino)
            finally:
                os.close(descriptor)

    def test_a_refusal_without_o_path_still_raises(self):
        helper = load_stage_helper()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            home = self.tree(directory)
            guard = TraverseOnlyAncestors(home)
            with patch.object(os, "O_PATH", None, create=True), \
                 patch.object(os, "open", side_effect=guard), \
                 self.assertRaises(PermissionError):
                helper._open_walk_dir(str(home.parent), None)

if __name__ == "__main__":
    unittest.main()

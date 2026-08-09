"""Unit tests for `./sb zip` — the dist-archive alternative (docs/plugin-zip.md).

Stdlib `unittest` only, no docker: `.distignore` matching, the git build stamp,
in-memory version stamping, the guards, and one end-to-end archive build over a
temporary fixture repo. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.zip as z  # noqa: E402


def _git_init(path: Path, branch: str = "main", commits: int = 1) -> None:
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)],
                   check=True, capture_output=True)
    for i in range(commits):
        (path / ".commit-marker").write_text(str(i))
        subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.email=t@e", "-c", "user.name=t",
                        "commit", "-qm", f"c{i}"], check=True, capture_output=True)
    (path / ".commit-marker").unlink()
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "drop marker"], check=True, capture_output=True)


def _fixture(tmp: Path, branch: str = "feature/zip") -> Path:
    """A minimal but realistic plugin repo: main file + readme carrying the same
    version, an asset tree, a vendor tree, and a `.distignore` with a dev block."""
    root = tmp / "myplugin"
    (root / "assets" / "img").mkdir(parents=True)
    (root / "vendor" / "acme" / "lib").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "myplugin.php").write_text(
        "<?php\n/**\n * Plugin Name: My Plugin\n * Version: 2.3.1\n */\n"
        "define( 'MYPLUGIN_VERSION', '2.3.1' );\n")
    (root / "readme.txt").write_text("Stable tag: 2.3.1\n")
    (root / "assets" / "app.css").write_text("body{}\n")
    (root / "assets" / "app.js.map").write_text("{}\n")
    (root / "assets" / "img" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (root / "vendor" / "acme" / "lib" / "a.php").write_text("<?php\n")
    (root / "tests" / "t.php").write_text("<?php\n")
    (root / "package.json").write_text('{"name":"myplugin","version":"0.0.1"}\n')
    (root / ".distignore").write_text(
        ".git\n.distignore\ntests/\npackage.json\n"
        f"{z.DEV_BLOCK_START}\n*.map\n{z.DEV_BLOCK_END}\n")
    _git_init(root, branch=branch, commits=3)
    return root


class TestDistIgnore(unittest.TestCase):
    def test_floating_entry_matches_at_any_depth(self):
        ig = z.DistIgnore(["node_modules", "*.sql"])
        self.assertTrue(ig.match("node_modules/pkg/index.js"))
        self.assertTrue(ig.match("modules/a/node_modules/x.js"))
        self.assertTrue(ig.match("db/dump.sql"))
        self.assertFalse(ig.match("includes/Loader.php"))

    def test_anchored_entry_only_matches_from_root(self):
        ig = z.DistIgnore(["/build", "docs/internal"])
        self.assertTrue(ig.match("build/out.js"))
        self.assertTrue(ig.match("docs/internal/notes.md"))
        self.assertFalse(ig.match("modules/build/out.js"))
        self.assertFalse(ig.match("a/docs/internal/notes.md"))

    def test_comments_and_blanks_ignored(self):
        ig = z.DistIgnore(["# a comment", "", "  ", "tests/"])
        self.assertTrue(ig.match("tests/t.php"))
        self.assertFalse(ig.match("a-comment"))


class TestDevBlock(unittest.TestCase):
    def test_dev_block_entries_ship_only_in_dev_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            prod, _ = z._read_distignore(root, dev=False)
            dev, found = z._read_distignore(root, dev=True)
            self.assertTrue(found)
            self.assertTrue(prod.match("assets/app.js.map"))
            self.assertFalse(dev.match("assets/app.js.map"))
            # Entries OUTSIDE the dev block stay excluded either way.
            self.assertTrue(dev.match("tests/t.php"))
            self.assertTrue(dev.match("package.json"))


class TestBuildStamp(unittest.TestCase):
    def test_feature_branch_gets_commit_count_postfix(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td), branch="feature/zip")
            stamp = z._resolve_build_stamp(root, "2.3.1", clean=False, with_hash=False,
                                           release_branches=z.RELEASE_BRANCHES)
            self.assertTrue(stamp["stamped"])
            self.assertEqual(stamp["branch_slug"], "feature-zip")
            self.assertEqual(stamp["version"], f"2.3.1.{stamp['git']['count']}")

    def test_release_branch_ships_declared_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td), branch="main")
            stamp = z._resolve_build_stamp(root, "2.3.1", clean=False, with_hash=False,
                                           release_branches=z.RELEASE_BRANCHES)
            self.assertFalse(stamp["stamped"])
            self.assertEqual(stamp["version"], "2.3.1")
            self.assertEqual(stamp["branch_slug"], "")

    def test_clean_beats_a_feature_branch(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td), branch="feature/zip")
            stamp = z._resolve_build_stamp(root, "2.3.1", clean=True, with_hash=False,
                                           release_branches=z.RELEASE_BRANCHES)
            self.assertFalse(stamp["stamped"])
            self.assertEqual(stamp["branch_slug"], "")

    def test_hash_flag_appends_sha(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td), branch="feature/zip")
            stamp = z._resolve_build_stamp(root, "2.3.1", clean=False, with_hash=True,
                                           release_branches=z.RELEASE_BRANCHES)
            self.assertTrue(stamp["version"].endswith(stamp["git"]["sha"]))

    def test_non_git_dir_is_not_stamped(self):
        with tempfile.TemporaryDirectory() as td:
            stamp = z._resolve_build_stamp(Path(td), "2.3.1", clean=False, with_hash=False,
                                           release_branches=z.RELEASE_BRANCHES)
            self.assertFalse(stamp["stamped"])
            self.assertEqual(stamp["version"], "2.3.1")


class TestVersionStamping(unittest.TestCase):
    SITES = [("myplugin.php", "header+literal"), ("readme.txt", "header"),
             ("includes/Plugin.php", "literal")]

    def test_main_file_header_and_constant_both_rewritten(self):
        src = (b"<?php\n/**\n * Version: 2.3.1\n */\n"
               b"define( 'MYPLUGIN_VERSION', '2.3.1' );\n")
        out = z._stamp_file("myplugin.php", src, "2.3.1", "2.3.1.42", self.SITES)
        self.assertIn(b"Version: 2.3.1.42", out)
        self.assertIn(b"'2.3.1.42'", out)

    def test_readme_stable_tag(self):
        out = z._stamp_file("README.txt", b"Stable tag: 2.3.1\n", "2.3.1", "2.3.1.42",
                            self.SITES)
        self.assertEqual(out, b"Stable tag: 2.3.1.42\n")

    def test_unrelated_file_untouched(self):
        self.assertIsNone(
            z._stamp_file("includes/Other.php", b"<?php $v = '2.3.1';\n", "2.3.1",
                          "2.3.1.42", self.SITES))

    def test_unexpected_declared_version_left_alone(self):
        # The file says something other than the declared version — mangling it
        # would be worse than shipping it as-is.
        self.assertIsNone(
            z._stamp_file("readme.txt", b"Stable tag: 9.9.9\n", "2.3.1", "2.3.1.42",
                          self.SITES))


class TestGuards(unittest.TestCase):
    def test_executable_is_flagged_whatever_the_extension(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tool.dat").write_bytes(b"MZ\x90\x00rest")
            violations = z._check_mime_mismatches(["tool.dat"], root)
            self.assertEqual(len(violations), 1)
            self.assertIn("dangerous binary format", violations[0]["reason"])

    def test_all_macho_magic_variants_are_flagged_as_dangerous(self):
        variants = {
            "macho-big-32.dat": b"\xfe\xed\xfa\xce",
            "macho-big-64.dat": b"\xfe\xed\xfa\xcf",
            "macho-fat-32.dat": b"\xca\xfe\xba\xbe",
            "macho-fat-32-swapped.dat": b"\xbe\xba\xfe\xca",
            "macho-fat-64.dat": b"\xca\xfe\xba\xbf",
            "macho-fat-64-swapped.dat": b"\xbf\xba\xfe\xca",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for filename, magic in variants.items():
                with self.subTest(filename=filename):
                    (root / filename).write_bytes(magic + b"payload")
                    violations = z._check_mime_mismatches([filename], root)
                    self.assertEqual(len(violations), 1)
                    self.assertIn("dangerous binary format", violations[0]["reason"])

    def test_extension_mismatch_is_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "logo.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
            violations = z._check_mime_mismatches(["logo.jpg"], root)
            self.assertIn("image/png", violations[0]["reason"])

    def test_php_hiding_in_a_non_php_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.txt").write_bytes(b"<?php system($_GET['c']);")
            violations = z._check_mime_mismatches(["notes.txt"], root)
            self.assertIn("PHP opening tag", violations[0]["reason"])

    def test_matching_extension_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
            (root / "code.php").write_bytes(b"<?php echo 1;")
            self.assertEqual(z._check_mime_mismatches(["logo.png", "code.php"], root), [])

    def test_duplicates_reported_but_hashed_names_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.css").write_text("body{}")
            (root / "b.css").write_text("body{}")
            (root / "app.a1b2c3d4.js").write_text("body{}")
            (root / "empty.css").write_text("")
            groups = z._find_duplicates(["a.css", "b.css", "app.a1b2c3d4.js", "empty.css"], root)
            self.assertEqual(len(groups), 1)
            self.assertEqual(sorted(groups[0]["files"]), ["a.css", "b.css"])


class TestDiscovery(unittest.TestCase):
    def test_distignore_prunes_and_git_is_never_shipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            ignore, _ = z._read_distignore(root, dev=False)
            files = z._discover_files(root, ignore)
            self.assertIn("myplugin.php", files)
            self.assertIn("vendor/acme/lib/a.php", files)
            self.assertNotIn("tests/t.php", files)
            self.assertNotIn("package.json", files)
            self.assertNotIn("assets/app.js.map", files)
            self.assertFalse([f for f in files if f.startswith(".git/")])


class TestEndToEnd(unittest.TestCase):
    """The full command, via its argparse-shaped args object."""

    class _Args:
        def __init__(self, **kw):
            self.__dict__.update(
                {"project_dir": None, "dev": False, "clean": False, "hash": False,
                 "out": None, "json": True, **kw})

    def _run(self, root: Path, out: Path, **kw) -> dict:
        import contextlib
        import io
        import json as _json
        from unittest import mock
        buf = io.StringIO()
        # A tempdir sits outside $HOME, which find_project_root refuses by
        # design — the same escape hatch a repo outside home uses.
        with mock.patch.dict("os.environ",
                             {"SANDBOX_PROJECT_ROOTS": str(root.parent)}), \
                contextlib.redirect_stdout(buf):
            z.cmd_zip({}, self._Args(project_dir=str(root), out=str(out), **kw))
        return _json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_builds_a_branch_tagged_stamped_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            out = Path(td) / "out"
            result = self._run(root, out)

            self.assertTrue(result["ok"])
            self.assertTrue(result["stamped"])
            self.assertTrue(Path(result["zip_path"]).name.startswith(
                "myplugin-feature-zip."))
            self.assertEqual(sorted(result["stamped_files"]),
                             ["myplugin.php", "readme.txt"])

            with zipfile.ZipFile(result["zip_path"]) as zf:
                names = zf.namelist()
                # Everything under a `<slug>/` folder, as WordPress expects.
                self.assertTrue(all(n.startswith("myplugin/") for n in names))
                self.assertIn("myplugin/vendor/acme/lib/a.php", names)
                self.assertNotIn("myplugin/tests/t.php", names)
                main = zf.read("myplugin/myplugin.php").decode()
                self.assertIn(f"Version: {result['version']}", main)
                self.assertIn(f"'{result['version']}'", main)

            # The stamp exists only in the archived bytes.
            self.assertIn("Version: 2.3.1", (root / "myplugin.php").read_text())
            self.assertEqual(
                subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip(), "")

    def test_dev_build_keeps_dev_files_and_tags_the_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            out = Path(td) / "out"
            result = self._run(root, out, dev=True)
            self.assertIn("myplugin-dev-feature-zip.", Path(result["zip_path"]).name)
            with zipfile.ZipFile(result["zip_path"]) as zf:
                self.assertIn("myplugin/assets/app.js.map", zf.namelist())

    def test_clean_build_is_a_plain_release_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            out = Path(td) / "out"
            result = self._run(root, out, clean=True)
            self.assertFalse(result["stamped"])
            self.assertEqual(Path(result["zip_path"]).name, "myplugin.2.3.1.zip")
            with zipfile.ZipFile(result["zip_path"]) as zf:
                self.assertIn("Version: 2.3.1\n",
                              zf.read("myplugin/myplugin.php").decode())

    def test_repeat_build_excludes_an_output_directory_inside_the_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            out = root / "dist"

            first = self._run(root, out)
            second = self._run(root, out)

            self.assertEqual(second["files"], first["files"])
            with zipfile.ZipFile(second["zip_path"]) as zf:
                self.assertFalse(
                    [name for name in zf.namelist()
                     if name.startswith("myplugin/dist/")]
                )

    def test_rejects_project_root_as_output_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))

            import contextlib
            import io
            stderr = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
                self._run(root, root)

            self.assertIn("project root", stderr.getvalue().lower())
            self.assertFalse(list(root.glob("myplugin*.zip")))

    def test_rejects_a_file_symlink_that_escapes_the_project_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            outside = Path(td) / "outside-secret.txt"
            outside.write_text("outside-secret-marker\n")
            (root / "assets" / "linked-secret.txt").symlink_to(outside)
            out = Path(td) / "out"

            import contextlib
            import io
            stderr = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
                self._run(root, out)

            message = stderr.getvalue().lower()
            self.assertIn("symlink", message)
            self.assertIn("outside", message)
            self.assertFalse(out.exists() and any(out.iterdir()))

    def test_rejects_a_file_symlink_to_an_in_project_fifo(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            fifo = root / "assets" / "blocking-fifo"
            os.mkfifo(fifo)
            (root / "assets" / "fifo-link").symlink_to(fifo)
            out = Path(td) / "out"

            import contextlib
            import io
            stderr = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
                self._run(root, out)

            message = stderr.getvalue().lower()
            self.assertIn("regular file", message)
            self.assertFalse(out.exists() and any(out.iterdir()))

    def test_regular_file_inside_the_project_still_archives(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            (root / "assets" / "safe.txt").write_text("safe\n")
            out = Path(td) / "out"

            result = self._run(root, out)

            with zipfile.ZipFile(result["zip_path"]) as zf:
                self.assertEqual(zf.read("myplugin/assets/safe.txt"), b"safe\n")

    def test_executable_aborts_the_build(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            out = Path(td) / "out"
            (root / "assets" / "tool.bin").write_bytes(b"MZ\x90\x00rest")
            import contextlib
            import io
            with self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self._run(root, out)
            self.assertFalse(out.exists() and any(out.iterdir()))


if __name__ == "__main__":
    unittest.main()

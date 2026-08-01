from pathlib import Path
import tempfile
import unittest


class TestIsolationPolicy(unittest.TestCase):
    def test_source_is_read_only_by_default_and_writes_are_explicit(self):
        from sandbox.isolation.policy import MountPolicyCompiler
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "project"; state = root / "state"
            source.mkdir(); state.mkdir()
            result = MountPolicyCompiler(allowed_sources=(root,)).compile(
                read_only=({"source": source, "target": "/workspace"},),
                writable=({"source": state, "target": "/var/lib/sandbox"},),
            )
        self.assertEqual(result["read_only"][0]["mode"], "ro")
        self.assertEqual(result["writable"][0]["mode"], "rw")

    def test_symlink_source_parent_escape_and_control_targets_are_rejected(self):
        from sandbox.isolation.policy import MountPolicyCompiler
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); allowed = root / "allowed"; allowed.mkdir()
            outside = root / "outside"; outside.mkdir()
            link = allowed / "link"; link.symlink_to(outside, target_is_directory=True)
            compiler = MountPolicyCompiler(allowed_sources=(allowed,))
            for item in (
                {"source": link, "target": "/workspace"},
                {"source": outside, "target": "/workspace"},
                {"source": allowed, "target": "/proc/host"},
            ):
                with self.subTest(item=item), self.assertRaises(ValueError):
                    compiler.compile(read_only=(item,))


if __name__ == "__main__": unittest.main()

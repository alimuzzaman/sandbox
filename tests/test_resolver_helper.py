from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parent.parent
HELPER = ROOT / "tools" / "resolver-helper.sh"


def candidate_text(suffix: str = "test") -> str:
    return (
        f"# sandbox-resolver v1 suffix={suffix}\n"
        "[Resolve]\n"
        "DNS=127.0.0.54:5300\n"
        f"Domains=~{suffix}\n"
    )


class TestResolverHelper(unittest.TestCase):
    def run_helper(self, *args: str):
        return subprocess.run(
            [str(HELPER), *args], capture_output=True, text=True, timeout=5,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")},
        )

    def test_unknown_verb_is_rejected(self):
        result = self.run_helper("shell", "whoami")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage", result.stderr.lower())

    def test_candidate_schema_accepts_only_owned_bounded_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "resolved-test.conf"
            candidate.write_text(candidate_text())
            candidate.chmod(0o600)
            result = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "127.0.0.54", "5300",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "candidate-ok")

    def test_invalid_suffix_and_non_loopback_address_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "candidate.conf"
            candidate.write_text(candidate_text())
            candidate.chmod(0o600)
            suffix = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "../test", "127.0.0.54", "5300",
            )
            address = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "0.0.0.0", "5300",
            )
        self.assertNotEqual(suffix.returncode, 0)
        self.assertNotEqual(address.returncode, 0)

    def test_symlink_and_outside_root_candidates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "network"
            authority = root / "authority"
            authority.mkdir(parents=True)
            outside = Path(tmp) / "outside.conf"
            outside.write_text(candidate_text())
            outside.chmod(0o600)
            symlink = authority / "resolved-test.conf"
            symlink.symlink_to(outside)
            linked = self.run_helper(
                "check-candidate", str(root), str(symlink),
                "test", "127.0.0.54", "5300",
            )
            external = self.run_helper(
                "check-candidate", str(root), str(outside),
                "test", "127.0.0.54", "5300",
            )
        self.assertNotEqual(linked.returncode, 0)
        self.assertNotEqual(external.returncode, 0)

    def test_group_or_world_writable_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "resolved-test.conf"
            candidate.write_text(candidate_text())
            candidate.chmod(0o666)
            result = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "127.0.0.54", "5300",
            )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

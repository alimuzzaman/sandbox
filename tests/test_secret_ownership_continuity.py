"""Focused regressions for metadata-only source continuity and source_view (T052).

Covers:
- Unreliable reason detection: not_regular_file, unexpected_links, foreign_owner,
  permissions_too_broad, ctime_in_future, coarse_timestamps.
- Same-as comparisons (mtime, ctime, inode, size, names_sha256).
- source_view pure gates: read_values refuses source_changed,
  refuses unreliable continuity with continuity_unreliable,
  refuses non-dotenv format with source_unsupported,
  selection validation refuses invalid, duplicate, or empty keys.
- Same-fd stat capture and restored-mtime tamper detection with registered
  synthetic dotenv fixture through SourceRegistry.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sandbox.secrets.models import SafeSource, SecretBrokerError
from sandbox.secrets.ownership.continuity import capture
from sandbox.secrets.ownership.models import SourceContinuity
from sandbox.secrets.ownership.source_view import read_names, read_values
from sandbox.secrets.sources import SourceRegistry
from tests.secret_ownership_support import private_write, require


class FakeStatResult:
    """Mock os.stat_result with controllable metadata."""
    def __init__(
        self,
        *,
        st_mode: int = 0o100600,
        st_ino: int = 12345,
        st_dev: int = 67890,
        st_nlink: int = 1,
        st_uid: int | None = None,
        st_gid: int = 100,
        st_size: int = 42,
        mtime_ns: int = 1_000_123,
        ctime_ns: int = 1_000_456,
    ) -> None:
        self.st_mode = st_mode
        self.st_ino = st_ino
        self.st_dev = st_dev
        self.st_nlink = st_nlink
        self.st_uid = os.geteuid() if st_uid is None else st_uid
        self.st_gid = st_gid
        self.st_size = st_size
        self.st_mtime_ns = mtime_ns
        self.st_ctime_ns = ctime_ns


class TestSecretOwnershipContinuity(unittest.TestCase):
    """Source continuity metadata and source_view regressions."""

    def test_capture_continuity_reliable_baseline(self) -> None:
        """Valid mode 0600 file metadata produces reliable SourceContinuity."""
        now_ns = 5_000_000_000
        stat_valid = FakeStatResult(st_mode=0o100600, st_uid=os.geteuid(), st_nlink=1,
                                    mtime_ns=1_000_123_456, ctime_ns=1_000_123_789)
        cont = capture("dotenv-src", stat_valid, ["ALPHA", "BETA"], now_ns=now_ns)
        require(cont.reliable is True, "valid baseline metadata must be reliable")
        require(cont.unreliable_reason is None, "reliable continuity has no unreliable_reason")
        require(stat.S_IMODE(cont.mode) == 0o600, "file mode must be 0600")
        require(cont.nlink == 1, "link count must be 1")

    def test_unreliable_reasons_detection(self) -> None:
        """Verify each condition triggers the expected unreliable_reason string."""
        now_ns = 5_000_000_000

        # 1. not_regular_file (e.g. directory or symlink)
        s_dir = FakeStatResult(st_mode=stat.S_IFDIR | 0o700)
        c_dir = capture("src", s_dir, ["K1"], now_ns=now_ns)
        require(c_dir.reliable is False, "directory must be unreliable")
        require(c_dir.unreliable_reason == "not_regular_file", "reason must be not_regular_file")

        # 2. unexpected_links (nlink != 1)
        s_link = FakeStatResult(st_nlink=2)
        c_link = capture("src", s_link, ["K1"], now_ns=now_ns)
        require(c_link.reliable is False, "hard link must be unreliable")
        require(c_link.unreliable_reason == "unexpected_links", "reason must be unexpected_links")

        # 3. foreign_owner (uid != euid)
        s_owner = FakeStatResult(st_uid=os.geteuid() + 1)
        c_owner = capture("src", s_owner, ["K1"], now_ns=now_ns)
        require(c_owner.reliable is False, "foreign owner must be unreliable")
        require(c_owner.unreliable_reason == "foreign_owner", "reason must be foreign_owner")

        # 4. permissions_too_broad (group/other bits set)
        s_perm = FakeStatResult(st_mode=0o100644)
        c_perm = capture("src", s_perm, ["K1"], now_ns=now_ns)
        require(c_perm.reliable is False, "broad permissions must be unreliable")
        require(c_perm.unreliable_reason == "permissions_too_broad",
                "reason must be permissions_too_broad")

        # 5. ctime_in_future (> now + 5s)
        s_future = FakeStatResult(ctime_ns=now_ns + 10_000_000_000)
        c_future = capture("src", s_future, ["K1"], now_ns=now_ns)
        require(c_future.reliable is False, "future ctime must be unreliable")
        require(c_future.unreliable_reason == "ctime_in_future", "reason must be ctime_in_future")

        # 6. coarse_timestamps (exact second boundary on both mtime and ctime)
        s_coarse = FakeStatResult(mtime_ns=1_000_000_000, ctime_ns=2_000_000_000)
        c_coarse = capture("src", s_coarse, ["K1"], now_ns=now_ns)
        require(c_coarse.reliable is False, "coarse timestamp must be unreliable")
        require(c_coarse.unreliable_reason == "coarse_timestamps",
                "reason must be coarse_timestamps")

    def test_continuity_same_as_tamper_detection(self) -> None:
        """same_as returns False on any change to dev, ino, size, mtime, ctime, or names."""
        stat_orig = FakeStatResult()
        cont_orig = capture("src", stat_orig, ["ALPHA", "BETA"], now_ns=5_000_000_000)
        require(cont_orig.reliable is True, "baseline must be reliable")

        # Inode change
        stat_ino = FakeStatResult(st_ino=99999)
        cont_ino = capture("src", stat_ino, ["ALPHA", "BETA"], now_ns=5_000_000_000)
        require(cont_orig.same_as(cont_ino) is False, "changed ino must not be same_as")

        # ctime change
        stat_ctime = FakeStatResult(ctime_ns=stat_orig.st_ctime_ns + 100)
        cont_ctime = capture("src", stat_ctime, ["ALPHA", "BETA"], now_ns=5_000_000_000)
        require(cont_orig.same_as(cont_ctime) is False, "changed ctime must not be same_as")

        # Names change
        cont_names = capture("src", stat_orig, ["ALPHA", "GAMMA"], now_ns=5_000_000_000)
        require(cont_orig.same_as(cont_names) is False, "changed names must not be same_as")

    def test_source_view_read_values_refuses_source_changed(self) -> None:
        """read_values raises source_changed when stat or content changes."""
        mock_registry = MagicMock()
        mock_policy = SimpleNamespace(type="file", format="dotenv")
        mock_registry.policy.return_value = mock_policy

        stat1 = FakeStatResult(mtime_ns=1_000_123, ctime_ns=1_000_456)
        stat2 = FakeStatResult(mtime_ns=1_000_123, ctime_ns=2_000_789)  # ctime changed!

        cont1 = capture("dotenv-src", stat1, ["ALPHA"], now_ns=5_000_000_000)

        safe_src = SafeSource(
            policy=mock_policy,
            content=b"ALPHA='synthetic-canary-1'\n",
            signature=(1, 2, 3, 4, 5),
        )
        mock_registry.read_with_stat.return_value = (safe_src, stat2)

        with self.assertRaises(SecretBrokerError) as ctx:
            read_values(mock_registry, "dotenv-src", ("ALPHA",), cont1)
        require(ctx.exception.code == "source_changed",
                "ctime modification must raise source_changed")

    def test_source_view_read_values_refuses_unreliable_continuity(self) -> None:
        """read_values raises continuity_unreliable when given an unreliable snapshot."""
        mock_registry = MagicMock()
        mock_policy = SimpleNamespace(type="file", format="dotenv")
        mock_registry.policy.return_value = mock_policy

        # Coarse timestamps snapshot
        stat_coarse = FakeStatResult(mtime_ns=1_000_000_000, ctime_ns=2_000_000_000)
        cont_unreliable = capture("dotenv-src", stat_coarse, ["ALPHA"], now_ns=5_000_000_000)
        require(cont_unreliable.reliable is False, "fixture must be unreliable")

        with self.assertRaises(SecretBrokerError) as ctx:
            read_values(mock_registry, "dotenv-src", ("ALPHA",), cont_unreliable)
        require(ctx.exception.code == "continuity_unreliable",
                "unreliable continuity must raise continuity_unreliable")

    def test_source_view_refuses_non_dotenv_source(self) -> None:
        """read_names/read_values refuses sources whose registered format is not dotenv."""
        mock_registry = MagicMock()
        mock_policy = SimpleNamespace(type="file", format="json")  # NOT dotenv
        mock_registry.policy.return_value = mock_policy

        with self.assertRaises(SecretBrokerError) as ctx:
            read_names(mock_registry, "json-source")
        require(ctx.exception.code == "source_unsupported",
                "non-dotenv format must raise source_unsupported")

    def test_source_view_selection_validation(self) -> None:
        """read_values rejects empty, duplicate, non-tuple/list, or malformed key names."""
        mock_registry = MagicMock()
        stat1 = FakeStatResult()
        cont1 = capture("dotenv-src", stat1, ["ALPHA", "BETA"], now_ns=5_000_000_000)

        # Empty selection
        with self.assertRaises(SecretBrokerError) as ctx1:
            read_values(mock_registry, "dotenv-src", (), cont1)
        require(ctx1.exception.code == "selection_invalid", "empty keys must raise selection_invalid")

        # Duplicate key selection
        with self.assertRaises(SecretBrokerError) as ctx2:
            read_values(mock_registry, "dotenv-src", ("ALPHA", "ALPHA"), cont1)
        require(ctx2.exception.code == "selection_invalid", "duplicate keys must raise selection_invalid")

        # Malformed key name
        with self.assertRaises(SecretBrokerError) as ctx3:
            read_values(mock_registry, "dotenv-src", ("BAD-KEY-NAME!",), cont1)
        require(ctx3.exception.code == "selection_invalid", "malformed key must raise selection_invalid")

    def test_continuity_same_fd_stat_capture_and_restored_mtime_source_changed(self) -> None:
        """SourceRegistry same-fd capture detects value-only edits even when mtime is restored."""
        with tempfile.TemporaryDirectory() as raw_tmp:
            project_dir = Path(os.path.realpath(raw_tmp)) / "project"
            project_dir.mkdir(mode=0o700)
            env_file = project_dir / ".env"
            personal_dir = project_dir / ".personal"
            personal_dir.mkdir(mode=0o700)

            # Generate synthetic dotenv fixture with mode 0600
            initial_val = secrets.token_hex(16)
            private_write(env_file, f"ALPHA={initial_val}\nBETA=second_val\n".encode("utf-8"))

            sources_config = {
                "synthetic-dotenv": {
                    "type": "file",
                    "path": ".env",
                    "format": "dotenv",
                },
            }
            registry = SourceRegistry(
                project_root=project_dir,
                sources=sources_config,
                personal_path=personal_dir,
            )

            # 1. Read names and capture continuity via SourceRegistry (same-fd fstat)
            approved_cont, names = read_names(registry, "synthetic-dotenv")
            require(approved_cont.reliable is True, "newly generated fixture must be reliable")
            require(approved_cont.unreliable_reason is None, "unreliable_reason must be None")
            require(names == ("ALPHA", "BETA"), "entry names must match fixture")
            require(stat.S_IMODE(approved_cont.mode) == 0o600, "captured mode must be 0600")

            # 2. Capture stat before edit
            stat_before = env_file.stat()

            # 3. Modify dotenv fixture in-place (synthetic canary value)
            replacement_val = secrets.token_hex(16)
            private_write(env_file, f"ALPHA={replacement_val}\nBETA=second_val\n".encode("utf-8"))

            # 4. Restore mtime to match original timestamp
            os.utime(env_file, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))

            # 5. Calling read_values with approved_cont must detect change and raise source_changed
            with self.assertRaises(SecretBrokerError) as ctx:
                read_values(registry, "synthetic-dotenv", ("ALPHA",), approved_cont)
            require(ctx.exception.code == "source_changed",
                    "value edit with restored mtime must raise source_changed")


if __name__ == "__main__":
    unittest.main()

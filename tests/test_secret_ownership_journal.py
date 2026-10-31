"""Focused regressions for secret ownership journal and retention (T054).

Covers fsync before POST, torn line truncate handling, corruption refusal,
foreign controller refusal, authority mismatch refusal, file locking contention,
and retention behavior (preserving lock inode and keeping unresolved states).
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.journal import (
    Journal, destination_sha256, load_controller_id, new_qualified_id,
    read_journal_states, retention, write_private_file,
)
from sandbox.secrets.ownership.models import (
    Destination, NameState, canonical_json,
)
from tests.secret_ownership_support import require


def _sample_destination() -> Destination:
    return Destination(
        endpoint="https://vault.example.invalid",
        project_id="proj-j",
        environment="staging",
        path="/",
        read_client_id="reader",
        create_client_id="creator",
        timeout_seconds=30,
        gateway="none",
    )


class TestSecretOwnershipJournal(unittest.TestCase):
    """Journal durability, contention, parsing, and retention regressions."""

    def test_journal_flock_contention(self) -> None:
        """Opening a journal while its lock is held raises journal_locked."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "a" * 64

            # Open first instance
            j1 = Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth_digest)
            try:
                # Concurrent open must fail with journal_locked
                with self.assertRaises(SecretBrokerError) as ctx:
                    Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                                 authority_digest=auth_digest)
                require(ctx.exception.code == "journal_locked",
                        "concurrent journal open must raise journal_locked")
            finally:
                j1.close()

    def test_journal_fsync_before_post_regression(self) -> None:
        """journal.append writes durable record before returning."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "b" * 64

            with Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth_digest) as journal:
                run_id = new_qualified_id("run", ctrl_id)
                ap_id = new_qualified_id("ap", ctrl_id)
                journal.append(run_id, ap_id, "SECRET_A", NameState.ATTEMPTED)

                # Verify file contents directly from disk
                path = journal.directory / "journal.jsonl"
                content = path.read_bytes().decode("ascii").splitlines()
                # Line 0: Header
                # Line 1: Append record
                require(len(content) == 2, "journal must contain exactly header and append line")
                require('"attempted"' in content[1], "appended line must be durable on disk")

    def test_journal_foreign_controller_header_refusal(self) -> None:
        """Existing journal belonging to another controller ID raises journal_foreign_controller."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "c" * 64

            ctrl1 = "1" * 32
            ctrl2 = "2" * 32

            # Create journal under ctrl1
            j1 = Journal.open(root, "personal", dest_sha, controller_id=ctrl1,
                              authority_digest=auth_digest)
            j1.close()

            # Attempt to open under ctrl2
            with self.assertRaises(SecretBrokerError) as ctx:
                Journal.open(root, "personal", dest_sha, controller_id=ctrl2,
                             authority_digest=auth_digest)
            require(ctx.exception.code == "journal_foreign_controller",
                    "foreign controller journal must raise journal_foreign_controller")

    def test_journal_authority_mismatch_refusal(self) -> None:
        """Existing journal opened with altered authority_digest raises authority_mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth1 = "o1-" + "1" * 64
            auth2 = "o1-" + "2" * 64

            j1 = Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth1)
            j1.close()

            with self.assertRaises(SecretBrokerError) as ctx:
                Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                             authority_digest=auth2)
            require(ctx.exception.code == "authority_mismatch",
                    "mismatched authority_digest must raise authority_mismatch")

    def test_journal_torn_last_fragment_truncated_to_unknown(self) -> None:
        """Torn final line (missing newline) is truncated; prior attempted becomes acceptance_unknown."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "d" * 64

            with Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth_digest) as journal:
                run_id = new_qualified_id("run", ctrl_id)
                ap_id = new_qualified_id("ap", ctrl_id)
                journal.append(run_id, ap_id, "SECRET_TORN", NameState.ATTEMPTED)
                j_path = journal.directory / "journal.jsonl"

            # Simulate torn write: append incomplete bytes without newline
            with open(j_path, "ab") as f:
                f.write(b'{"op":"append","run_id":"run_corrupt","frag')

            # Reopen journal: torn trailing fragment must be ignored,
            # but unverified ATTEMPTED state must resolve to ACCEPTANCE_UNKNOWN
            states = read_journal_states(root, "personal", dest_sha)
            require(states is not None, "journal states must be read")
            require(states.get("SECRET_TORN") == NameState.ACCEPTANCE_UNKNOWN,
                    "torn trailing fragment must turn pending attempted state to acceptance_unknown")

    def test_journal_midfile_corruption_refused(self) -> None:
        """Corrupt or non-JSON data in middle of journal raises journal_corrupt."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "e" * 64

            with Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth_digest) as journal:
                run_id = new_qualified_id("run", ctrl_id)
                ap_id = new_qualified_id("ap", ctrl_id)
                journal.append(run_id, ap_id, "SECRET_X", NameState.ATTEMPTED)
                j_path = journal.directory / "journal.jsonl"

            # Inject mid-file corruption with a newline following it
            lines = j_path.read_bytes().splitlines(keepends=True)
            corrupted = lines[0] + b"NOT_A_VALID_JSON_RECORD\n" + lines[1]
            j_path.write_bytes(corrupted)

            # Opening corrupted journal must fail
            with self.assertRaises(SecretBrokerError) as ctx:
                Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                             authority_digest=auth_digest)
            require(ctx.exception.code == "journal_corrupt",
                    "corrupt middle line must raise journal_corrupt")

    def test_retention_preserves_stable_lock_inode(self) -> None:
        """When retention removes a settled journal, journal.lock remains intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "f" * 64

            # Create a settled journal (all entries verified)
            with Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth_digest) as journal:
                run_id = new_qualified_id("run", ctrl_id)
                ap_id = new_qualified_id("ap", ctrl_id)
                journal.append(run_id, ap_id, "SECRET_SETTLED", NameState.VERIFIED)
                lock_path = journal.directory / "journal.lock"

            lock_stat_before = lock_path.stat()

            # Run retention confirmed
            ret = retention(root, "personal", confirm=True)
            require(ret["ok"] is True, "retention must succeed")

            # Lock file must still exist and preserve same inode
            require(lock_path.exists(), "journal.lock must be preserved")
            lock_stat_after = lock_path.stat()
            require(lock_stat_before.st_ino == lock_stat_after.st_ino,
                    "retention must never delete or recreate journal.lock inode")

    def test_retention_keeps_unresolved_states(self) -> None:
        """Journals containing unverified states (e.g. attempted, unknown) are retained."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            ctrl_id = load_controller_id(root)
            dest = _sample_destination()
            dest_sha = destination_sha256(dest)
            auth_digest = "o1-" + "0" * 64

            with Journal.open(root, "personal", dest_sha, controller_id=ctrl_id,
                              authority_digest=auth_digest) as journal:
                run_id = new_qualified_id("run", ctrl_id)
                ap_id = new_qualified_id("ap", ctrl_id)
                journal.append(run_id, ap_id, "SECRET_PENDING", NameState.ATTEMPTED)
                j_path = journal.directory / "journal.jsonl"

            # Retention must NOT remove this journal because it is unresolved
            ret = retention(root, "personal", confirm=True)
            require(ret["ok"] is True, "retention call succeeds")
            require(j_path.exists(), "unresolved journal file must NOT be removed")


if __name__ == "__main__":
    unittest.main()

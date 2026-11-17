"""classify --check-destination run deadline is separate from per-request timeoutSeconds.

Regression: the whole existence check (one GET per mapped name) was bounded
by the destination's `timeoutSeconds`, the provider contract's per-request
cap, so a 146-name office profile over a ~0.8 s/request link always timed
out. The run deadline is now `--timeout-seconds` (default 300, 1..3600), and
the destination, and so the profile's authority, is unchanged.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from sandbox.secrets.ownership.migrate import authority_for
from tests.secret_ownership_support import (
    FakeAPI, FakeOwnershipAdapter, SyntheticOwnership, command_in_process, require,
)


class TestClassifyDeadline(unittest.TestCase):
    def setUp(self) -> None:
        self.exit_stack = ExitStack()
        self.addCleanup(self.exit_stack.close)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("network or native credential backend reached")

        import sandbox.secrets.credentials.manifest as credentials
        import socket
        self.exit_stack.enter_context(patch.object(credentials, "create_credential_store", forbidden))
        self.exit_stack.enter_context(patch.object(socket, "create_connection", forbidden))
        previous_umask = os.umask(0o077)
        self.addCleanup(lambda: os.umask(previous_umask))
        temp_root = Path("/tmp").resolve(strict=True)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="s069-classify-deadline-", dir=temp_root)
        self.addCleanup(self.temp_dir.cleanup)
        self.syn = SyntheticOwnership(Path(self.temp_dir.name).resolve())

    def _classify(self, *extra):
        ctx = self.syn.context()
        adapter = FakeOwnershipAdapter(FakeAPI())
        deadlines = []
        original = adapter.open_session

        def tracking(grant, *, deadline):
            deadlines.append((grant.destination.timeout_seconds, deadline - time.time()))
            return original(grant, deadline=deadline)

        adapter.open_session = tracking
        code, body = command_in_process(
            ctx, self.exit_stack, adapter,
            "ownership", "classify", "--profile", "personal", "--check-destination", *extra,
        )
        return ctx, code, body, deadlines

    def test_default_run_deadline_is_300s_not_per_request_timeout(self) -> None:
        _ctx, code, body, deadlines = self._classify()
        require(code == 0 and body["code"] == "classified", f"classify must succeed: {code} {body}")
        require(len(deadlines) == 1, "exactly one session")
        per_request, run_seconds = deadlines[0]
        require(per_request == 2, "destination timeoutSeconds stays the per-request cap")
        require(295 < run_seconds <= 300, f"run deadline must be ~300s, got {run_seconds}")

    def test_explicit_timeout_seconds_sets_run_deadline(self) -> None:
        _ctx, code, _body, deadlines = self._classify("--timeout-seconds", "45")
        require(code == 0, "classify must succeed")
        require(40 < deadlines[0][1] <= 45, f"run deadline must be ~45s, got {deadlines[0][1]}")

    def test_timeout_out_of_range_refuses_before_session(self) -> None:
        for value in ("0", "3601"):
            _ctx, code, body, deadlines = self._classify("--timeout-seconds", value)
            require(code != 0 and body["code"] == "input_invalid", f"{value} must refuse: {body}")
            require(not deadlines, "no session may open for an invalid timeout")

    def test_run_timeout_does_not_change_authority(self) -> None:
        ctx = self.syn.context()
        before = authority_for(ctx, "personal", "read")
        self._classify("--timeout-seconds", "900")
        require(authority_for(self.syn.context(), "personal", "read") == before,
                "authority digest must not depend on the run deadline")


if __name__ == "__main__":
    unittest.main()

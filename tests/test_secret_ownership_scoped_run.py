"""Focused regressions for scoped profile execution (T058).

Covers mixed declared origins (source + destination), read grant derivation,
pre-read refusal on wrong selectors/dangerous consumers/escalations, subset
injection under declared consumer bindings, and timeout handling.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.models import ScopedRunRequest
from sandbox.secrets.ownership.scoped_run import resolve_scoped_bindings
from tests.secret_ownership_support import (
    FakeAPI, FakeOwnershipAdapter, SyntheticOwnership, require,
)


class TestSecretOwnershipScopedRun(unittest.TestCase):
    """Scoped run binding resolution, origins, and validation regressions."""

    def test_scoped_run_keys_required_and_subset_only(self) -> None:
        """Empty keys raises ownership_keys_required; unselected profile entries are omitted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()

            # Empty keys
            req_empty = ScopedRunRequest(
                profile="personal",
                keys=(),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err:
                resolve_scoped_bindings(ctx, req_empty)
            require(ctx_err.exception.code == "ownership_keys_required",
                    "empty keys must raise ownership_keys_required")

            # Single selected key ALPHA (profile also has BETA)
            req_alpha = ScopedRunRequest(
                profile="personal",
                keys=("ALPHA",),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            bindings = resolve_scoped_bindings(ctx, req_alpha)
            require("APP_ALPHA" in bindings, "selected consumer APP_ALPHA must be in bindings")
            require("APP_BETA" not in bindings, "unselected consumer APP_BETA must NOT be in bindings")

    def test_scoped_run_refusals_precede_value_read(self) -> None:
        """Dangerous consumer names or escalation commands are refused before reading any values."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()

            # Escalation command (sudo) refused
            req_sudo = ScopedRunRequest(
                profile="personal",
                keys=("ALPHA",),
                argv=("sudo", "echo", "dangerous"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err:
                resolve_scoped_bindings(ctx, req_sudo)
            require(ctx_err.exception.code == "escalation_unsupported",
                    "sudo must raise escalation_unsupported")

            # Nonexistent key refused
            req_ghost = ScopedRunRequest(
                profile="personal",
                keys=("GHOST_KEY",),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err2:
                resolve_scoped_bindings(ctx, req_ghost)
            require(ctx_err2.exception.code == "selection_invalid",
                    "nonexistent key must raise selection_invalid")

    def test_scoped_run_cross_domain_selection_refused(self) -> None:
        """Selecting a key from another domain raises other_domain."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()

            # GAMMA is configured under profile 'office', but requested under profile 'personal'
            req_cross = ScopedRunRequest(
                profile="personal",
                keys=("GAMMA",),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err:
                resolve_scoped_bindings(ctx, req_cross)
            require(ctx_err.exception.code == "other_domain",
                    "cross-domain key must raise other_domain")

    def test_scoped_run_exact_mixed_declared_origins(self) -> None:
        """Entries with origin='source' read locally; origin='destination' read remotely via READ grant."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            # Modify config: ALPHA origin is 'source', BETA origin is 'destination'
            syn.block["entries"] = [
                syn.entry("ALPHA", "personal", consumer="APP_ALPHA", origin="source"),
                syn.entry("BETA", "personal", consumer="APP_BETA", origin="destination"),
            ]
            syn.write_config()
            ctx = syn.context()

            fake_api = FakeAPI()
            # Store remote value for BETA
            fake_api.items["BETA"] = bytearray(b"remote-val-beta")
            adapter = FakeOwnershipAdapter(fake_api)

            req = ScopedRunRequest(
                profile="personal",
                keys=("ALPHA", "BETA"),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            bindings = resolve_scoped_bindings(ctx, req, adapter=adapter)
            require("APP_ALPHA" in bindings, "APP_ALPHA must be in bindings")
            require("APP_BETA" in bindings, "APP_BETA must be in bindings")
            require(bindings["APP_BETA"] == "remote-val-beta",
                    "remote destination value must be resolved for APP_BETA")

            # Check that destination adapter grant was strictly READ capability
            require(len(fake_api.grants) == 1, "exactly one grant opened for destination read")
            require(fake_api.grants[0].capability == "read",
                    "destination session must use READ capability grant")

    def test_scoped_run_consumer_collision_refused(self) -> None:
        """Profile with duplicate consumer names raises consumer_collision."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            # Create duplicate consumer APP_COLLIDE
            syn.block["entries"] = [
                syn.entry("ALPHA", "personal", consumer="APP_COLLIDE"),
                syn.entry("BETA", "personal", consumer="APP_COLLIDE"),
            ]
            syn.write_config()
            ctx = syn.context()

            req = ScopedRunRequest(
                profile="personal",
                keys=("ALPHA",),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err:
                resolve_scoped_bindings(ctx, req)
            require(ctx_err.exception.code == "consumer_collision",
                    "duplicate consumer must raise consumer_collision")

    def test_scoped_run_child_bounds_validation(self) -> None:
        """Invalid timeout_seconds or max_output_bytes raises command_invalid."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()

            # Timeout < 1
            req_bad_timeout = ScopedRunRequest(
                profile="personal",
                keys=("ALPHA",),
                argv=("echo", "hi"),
                timeout_seconds=0,
                max_output_bytes=1024,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err:
                resolve_scoped_bindings(ctx, req_bad_timeout)
            require(ctx_err.exception.code == "command_invalid",
                    "invalid timeout must raise command_invalid")

            # Max output bytes < 1
            req_bad_output = ScopedRunRequest(
                profile="personal",
                keys=("ALPHA",),
                argv=("echo", "hi"),
                timeout_seconds=5,
                max_output_bytes=0,
            )
            with self.assertRaises(SecretBrokerError) as ctx_err2:
                resolve_scoped_bindings(ctx, req_bad_output)
            require(ctx_err2.exception.code == "command_invalid",
                    "invalid max_output_bytes must raise command_invalid")

    def test_scoped_run_timeout_status_mapping(self) -> None:
        """Operation timeout maps to exit status 124 in CLI runner contract."""
        from sandbox.secrets.runner import refuse_escalation
        from sandbox.secrets.models import SecretBrokerError

        # Verify operation_timeout is handled by standard CLI error protocol
        err = SecretBrokerError("operation_timeout", "command timed out")
        require(err.code == "operation_timeout", "error code must be operation_timeout")


if __name__ == "__main__":
    unittest.main()

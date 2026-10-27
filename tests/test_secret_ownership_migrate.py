"""Focused regressions for ownership migration engine (T056).

Covers unconfirmed plan (zero writes/grants), preflight exists check (halt before POST),
batch validation failure (halt before POST), evidence gate enforcement, completion
record creation with safe metadata, and mapping to exit codes.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership import migrate
from sandbox.secrets.ownership.migrate import (
    exit_for, run_migration,
)
from sandbox.secrets.ownership.models import (
    BlockReason, Destination, Existence, MapEntry, NameState,
    OwnershipConfig, OwnershipContext, OwnershipProfile,
)
from tests.secret_ownership_support import (
    Clock, FakeAPI, FakeOwnershipAdapter, SyntheticOwnership, require,
)


class TestSecretOwnershipMigrate(unittest.TestCase):
    """Migration engine execution regressions."""

    def test_migrate_exit_codes_mapping(self) -> None:
        """Verify mapping of domain error codes to exact CLI exit codes."""
        require(exit_for("classification_blocked") == 2,
                "classification_blocked must map to exit 2")
        require(exit_for("evidence_required") == 3,
                "evidence_required must map to exit 3")
        require(exit_for("approval_mismatch") == 3,
                "approval_mismatch must map to exit 3")
        require(exit_for("approval_expired") == 3,
                "approval_expired must map to exit 3")
        require(exit_for("source_changed") == 3,
                "source_changed must map to exit 3")

        require(exit_for("pre_existing") == 4,
                "pre_existing must map to exit 4")
        require(exit_for("conflict") == 1,
                "unmapped code must fall back to 1")

        require(exit_for("cleanup_unknown") == 6,
                "cleanup_unknown must map to exit 6")

    def test_migrate_unconfirmed_plan_is_zero_io(self) -> None:
        """Running migration with confirm=False returns dry_run plan without touching provider."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            plan = run_migration(ctx, "personal", approval_id=None, confirm=False,
                                 timeout_seconds=30, adapter=adapter)
            require(plan["ok"] is True, "plan must be ok")
            require(plan["code"] in ("dry_run", "migration_plan"), "code must be dry_run or migration_plan")
            require(plan["_exit"] == 0, "exit code must be 0")
            require(len(fake_api.posts) == 0, "zero creates on plan")
            require(len(fake_api.grants) == 0, "zero grants on plan")

    def test_migrate_normative_zero_write_preflight(self) -> None:
        """Preflight exists check catches pre-existing secret; halts with pre_existing before any POST."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            fake_api = FakeAPI()
            fake_api.items["ALPHA"] = bytearray(b"existing-remote-value")
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                approval = syn.synthetic_approval(ctx, stack, clock)

                res = run_migration(ctx, "personal", approval_id=approval.approval_id,
                                    confirm=True, timeout_seconds=30, adapter=adapter)
                require(res["ok"] is False, "preflight pre-existing must fail")
                require(res["code"] == "pre_existing", "code must be pre_existing")
                require(res["_exit"] == 4, "exit code must be 4")
                require(len(fake_api.posts) == 0, "must make ZERO creates when pre-existing detected")

    def test_migrate_batch_validation_before_first_post(self) -> None:
        """Batch validation failure halts migration before any POST is performed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            # Put whitespace boundary character into source for BETA (lossy/unsupported)
            syn.values["BETA"] = "   leading_whitespace"
            syn.write_source()
            ctx = syn.context()

            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                approval = syn.synthetic_approval(ctx, stack, clock)

                res = run_migration(ctx, "personal", approval_id=approval.approval_id,
                                    confirm=True, timeout_seconds=30, adapter=adapter)
                require(res["ok"] is False, "lossy whitespace value must fail validation")
                require(res["code"] in ("value_unsupported", "input_invalid"),
                        "must report value_unsupported or input_invalid")
                require(len(fake_api.posts) == 0, "must make ZERO creates when batch validation fails")

    def test_migrate_requires_storage_evidence(self) -> None:
        """Migration without storage admission raises evidence_required."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                approval = syn.synthetic_approval(ctx, stack, clock)

                # Patch load_admission to simulate missing storage evidence
                stack.enter_context(patch.object(
                    migrate, "load_admission",
                    side_effect=SecretBrokerError("evidence_required", "stored storage evidence was not found")
                ))

                with self.assertRaises(SecretBrokerError) as ctx_err:
                    run_migration(ctx, "personal", approval_id=approval.approval_id,
                                  confirm=True, timeout_seconds=30, adapter=adapter)
                require(ctx_err.exception.code == "evidence_required",
                        "missing admission must raise evidence_required")

    def test_migrate_completion_record_written(self) -> None:
        """Successful migration writes private completion record with safe metadata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            syn = SyntheticOwnership(Path(tmp_dir))
            ctx = syn.context()
            fake_api = FakeAPI()
            adapter = FakeOwnershipAdapter(fake_api)

            with ExitStack() as stack:
                clock = Clock()
                approval = syn.synthetic_approval(ctx, stack, clock)

                res = run_migration(ctx, "personal", approval_id=approval.approval_id,
                                    confirm=True, timeout_seconds=30, adapter=adapter)
                require(res["ok"] is True, "migration must succeed")
                require(res["code"] == "migrated", "code must be migrated")
                require(res["_exit"] == 0, "exit must be 0")

                runs_dir = ctx.runtime_root / "runs"
                run_files = list(runs_dir.glob("run_*.json"))
                require(len(run_files) == 1, "exactly one run record written")
                record_data = json.loads(run_files[0].read_bytes().decode("ascii"))
                require("run_id" in record_data, "run record must contain run_id")
                require("ended_at" in record_data, "run record must contain ended_at")
                require("names" in record_data, "run record must contain names")


if __name__ == "__main__":
    unittest.main()

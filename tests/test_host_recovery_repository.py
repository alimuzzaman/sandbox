import json
import tempfile
import unittest
import os
import hashlib
import copy
from unittest.mock import patch
from pathlib import Path

from sandbox.hosting.recovery.models import (
    RecoveryAction, RecoveryRequest, RecoveryResult, TargetIdentity,
)
from sandbox.hosting.recovery.repository import (
    MAX_ATTEMPTS, MAX_TOMBSTONES, RecoveryRepository,
)


class HostRecoveryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repository = RecoveryRepository(root / "hosts.json", root / "locks")
        self.target = TargetIdentity("remote", "project", "development")

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, suffix="1", generation=0):
        return RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, f"recover-{suffix}", "a" * 32,
            "apply-1", self.target, generation,
        )

    def test_feature_047_v2_image_state_is_preserved_as_non_authorizing_sibling(self):
        image_state = {
            "schema_version": 1, "declaration": "declared",
            "migration": "native_v2", "planes": {"requested": None},
            "current": {"opaque": "feature-047"}, "previous": None,
            "discrepancies": [],
        }
        self.repository._write({"version": 2, "hosts": {self.target.key: {
            "generation": 0, "images": image_state, "feature_047_marker": "kept",
        }}})

        loaded = self.repository.load()
        self.repository.begin(loaded, self.target.key, self.request("v2-preserve"))
        persisted = self.repository.load()

        self.assertEqual(persisted["version"], 2)
        self.assertEqual(persisted["hosts"][self.target.key]["images"], image_state)
        self.assertEqual(
            persisted["hosts"][self.target.key]["feature_047_marker"], "kept")

    def test_begin_and_commit_are_owner_only_and_generation_fenced(self):
        request = self.request()
        with self.repository.target_lock(self.target.key):
            state = self.repository.load()
            self.repository.begin(state, self.target.key, request)
            result = RecoveryResult(
                request, "success", "observation_reconciled", 1,
                "sha256:" + "1" * 64)
            committed = self.repository.commit(state, self.target.key, request, result)
            committed["evidence"]["id"] = "sha256:" + "f" * 64
        saved = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(saved["generation"], 1)
        self.assertEqual(saved["recovery_attempts"][0]["evidence"]["id"],
                         "sha256:" + "1" * 64)
        self.assertIsNone(saved["active_operation"])
        for field in ("accepted_at", "started_at", "completed_at"):
            self.assertIsInstance(saved["recovery_attempts"][0][field], int)
        self.assertEqual(self.repository.state_path.stat().st_mode & 0o777, 0o600)

    def test_request_identity_cannot_change_on_replay(self):
        request = self.request()
        state = self.repository.load()
        result = RecoveryResult(request, "refused", "legacy_evidence", 0)
        self.repository.commit(state, self.target.key, request, result)
        changed = RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, request.request_id, "b" * 32,
            "apply-1", self.target, 0,
        )
        with self.assertRaisesRegex(ValueError, "binding_mismatch"):
            self.repository.replay(
                self.repository.load()["hosts"][self.target.key], changed)

    def test_confirmation_cannot_change_on_edge_replay(self):
        common = dict(
            action=RecoveryAction.CONTINUE_EDGE, request_id="edge-same",
            job_id="a" * 32, original_request_id="apply-1", target=self.target,
            expected_generation=0, observation_request_id="recover-prior",
            evidence_id="sha256:" + "2" * 64)
        unconfirmed = RecoveryRequest(confirmed=False, **common)
        state = self.repository.load()
        self.repository.commit(state, self.target.key, unconfirmed,
                               RecoveryResult(unconfirmed, "refused",
                                              "confirmation_required", 0))
        with self.assertRaisesRegex(ValueError, "binding_mismatch"):
            self.repository.replay(
                self.repository.load()["hosts"][self.target.key],
                RecoveryRequest(confirmed=True, **common))

    def test_compaction_keeps_non_reusable_tombstone(self):
        for index in range(65):
            request = self.request(str(index))
            state = self.repository.load()
            result = RecoveryResult(request, "refused", "legacy_evidence", 0)
            self.repository.commit(state, self.target.key, request, result)
        record = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(len(record["recovery_attempts"]), 64)
        self.assertIn("recover-0", record["recovery_tombstones"])
        self.assertEqual(self.repository.replay(record, self.request("0"))["result_class"],
                         "legacy_evidence")

    def test_torn_duplicate_state_is_non_authorizing(self):
        state = self.repository.load()
        record = self.repository.target(state, self.target.key)
        attempt = RecoveryResult(
            self.request(), "refused", "legacy_evidence", 0).as_dict()
        attempt["completed_at"] = 1
        record["recovery_attempts"] = [attempt, dict(attempt)]
        self.repository._write(state)
        with self.assertRaisesRegex(ValueError, "duplicate recovery identity"):
            self.repository.target(self.repository.load(), self.target.key)

    def test_shared_state_lock_serializes_different_targets(self):
        with self.repository.target_lock(self.target.key):
            with self.assertRaisesRegex(TimeoutError, "operation_busy"):
                with self.repository.target_lock(
                        "other/project/development", timeout_seconds=0.01):
                    self.fail("shared state lock must serialize cross-target writers")

    def test_effect_lease_does_not_block_unrelated_target_transaction(self):
        with self.repository.effect_lock(self.target.key):
            with self.repository.target_lock(
                    "other/project/development", timeout_seconds=0.05):
                state = self.repository.load()
                state["hosts"]["other/project/development"] = {"generation": 0}
                self.repository._write(state)
        self.assertIn("other/project/development", self.repository.load()["hosts"])

    def test_commit_cannot_clear_a_different_active_owner(self):
        owner = self.request("owner")
        other = self.request("other")
        state = self.repository.load()
        self.repository.begin(state, self.target.key, owner)
        current = self.repository.load()
        with self.assertRaisesRegex(ValueError, "operation_busy"):
            self.repository.commit(
                current, self.target.key, other,
                RecoveryResult(other, "refused", "mutation_required", 0))
        saved = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(saved["active_operation"]["request_id"], owner.request_id)

    def test_full_retention_refuses_before_active_owner_is_written(self):
        state = self.repository.load()
        record = self.repository.target(state, self.target.key)
        template = RecoveryResult(
            self.request("template"), "refused", "legacy_evidence", 0).as_dict()
        template["completed_at"] = 1
        record["recovery_attempts"] = []
        for index in range(MAX_ATTEMPTS):
            item = dict(template, request_id=f"attempt-{index}",
                        request_digest="sha256:" + f"{index:064x}")
            record["recovery_attempts"].append(item)
        record["recovery_tombstones"] = {}
        for index in range(MAX_TOMBSTONES):
            item = dict(template, request_id=f"old-{index}",
                        request_digest="sha256:" + f"{index + MAX_ATTEMPTS:064x}",
                        effect_unknown=False, phases=[])
            record["recovery_tombstones"][item["request_id"]] = item
        with self.assertRaisesRegex(ValueError, "retention_full"):
            self.repository.begin(state, self.target.key, self.request("new"))
        self.assertNotIn("active_operation", record)

    def test_atomic_writer_fsyncs_file_and_parent_directory(self):
        state = self.repository.load()
        with patch("sandbox.hosting.recovery.repository.os.fsync",
                   wraps=os.fsync) as fsync:
            self.repository._write(state)
        self.assertGreaterEqual(fsync.call_count, 2)

    def test_lock_and_state_inodes_must_be_owner_only_regular_single_links(self):
        root = Path(self.temporary.name)
        real_locks = root / "real-locks"
        real_locks.mkdir(mode=0o700)
        linked = RecoveryRepository(root / "linked-hosts.json", root / "linked-locks")
        linked.lock_dir.symlink_to(real_locks, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            with linked.state_lock():
                self.fail("symlinked lock directory must not authorize ownership")

        unsafe = RecoveryRepository(root / "unsafe-hosts.json", root / "unsafe-locks")
        unsafe.lock_dir.mkdir(mode=0o700)
        unsafe.lock_dir.chmod(0o750)
        with self.assertRaisesRegex(ValueError, "lock directory is unsafe"):
            with unsafe.effect_lock(self.target.key):
                self.fail("unsafe directory must not authorize a lock")

        unsafe.lock_dir.chmod(0o700)
        victim = root / "victim.lock"
        victim.write_text("")
        victim.chmod(0o600)
        lock_name = hashlib.sha256(self.target.key.encode()).hexdigest() + ".lock"
        os.link(victim, unsafe.lock_dir / lock_name)
        with self.assertRaisesRegex(ValueError, "effect lock is unsafe"):
            with unsafe.effect_lock(self.target.key):
                self.fail("hard-linked lock must not authorize ownership")

        (unsafe.lock_dir / "state.lock").mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, "state lock is unsafe"):
            with unsafe.state_lock():
                self.fail("non-regular state lock must not authorize ownership")

        state_victim = root / "state-victim.json"
        state_victim.write_text('{"version":1,"hosts":{}}')
        state_victim.chmod(0o600)
        os.link(state_victim, unsafe.state_path)
        with self.assertRaisesRegex(ValueError, "managed-host state is unsafe"):
            unsafe.load()

    def test_trusted_0755_runtime_parent_accepts_owner_only_state_file(self):
        root = Path(self.temporary.name)
        runtime = root / "runtime-compatible"
        runtime.mkdir(mode=0o755)
        repository = RecoveryRepository(
            runtime / "hosts.json", root / "strict-locks")
        repository._write({"version": 1, "hosts": {}})
        self.assertEqual(repository.load(), {"version": 1, "hosts": {}})
        self.assertEqual(repository.state_path.stat().st_mode & 0o777, 0o600)

        repository.state_path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "managed-host state is unsafe"):
            repository.load()

    def test_symlinked_lock_parent_creates_no_managed_child(self):
        root = Path(self.temporary.name)
        target = root / "symlink-target"
        target.mkdir(mode=0o700)
        linked_parent = root / "linked-parent"
        linked_parent.symlink_to(target, target_is_directory=True)
        repository = RecoveryRepository(
            root / "hosts-safe.json", linked_parent / "locks")
        with self.assertRaisesRegex(ValueError, "parent path is unsafe"):
            with repository.effect_lock(self.target.key):
                self.fail("symlinked parent must not authorize a lock")
        self.assertEqual(list(target.iterdir()), [])

    def test_empty_active_owner_and_provisional_are_invalid_non_null_state(self):
        for field in ("active_operation", "recovery_provisional"):
            with self.subTest(field=field):
                state = self.repository.load()
                record = self.repository.target(state, self.target.key)
                record[field] = {}
                self.repository._write(state)
                with self.assertRaisesRegex(ValueError, "invalid"):
                    self.repository.target(self.repository.load(), self.target.key)
                record.pop(field)
                self.repository._write(state)

    def test_non_null_malformed_uncertainty_is_invalid_state(self):
        for malformed in ({}, "unknown", {"schema_version": 1,
                           "request_id": "edge", "request_digest": "sha256:x",
                           "action": "continue_edge", "generation": 0,
                           "effect_scope": "edge_only"}):
            with self.subTest(malformed=malformed):
                state = self.repository.load()
                record = self.repository.target(state, self.target.key)
                record["recovery_uncertainty"] = malformed
                self.repository._write(state)
                with self.assertRaisesRegex(ValueError, "invalid recovery uncertainty"):
                    self.repository.target(self.repository.load(), self.target.key)
                record.pop("recovery_uncertainty")
                self.repository._write(state)

    def test_malformed_terminal_schemas_never_replay_or_authorize(self):
        request = self.request("strict")
        valid = RecoveryResult(
            request, "success", "observation_reconciled", 1,
            "sha256:" + "2" * 64).as_dict()
        valid["completed_at"] = 1
        malformed = []
        with_private = dict(valid, private_material="must-not-escape")
        malformed.append(with_private)
        malformed.append(dict(valid, action="continue_edge"))
        malformed.append(dict(valid, effect_scope="edge_only"))
        malformed.append(dict(valid, generation={"expected": 0, "resulting": 9}))
        malformed.append(dict(valid, result_family="refused", ok=False))
        malformed.append(dict(valid, result_family="failed", result_class="legacy_evidence",
                              ok=False, generation={"expected": 0, "resulting": 0}))
        for item in malformed:
            with self.subTest(item=item):
                state = self.repository.load()
                state["hosts"][self.target.key] = {
                    "generation": 1, "recovery_attempts": [item],
                    "recovery_tombstones": {}}
                self.repository._write(state)
                with self.assertRaisesRegex(ValueError, "invalid recovery attempt state"):
                    record = self.repository.target(self.repository.load(), self.target.key)
                    self.repository.replay(record, request)

        state = self.repository.load()
        state["hosts"][self.target.key] = {
            "generation": 0, "recovery_attempts": [valid],
            "recovery_tombstones": {}}
        self.repository._write(state)
        with self.assertRaisesRegex(ValueError, "invalid recovery terminal generation"):
            self.repository.target(self.repository.load(), self.target.key)

    def test_malformed_tombstone_is_rejected_before_replay(self):
        request = self.request("tomb")
        item = RecoveryResult(request, "refused", "legacy_evidence", 0).as_dict()
        item.update({"completed_at": 1, "effect_unknown": True, "phases": []})
        state = self.repository.load()
        state["hosts"][self.target.key] = {
            "generation": 0, "recovery_attempts": [],
            "recovery_tombstones": {request.request_id: item}}
        self.repository._write(state)
        with self.assertRaisesRegex(ValueError, "invalid recovery tombstone state"):
            self.repository.target(self.repository.load(), self.target.key)

    def test_commit_validates_full_terminal_before_any_state_mutation(self):
        for kind in ("hostile_phase", "private_material"):
            with self.subTest(kind=kind):
                request = self.request(kind)
                state = {"version": 1, "hosts": {}}
                self.repository._write(state)
                self.repository.begin(state, self.target.key, request)
                state = self.repository.load()
                before = copy.deepcopy(state)
                if kind == "hostile_phase":
                    result = RecoveryResult(
                        request, "success", "observation_reconciled", 1,
                        "sha256:" + "4" * 64,
                        ({"phase": "runtime", "state": "complete",
                          "private_material": "must-not-escape"},))
                else:
                    payload = RecoveryResult(
                        request, "success", "observation_reconciled", 1,
                        "sha256:" + "4" * 64).as_dict()
                    payload["private_material"] = "must-not-escape"

                    class HostileResult:
                        def as_dict(self):
                            return payload

                    result = HostileResult()
                with self.assertRaisesRegex(ValueError, "invalid recovery attempt state") as raised:
                    self.repository.commit(
                        state, self.target.key, request, result,
                        receipt={"operation_digest": "sha256:" + "5" * 64})
                self.assertNotIn("must-not-escape", str(raised.exception))
                self.assertEqual(state, before)
                self.assertEqual(self.repository.load(), before)

    def test_terminal_identity_cannot_intersect_active_or_provisional_owner(self):
        observation = self.request("owner-cross")
        terminal = RecoveryResult(
            observation, "refused", "legacy_evidence", 0).as_dict()
        terminal["completed_at"] = 1
        active_observation = {
            "schema_version": 1, "request_id": observation.request_id,
            "request_digest": observation.digest, "action": observation.action.value,
            "expected_generation": 0, "accepted_at": 1, "started_at": 1,
            "phase": "observation_pending", "effect_entered": False,
        }
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-owner-cross", "a" * 32,
            "apply-1", self.target, 0,
            observation_request_id="prior-observation",
            evidence_id="sha256:" + "6" * 64, confirmed=True)
        edge_terminal = RecoveryResult(
            edge, "refused", "confirmation_required", 0).as_dict()
        edge_terminal["completed_at"] = 1
        active_edge = {
            "schema_version": 1, "request_id": edge.request_id,
            "request_digest": edge.digest, "action": edge.action.value,
            "expected_generation": 0, "accepted_at": 1, "started_at": 1,
            "phase": "effect_entered", "effect_entered": True,
            "effect_entered_at": 1,
        }
        for active, attempt in ((active_observation, terminal),
                                (active_edge, edge_terminal)):
            with self.subTest(phase=active["phase"]):
                state = {"version": 1, "hosts": {self.target.key: {
                    "generation": 0, "active_operation": active,
                    "recovery_attempts": [attempt], "recovery_tombstones": {}}}}
                self.repository._write(state)
                with self.assertRaisesRegex(ValueError, "invalid active recovery operation"):
                    self.repository.target(self.repository.load(), self.target.key)

        provisional = {
            "schema_version": 1, "request_id": observation.request_id,
            "request_digest": observation.digest,
            "operation_digest": "sha256:" + "7" * 64,
            "evidence_id": "sha256:" + "8" * 64,
            "expected_generation": 0, "authorizing": False, "created_at": 1,
        }
        active_observation["phase"] = "reconciliation_provisional"
        state = {"version": 1, "hosts": {self.target.key: {
            "generation": 0, "active_operation": active_observation,
            "recovery_provisional": provisional,
            "recovery_attempts": [terminal], "recovery_tombstones": {}}}}
        self.repository._write(state)
        with self.assertRaisesRegex(ValueError, "invalid recovery provisional state"):
            self.repository.target(self.repository.load(), self.target.key)


if __name__ == "__main__":
    unittest.main()

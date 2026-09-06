from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import unittest


class ServerConfigModelTests(unittest.TestCase):
    def setUp(self):
        from sandbox.server_config.models import ServerConfigFragment, ServerType

        self.now = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
        content = b"location = /cached { return 200; }\n"
        self.fragment = ServerConfigFragment.create(
            name="page-cache",
            authority="wordpress-cache-v1",
            server_type=ServerType.NGINX,
            content=content,
            content_locator=(
                "fragments/" + hashlib.sha256(content).hexdigest() + ".fragment"
            ),
            instance_incarnation_id="inc_" + "1" * 32,
            created_at=self.now,
            policy_revision="wordpress-cache-v1/nginx-v1",
        )

    def test_fragment_identity_uses_exact_bytes_and_private_locator_is_not_projected(self):
        from sandbox.server_config.models import ServerConfigFragment, ServerType

        changed_content = b"location = /cached { return 204; }\n"
        changed = ServerConfigFragment.create(
            name="page-cache",
            authority="wordpress-cache-v1",
            server_type=ServerType.NGINX,
            content=changed_content,
            content_locator=(
                "fragments/" + hashlib.sha256(changed_content).hexdigest() + ".fragment"
            ),
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            created_at=self.now,
            policy_revision=self.fragment.policy_revision,
        )
        self.assertRegex(self.fragment.content_id, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(self.fragment.content_id, changed.content_id)
        self.assertNotIn("content_locator", self.fragment.to_public_dict())
        self.assertNotIn("location =", repr(self.fragment))
        with self.assertRaises(FrozenInstanceError):
            self.fragment.name = "changed"

    def test_fragment_model_refuses_invalid_name_owner_and_private_locator(self):
        from sandbox.server_config.models import ServerConfigFragment, ServerType

        common = {
            "authority": "wordpress-cache-v1",
            "server_type": ServerType.NGINX,
            "content": b"safe\n",
            "instance_incarnation_id": self.fragment.instance_incarnation_id,
            "created_at": self.now,
            "policy_revision": self.fragment.policy_revision,
        }
        for name, locator in (
            ("Bad", "fragments/safe.fragment"),
            ("safe", "../outside"),
            ("safe", "/private/fragment"),
            ("safe", "fragments\\outside"),
        ):
            with self.subTest(name=name, locator=locator):
                with self.assertRaises(ValueError):
                    ServerConfigFragment.create(name=name, content_locator=locator, **common)

    def test_fragment_set_is_ordered_duplicate_free_and_identity_is_owner_bound(self):
        from sandbox.server_config.models import FragmentSet, ServerConfigFragment, ServerType

        alpha = ServerConfigFragment.create(
            name="alpha-cache", authority="wordpress-cache-v1",
            server_type=ServerType.NGINX, content=b"alpha\n",
            content_locator=(
                "fragments/" + hashlib.sha256(b"alpha\n").hexdigest() + ".fragment"
            ),
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            created_at=self.now, policy_revision=self.fragment.policy_revision,
        )
        fragment_set = FragmentSet.create(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX,
            fragments=(self.fragment, alpha),
            renderer_revision="nginx-renderer-v1",
            rendered_generation_id="sha256:" + "a" * 64,
            created_at=self.now,
        )
        self.assertEqual([item.name for item in fragment_set.fragments], ["alpha-cache", "page-cache"])
        self.assertRegex(fragment_set.fragment_set_id, r"^sha256:[0-9a-f]{64}$")

        rerendered = FragmentSet.create(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX,
            fragments=(alpha, self.fragment),
            renderer_revision="nginx-renderer-v2",
            rendered_generation_id="sha256:" + "f" * 64,
            created_at=self.now,
        )
        self.assertEqual(fragment_set.fragment_set_id, rerendered.fragment_set_id)

        other_owner = FragmentSet.create(
            instance_incarnation_id="inc_" + "2" * 32,
            server_type=ServerType.NGINX, fragments=(),
            renderer_revision="nginx-renderer-v1",
            rendered_generation_id="sha256:" + "b" * 64, created_at=self.now,
        )
        empty = FragmentSet.create(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, fragments=(),
            renderer_revision="nginx-renderer-v1",
            rendered_generation_id="sha256:" + "b" * 64, created_at=self.now,
        )
        self.assertNotEqual(empty.fragment_set_id, other_owner.fragment_set_id)
        with self.assertRaisesRegex(ValueError, "duplicate fragment name"):
            FragmentSet.create(
                instance_incarnation_id=self.fragment.instance_incarnation_id,
                server_type=ServerType.NGINX,
                fragments=(self.fragment, self.fragment),
                renderer_revision="nginx-renderer-v1",
                rendered_generation_id="sha256:" + "a" * 64, created_at=self.now,
            )
        with self.assertRaisesRegex(ValueError, "fragment_set_id"):
            type(fragment_set)(
                fragment_set_id="sha256:" + "0" * 64,
                instance_incarnation_id=fragment_set.instance_incarnation_id,
                server_type=fragment_set.server_type,
                fragments=fragment_set.fragments,
                renderer_revision=fragment_set.renderer_revision,
                rendered_generation_id=fragment_set.rendered_generation_id,
                created_at=fragment_set.created_at,
            )

    def test_runtime_observation_requires_exact_fresh_ready_preconditions(self):
        from sandbox.server_config.models import Readiness, RuntimeObservation, ServerType

        observation = RuntimeObservation(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, runtime_id="runtime-1",
            image_id="sha256:" + "a" * 64, mount_id="sha256:" + "b" * 64,
            observed_generation_id="sha256:" + "c" * 64, readiness=Readiness.READY,
            observed_at=self.now,
        )
        self.assertTrue(observation.authorizes(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, mount_id="sha256:" + "b" * 64,
            generation_id="sha256:" + "c" * 64, not_before=self.now - timedelta(seconds=1),
        ))
        self.assertFalse(observation.authorizes(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, mount_id="sha256:" + "d" * 64,
            generation_id="sha256:" + "c" * 64, not_before=self.now - timedelta(seconds=1),
        ))
        self.assertFalse(observation.authorizes(
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, mount_id="sha256:" + "b" * 64,
            generation_id="sha256:" + "c" * 64, not_before=self.now + timedelta(seconds=1),
        ))
        with self.assertRaisesRegex(ValueError, "image_id"):
            RuntimeObservation(
                instance_incarnation_id=self.fragment.instance_incarnation_id,
                server_type=ServerType.NGINX, runtime_id="runtime-1", image_id="",
                mount_id="sha256:" + "b" * 64, observed_generation_id="sha256:" + "c" * 64,
                readiness=Readiness.READY, observed_at=self.now,
            )

    def test_validation_evidence_digest_is_content_free_and_phase_map_is_immutable(self):
        from sandbox.server_config.models import PhaseResult, ServerType, ValidationEvidence

        evidence = ValidationEvidence.create(
            adapter=ServerType.NGINX, candidate_generation_id="sha256:" + "a" * 64,
            runtime_precondition_digest="sha256:" + "b" * 64,
            policy=PhaseResult("accepted", "sha256:" + "c" * 64, self.now),
            native_validation=PhaseResult("accepted", "sha256:" + "d" * 64, self.now),
            inclusion_proof=PhaseResult("accepted", "sha256:" + "e" * 64, self.now),
            started_at=self.now, ended_at=self.now + timedelta(seconds=3),
        )
        self.assertRegex(evidence.evidence_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("output", evidence.to_public_dict())
        with self.assertRaises(FrozenInstanceError):
            evidence.policy = PhaseResult("refused", None, self.now)
        with self.assertRaisesRegex(ValueError, "phase evidence"):
            ValidationEvidence(
                adapter=ServerType.NGINX,
                candidate_generation_id="sha256:" + "a" * 64,
                runtime_precondition_digest="sha256:" + "b" * 64,
                policy="caller content", native_validation=evidence.native_validation,
                inclusion_proof=evidence.inclusion_proof,
                started_at=self.now, ended_at=self.now,
                evidence_digest="sha256:" + "c" * 64,
            )
        with self.assertRaisesRegex(ValueError, "evidence_digest"):
            ValidationEvidence(
                adapter=evidence.adapter,
                candidate_generation_id=evidence.candidate_generation_id,
                runtime_precondition_digest=evidence.runtime_precondition_digest,
                policy=evidence.policy, native_validation=evidence.native_validation,
                inclusion_proof=evidence.inclusion_proof,
                started_at=evidence.started_at, ended_at=evidence.ended_at,
                evidence_digest="sha256:" + "0" * 64,
            )

    def test_known_good_receipt_and_transaction_enforce_safe_transitions(self):
        from sandbox.server_config.models import (
            ActivationTransaction, KnownGoodReceipt, Operation, ServerType,
            TerminalOutcome, TransactionPhase,
        )

        receipt = KnownGoodReceipt(
            schema=1, instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, fragment_set_id="sha256:" + "a" * 64,
            generation_id="sha256:" + "b" * 64, runtime_image_id="sha256:" + "c" * 64,
            mount_id="sha256:" + "d" * 64, validation_evidence_id="sha256:" + "e" * 64,
            readiness_evidence_id="sha256:" + "f" * 64, committed_at=self.now,
        )
        with self.assertRaises(FrozenInstanceError):
            receipt.generation_id = "sha256:" + "0" * 64

        tx = ActivationTransaction.requested(
            transaction_id="txn_" + "1" * 32, operation=Operation.APPLY,
            fragment_name="page-cache",
            instance_incarnation_id=self.fragment.instance_incarnation_id,
            server_type=ServerType.NGINX, prior_set_id="sha256:" + "a" * 64,
            prior_generation_id="sha256:" + "b" * 64, candidate_set_id="sha256:" + "c" * 64,
            candidate_generation_id="sha256:" + "d" * 64,
            runtime_precondition_digest="sha256:" + "e" * 64,
            deadline_at=self.now + timedelta(seconds=180),
        )
        prepared = tx.transition(TransactionPhase.PREPARED)
        validated = prepared.transition(TransactionPhase.VALIDATED)
        activating = validated.transition(TransactionPhase.ACTIVATING)
        restoring = activating.begin_rollback(code="reload_failed", at=self.now)
        self.assertTrue(restoring.rollback_attempted)
        self.assertEqual(restoring.phase, TransactionPhase.RESTORING_PRIOR)
        with self.assertRaisesRegex(ValueError, "rollback already attempted"):
            restoring.begin_rollback(code="again", at=self.now)
        with self.assertRaisesRegex(ValueError, "invalid transaction transition"):
            prepared.transition(TransactionPhase.COMMITTED)

        rolled_back = restoring.transition(TransactionPhase.RECOVERY_RELOADING).transition(
            TransactionPhase.RECOVERY_OBSERVING_READY
        ).finish(TerminalOutcome.ROLLED_BACK)
        self.assertTrue(rolled_back.is_terminal)
        with self.assertRaisesRegex(ValueError, "terminal transaction"):
            rolled_back.transition(TransactionPhase.PREPARED)

    def test_bounded_outcome_projection_contains_only_codes_and_identities(self):
        from sandbox.server_config.models import OperationResult, TerminalOutcome

        result = OperationResult(
            outcome=TerminalOutcome.REFUSED, code="fragment_policy_refused",
            mutated=False, instance_incarnation_id=self.fragment.instance_incarnation_id,
            fragment_name=self.fragment.name, fragment_set_id="sha256:" + "a" * 64,
            phase_codes=("policy_refused",),
        )
        projected = result.to_public_dict()
        self.assertEqual(projected["mutated"], False)
        self.assertNotIn("content", projected)
        self.assertLessEqual(len(projected["code"]), 128)

        unknown = OperationResult(
            outcome=TerminalOutcome.RECOVERY_NEEDED, code="recovery_unproven",
            mutated=None, instance_incarnation_id=self.fragment.instance_incarnation_id,
            fragment_name=self.fragment.name, fragment_set_id="sha256:" + "b" * 64,
        )
        self.assertIsNone(unknown.to_public_dict()["mutated"])
        invalid_mutation_flags = (
            (TerminalOutcome.ACTIVE, False),
            (TerminalOutcome.NO_OP, True),
            (TerminalOutcome.REFUSED, True),
            (TerminalOutcome.CONFLICT, True),
            (TerminalOutcome.ROLLED_BACK, False),
            (TerminalOutcome.RECOVERY_NEEDED, False),
        )
        for outcome, mutated in invalid_mutation_flags:
            with self.subTest(outcome=outcome, mutated=mutated):
                with self.assertRaisesRegex(ValueError, "mutated.*outcome"):
                    OperationResult(
                        outcome=outcome, code="invalid_result", mutated=mutated,
                        instance_incarnation_id=self.fragment.instance_incarnation_id,
                        fragment_name=self.fragment.name, fragment_set_id=None,
                    )
        with self.assertRaisesRegex(ValueError, "fragment name"):
            OperationResult(
                outcome=TerminalOutcome.REFUSED, code="refused", mutated=False,
                instance_incarnation_id=self.fragment.instance_incarnation_id,
                fragment_name="caller supplied content here",
                fragment_set_id=None,
            )

    def test_behavior_evidence_accepts_only_bounded_integer_or_digest_sentinels(self):
        from sandbox.server_config.models import BehaviorEvidence, Readiness

        common = {
            "instance_incarnation_id": self.fragment.instance_incarnation_id,
            "runtime_id": "runtime-1", "image_id": "sha256:" + "a" * 64,
            "fragment_set_id": "sha256:" + "b" * 64, "request_id": "request-1",
            "response_status": 200, "server_marker": "HIT",
            "readiness": Readiness.READY, "observed_at": self.now,
        }
        evidence = BehaviorEvidence(
            php_sentinel_before=1, php_sentinel_after="sha256:" + "c" * 64, **common
        )
        self.assertNotIn("content", evidence.to_public_dict())
        with self.assertRaisesRegex(ValueError, "php_sentinel_before"):
            BehaviorEvidence(
                php_sentinel_before="caller response body" * 100_000,
                php_sentinel_after=1,
                **common,
            )
        for marker in (
            "Authorization: bearer synthetic-secret", "HIT\nSet-Cookie: bad", b"HIT"
        ):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(ValueError, "server_marker"):
                    BehaviorEvidence(
                        php_sentinel_before=1, php_sentinel_after=1,
                        **{**common, "server_marker": marker},
                    )
        with self.assertRaisesRegex(ValueError, "php_sentinel_before"):
            BehaviorEvidence(
                php_sentinel_before=1 << 3322, php_sentinel_after=1, **common
            )


if __name__ == "__main__":
    unittest.main()

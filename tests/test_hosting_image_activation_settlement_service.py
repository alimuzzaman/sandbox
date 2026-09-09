import copy
import unittest
from types import SimpleNamespace

from sandbox.hosting.images.activation.repository import ActivationRepository
from sandbox.hosting.images.activation.settlement_models import SettlementApproval, SettlementObservation
from sandbox.hosting.images.activation.settlement_service import SettlementService
from sandbox.hosting.images.activation.settlement_service import SettlementError
from tests.test_hosting_image_activation_settlement_models import ssh_signature
from tests.test_hosting_image_activation_settlement_repository import _state, _plan
from tests.test_hosting_image_activation_v2 import (
    FakeHostStatePort, FakeStageRepositoryPort, FakeTargetMutationPort,
)


class Store:
    def __init__(self, approval):
        self.approval = approval
        self.reads = 0

    def read_settlement(self, plan, approval_digest, *, now):
        self.reads += 1
        if (approval_digest != self.approval.approval_digest
                or plan.plan_digest != self.approval.plan_digest
                or not self.approval.issued_at <= now < self.approval.expires_at):
            raise ValueError("authority_missing")
        return self.approval


class Observer:
    def __init__(self, observation):
        self.observation = observation
        self.calls = 0
        self.change_after = None

    def observe(self, *, transaction, generation):
        self.calls += 1
        if self.change_after is not None and self.calls > self.change_after:
            raw = self.observation.body_mapping()
            raw.pop("schema_version")
            raw["target"] = self.observation.target
            raw["container_identities"] = ("2" * 64,)
            raw["preserved_identities"] = tuple(raw["preserved_identities"])
            raw["process_identities"] = ()
            return SettlementObservation.create(**raw)
        return self.observation


class SettlementServiceTests(unittest.TestCase):
    def test_refusal_detail_survives_observe_without_state_or_authority_changes(self):
        detail = {'schema_version': 1, 'reason': 'helper_activity_present',
                  'subject': 'helper_activity', 'sample': 'first'}
        def refuse(**kwargs):
            raise SettlementError('not_quiescent', detail)
        self.observer.observe = refuse
        before = copy.deepcopy(self.host.state)
        with self.assertRaises(SettlementError) as caught:
            self.service._observe(self.host.state['active'], 0)
        self.assertEqual(caught.exception.code, 'not_quiescent')
        self.assertEqual(caught.exception.diagnostic, detail)
        self.assertEqual(self.host.state, before)
        self.assertEqual(self.store.reads, 0)
        self.assertEqual(self.stage.custody.released, 0)
        result = self.service.apply(self.plan, approval_digest=self.approval.approval_digest)
        self.assertEqual(result['code'], 'not_quiescent')
        self.assertNotIn('diagnostic', result)
        self.assertEqual(self.host.state, before)
        self.assertEqual(self.stage.custody.released, 0)

    def setUp(self):
        self.plan = _plan()
        self.approval = SettlementApproval.create(authority_id="settlement/operator",
            authority_revision="1", plan_digest=self.plan.plan_digest,
            issued_at=50, expires_at=200, signature=ssh_signature())
        self.host = FakeHostStatePort(); self.host.state = _state()
        self.stage = FakeStageRepositoryPort()
        pin = self.host.state["active"]["proof_pin"]
        self.stage.custody.lease = SimpleNamespace(
            **{key: pin[key] for key in ("lease_id", "holder", "proof_digest")},
            target_identity=self.plan.target.target_identity,
            acceptance_receipt=pin["host_acceptance_receipt"])
        self.repository = ActivationRepository(host_state_port=self.host,
            stage_repository=self.stage, target_mutation_port=FakeTargetMutationPort())
        self.observer = Observer(self.plan.observation)
        self.store = Store(self.approval)
        self.service = SettlementService(repository=self.repository,
            observer=self.observer, approval_store=self.store, clock=lambda: 100)

    def test_plan_observes_without_committing_or_releasing(self):
        before = copy.deepcopy(self.host.state)
        plan = self.service.plan(target=self.plan.target.target_identity,
            request_id=self.plan.request_id, transaction_digest=self.plan.transaction_digest,
            expected_generation=0, data_assessment=self.plan.data_assessment)
        self.assertEqual(plan, self.plan)
        self.assertEqual(self.host.state, before)
        self.assertEqual(self.observer.calls, 1)
        self.assertEqual(self.stage.custody.released, 0)

    def test_apply_reobserves_commits_then_releases_and_replay_has_no_observations(self):
        original = copy.deepcopy(self.host.state["results"])
        release = self.stage.custody.release
        def after_commit(lease, evidence):
            self.assertIsNone(self.host.state["active"])
            self.assertIn(self.plan.request_id, self.host.state["settlements"])
            release(lease, evidence)
            self.stage.custody.lease = None
        self.stage.custody.release = after_commit
        result = self.service.apply(self.plan, approval_digest=self.approval.approval_digest)
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "abandoned_with_effects")
        self.assertEqual(self.host.state["generation"], 0)
        self.assertEqual(self.host.state["results"], original)
        self.assertEqual(self.observer.calls, 2)
        self.assertEqual(self.store.reads, 2)
        self.assertEqual(self.stage.custody.released, 1)
        self.store.read_settlement = lambda *a, **kw: self.fail("replay reopened authority")
        self.observer.observe = lambda **kw: self.fail("replay reobserved runtime")
        self.assertEqual(self.service.apply(self.plan,
            approval_digest=self.approval.approval_digest), result)

    def test_observation_drift_or_missing_approval_retains_owner(self):
        for changed in (True, False):
            before = copy.deepcopy(self.host.state)
            self.observer.calls = 0
            self.observer.change_after = 1 if changed else None
            result = self.service.apply(self.plan, approval_digest=(
                self.approval.approval_digest if changed else "sha256:" + "f" * 64))
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "evidence_changed" if changed else "authority_missing")
            self.assertEqual(self.host.state, before)
            self.assertEqual(self.stage.custody.released, 0)

    def test_empty_runtime_requires_explicit_approval_and_retains_data_uncertainty(self):
        body = self.plan.observation.body_mapping()
        body.pop("schema_version")
        body.update(container_identities=(), preserved_identities=(), process_identities=())
        self.observer.observation = SettlementObservation.create(**body)
        plan = self.service.plan(target=self.plan.target.target_identity,
            request_id=self.plan.request_id, transaction_digest=self.plan.transaction_digest,
            expected_generation=0, data_assessment=self.plan.data_assessment)
        before = copy.deepcopy(self.host.state)
        refused = self.service.apply(plan, approval_digest=self.approval.approval_digest)
        self.assertEqual(refused["code"], "authority_missing")
        self.assertEqual(self.host.state, before)
        self.assertEqual(self.stage.custody.released, 0)
        approval = SettlementApproval.create(authority_id="settlement/operator", authority_revision="1",
            plan_digest=plan.plan_digest, issued_at=50, expires_at=200, signature=ssh_signature())
        self.store.approval = approval
        result = self.service.apply(plan, approval_digest=approval.approval_digest)
        self.assertEqual(result["code"], "abandoned_with_effects")
        self.assertEqual(self.host.state["results"], before["results"])
        self.assertEqual(self.host.state["generation"], 0)
        self.assertIsNone(self.host.state["current"])

    def test_lost_commit_ack_never_releases_until_durable_replay(self):
        update = self.host.update_activation_nested
        def lose_ack(*args, **kwargs):
            update(*args, **kwargs)
            raise OSError("synthetic lost write acknowledgement")
        self.host.update_activation_nested = lose_ack
        result = self.service.apply(self.plan, approval_digest=self.approval.approval_digest)
        self.assertEqual(result["code"], "persistence_uncertain")
        self.assertEqual(self.stage.custody.released, 0)
        self.assertIsNone(self.host.state["active"])
        self.observer.observe = lambda **kw: self.fail("committed replay reobserved")
        self.assertTrue(self.service.apply(self.plan,
            approval_digest=self.approval.approval_digest)["ok"])
        self.assertEqual(self.stage.custody.released, 1)

    def test_release_failure_keeps_terminal_and_replay_only_releases(self):
        release = self.stage.custody.release
        self.stage.custody.release = lambda *a: (_ for _ in ()).throw(OSError("lost release ack"))
        result = self.service.apply(self.plan, approval_digest=self.approval.approval_digest)
        self.assertEqual(result["code"], "custody_pending")
        self.assertIsNone(self.host.state["active"])
        self.stage.custody.release = release
        self.observer.observe = lambda **kw: self.fail("release replay reobserved")
        self.assertTrue(self.service.apply(self.plan,
            approval_digest=self.approval.approval_digest)["ok"])
        self.assertEqual(self.stage.custody.released, 1)

"""Root-run retained target-preparation definitions; no host claim is made.

The shared fixture exercises genuine gate, broker and ledger admission. Its
registered source/resolver metadata remain synthetic ports, not installed proof.
"""
import json
import time
from types import SimpleNamespace
import unittest

from tests import test_host_credentials_fixed_consumers as fixtures
from sandbox.hosting.shared_credentials.admission import (
    AdmissionError, CredentialAdmissionService, prepare_target, require_target_preparation,
)
from sandbox.hosting.shared_credentials.broker import _compose_registered_broker
from sandbox.hosting.shared_credentials.models import RequestScope, parse_operation
from sandbox.hosting.shared_credentials.profiles import registered_profiles
from sandbox.jobs.credential_operations import LedgerError


class TargetPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FixedManifestResolverTests("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        original = self.fixture.intent
        mapping = original.mappings[0].as_dict()
        profiles = registered_profiles({"creation-profile": dict(source=original.source_ref,
            action="stack_up", environment=original.environment, sourceCommit=original.source_commit,
            artifactDigest=original.artifact_digest, manifestDigest=original.manifest_digest,
            mappings=[mapping])})
        self.body = original.as_dict()
        self.body.pop("principal_id")
        self.body.pop("target_ref")
        self.body.pop("retry_of")
        self.body.update(action="stack_up", request_id="prepare:original", name="fixture",
                         profile_ref="creation-profile", profile_revision=profiles["creation-profile"].revision,
                         requested_deadline=time.time() + 30)
        def snapshot(source, keys, **kwargs):
            return {"names": tuple((key, "present") for key in keys), "registration": "fixture-registration"}
        self.broker = _compose_registered_broker(SimpleNamespace(hosting_profiles=profiles,
            _hosting_snapshot=snapshot), self.fixture.actor)
        self.ledger = self.fixture.receipt._issuer._ledger

    def prepare(self, body=None):
        return prepare_target(self.ledger, self.fixture.actor,
            body=json.dumps(body or self.body).encode(), project_identity="fixture-identity", project="fixture")

    def admit(self, prepared):
        intent = parse_operation(json.dumps(prepared.operation_request()).encode(), self.fixture.actor)
        self.ledger.reserve(intent, project_identity="fixture-identity", effective_deadline=intent.requested_deadline)
        admission = CredentialAdmissionService(self.ledger, self.broker, revalidate=self.fixture.resolver.resolve)
        receipt = admission.commit(self.fixture.actor, intent, self.broker.preflight(intent))
        return receipt, intent

    def expected(self, prepared):
        return dict(name=prepared.name, project=prepared.project, environment=prepared.environment,
                    copy_id=prepared.copy_id, incarnation=prepared.incarnation, source_commit=prepared.source_commit)

    def test_lost_prepare_reply_replays_same_metadata_without_operation_or_claim(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual((first.copy_id, first.incarnation, first.canonical_intent),
                         (second.copy_id, second.incarnation, second.canonical_intent))
        self.assertIsNone(first.operation_id)
        self.assertIsNone(self.ledger.lookup(first.scope))
        self.assertNotIn("principal_id", first.operation_request())

    def test_changed_source_or_deadline_cannot_replace_original_preparation(self):
        original = self.prepare()
        for changes in ({"source_commit": "d" * 40}, {"requested_deadline": self.body["requested_deadline"] + 1}):
            with self.subTest(changes=changes), self.assertRaises(LedgerError) as failure:
                self.prepare(dict(self.body, **changes))
            self.assertEqual(failure.exception.code, "credential_intent_conflict")
        self.assertEqual(self.ledger.target_preparation(original.scope).canonical_intent, original.canonical_intent)

    def test_only_live_committed_receipt_can_observe_claim_preparation_authority(self):
        prepared = self.prepare()
        receipt, intent = self.admit(prepared)
        evidence = require_target_preparation(receipt, **self.expected(prepared))
        self.assertEqual(evidence.intent_digest, intent.intent_digest())
        self.assertEqual(evidence.operation_id, self.ledger.lookup(prepared.scope).operation_id)
        with self.assertRaises(AdmissionError):
            require_target_preparation(evidence, **self.expected(prepared))
        changed = self.expected(prepared)
        changed["copy_id"] = "f" * 8 if prepared.copy_id != "f" * 8 else "e" * 8
        with self.assertRaises(AdmissionError):
            require_target_preparation(receipt, **changed)
        self.ledger.cancel(prepared.scope, evidence.operation_id)
        with self.assertRaises(AdmissionError):
            require_target_preparation(receipt, **self.expected(prepared))

    def test_client_cannot_provide_copy_or_incarnation_to_prepare(self):
        body = dict(self.body, target_ref=dict(target_id="fixture", copy_id="a" * 8, incarnation="i-" + "b" * 32))
        with self.assertRaises(AdmissionError):
            self.prepare(body)

    def test_lookup_and_retry_use_the_host_64_character_request_grammar(self):
        valid = "a:" + "b" * 62
        prepared = self.prepare(dict(self.body, request_id=valid))
        lookup = dict(schema_version=1, installation_id=prepared.scope.installation_id,
                      incarnation=prepared.scope.incarnation, project_ref=prepared.scope.project_ref, request_id=valid)
        self.assertEqual(RequestScope.parse_lookup(json.dumps(lookup).encode(), self.fixture.actor).request_id, valid)
        for invalid in (valid + "c", ":leading", "has/slash", "has+plus", "has\nnewline"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    RequestScope.parse_lookup(json.dumps(dict(lookup, request_id=invalid)).encode(), self.fixture.actor)
                request = prepared.operation_request()
                request["retry_of"] = invalid
                with self.assertRaises(ValueError):
                    parse_operation(json.dumps(request).encode(), self.fixture.actor)

"""Authority races at actual delivery/completion, with no production effects.

Gate receipts, broker proofs, retained admission and effect CAS are real source
paths. The secret source, registered consumer and host supervisor are isolated
fixtures. These tests establish no installed grant, native credential access,
provider compatibility or real host fence acceptance. Root runs them explicitly.
"""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp" / "wp-server"))
from tailnet_access import ControlGate
from sandbox.hosting.recovery.repository import RecoveryRepository
from sandbox.hosting.shared_credentials.admission import CredentialAdmissionService
from sandbox.hosting.shared_credentials.broker import _compose_registered_broker
from sandbox.hosting.shared_credentials.models import parse_operation
from sandbox.hosting.shared_credentials.profiles import registered_profiles
from sandbox.hosting.shared_credentials import service
from sandbox.jobs.credential_operations import OperationState
from sandbox.jobs.registry import JobRepository


class DeliveryAuthorityRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.revoked = False
        self.host_revoked = False
        self.revoke_during_resolution = False
        self.revoke_during_consumer = False
        self.revoke_host_during_resolution = False
        self.revoke_host_during_consumer = False
        self.consumer = SimpleNamespace(consume=Mock(side_effect=self.consume))
        self.manifest = ModuleType("sandbox.hosting.shared_credentials.manifest")
        self.manifest.require_consumer = lambda intent, *, consumer_id: self.consumer
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch.dict(sys.modules,
            {"sandbox.hosting.shared_credentials.manifest": self.manifest}))
        self.writer = object()
        self.expected = SimpleNamespace(canonical_target_key="a" * 64)
        self.stack.enter_context(patch.object(service, "_host_binding", return_value=self.expected))
        self.stack.enter_context(patch.object(service, "_require_host_writer", side_effect=self.host_current))
        gate = ControlGate("synthetic-test-token", None, None)
        _, actor = asyncio.run(gate.admit_request("Bearer synthetic-test-token", None))
        mapping = dict(kind="provider", source_key="PROVIDER_KEY", destination="API_KEY",
                       consumer_id="fixture-app", consumer_revision="consumer-r1", purpose="app")
        descriptor = dict(source="fixture-source", action="host_apply", environment="fixture",
                          sourceCommit="c" * 40, artifactDigest="d" * 64,
                          manifestDigest="e" * 64, mappings=[mapping])
        profiles = registered_profiles({"fixture-profile": descriptor})
        binding = dict(schema_version=1, installation_id="1" * 32, incarnation="2" * 32,
                       machine_identity="3" * 24, ownership_marker="4" * 24,
                       runtime_revision="5" * 24, policy_revision="admitted-owner-v1",
                       policy_digest="6" * 64, execution_capabilities={}, layout_kind="owner")
        body = dict(schema_version=1, request_id="fixture:request", action="host_apply",
                    project_ref="fixture-project", expected_installation=binding,
                    target_ref=dict(target_id="fixture-target", copy_id="fixture-copy",
                                    incarnation="i-" + "7" * 32),
                    source_commit=descriptor["sourceCommit"], artifact_digest=descriptor["artifactDigest"],
                    manifest_digest=descriptor["manifestDigest"], environment="fixture",
                    source_ref="fixture-source", source_revision="source-r1",
                    profile_ref="fixture-profile", profile_revision=profiles["fixture-profile"].revision,
                    mappings=[mapping], generate_requested=False, requested_deadline=time.time() + 30)
        self.intent = parse_operation(json.dumps(body).encode(), actor)
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="sandbox-delivery-race-"))
        root = Path(temporary)
        self.jobs = JobRepository(root / "jobs.sqlite")
        self.ledger = self.jobs.credential_operations()
        self.target = RecoveryRepository(root / "hosts.json", root / "locks")
        secrets = SimpleNamespace(hosting_profiles=profiles, _hosting_snapshot=self.snapshot)
        self.broker = _compose_registered_broker(secrets, actor)
        reserved = self.ledger.reserve(self.intent, project_identity="fixture-project-identity",
                                       effective_deadline=self.intent.requested_deadline)
        self.operation_id = reserved.operation_id
        admission = CredentialAdmissionService(self.ledger, self.broker, revalidate=self.current)
        self.receipt = admission.commit(actor, self.intent, self.broker.preflight(self.intent))
        self.grant = self.broker.issue(self.receipt, self.intent)

    def current(self, intent):
        if self.revoked:
            raise ValueError("synthetic resolver authority revoked")

    def host_current(self, proof, expected):
        self.assertIs(proof, self.writer)
        self.assertIs(expected, self.expected)
        if self.host_revoked:
            raise service.CredentialExecutionError()

    def snapshot(self, source, keys, *, consumer=None, **kwargs):
        result = dict(names=tuple((key, "present") for key in keys), registration="fixture-registration")
        if consumer is not None:
            if self.revoke_during_resolution:
                self.revoked = True
            if self.revoke_host_during_resolution:
                self.host_revoked = True
            result["code"] = consumer({key: "synthetic-provider-value" for key in keys})
        return result

    def consume(self, slots):
        self.assertEqual(slots, {"API_KEY": "synthetic-provider-value"})
        if self.revoke_during_consumer:
            self.revoked = True
        if self.revoke_host_during_consumer:
            self.host_revoked = True
        return "succeeded"

    def exercise(self):
        with self.assertRaises(service.CredentialExecutionError) as failure:
            with service.held_credential_execution(self.receipt, intent=self.intent,
                    held_writer=self.writer, canonical_target_key="a" * 64,
                    target_repository=self.target):
                self.broker.consume(self.grant, "fixture-app", "deliver-provider")
        self.assertEqual(failure.exception.code, "credential_effects_unknown")
        row = self.ledger.lookup(self.intent.request_scope(), operation_id=self.operation_id)
        self.assertIs(row.state, OperationState.EFFECTS_UNKNOWN)
        effects = self.ledger.effects(self.intent.request_scope(), self.operation_id)
        self.assertEqual([item.outcome for item in effects], ["unknown"])

    def test_resolver_revoked_while_secret_resolution_waits_prevents_delivery(self):
        self.revoke_during_resolution = True
        self.exercise()
        self.consumer.consume.assert_not_called()

    def test_host_revoked_while_secret_resolution_waits_prevents_delivery(self):
        self.revoke_host_during_resolution = True
        self.exercise()
        self.consumer.consume.assert_not_called()

    def test_resolver_revoked_during_consumer_cannot_commit_success(self):
        self.revoke_during_consumer = True
        self.exercise()
        self.consumer.consume.assert_called_once()

    def test_host_revoked_during_consumer_cannot_commit_success(self):
        self.revoke_host_during_consumer = True
        self.exercise()
        self.consumer.consume.assert_called_once()

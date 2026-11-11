"""Root-run definitions for the actual fixed registry and DNS consumer.

No test calls a provider. Gate, private broker proof and retained credential
admission are actual code. Source values and registered metadata are fixture
ports; their use establishes no installed/artifact/recipient acceptance.
"""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp" / "wp-server"))
from tailnet_access import ControlGate
from sandbox.core import _cloudflare
from sandbox.hosting.shared_credentials.admission import AdmissionError, CredentialAdmissionService
from sandbox.hosting.shared_credentials.broker import _compose_registered_broker
from sandbox.hosting.shared_credentials.consumers import (
    CloudflareRecordAction, CloudflareRecordConsumer, ConsumerError,
)
from sandbox.hosting.shared_credentials.manifest import _bind_fixed_consumers, require_consumer
from sandbox.hosting.shared_credentials.models import parse_operation
from sandbox.hosting.shared_credentials.profiles import registered_profiles
from sandbox.hosting.shared_credentials.resolver import (
    RegisteredCredentialResolver, ResolvedCredentialOperation, ResolutionError,
)
from sandbox.jobs.registry import JobRepository


class FixedDNSConsumerTests(unittest.TestCase):
    def action(self, **overrides):
        return CloudflareRecordAction(**dict(dict(operation="create_address", zone_id="a" * 32,
            hostname="fixture.example.test", address="192.0.2.15", proxied=False, record_id=None),
            **overrides))

    def test_typed_zone_record_hostname_address_and_boolean(self):
        for values in ({"zone_id": "../wrong"}, {"hostname": "example.test/path"},
                       {"address": 1}, {"address": "fe80::1%eth0"}, {"address": "invalid"},
                       {"proxied": 1}, {"record_id": "b" * 32},
                       {"operation": "update_address"}, {"operation": "arbitrary_http"}):
            with self.subTest(values=values), self.assertRaises(ConsumerError):
                self.action(**values)

    def test_explicit_token_one_exact_request_and_no_ambient_fallback(self):
        consumer = CloudflareRecordConsumer("f" * 64, self.action())
        with patch.object(_cloudflare, "cloudflare_token", side_effect=AssertionError("ambient lookup")) as fallback:
            with patch.object(_cloudflare.Client, "_request", return_value={"success": True}) as request:
                self.assertEqual(consumer.consume({"CLOUDFLARE_API_TOKEN": "synthetic-token"}), "succeeded")
                request.assert_called_once_with("POST", "/zones/" + "a" * 32 + "/dns_records",
                    {"type": "A", "name": "fixture.example.test", "content": "192.0.2.15",
                     "proxied": False, "ttl": _cloudflare.DNS_ONLY_TTL_SECONDS})
            for slots in ({}, {"CLOUDFLARE_API_TOKEN": ""}, {"CLOUDFLARE_API_TOKEN": " "},
                          {"CLOUDFLARE_API_TOKEN": "fixture", "OTHER": "fixture"}):
                with patch.object(_cloudflare.Client, "_request") as request:
                    self.assertEqual(consumer.consume(slots), "failed")
                    request.assert_not_called()
            fallback.assert_not_called()

    def test_update_has_exact_record_path_and_ipv6_type(self):
        consumer = CloudflareRecordConsumer("f" * 64, self.action(operation="update_address",
            record_id="b" * 32, address="2001:db8::15", proxied=True))
        with patch.object(_cloudflare.Client, "_request", return_value={}) as request:
            self.assertEqual(consumer.consume({"CLOUDFLARE_API_TOKEN": "synthetic-token"}), "succeeded")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[:2],
                ("PATCH", "/zones/" + "a" * 32 + "/dns_records/" + "b" * 32))
            self.assertEqual(request.call_args.args[2]["type"], "AAAA")

    def test_provider_failure_is_unknown_without_body_or_output(self):
        consumer = CloudflareRecordConsumer("f" * 64, self.action())
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(_cloudflare.Client, "_request", side_effect=RuntimeError("synthetic-private-provider-body")):
            with redirect_stdout(output), redirect_stderr(errors):
                result = consumer.consume({"CLOUDFLARE_API_TOKEN": "synthetic-token"})
        self.assertEqual(result, "effects_unknown")
        self.assertEqual(output.getvalue() + errors.getvalue(), "")
        self.assertNotIn("synthetic", result)


class FixedManifestResolverTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="sandbox-fixed-consumer-"))
        self.home = Path(self.directory)
        mapping = dict(kind="provider", source_key="CF_SOURCE", destination="CLOUDFLARE_API_TOKEN",
            consumer_id="cloudflare-dns-address", consumer_revision="cloudflare-dns-address-v1", purpose="dns-address")
        profiles = registered_profiles({"dns-profile": dict(source="fixture-source", action="host_apply",
            environment="fixture", sourceCommit="a" * 40, artifactDigest="b" * 64,
            manifestDigest="c" * 64, mappings=[mapping])})
        binding = dict(schema_version=1, installation_id="1" * 32, incarnation="2" * 32,
            machine_identity="3" * 24, ownership_marker="4" * 24, runtime_revision="5" * 24,
            policy_revision="admitted-owner-v1", policy_digest="6" * 64, execution_capabilities={}, layout_kind="owner")
        _, self.actor = asyncio.run(ControlGate("fixture-token", None, None).admit_request("Bearer fixture-token", None))
        body = dict(schema_version=1, request_id="fixed:consumer", action="host_apply",
            project_ref="fixture-project", expected_installation=binding,
            target_ref=dict(target_id="fixture-target", copy_id="fixture-copy", incarnation="i-" + "7" * 32),
            source_commit="a" * 40, artifact_digest="b" * 64, manifest_digest="c" * 64,
            environment="fixture", source_ref="fixture-source", source_revision="source-r1",
            profile_ref="dns-profile", profile_revision=profiles["dns-profile"].revision,
            mappings=[mapping], generate_requested=False, requested_deadline=time.time() + 30)
        self.intent = parse_operation(json.dumps(body).encode(), self.actor)
        self.consumer = CloudflareRecordConsumer(self.intent.intent_digest(), CloudflareRecordAction(
            "create_address", "d" * 32, "fixture.example.test", "192.0.2.10", False))
        self.resolver = RegisteredCredentialResolver(home=self.home)
        self.current_consumers = (self.consumer,)
        # Only the registered metadata I/O port is replaced. The production
        # validate_consumer implementation performs its actual exact comparison.
        self.enterContext(patch.object(self.resolver, "_resolve", side_effect=self.resolved))
        def snapshot(source, keys, **kwargs):
            return {"names": tuple((key, "present") for key in keys), "registration": "fixture-registration"}
        secrets = SimpleNamespace(hosting_profiles=profiles, _hosting_snapshot=snapshot)
        self.broker = _compose_registered_broker(secrets, self.actor)
        ledger = JobRepository(self.home / "jobs.sqlite").credential_operations()
        ledger.reserve(self.intent, project_identity="fixture-identity", effective_deadline=self.intent.requested_deadline)
        self.admission = CredentialAdmissionService(ledger, self.broker,
            revalidate=self.resolver.resolve, validate_consumer=self.resolver.validate_consumer)
        self.receipt = self.admission.commit(self.actor, self.intent, self.broker.preflight(self.intent))

    def resolved(self, intent):
        return ResolvedCredentialOperation("fixture-identity", "e" * 64, "fixture", "fixture",
            intent.target_ref, (), "f" * 64, self.home, {}, self.current_consumers)

    def test_real_private_registry_requires_real_committed_receipt_and_resolver(self):
        with self.assertRaises(ConsumerError):
            require_consumer(self.intent, consumer_id="cloudflare-dns-address")
        with _bind_fixed_consumers(self.receipt, intent=self.intent, consumers=(self.consumer,)):
            self.assertIs(require_consumer(self.intent, consumer_id="cloudflare-dns-address"), self.consumer)
        with self.assertRaises(ConsumerError):
            require_consumer(self.intent, consumer_id="cloudflare-dns-address")

    def test_registered_action_change_refuses_original_consumer(self):
        with _bind_fixed_consumers(self.receipt, intent=self.intent, consumers=(self.consumer,)):
            changed = CloudflareRecordConsumer(self.intent.intent_digest(), CloudflareRecordAction(
                "create_address", "d" * 32, "different.example.test", "192.0.2.10", False))
            self.current_consumers = (changed,)
            with self.assertRaises(AdmissionError) as failure:
                require_consumer(self.intent, consumer_id="cloudflare-dns-address")
            self.assertEqual(failure.exception.code, "credential_consumer_binding_changed")

    def test_unregistered_callback_cannot_be_bound(self):
        with self.assertRaises(ConsumerError):
            with _bind_fixed_consumers(self.receipt, intent=self.intent,
                                      consumers=(SimpleNamespace(consume=lambda slots: "succeeded"),)):
                self.fail("caller callback was accepted")

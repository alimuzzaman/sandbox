"""Focused route-contract and bounded observer coverage for Feature 054."""

from __future__ import annotations

from io import BytesIO
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from sandbox.config.delivery import (
    DeliveryRouteError,
    normalize_delivery,
    wordpress_delivery_contract,
)
from sandbox.delivery.routes import observe_routes, prepare_route_verification


class _FakeProcess:
    def __init__(self, output=b"", *, timeout=False):
        self.args = ["route_worker.py"]
        self.returncode = 0
        self.stdin = BytesIO()
        self.stdout = BytesIO()
        self._output = output
        self._timeout = timeout
        self.killed = False
        self.waited = False

    def communicate(self, _payload, timeout):
        if self._timeout:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self._output, b""

    def poll(self):
        return None if self._timeout and not self.killed else self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


def _prepared(hostname="default-templately-ai-builder.sandbox.asb.bd", timeout=10):
    return prepare_route_verification(
        None,
        runtime_kind="wordpress",
        primary_hostname=hostname,
        aliases=(),
        verify_timeout=timeout,
    )


def _worker_hosts(hostname, *, dns_result="passed", check=None):
    host = {
        "hostname": hostname,
        "dns": {"result": dns_result, "observed_at": "2026-09-09T06:35:25.839543Z"},
        "checks": [] if check is None else [check],
        "release_identity": {"state": "unsupported"},
    }
    if dns_result == "passed":
        host["dns"].update({
            "address_count": 1,
            "address_digest": "sha256:" + "a" * 64,
        })
    else:
        host["dns"]["reason"] = "dns_unavailable"
    return {"hosts": [host]}


class TestDeliveryRouteContract(unittest.TestCase):
    def test_wordpress_default_contract_is_closed_and_public(self):
        contract = wordpress_delivery_contract()

        self.assertEqual(contract["schemaVersion"], 1)
        self.assertEqual(contract["routes"]["checks"], [{
            "path": "/wp-json/",
            "statuses": [200],
            "markers": [
                {"kind": "json_pointer_equals", "field": "/url", "expected": "$primary_origin"},
                {"kind": "json_array_contains", "field": "/namespaces", "expected": "wp/v2"},
            ],
        }])
        self.assertEqual(contract["routes"]["releaseIdentity"], {"required": False})
        self.assertEqual(contract["routes"]["edgeProof"], {"required": False})

    def test_prepare_freezes_timeout_hosts_and_digest_without_mutating_input(self):
        raw = {"schemaVersion": 1, "routes": {"checks": [{
            "path": "/health", "statuses": [200],
            "markers": [{"kind": "header_equals", "field": "X-Application", "expected": "demo"}],
        }]}}
        before = copy.deepcopy(raw)

        prepared = prepare_route_verification(
            raw,
            runtime_kind="compose",
            primary_hostname="example.asb.bd",
            aliases=["alias.asb.bd"],
            verify_timeout=10,
        )

        self.assertEqual(raw, before)
        self.assertEqual(prepared["hostnames"], ["example.asb.bd", "alias.asb.bd"])
        self.assertEqual(prepared["delivery"]["routes"]["deadlineSeconds"], 10)
        self.assertTrue(prepared["contract_digest"].startswith("sha256:"))
        without_digest = dict(prepared)
        digest = without_digest.pop("contract_digest")
        self.assertEqual(digest, prepare_route_verification(
            without_digest["delivery"], runtime_kind="compose",
            primary_hostname="example.asb.bd", aliases=["alias.asb.bd"], verify_timeout=10,
        )["contract_digest"])

    def test_invalid_contracts_refuse_before_observation(self):
        with self.assertRaises(DeliveryRouteError) as generic:
            prepare_route_verification(None, runtime_kind="compose", primary_hostname="example.asb.bd")
        self.assertEqual(generic.exception.code, "delivery_route_contract_required")

        with self.assertRaises(DeliveryRouteError) as timeout:
            prepare_route_verification(None, runtime_kind="wordpress", primary_hostname="example.asb.bd", verify_timeout=9)
        self.assertEqual(timeout.exception.code, "delivery_route_timeout_invalid")

        invalid = {"schemaVersion": 1, "routes": {"checks": [{
            "path": "/health", "statuses": [200],
            "markers": [{"kind": "header_equals", "field": "X-Application", "expected": "demo"}],
            "unexpected": True,
        }]}}
        with self.assertRaises(DeliveryRouteError):
            normalize_delivery(invalid)

    def test_observer_maps_http_upgrade_failure_to_failed(self):
        prepared = _prepared()
        check = {
            "path": "/wp-json/", "result": "failed", "attempts": 1,
            "started_at": "2026-09-09T06:35:25.839560Z",
            "finished_at": "2026-09-09T06:35:26.290706Z",
            "reason": "http_https_upgrade_missing",
        }
        process = _FakeProcess(json.dumps(_worker_hosts(prepared["primary_hostname"], check=check)).encode())
        with patch("sandbox.delivery.routes.subprocess.Popen", return_value=process):
            observed = observe_routes(prepared)

        self.assertEqual(observed["result"], "failed")
        self.assertEqual(observed["hosts"][0]["dns"]["result"], "passed")
        self.assertEqual(observed["hosts"][0]["checks"][0]["reason"], "http_https_upgrade_missing")
        self.assertEqual(observed["scope"], "application_availability_only")
        self.assertEqual(observed["release_identity_state"], "unsupported")
        self.assertTrue(process.waited)

    def test_observer_maps_missing_dns_to_incomplete(self):
        prepared = _prepared("default-templately-ai-builder.sandbox.invalid")
        process = _FakeProcess(json.dumps(_worker_hosts(
            prepared["primary_hostname"], dns_result="incomplete",
        )).encode())
        with patch("sandbox.delivery.routes.subprocess.Popen", return_value=process):
            observed = observe_routes(prepared)

        self.assertEqual(observed["result"], "incomplete")
        self.assertEqual(observed["hosts"][0]["dns"]["reason"], "dns_unavailable")
        self.assertEqual(observed["hosts"][0]["checks"], [])

    def test_observer_reaps_worker_and_reports_deadline(self):
        prepared = _prepared()
        process = _FakeProcess(timeout=True)
        with patch("sandbox.delivery.routes.subprocess.Popen", return_value=process):
            observed = observe_routes(prepared)

        self.assertEqual(observed["result"], "incomplete")
        self.assertEqual(observed["reason"], "deadline_exceeded")
        self.assertEqual(observed["deadline_seconds"], 10)
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    def test_tampered_prepared_digest_is_rejected(self):
        prepared = _prepared()
        prepared["hostnames"] = ["other.asb.bd"]

        with self.assertRaises(DeliveryRouteError):
            observe_routes(prepared)


if __name__ == "__main__":
    unittest.main()

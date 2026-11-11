"""Spec 084: stack targets reach their delivery records through the hosting record's scope."""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.commands import delivery as delivery_cmd
from sandbox.delivery import hosting as delivery
from sandbox.delivery.models import DeliveryError, request_scope
from sandbox.delivery.repository import DeliveryRepository
from sandbox.delivery.service import DeliveryService
from sandbox.hosting import target_resolution
from tests.test_delivery_models import make_operation, make_target

OLD_ROOT = "sha256:" + "1" * 64


def _recorded(request_id="stack-attempt", **extra):
    return {"request_id": request_id, "project_identity": "fixture-project",
            "project_root_digest": OLD_ROOT,
            "target": {"remote": "fixture-remote", "project": "site", "environment": "qa"},
            **extra}


class RetireThroughRecordedScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.repository = DeliveryRepository(self.home)
        # The attempt was admitted from a checkout a later `stack up` replaced.
        operation = make_operation("stack-attempt")
        self.repository.reserve_request(
            request_scope(make_target()), operation["request_id"], operation["operation_id"],
            operation["intent_digest"], operation=operation)
        self.current = {**make_target(), "project_identity": "fixture-project-new-checkout",
                        "project_root_digest": "sha256:" + "3" * 64}
        self.validated = {"project": "site", "project_root": "/fixture/new", "environment": "qa"}
        for target, value in ((delivery, "RUNTIME_DIR"), ):
            self.enterContext(patch.object(target, value, self.home / "runtime"))
        self.enterContext(patch.object(delivery, "target_for_project", return_value=self.current))

    def retire(self, recorded):
        return delivery.retire_interrupted_operation(
            self.validated, "fixture-remote", "stack-attempt",
            job_lookup=lambda _job: {"lifecycle": "failed"},
            recorded_operation=(lambda: recorded) if recorded is not None else None)

    def test_the_recorded_scope_reaches_the_attempt(self):
        summary = self.retire(_recorded())
        self.assertEqual(summary["request_id"], "stack-attempt")
        self.assertEqual(summary["execution_state"], "interrupted")

    def test_without_the_record_the_attempt_is_not_found(self):
        with self.assertRaises(DeliveryError) as caught:
            self.retire(None)
        self.assertEqual(caught.exception.code, "required_evidence_missing")

    def test_a_record_naming_another_request_is_not_used(self):
        with self.assertRaises(DeliveryError) as caught:
            self.retire(_recorded("another-attempt"))
        self.assertEqual(caught.exception.code, "required_evidence_missing")

    def test_a_record_bound_to_another_root_or_target_refuses(self):
        for recorded in (_recorded(project_root_digest="sha256:" + "9" * 64),
                         _recorded(target={"remote": "other-remote", "environment": "qa"})):
            with self.subTest(recorded=recorded), self.assertRaises(DeliveryError) as caught:
                self.retire(recorded)
            self.assertEqual(caught.exception.code, "binding_mismatch")


class InspectThroughRecordedScopeTests(unittest.TestCase):
    def test_the_selectors_carry_the_recorded_scope(self):
        resolved = target_resolution.ResolvedTarget(
            remote="fixture-remote", project="site", environment="qa", kind="stack",
            project_dir="/sandbox/runtime/stacks/site", slug="site")
        state = {"hosts": {resolved.state_key: {"hosting_operation": _recorded()}}}
        args = types.SimpleNamespace(remote="fixture-remote", name="site", target=None,
                                     project_dir=None, environment=None, label=None)
        with patch.object(target_resolution, "resolve", return_value=resolved), \
                patch("sandbox.core._hosting.load_host_state", return_value=state):
            selectors = delivery_cmd._recorded_target_selectors(args)
        self.assertEqual(selectors["project_dir"], "/sandbox/runtime/stacks/site")
        self.assertEqual(selectors["environment"], "qa")
        self.assertEqual(selectors["recorded_scope"],
                         {"project_identity": "fixture-project", "project_root_digest": OLD_ROOT})

    def test_a_record_of_another_project_gives_no_recorded_scope(self):
        resolved = target_resolution.ResolvedTarget(
            remote="fixture-remote", project="site", environment="qa", kind="stack",
            project_dir="/sandbox/runtime/stacks/site", slug="site")
        other = _recorded(target={"remote": "fixture-remote", "project": "other", "environment": "qa"})
        state = {"hosts": {resolved.state_key: {"hosting_operation": other}}}
        args = types.SimpleNamespace(remote="fixture-remote", name="site", target=None,
                                     project_dir=None, environment=None, label=None)
        with patch.object(target_resolution, "resolve", return_value=resolved), \
                patch("sandbox.core._hosting.load_host_state", return_value=state):
            self.assertNotIn("recorded_scope", delivery_cmd._recorded_target_selectors(args))

    def test_an_unknown_stack_refuses_with_the_resolution_code(self):
        args = types.SimpleNamespace(remote="fixture-remote", name="missing", target=None,
                                     project_dir=None, environment=None, label=None)
        with patch.object(target_resolution, "resolve", side_effect=target_resolution.TargetRefusal(
                target_resolution.TARGET_UNKNOWN, "no stack named 'missing'")):
            result = delivery_cmd._recorded_target_selectors(args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "host_target_unknown")

    def test_inspect_reads_the_recorded_scope(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = DeliveryRepository(Path(temporary.name))
        operation = make_operation("stack-attempt")
        repository.reserve_request(request_scope(make_target()), operation["request_id"],
                                   operation["operation_id"], operation["intent_digest"],
                                   operation=operation)
        seen = {}

        def resolver(*, project_dir, remote, environment=None, label=None, recorded_scope=None):
            seen["recorded_scope"] = recorded_scope
            target = {**make_target(), "project_identity": "fixture-project-new-checkout",
                      "project_root_digest": "sha256:" + "3" * 64}
            if recorded_scope is not None:
                target.update(recorded_scope)
            return target

        service = DeliveryService(repository, target_resolver=resolver)
        missing = service.inspect(project_dir="/stack", remote="fixture-remote", environment="qa",
                                  request_id="stack-attempt")
        self.assertIsNone(missing["selected_operation"])
        found = service.inspect(project_dir="/stack", remote="fixture-remote", environment="qa",
                                request_id="stack-attempt",
                                recorded_scope={"project_identity": "fixture-project",
                                                "project_root_digest": OLD_ROOT})
        self.assertEqual(found["selected_operation"]["request_id"], "stack-attempt")
        with self.assertRaises(DeliveryError):
            service.inspect(project_dir="/stack", remote="fixture-remote", environment="qa",
                            recorded_scope={"project_identity": "fixture-project"})

        def other_runtime(**query):
            return {**resolver(**query), "runtime_identity": "another-hosted-project"}

        confined = DeliveryService(repository, target_resolver=other_runtime)
        for selectors in ({"request_id": "stack-attempt"}, {}):
            with self.subTest(selectors=selectors), self.assertRaises(DeliveryError) as caught:
                confined.inspect(project_dir="/stack", remote="fixture-remote", environment="qa",
                                 recorded_scope={"project_identity": "fixture-project",
                                                 "project_root_digest": OLD_ROOT}, **selectors)
            self.assertEqual(caught.exception.code, "binding_mismatch")


if __name__ == "__main__":
    unittest.main()

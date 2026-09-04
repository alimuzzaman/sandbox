from __future__ import annotations
import unittest
from sandbox.resources.host_memory.remote import (
    ACTIONS, HostMemoryRemote, RemoteProtocolError, validate_request, validate_response,
)
from tests.host_memory_fixtures import MARKER, REVISION, status_state
from tests.host_memory_assertions import assert_privacy_bounded


class HostMemoryRemoteTest(unittest.TestCase):
    def test_exact_wire_allowlist_and_no_plan_action(self):
        self.assertEqual(ACTIONS,{"host_memory_status","host_memory_history","host_memory_apply"})
        for action in ("host_memory_plan","status","shell"):
            with self.assertRaises(RemoteProtocolError): validate_request({"action":action,"remote_name":"r"})
    def test_unknown_paths_argv_and_top_level_size_refuse(self):
        for key in ("path","argv","shell","size_gib"):
            with self.assertRaises(RemoteProtocolError): validate_request({"action":"host_memory_status","remote_name":"r",key:"x"})
    def test_revision_marker_schema_and_bounds_are_authorizing(self):
        response={"resource_schema":1,"host_memory_schema":1,"transport":"control","service":{"ownership_marker":MARKER,"runtime_revision":REVISION},"result":status_state()}
        self.assertEqual(validate_response(response,marker=MARKER,revision=REVISION,
                                           action="host_memory_status")["evidence_state"],"known")
        for marker,revision in (("c"*24,REVISION),(MARKER,"d"*24)):
            with self.assertRaises(RemoteProtocolError): validate_response(
                response,marker=marker,revision=revision,action="host_memory_status")
    def test_apply_requires_confirmation_and_canonical_schema(self):
        base={"action":"host_memory_apply","remote_name":"r","operation_id":"a"*64,"plan":{},"confirmed":False,"budget_seconds":1}
        with self.assertRaises(RemoteProtocolError): validate_request(base)

    def test_response_rejects_raw_output_unknown_envelope_and_oversize(self):
        base={"resource_schema":1,"host_memory_schema":1,"transport":"control",
              "service":{"ownership_marker":MARKER,"runtime_revision":REVISION},
              "result":status_state()}
        assert_privacy_bounded(self, validate_response(
            base,marker=MARKER,revision=REVISION,action="host_memory_status"))
        with self.assertRaises(RemoteProtocolError):
            validate_response({**base,"unknown":True},marker=MARKER,revision=REVISION,
                              action="host_memory_status")
        raw={**base,"result":{"stdout":"private"}}
        with self.assertRaises(RemoteProtocolError):
            validate_response(raw,marker=MARKER,revision=REVISION,action="host_memory_status")
        huge={**base,"result":{"safe":"x"*(1024*1024)}}
        with self.assertRaises(RemoteProtocolError):
            validate_response(huge,marker=MARKER,revision=REVISION,action="host_memory_status")

    def test_known_only_and_nested_unexpected_status_fields_are_rejected(self):
        envelope={"resource_schema":1,"host_memory_schema":1,"transport":"control",
                  "service":{"ownership_marker":MARKER,"runtime_revision":REVISION}}
        for result in (
            {"evidence_state":"known"},
            {**status_state(), "source_path":"/proc/meminfo"},
            status_state(memory={**status_state()["memory"], "secret":"x"}),
            status_state(monitor={**status_state()["monitor"],
                "retention":{**status_state()["monitor"]["retention"],
                             "current_files":"not-an-integer"}}),
        ):
            with self.subTest(result=result), self.assertRaises(RemoteProtocolError):
                validate_response({**envelope,"result":result},marker=MARKER,
                                  revision=REVISION,action="host_memory_status")

    def test_request_budget_and_ranges_are_strict(self):
        for payload in (
            {"action":"host_memory_status","remote_name":"r","budget_seconds":True},
            {"action":"host_memory_status","remote_name":"r","budget_seconds":0},
            {"action":"host_memory_history","remote_name":"r","since":"bad",
             "until":None,"limit":1,"budget_seconds":1},
        ):
            with self.assertRaises(RemoteProtocolError):
                validate_request(payload)

    def test_status_contract_has_no_mutation_inputs(self):
        payload = validate_request({"action":"host_memory_status", "remote_name":"r",
                                    "budget_seconds":15})
        self.assertEqual(set(payload), {"action", "remote_name", "budget_seconds"})

    def test_fractional_finite_budget_is_supported(self):
        payload = validate_request({"action":"host_memory_status", "remote_name":"r",
                                    "budget_seconds":1.5})
        self.assertEqual(payload["budget_seconds"], 1.5)

    def test_no_plan_action_and_no_shell_syntax_on_the_wire(self):
        for payload in (
            {"action":"host_memory_plan","remote_name":"r","budget_seconds":15},
            {"action":"host_memory_status;id","remote_name":"r","budget_seconds":15},
            {"action":"host_memory_status","remote_name":"r","budget_seconds":15,
             "argv":["id"]},
            {"action":"host_memory_status","remote_name":"r","budget_seconds":15,
             "path":"/tmp/x"},
        ):
            with self.assertRaises(RemoteProtocolError):
                validate_request(payload)

    def _apply_request(self, **overrides):
        plan = {"plan_id":"a"*64, "operation":"enable", "target_identity":"host",
                "service_ownership_marker":MARKER, "runtime_revision":REVISION,
                "expires_at":"2026-08-30T12:15:00Z", "observation_digest":"b"*64,
                "effective_policy":{"size_gib":4}, "intended_artifact_digests":[],
                "rollback_scope":[]}
        plan.update(overrides.pop("plan", {}))
        request = {"action":"host_memory_apply", "remote_name":"r",
                   "operation_id":"c"*64, "plan":plan, "confirmed":True,
                   "budget_seconds":15}
        request.update(overrides)
        return request

    def test_apply_rejects_non_canonical_effective_policy(self):
        for policy in ({"size_gib":0}, {"size_gib":9}, {"size_gib":"4"}, {}):
            with self.subTest(policy=policy), self.assertRaises(RemoteProtocolError):
                validate_request(self._apply_request(plan={"effective_policy":policy}))
        with self.assertRaises(RemoteProtocolError):
            validate_request(self._apply_request(size_gib=4))

    def test_apply_response_requires_normative_typed_result(self):
        envelope={"resource_schema":1,"host_memory_schema":1,"transport":"control",
                  "service":{"ownership_marker":MARKER,"runtime_revision":REVISION}}
        good = validate_response({**envelope,"result":{"status":"applied",
                                 "operation_id":"c"*64}},marker=MARKER,revision=REVISION,
                                 action="host_memory_apply")
        self.assertEqual(good["status"], "applied")
        for result in ({"status":"bogus"}, {"outcome":"applied"}, {}):
            with self.subTest(result=result), self.assertRaises(RemoteProtocolError):
                validate_response({**envelope,"result":result},marker=MARKER,
                                  revision=REVISION,action="host_memory_apply")

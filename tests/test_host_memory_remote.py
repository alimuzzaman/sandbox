from __future__ import annotations
import unittest
from sandbox.resources.host_memory.remote import (
    ACTIONS, HostMemoryRemote, RemoteProtocolError, validate_request, validate_response,
)
from tests.host_memory_fixtures import MARKER, REVISION


class HostMemoryRemoteTest(unittest.TestCase):
    def test_exact_wire_allowlist_and_no_plan_action(self):
        self.assertEqual(ACTIONS,{"host_memory_status","host_memory_history","host_memory_apply"})
        for action in ("host_memory_plan","status","shell"):
            with self.assertRaises(RemoteProtocolError): validate_request({"action":action,"remote_name":"r"})
    def test_unknown_paths_argv_and_top_level_size_refuse(self):
        for key in ("path","argv","shell","size_gib"):
            with self.assertRaises(RemoteProtocolError): validate_request({"action":"host_memory_status","remote_name":"r",key:"x"})
    def test_revision_marker_schema_and_bounds_are_authorizing(self):
        response={"resource_schema":1,"host_memory_schema":1,"transport":"control","service":{"ownership_marker":MARKER,"runtime_revision":REVISION},"result":{"evidence_state":"known"}}
        self.assertEqual(validate_response(response,marker=MARKER,revision=REVISION)["evidence_state"],"known")
        for marker,revision in (("c"*24,REVISION),(MARKER,"d"*24)):
            with self.assertRaises(RemoteProtocolError): validate_response(response,marker=marker,revision=revision)
    def test_apply_requires_confirmation_and_canonical_schema(self):
        base={"action":"host_memory_apply","remote_name":"r","operation_id":"a"*64,"plan":{},"confirmed":False,"budget_seconds":1}
        with self.assertRaises(RemoteProtocolError): validate_request(base)

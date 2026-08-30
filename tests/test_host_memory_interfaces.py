from __future__ import annotations
import ast, unittest
from pathlib import Path
from sandbox.resources.host_memory import HostMemoryStatusProjection

class HostMemoryInterfacesTest(unittest.TestCase):
    def test_public_package_exports_projection_only(self):
        import sandbox.resources.host_memory as package
        self.assertEqual(set(package.__all__),{"HostMemoryStatusProjection"})
        self.assertFalse(hasattr(package,"HostMemoryService"))
    def test_feature_047_has_no_mutation_import(self):
        for path in Path("sandbox").rglob("*.py"):
            if "governance" not in path.name and "host_governance" not in str(path): continue
            source=path.read_text()
            self.assertNotIn("host_memory.provider",source); self.assertNotIn("host_memory.repository",source)

    def test_public_projection_has_no_mutation_or_plan_fields(self):
        fields = set(HostMemoryStatusProjection.__dataclass_fields__)
        forbidden = {"plan_id", "confirmation", "provider", "repository", "apply",
                     "receipt", "artifacts", "rollback_scope"}
        self.assertFalse(fields & forbidden)

    def test_apply_is_not_registered_before_t047(self):
        source=Path("mcp/wp-server/server.py").read_text()
        dispatch=source.split("def _resource_contract",1)[1].split("def _host_memory_contract",1)[0]
        self.assertNotIn('"host_memory_apply"',dispatch)

    def test_status_server_joins_repository_monitor_evidence_only(self):
        source=Path("mcp/wp-server/server.py").read_text()
        dispatch=source.split("def _resource_contract",1)[1].split("def _host_memory_contract",1)[0]
        self.assertIn('{"host_memory_status"}',dispatch)
        self.assertNotIn("host_memory_history",dispatch)
        contract=source.split("def _host_memory_contract",1)[1].split("def _remote_wp_error",1)[0]
        self.assertIn("status_monitor_evidence",contract)
        self.assertIn("history_path=HISTORY",contract)
        self.assertIn('history_ancestor_root=Path("/")',contract)
        self.assertIn("provider.observe(deadline=deadline)",contract)
        self.assertIn("status_monitor_evidence(now=provider.now(),deadline=deadline)",contract)
        self.assertNotIn("provider.apply",contract)

    def test_context_projection_adapter_returns_no_service(self):
        from sandbox.resources import context
        status = {"target_identity":"host", "observed_at":"2026-08-30T12:00:00Z",
                  "evidence_state":"known", "memory":{"total_bytes":10,"available_bytes":8},
                  "swap_areas":[], "ownership":"absent",
                  "monitor":{"freshness":"missing", "pressure_state":"unknown"},
                  "operation_block":None}
        class Service:
            def status(self, budget_seconds): return {"ok":True, "data":status}
            def projection(self, value):
                return HostMemoryStatusProjection("host",value["observed_at"],"known",10,8,0,0,
                                                  "absent","missing",False,"unknown",None)
        from unittest.mock import patch
        with patch.object(context, "_build_host_memory_service", return_value=Service()):
            projection = context.host_memory_status_projection("fixture")
        self.assertIsInstance(projection, HostMemoryStatusProjection)
        self.assertFalse(hasattr(projection, "apply"))

    def test_context_and_service_hide_authority_dependencies(self):
        from sandbox.resources import context
        from sandbox.resources.host_memory.service import HostMemoryService
        self.assertFalse(hasattr(context,"host_memory_service"))
        service=HostMemoryService(object())
        self.assertFalse(hasattr(service,"remote"))
        self.assertFalse(hasattr(service,"repository"))

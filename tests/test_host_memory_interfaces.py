from __future__ import annotations
import ast, unittest
from pathlib import Path
from sandbox.resources.host_memory import HostMemoryStatusProjection

class HostMemoryInterfacesTest(unittest.TestCase):
    def test_public_package_exports_projection_and_service_only(self):
        import sandbox.resources.host_memory as package
        self.assertEqual(set(package.__all__),{"HostMemoryService","HostMemoryStatusProjection"})
    def test_feature_047_has_no_mutation_import(self):
        for path in Path("sandbox").rglob("*.py"):
            if "governance" not in path.name and "host_governance" not in str(path): continue
            source=path.read_text()
            self.assertNotIn("host_memory.provider",source); self.assertNotIn("host_memory.repository",source)

    def test_apply_is_not_registered_before_t047(self):
        source=Path("mcp/wp-server/server.py").read_text()
        dispatch=source.split("def _resource_contract",1)[1].split("def _host_memory_contract",1)[0]
        self.assertNotIn('"host_memory_apply"',dispatch)

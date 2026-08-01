from pathlib import Path
import tempfile
import unittest
from unittest import mock


class TestNativeContextDispatch(unittest.TestCase):
    def test_explicit_native_selection_never_calls_legacy_compose_operation(self):
        from sandbox.application.context import runtime_service
        from sandbox.runtimes.base import OperationRequest
        import sandbox_core as sc
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(sc, "sandbox_base", return_value=Path(directory)), \
                mock.patch.object(sc, "load_project_config", return_value={
                    "kind": "wordpress", "wordpressRuntime": {
                        "mode": "managed_native", "adapter": "ubuntu-nspawn",
                        "explicit": True,
                    },
                }), \
                mock.patch("sandbox.core.ensure_instance") as legacy:
            result = runtime_service({}).invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok); legacy.assert_not_called()
        self.assertIn(result.data["reason"]["code"], {
            "isolation_prerequisite_missing", "managed_runtime_unproven",
        })


if __name__ == "__main__": unittest.main()

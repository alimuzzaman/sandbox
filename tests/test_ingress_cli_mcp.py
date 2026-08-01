from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock


class Service:
    def detect(self): return {"ok": True, "operation": "ingress_detect", "state": "ready", "observations": [], "mutated": False}
    def support(self): return {"ok": True, "operation": "ingress_support", "state": "ready", "adapters": [], "mutated": False}


class TestIngressCliMcp(unittest.TestCase):
    def test_cli_read_only_ingress_actions_emit_one_json_object(self):
        from sandbox.commands.domains import cmd_domains
        for subaction in ("detect", "status", "support"):
            args = SimpleNamespace(action="ingress", tld=subaction, json=True,
                                   project_dir=None, label="default", resolver=None)
            output = io.StringIO()
            with mock.patch("sandbox.application.context.ingress_service", return_value=Service()), \
                    redirect_stdout(output):
                cmd_domains({}, args)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["mutated"])


if __name__ == "__main__": unittest.main()

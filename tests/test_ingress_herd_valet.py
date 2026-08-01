from __future__ import annotations

import subprocess
import unittest


class Process:
    def __init__(self, outputs=()): self.calls = []; self.outputs = iter(outputs)
    def run(self, argv, *, timeout):
        self.calls.append((tuple(argv), timeout))
        try: code, output = next(self.outputs)
        except StopIteration: code, output = 0, ""
        return subprocess.CompletedProcess(argv, code, output, "failed" if code else "")


class TestIngressHerdValet(unittest.TestCase):
    def plan(self, adapter, protocols=("http",)):
        return adapter.plan_route(
            {"protocols": protocols},
            {"hostname": "demo.test", "owner": "/tmp/project::default"},
            {"address": "127.0.0.1", "port": 8123},
        )

    def test_proxy_and_secure_use_documented_cli_only(self):
        from sandbox.ingress.adapters.herd_valet import HerdValetAdapter
        process = Process(); adapter = HerdValetAdapter(
            product="herd", executable="/usr/local/bin/herd", process=process,
        )
        result = adapter.activate(self.plan(adapter, ("http", "https")))
        self.assertTrue(result["ok"])
        self.assertEqual([call[0] for call in process.calls], [
            ("/usr/local/bin/herd", "proxy", "demo", "http://127.0.0.1:8123"),
            ("/usr/local/bin/herd", "secure", "demo"),
        ])

    def test_secure_failure_removes_new_proxy_and_never_touches_private_files(self):
        from sandbox.ingress.adapters.herd_valet import HerdValetAdapter
        process = Process(((0, ""), (1, ""), (0, "")))
        adapter = HerdValetAdapter(product="valet", executable="valet", process=process)
        result = adapter.activate(self.plan(adapter, ("http", "https")))
        self.assertFalse(result["ok"])
        self.assertEqual(process.calls[-1][0], ("valet", "unproxy", "demo"))

    def test_non_test_and_non_loopback_targets_fail_before_cli(self):
        from sandbox.ingress.adapters.herd_valet import HerdValetAdapter
        process = Process(); adapter = HerdValetAdapter(
            product="herd", executable="herd", process=process,
        )
        with self.assertRaisesRegex(ValueError, r"\.test"):
            adapter.plan_route({"protocols": ("http",)},
                {"hostname": "demo.example.com", "owner": "owner"},
                {"address": "127.0.0.1", "port": 8123})
        with self.assertRaisesRegex(ValueError, "loopback"):
            adapter.plan_route({"protocols": ("http",)},
                {"hostname": "demo.test", "owner": "owner"},
                {"address": "10.0.0.2", "port": 8123})
        self.assertEqual(process.calls, [])


if __name__ == "__main__": unittest.main()

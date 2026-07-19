"""Unit tests for the shared fan-out helper (docs/ci-e2e-runner-spec.md §4.2).

Stdlib `unittest` only, no docker — `ensure_instance` and `_teardown_instance`
are monkeypatched so this exercises pure concurrency/aggregation logic.
Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core as core  # noqa: E402
import sandbox.core._fanout as fanout  # noqa: E402


class TestRunAcrossInstances(unittest.TestCase):
    def setUp(self):
        # Fake ensure_instance: records calls, "boots" instantly, never touches
        # docker. Patched directly on the _fanout module's own namespace entry
        # (back-filled there at package-import time) — not on _instances,
        # which other tests may still exercise for real.
        self._orig_ensure = fanout.ensure_instance
        self._orig_teardown = fanout._teardown_instance
        self.teardown_calls = []
        self.provisioned = []

        def fake_ensure(cfg, root, label="default", create=False,
                        php_version=None, wp_version=None, config_label=None):
            self.provisioned.append(label)
            return {"instance": f"proj-{label}", "label": label, "status": "ready"}

        def fake_teardown(name):
            self.teardown_calls.append(name)

        fanout.ensure_instance = fake_ensure
        fanout._teardown_instance = fake_teardown

    def tearDown(self):
        fanout.ensure_instance = self._orig_ensure
        fanout._teardown_instance = self._orig_teardown

    def test_all_pass_and_teardown(self):
        specs = [{"label": f"e2e-w{i}"} for i in range(3)]
        result = core.run_across_instances(
            {}, "/tmp/proj", specs,
            worker_fn=lambda entry, spec: {"status": "passed"},
            concurrency=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["concurrency"], 2)
        self.assertEqual(len(result["units"]), 3)
        self.assertEqual(sorted(self.teardown_calls),
                         ["proj-e2e-w0", "proj-e2e-w1", "proj-e2e-w2"])
        self.assertEqual(sorted(self.provisioned),
                         ["e2e-w0", "e2e-w1", "e2e-w2"])

    def test_concurrency_cap_is_respected(self):
        # 4 units, cap 2 -> peak concurrent worker_fn calls must never exceed 2.
        specs = [{"label": f"ci-w{i}"} for i in range(4)]
        active = {"n": 0, "peak": 0}
        lock = threading.Lock()

        def worker(entry, spec):
            with lock:
                active["n"] += 1
                active["peak"] = max(active["peak"], active["n"])
            time.sleep(0.05)
            with lock:
                active["n"] -= 1
            return {"status": "passed"}

        result = core.run_across_instances({}, "/tmp/proj", specs, worker,
                                           concurrency=2)
        self.assertTrue(result["ok"])
        self.assertLessEqual(active["peak"], 2)

    def test_one_failure_does_not_abort_others(self):
        specs = [{"label": "e2e-w0"}, {"label": "e2e-w1"}, {"label": "e2e-w2"}]

        def worker(entry, spec):
            if spec["label"] == "e2e-w1":
                raise RuntimeError("boom")
            return {"status": "passed"}

        result = core.run_across_instances({}, "/tmp/proj", specs, worker,
                                           concurrency=3)
        self.assertFalse(result["ok"])
        by_label = {u["label"]: u for u in result["units"]}
        self.assertEqual(by_label["e2e-w0"]["status"], "passed")
        self.assertEqual(by_label["e2e-w1"]["status"], "failed")
        self.assertEqual(by_label["e2e-w2"]["status"], "passed")
        # All three still get torn down — keep_on_fail defaults to False.
        self.assertEqual(len(self.teardown_calls), 3)

    def test_keep_on_fail_preserves_failed_units_only(self):
        specs = [{"label": "e2e-w0"}, {"label": "e2e-w1"}]

        def worker(entry, spec):
            if spec["label"] == "e2e-w1":
                return {"status": "failed"}
            return {"status": "passed"}

        result = core.run_across_instances({}, "/tmp/proj", specs, worker,
                                           concurrency=2, keep_on_fail=True)
        self.assertFalse(result["ok"])
        # w0 passed -> torn down; w1 failed -> preserved (no teardown call).
        self.assertEqual(self.teardown_calls, ["proj-e2e-w0"])

    def test_provision_failure_is_recorded_not_fatal_by_default(self):
        def flaky_ensure(cfg, root, label="default", create=False,
                         php_version=None, wp_version=None, config_label=None):
            if label == "e2e-w1":
                raise RuntimeError("port exhausted")
            self.provisioned.append(label)
            return {"instance": f"proj-{label}", "label": label, "status": "ready"}
        fanout.ensure_instance = flaky_ensure

        specs = [{"label": "e2e-w0"}, {"label": "e2e-w1"}]
        result = core.run_across_instances({}, "/tmp/proj", specs,
                                           worker_fn=lambda e, s: {"status": "passed"},
                                           concurrency=2)
        self.assertFalse(result["ok"])
        by_label = {u["label"]: u for u in result["units"]}
        self.assertEqual(by_label["e2e-w0"]["status"], "passed")
        self.assertEqual(by_label["e2e-w1"]["status"], "provision_failed")
        # The healthy unit still ran and reported ok.
        self.assertEqual(by_label["e2e-w0"]["result"]["status"], "passed")

    def test_custom_runtime_provisioner_and_teardown_are_used(self):
        provisioned, torn_down = [], []

        def provision(spec):
            provisioned.append(spec["label"])
            return {"instance": f"compose-{spec['label']}", "label": spec["label"],
                    "kind": "compose", "http_port": 8173}

        def teardown(entry):
            torn_down.append(entry["instance"])

        result = core.run_across_instances({}, "/tmp/proj", [{"label": "ci-compose"}],
                                           worker_fn=lambda entry, spec: {"status": "passed"},
                                           concurrency=1, provision_instance=provision,
                                           teardown_instance=teardown)
        self.assertTrue(result["ok"])
        self.assertEqual(provisioned, ["ci-compose"])
        self.assertEqual(torn_down, ["compose-ci-compose"])
        self.assertEqual(self.provisioned, [])
        self.assertEqual(self.teardown_calls, [])

    def test_strict_provision_reraises(self):
        def flaky_ensure(cfg, root, label="default", create=False,
                         php_version=None, wp_version=None, config_label=None):
            raise RuntimeError("boom")
        fanout.ensure_instance = flaky_ensure

        specs = [{"label": "e2e-w0"}]
        result = core.run_across_instances({}, "/tmp/proj", specs,
                                           worker_fn=lambda e, s: {"status": "passed"},
                                           concurrency=1, strict_provision=True)
        # The exception is caught at the future-result boundary and recorded,
        # not propagated out of run_across_instances (one unit's failure never
        # crashes the whole call) — but it must still show as provision_failed.
        self.assertFalse(result["ok"])
        self.assertEqual(result["units"][0]["status"], "provision_failed")


if __name__ == "__main__":
    unittest.main()

"""Per-remote registration lock (spec 061, US5, FR-020..FR-022)."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._remote as sr  # noqa: E402
import sandbox.core._config as _cfgmod  # noqa: E402


class _Home:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        local = root / "sandbox.local.yml"
        self._patches = [
            patch.object(sr, "CONFIG_LOCAL", local),
            patch.object(_cfgmod, "CONFIG_LOCAL", local),
            patch.object(sr, "RUNTIME_DIR", root / "runtime"),
        ]

    def __enter__(self):
        for item in self._patches:
            item.__enter__()
        return self

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.__exit__(*exc)
        self._tmp.cleanup()


def _in_thread(function):
    result = {}

    def run():
        started = time.monotonic()
        try:
            result["value"] = function()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            result["error"] = exc
        result["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=run)
    worker.start()
    return worker, result


class RegistrationLockTests(unittest.TestCase):
    def test_lock_on_one_remote_does_not_block_another_or_list(self):
        with _Home():
            sr.put_remote("alpha", ssh="a@example.invalid")
            sr.put_remote("beta", ssh="b@example.invalid")
            with sr.registered_remote_lock("alpha"):
                worker, result = _in_thread(
                    lambda: sr.put_remote("beta", origin_ipv4="192.0.2.9"))
                worker.join(2)
                self.assertNotIn("error", result)
                self.assertLess(result["elapsed"], 1.0)
                self.assertIn("beta", sr.list_remotes())
                with sr.registered_remote_lock("beta", timeout_seconds=0.5):
                    pass
            self.assertEqual(sr.get_remote("beta")["origin_ipv4"], "192.0.2.9")

    def test_same_remote_waits_bounded_and_reports_holder(self):
        with _Home():
            sr.put_remote("alpha", ssh="a@example.invalid")
            with sr.registered_remote_lock("alpha"):
                worker, result = _in_thread(lambda: sr.put_remote(
                    "alpha", origin_ipv4="192.0.2.1", _lock_timeout=0.3))
                worker.join(3)
            error = result.get("error")
            self.assertIsInstance(error, TimeoutError)
            self.assertEqual(str(error), "remote_registration_busy")
            holder = getattr(error, "holder", None)
            self.assertIsInstance(holder, dict)
            self.assertEqual(holder["remote"], "alpha")
            self.assertIsInstance(holder["pid"], int)
            self.assertNotIn("origin_ipv4", sr.get_remote("alpha"))

    def test_same_remote_proceeds_after_release(self):
        with _Home():
            sr.put_remote("alpha", ssh="a@example.invalid")
            with sr.registered_remote_lock("alpha"):
                worker, result = _in_thread(
                    lambda: sr.put_remote("alpha", origin_ipv4="192.0.2.1"))
                time.sleep(0.1)
                self.assertNotIn("value", result)
            worker.join(2)
            self.assertNotIn("error", result)
            self.assertEqual(sr.get_remote("alpha")["origin_ipv4"], "192.0.2.1")

    def test_concurrent_changes_to_different_remotes_both_persist(self):
        with _Home():
            names = [f"r{i}" for i in range(8)]
            barrier = threading.Barrier(len(names))

            def register(name):
                barrier.wait()
                return sr.put_remote(name, ssh=f"{name}@example.invalid")

            workers = [_in_thread(lambda n=n: register(n)) for n in names]
            for worker, _ in workers:
                worker.join(5)
            for _, result in workers:
                self.assertNotIn("error", result)
            self.assertEqual(set(sr.list_remotes()), set(names))

    def test_unnamed_lock_still_excludes_every_remote(self):
        with _Home():
            sr.put_remote("alpha", ssh="a@example.invalid")
            with sr.registered_remote_lock():
                worker, result = _in_thread(lambda: sr.put_remote(
                    "alpha", origin_ipv4="192.0.2.1", _lock_timeout=0.2))
                worker.join(2)
            self.assertIsInstance(result.get("error"), TimeoutError)

    def test_named_lock_rejects_an_invalid_remote_name(self):
        with _Home():
            with self.assertRaises(ValueError):
                with sr.registered_remote_lock("../escape"):
                    self.fail("invalid name acquired a lock")


if __name__ == "__main__":
    unittest.main()

"""Strict-mode pins (spec 061 US2, FR-007..FR-014).

The remote pin program runs for real against a temporary "remote" Sandbox
home through a local ``sh -c`` stand-in for ``ssh_run``.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.remote_runtime import pins  # noqa: E402

REV_A = "a" * 24
REV_B = "b" * 24


class _LocalRemote:
    def __init__(self, home: Path):
        self.home = home
        self.calls = 0
        self.fail = False

    def __call__(self, _entry, command, timeout=30):
        self.calls += 1
        if self.fail:
            raise OSError("ssh: connect to host 192.0.2.1 token=secret")
        env = {"SANDBOX_HOME": str(self.home), "HOME": str(self.home),
               "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"}
        return subprocess.run(["sh", "-c", command], env=env, capture_output=True,
                              text=True, timeout=timeout)


class PinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote_home = root / "remote-home"
        self.remote_home.mkdir()
        self.checkout = root / "checkout"
        self.checkout.mkdir()
        self.remote = _LocalRemote(self.remote_home)
        self.store = pins.PinStore({"name": "fixture"}, self.remote)

    def tearDown(self):
        self._tmp.cleanup()

    def _status(self, local=REV_A, installed=REV_A):
        return {"local_runtime_revision": local, "installed_runtime_revision": installed,
                "runtime_revision_state": "match" if local == installed else "mismatch",
                "compatibility": {"state": "compatible", "ok": True}}

    def _gate(self, status=None, now=None):
        return pins.strict_gate({"name": "fixture"}, status or self._status(),
                                purpose="sb wp --remote fixture", store=self.store,
                                checkout=self.checkout, now=now)

    def test_holder_identity_is_stable_and_path_derived(self):
        first = pins.holder_identity("/home/a", "/repo/one")
        self.assertRegex(first, r"^h-[0-9a-f]{16}$")
        self.assertEqual(first, pins.holder_identity("/home/a", "/repo/one"))
        self.assertNotEqual(first, pins.holder_identity("/home/a", "/repo/two"))
        self.assertNotEqual(first, pins.holder_identity("/home/b", "/repo/one"))

    def test_strict_match_registers_one_private_pin_and_renews_it(self):
        start = time.time()
        result = self._gate(now=start)["compatibility"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "strict_pin_held")
        listed = self.store.list()
        self.assertEqual(len(listed), 1)
        pin = listed[0]
        self.assertEqual(pin.revision, REV_A)
        self.assertEqual(pin.checkout_path, os.path.realpath(self.checkout))
        self.assertLessEqual(pin.expires_at - pin.renewed_at, pins.MAX_TTL_SECONDS)
        directory = self.remote_home / "runtime" / "remote-pins"
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((directory / f"{pin.holder}.json").stat().st_mode), 0o600)
        self._gate(now=start + 60)
        renewed = self.store.list()
        self.assertEqual(len(renewed), 1)
        self.assertEqual(renewed[0].registered_at, pin.registered_at)
        self.assertGreater(renewed[0].expires_at, pin.expires_at)

    def test_strict_mismatch_refuses_and_registers_nothing(self):
        result = self._gate(self._status(REV_A, REV_B))["compatibility"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "strict_requires_exact_revision")
        self.assertEqual(self.store.list(), [])

    def test_unreachable_pins_fail_closed_without_leaking(self):
        self.remote.fail = True
        result = self._gate()["compatibility"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], pins.PIN_UNVERIFIABLE)
        self.assertNotIn("secret", json.dumps(result))

    def test_break_marks_pin_and_holder_refusal_reports_who_and_when(self):
        self._gate()
        holder = self.store.list()[0].holder
        breaker = pins.holder_identity("/other/home", "/other/checkout")
        self.assertEqual(self.store.break_pins([holder], breaker), [holder])
        self.assertEqual(self.store.break_pins([holder], breaker), [])
        refused = self._gate(self._status(REV_A, REV_B))["compatibility"]
        self.assertEqual(refused["broken_pin"]["broken_by"], breaker)
        self.assertIsInstance(refused["broken_pin"]["broken_at"], int)

    def test_release_removes_the_pin(self):
        self._gate()
        holder = self.store.list()[0].holder
        self.assertTrue(self.store.release(holder))
        self.assertFalse(self.store.release(holder))
        self.assertEqual(self.store.list(), [])

    def test_expired_pins_are_reported_and_swept_on_next_write(self):
        self._gate(now=time.time() - 2 * pins.MAX_TTL_SECONDS)
        listed = self.store.list()
        self.assertTrue(listed[0].expired())
        self.assertFalse(listed[0].binding())
        other = pins.Pin(pins.holder_identity("/x", "/y"), "/y", "0" * 16, REV_A, "p",
                         int(time.time()), int(time.time()), int(time.time()) + 60)
        self.store.register(other)
        self.assertEqual([pin.holder for pin in self.store.list()], [other.holder])

    def test_parse_rejects_malformed_or_secret_bearing_pins(self):
        good = pins.Pin(pins.holder_identity("/x", "/y"), "/y", "0" * 16, REV_A, "purpose",
                        100, 100, 100 + 3600).stored()
        self.assertIsNotNone(pins.parse_pin(good))
        for change in ({"holder": "../escape"}, {"revision": "zz"},
                       {"expires_at": 100 + pins.MAX_TTL_SECONDS + 1},
                       {"purpose": "x" * 121}, {"purpose": "token=abcdef0123456789"},
                       {"state": "broken"}, {"schema": 2}):
            with self.subTest(change=change):
                self.assertIsNone(pins.parse_pin({**good, **change}))

    def _other_pin(self, index, revision=REV_A):
        moment = int(time.time())
        return pins.Pin(pins.holder_identity("/x", f"/y{index}"), f"/y{index}", "0" * 16,
                        revision, "p", moment, moment, moment + 600)

    def test_fence_refuses_an_unacknowledged_binding_pin_and_takes_nothing(self):
        self._gate()
        holder = self.store.list()[0].holder
        installer = pins.holder_identity("/installer", "/checkout")
        with self.assertRaises(pins.PinError) as caught:
            self.store.fence(REV_B, [], installer)
        self.assertEqual(caught.exception.code, pins.PINS_UNACKNOWLEDGED)
        self.assertEqual(caught.exception.blocking, [holder])
        self.assertFalse((self.remote_home / "runtime" / "remote-pins" / ".replacing.json").exists())
        self.store.fence(REV_B, [holder], installer)
        self.store.unfence(installer)

    def test_registration_during_a_replacement_is_refused_for_other_revisions(self):
        """FR-015: no pin can appear between the migrate check and the install."""
        installer = pins.holder_identity("/installer", "/checkout")
        self.store.fence(REV_B, [], installer)
        fence = self.remote_home / "runtime" / "remote-pins" / ".replacing.json"
        self.assertEqual(stat.S_IMODE(fence.stat().st_mode), 0o600)
        refused = self._gate()["compatibility"]
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], pins.PIN_UNVERIFIABLE)
        self.assertEqual(self.store.list(), [])
        # The target revision itself is not harmed by the replacement.
        self.assertEqual(self._gate(self._status(REV_B, REV_B))["compatibility"]["reason"],
                         "strict_pin_held")
        other = pins.holder_identity("/second", "/installer")
        with self.assertRaises(pins.PinError) as caught:
            self.store.fence(REV_B, [], other)
        self.assertEqual(caught.exception.code, pins.REPLACEMENT_IN_PROGRESS)
        self.store.unfence(installer)
        self.assertFalse(fence.exists())
        self.assertTrue(self._gate()["compatibility"]["ok"])

    def test_capacity_is_enforced_and_a_full_list_is_authoritative(self):
        for index in range(pins.MAX_LISTED):
            self.store.register(self._other_pin(index))
        with self.assertRaises(pins.PinError) as caught:
            self.store.register(self._other_pin(pins.MAX_LISTED))
        self.assertEqual(caught.exception.code, pins.PIN_CAPACITY_REACHED)
        self.assertEqual(len(self.store.list()), pins.MAX_LISTED)
        # Renewing an existing holder is still allowed at capacity.
        self.store.register(self._other_pin(0))

    def test_a_list_beyond_capacity_is_refused_never_truncated(self):
        """FR-015: a 65th pin binding another revision must not be hidden."""
        directory = self.remote_home / "runtime" / "remote-pins"
        for index in range(pins.MAX_LISTED):
            self.store.register(self._other_pin(index, REV_B))
        extra = self._other_pin(999, REV_A)
        (directory / f"{extra.holder}.json").write_text(json.dumps(extra.stored()))
        with self.assertRaises(pins.PinError) as caught:
            self.store.list()
        self.assertEqual(caught.exception.code, pins.PINS_UNAVAILABLE)
        installer = pins.holder_identity("/installer", "/checkout")
        with self.assertRaises(pins.PinError) as caught:
            self.store.fence(REV_B, [], installer)
        self.assertEqual(caught.exception.blocking, [extra.holder])

    def test_recheck_releases_the_pin_when_the_runtime_changed_meanwhile(self):
        replaced = self._status(REV_B, REV_B)
        result = pins.strict_gate({"name": "fixture"}, self._status(), purpose="p",
                                  store=self.store, checkout=self.checkout,
                                  recheck=lambda: replaced)
        self.assertFalse(result["compatibility"]["ok"])
        self.assertEqual(result["compatibility"]["reason"], "strict_requires_exact_revision")
        self.assertEqual(result["runtime_revision_state"], "mismatch")
        self.assertEqual(self.store.list(), [])
        held = pins.strict_gate({"name": "fixture"}, self._status(), purpose="p",
                                store=self.store, checkout=self.checkout,
                                recheck=lambda: self._status())
        self.assertTrue(held["compatibility"]["ok"])

    def test_pin_record_shape_is_fixed_for_its_schema(self):
        """Pin records are versioned by pins.SCHEMA, not the control protocol
        (shapes.py excludes pins.py). Change a field only with a SCHEMA bump."""
        record = pins.Pin(pins.holder_identity("/x", "/y"), "/y", "0" * 16, REV_A, "p",
                          100, 100, 200).stored()
        expected = {1: {"schema", "holder", "checkout_path", "controller_home_digest",
                        "revision", "purpose", "registered_at", "renewed_at", "expires_at",
                        "state", "broken_by", "broken_at"}}
        self.assertEqual(set(record), expected[pins.SCHEMA])

    def test_purpose_is_bounded_and_printable(self):
        purpose = pins.purpose_for("sb\nwp", "/repo/" + "x" * 300)
        self.assertLessEqual(len(purpose), pins.MAX_PURPOSE)
        self.assertTrue(purpose.isprintable())


if __name__ == "__main__":
    unittest.main()

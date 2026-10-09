"""Strict-mode pins (spec 061 US2, FR-007..FR-014).

The remote pin program runs for real against a temporary "remote" Sandbox
home through a local ``sh -c`` stand-in for ``ssh_run``.
"""
import json
import os
import shlex
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
from tests.subprocess_support import synthetic_environment  # noqa: E402

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
        env = synthetic_environment({"SANDBOX_HOME": str(self.home), "HOME": str(self.home),
               "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"})
        return subprocess.run(["sh", "-c", command], env=env, capture_output=True,
                              text=True, timeout=timeout)


class PinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # A cleanup, not tearDown: gated installs started by a test are
        # released by later-registered cleanups, which run first.
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.remote_home = root / "remote-home"
        self.remote_home.mkdir()
        self.checkout = root / "checkout"
        self.checkout.mkdir()
        self.remote = _LocalRemote(self.remote_home)
        self.store = pins.PinStore({"name": "fixture"}, self.remote)

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

    # -- FR-015: the install runs under the pin lock (pins.INSTALL_GATE) -----

    def _gated(self, inner, acknowledged=(), target=REV_B, wait=0.5, timeout=30):
        command = pins.install_gate_command(target, acknowledged, inner, wait_seconds=wait)
        return self.remote(None, command, timeout=timeout)

    def _background(self, inner, target=REV_B, new_session=False):
        """Start a gated install that holds the lock until ``release`` exists."""
        release = self.remote_home / "release"
        started = self.remote_home / "started"
        script = f"touch {started}; while [ ! -e {release} ]; do sleep 0.05; done; {inner}"
        env = synthetic_environment({"SANDBOX_HOME": str(self.remote_home), "HOME": str(self.remote_home),
               "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"})
        proc = subprocess.Popen(["sh", "-c", pins.install_gate_command(target, (), script)],
                                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=new_session)
        deadline = time.time() + 20
        while not started.exists():
            self.assertLess(time.time(), deadline, "gated install never started")
            time.sleep(0.05)
        self.addCleanup(lambda: (release.touch(), proc.wait(30)))
        return proc, release

    def test_gate_refuses_an_unacknowledged_binding_pin_and_runs_nothing(self):
        self._gate()
        holder = self.store.list()[0].holder
        marker = self.remote_home / "installed"
        result = self._gated(f"touch {marker}")
        self.assertEqual(result.returncode, pins.INSTALL_BLOCKED_EXIT)
        refusal = pins.install_gate_refusal(result.returncode, result.stdout)
        self.assertEqual(refusal.code, pins.PINS_UNACKNOWLEDGED)
        self.assertEqual(refusal.blocking, [holder])
        self.assertFalse(marker.exists())
        self.assertEqual(self._gated(f"touch {marker}", [holder]).returncode, 0)
        self.assertTrue(marker.exists())
        # A pin for the target revision itself never blocks.
        self.assertEqual(self._gated("true", target=REV_A).returncode, 0)

    def test_gate_passes_stdin_and_the_child_status_through(self):
        command = pins.install_gate_command(REV_B, (), "IFS= read -r t; test \"$t\" = tok; exit 7")
        env = synthetic_environment({"SANDBOX_HOME": str(self.remote_home), "HOME": str(self.remote_home),
               "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"})
        result = subprocess.run(["sh", "-c", command], env=env, input="tok\n",
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 7)
        self.assertIsNone(pins.install_gate_refusal(7, result.stdout))

    def test_same_checkout_second_install_is_refused(self):
        """Two installs never overlap, whoever starts them."""
        _proc, release = self._background("true")
        second = self._gated("true")
        self.assertEqual(second.returncode, pins.INSTALL_BUSY_EXIT)
        self.assertEqual(pins.install_gate_refusal(second.returncode, "").code,
                         pins.REPLACEMENT_IN_PROGRESS)
        release.touch()

    def test_registration_waits_for_the_install_and_fails_closed(self):
        """No pin can appear between the check and the end of the install."""
        _proc, release = self._background("true")
        short = pins.PinStore({"name": "fixture"},
                              lambda e, c, timeout=0: self.remote(e, c, timeout=1))
        refused = pins.strict_gate({"name": "fixture"}, self._status(), purpose="p",
                                   store=short, checkout=self.checkout)["compatibility"]
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], pins.PIN_UNVERIFIABLE)
        release.touch()
        _proc.wait(30)
        self.assertEqual(self.store.list(), [])

    def test_old_registration_program_cannot_bypass_replacement(self):
        """An older controller's pin program takes the same exclusive flock
        (98a2d8b) and ignores fences; it still cannot write during an install."""
        legacy = (
            "import fcntl, os, sys, json\n"
            "root = os.path.join(sys.argv[1], 'runtime', 'remote-pins')\n"
            "lock = os.open(os.path.join(root, '.lock'), os.O_CREAT | os.O_RDWR, 0o600)\n"
            "fcntl.flock(lock, fcntl.LOCK_EX)\n"
            "open(os.path.join(root, 'h-' + '9' * 16 + '.json'), 'w').write('{}')\n")
        _proc, release = self._background("true")
        command = f"python3 -c {shlex.quote(legacy)} \"$SANDBOX_HOME\""
        with self.assertRaises(subprocess.TimeoutExpired):
            self.remote(None, command, timeout=1)
        directory = self.remote_home / "runtime" / "remote-pins"
        self.assertFalse((directory / ("h-" + "9" * 16 + ".json")).exists())
        release.touch()

    def test_lost_transport_keeps_the_lock_until_the_remote_install_ends(self):
        """A dropped SSH session hangs up the remote session (SIGHUP); the
        install and its lock outlive it, so nothing is released early."""
        import signal
        proc, release = self._background("true", new_session=True)
        os.killpg(proc.pid, signal.SIGHUP)
        time.sleep(0.3)
        self.assertIsNone(proc.poll(), "the gated install died on hangup")
        self.assertEqual(self._gated("true").returncode, pins.INSTALL_BUSY_EXIT)
        release.touch()
        self.assertEqual(proc.wait(30), 0)
        self.assertEqual(self._gated("true").returncode, 0)

    def test_a_child_left_running_does_not_hold_the_lock(self):
        """A daemon the install starts (the legacy MCP restart) must not inherit it."""
        release = self.remote_home / "release"
        self.addCleanup(release.touch)
        inner = f"(while [ ! -e {release} ]; do sleep 0.05; done) </dev/null >/dev/null 2>&1 &"
        self.assertEqual(self._gated(inner).returncode, 0)
        self.assertEqual(self._gated("true").returncode, 0)

    def test_live_legacy_fence_for_another_target_is_honoured(self):
        directory = self.remote_home / "runtime" / "remote-pins"
        directory.mkdir(parents=True)
        (directory / ".replacing.json").write_text(json.dumps(
            {"target": REV_A, "by": "h-" + "2" * 16, "expires_at": int(time.time()) + 600}))
        self.assertEqual(self._gated("true").returncode, pins.INSTALL_BUSY_EXIT)
        self.assertEqual(self._gated("true", target=REV_A).returncode, 0)
        refused = self._gate(self._status(REV_B, REV_B))["compatibility"]
        self.assertEqual(refused["reason"], pins.PIN_UNVERIFIABLE)

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
        result = self._gated("true")
        self.assertEqual(pins.install_gate_refusal(result.returncode, result.stdout).blocking,
                         [extra.holder])

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

    def test_recheck_failure_or_missing_revision_fails_closed_and_releases(self):
        def boom():
            raise OSError("ssh token=secret")
        for recheck in (boom, lambda: None, lambda: {"installed_runtime_revision": None}):
            with self.subTest(recheck=recheck):
                result = pins.strict_gate({"name": "fixture"}, self._status(), purpose="p",
                                          store=self.store, checkout=self.checkout,
                                          recheck=recheck)
                self.assertFalse(result["compatibility"]["ok"])
                self.assertEqual(result["compatibility"]["reason"], pins.PIN_UNVERIFIABLE)
                self.assertNotIn("secret", json.dumps(result))
                self.assertEqual(self.store.list(), [])

    def test_recheck_refuses_even_when_the_temporary_pin_cannot_be_released(self):
        def changed():
            self.remote.fail = True  # the release that follows cannot reach the remote
            return self._status(REV_B, REV_B)
        result = pins.strict_gate({"name": "fixture"}, self._status(), purpose="p",
                                  store=self.store, checkout=self.checkout, recheck=changed)
        self.assertFalse(result["compatibility"]["ok"])
        self.assertEqual(result["compatibility"]["reason"], "strict_requires_exact_revision")
        self.remote.fail = False
        # The leftover pin names a revision that is no longer installed and expires.
        leftover = self.store.list()
        self.assertEqual([pin.revision for pin in leftover], [REV_A])
        self.assertLessEqual(leftover[0].expires_at - leftover[0].renewed_at, pins.MAX_TTL_SECONDS)

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

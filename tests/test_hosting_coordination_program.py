"""The spec 060 remote coordination program, run for real (T003).

Each call goes through a local ``sh -c`` stand-in for ``ssh_run`` against a
temporary remote Sandbox home, exactly as the client sends it.
"""
import json
import os
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.hosting.coordination import program  # noqa: E402
from tests.hosting_coordination_support import TwoControllers  # noqa: E402

KEY = "london/shop/production"
OTHER = "london/blog/production"
CONTROLLER_A = "h-" + "a" * 16
CONTROLLER_B = "h-" + "b" * 16


def who(operation="apply", controller=CONTROLLER_A, request="r1"):
    return {"operation": operation, "request_id": request, "controller_id": controller,
            "session": "s1"}


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.env = TwoControllers()
        self.addCleanup(self.env.cleanup)
        self.store = self.env.remote_home / "runtime" / "hosting-leases"

    def raw(self, request):
        return self.env.remote(None, program.remote_command(request), timeout=15)

    def call(self, action, **fields):
        result = self.raw({"action": action, **fields})
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(value["ok"])
        self.assertEqual(value["action"], action)
        return value

    def enable(self):
        self.assertTrue(self.call("enable")["enabled"])

    def admit(self, key=KEY, **fields):
        fields.setdefault("holder", who())
        return self.call("admit", state_key=key, **fields)

    # -- capability -------------------------------------------------------

    def test_missing_capability_refuses_and_creates_nothing(self):
        self.assertFalse(self.call("capability")["enabled"])
        for action, fields in (("admit", {"state_key": KEY, "holder": who()}),
                               ("list", {}), ("cap-get", {}),
                               ("hold-claim", {"state_key": KEY, "purpose": "x",
                                               "holder": {"controller_id": CONTROLLER_A,
                                                          "session": "s"}})):
            with self.subTest(action=action):
                value = self.call(action, **fields)
                self.assertEqual(value["refused"], "lease_authority_unavailable")
                self.assertEqual(value["reason"], "capability_missing")
        self.assertFalse((self.env.remote_home / "runtime").exists())

    def test_enable_writes_private_store(self):
        self.enable()
        self.assertTrue(self.call("capability")["enabled"])
        self.admit()
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.store / "targets").stat().st_mode), 0o700)
        for path in [self.store / "capability.json", self.store / "coord.lock",
                     *(self.store / "targets").iterdir()]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)

    def test_read_only_list_creates_nothing(self):
        self.enable()
        before = sorted(p.name for p in self.store.iterdir())
        listing = self.call("list")
        self.assertEqual(listing["targets"], [])
        self.assertEqual(listing["build_cap"], program.DEFAULT_BUILD_CAP)
        self.assertIsNone(listing["shared_lease"])
        self.call("cap-get")
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), before)

    def test_malformed_requests_exit_non_zero(self):
        self.enable()
        for request in ({"action": "admit", "state_key": "no-slashes", "holder": who()},
                        {"action": "admit", "state_key": KEY, "holder": who(controller="x")},
                        {"action": "admit", "state_key": KEY, "holder": who(), "ttl": 91},
                        {"action": "shared-acquire", "state_key": KEY, "step": "edge",
                         "bound": 61},
                        {"action": "cap-set", "value": 0, "controller_id": CONTROLLER_A},
                        {"action": "nope"}):
            with self.subTest(request=request):
                self.assertNotEqual(self.raw(request).returncode, 0)

    # -- leases -----------------------------------------------------------

    def test_admit_renew_release_with_monotonic_fencing_token(self):
        self.enable()
        first = self.admit()
        self.assertTrue(first["admitted"])
        self.assertEqual(first["fencing_token"], 1)
        lease = first["lease"]
        self.assertRegex(lease["lease_id"], r"^l-[0-9a-f]{16}$")
        self.assertEqual(lease["kind"], "operation")
        self.assertLessEqual(lease["expires_at"] - lease["started_at"], program.LEASE_TTL_SECONDS)
        renewed = self.call("renew", state_key=KEY, lease_id=lease["lease_id"],
                            fencing_token=1)
        self.assertFalse(renewed["lost"])
        self.assertTrue(self.call("release", state_key=KEY, lease_id=lease["lease_id"],
                                  fencing_token=1)["released"])
        second = self.admit()
        self.assertEqual(second["fencing_token"], 2)
        # A release with the superseded token does nothing.
        self.assertFalse(self.call("release", state_key=KEY, lease_id=lease["lease_id"],
                                   fencing_token=1)["released"])

    def test_busy_target_refuses_with_holder_and_others_are_independent(self):
        self.enable()
        self.admit()
        busy = self.admit(holder=who(controller=CONTROLLER_B, request="r2"))
        self.assertEqual(busy["refused"], "target_busy")
        self.assertEqual(busy["holder"]["controller_id"], CONTROLLER_A)
        self.assertEqual(busy["target"], KEY)
        self.assertTrue(self.admit(OTHER, holder=who(controller=CONTROLLER_B))["admitted"])

    def test_expired_lease_without_phase_frees_target_and_renew_reports_lost(self):
        self.enable()
        lease = self.admit(ttl=1)["lease"]
        time.sleep(1.2)
        self.assertTrue(self.call("renew", state_key=KEY, lease_id=lease["lease_id"],
                                  fencing_token=1)["lost"])
        self.assertTrue(self.admit(holder=who(controller=CONTROLLER_B))["admitted"])
        history = json.loads(next((self.store / "targets").iterdir()).read_text())["history"]
        self.assertIn("expired", [h["event"] for h in history])

    def test_queue_is_fifo_with_lapsing_entries(self):
        self.enable()
        lease = self.admit()["lease"]
        deadline = int(time.time()) + 600
        first = {"waiter_id": "w-" + "1" * 16, "deadline": deadline}
        second = {"waiter_id": "w-" + "2" * 16, "deadline": deadline}
        self.assertEqual(self.admit(holder=who(request="q1"), waiter=first)["position"], 1)
        self.assertEqual(self.admit(holder=who(request="q2"), waiter=second)["position"], 2)
        self.call("release", state_key=KEY, lease_id=lease["lease_id"], fencing_token=1)
        behind = self.admit(holder=who(request="q2"), waiter=second)
        self.assertEqual(behind["refused"], "target_busy")
        self.assertTrue(behind["queued"])
        self.assertEqual(behind["holder"]["request_id"], "q1")
        # A caller that is not queued also waits behind the queue.
        self.assertEqual(self.admit(holder=who(request="x"))["refused"], "target_busy")
        admitted = self.admit(holder=who(request="q1"), waiter=first)
        self.assertTrue(admitted["admitted"])
        listing = self.call("list")["targets"][0]
        self.assertEqual([q["enqueued_at"] > 0 for q in listing["queue"]], [True])
        self.assertEqual(listing["state"], "leased")

    def test_interrupted_waiter_lapses_and_unblocks_the_queue(self):
        self.enable()
        lease = self.admit()["lease"]
        waiter = {"waiter_id": "w-" + "1" * 16, "deadline": int(time.time()) + 1}
        self.admit(holder=who(request="gone"), waiter=waiter)
        self.call("release", state_key=KEY, lease_id=lease["lease_id"], fencing_token=1)
        time.sleep(1.2)
        self.assertTrue(self.admit(holder=who(request="next"))["admitted"])

    def test_queue_is_bounded(self):
        self.enable()
        self.admit()
        deadline = int(time.time()) + 600
        for index in range(program.MAX_QUEUE):
            waiter = {"waiter_id": f"w-{index:016x}", "deadline": deadline}
            self.assertEqual(self.admit(waiter=waiter)["position"], index + 1)
        overflow = self.admit(waiter={"waiter_id": "w-" + "f" * 16, "deadline": deadline})
        self.assertTrue(overflow["queue_full"])
        self.assertIsNone(overflow["position"])

    # -- phases and fencing -------------------------------------------------

    def test_expiry_with_running_phase_fences_until_cessation(self):
        self.enable()
        first = self.admit(ttl=1)
        lease = first["lease"]
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(child.kill)
        self.call("phase-report", state_key=KEY, lease_id=lease["lease_id"], fencing_token=1,
                  event="start", phase_id="compose", pid=child.pid)
        time.sleep(1.2)
        refused = self.admit(holder=who(controller=CONTROLLER_B))
        self.assertEqual(refused["refused"], "predecessor_phase_running")
        self.assertEqual(refused["holder"]["controller_id"], CONTROLLER_A)
        self.assertEqual(self.call("list")["targets"][0]["state"], "fenced")
        # The stale holder can no longer start or check a phase.
        stale = self.call("phase-report", state_key=KEY, lease_id=lease["lease_id"],
                          fencing_token=1, event="check", phase_id="activate")
        self.assertEqual(stale["refused"], "lease_lost")
        child.kill()
        child.wait()
        admitted = self.admit(holder=who(controller=CONTROLLER_B))
        self.assertTrue(admitted["admitted"])
        self.assertEqual(admitted["fencing_token"], 2)

    def test_phase_end_from_expired_holder_clears_the_cessation_fence(self):
        self.enable()
        lease = self.admit(ttl=1)["lease"]
        self.call("phase-report", state_key=KEY, lease_id=lease["lease_id"], fencing_token=1,
                  event="start", phase_id="build")  # no probe: assumed running
        time.sleep(1.2)
        self.assertEqual(self.admit()["refused"], "predecessor_phase_running")
        ended = self.call("phase-report", state_key=KEY, lease_id=lease["lease_id"],
                          fencing_token=1, event="end", phase_id="build")
        self.assertTrue(ended["recorded"])
        self.assertEqual(ended["fence"], "none")
        self.assertTrue(self.admit()["admitted"])

    def test_phase_check_accepts_only_the_current_token(self):
        self.enable()
        admitted = self.admit()
        lease = admitted["lease"]
        valid = self.call("phase-report", state_key=KEY, lease_id=lease["lease_id"],
                          fencing_token=1, event="check", phase_id="compose")
        self.assertTrue(valid["valid"])
        wrong = self.call("phase-report", state_key=KEY, lease_id=lease["lease_id"],
                          fencing_token=7, event="check", phase_id="compose")
        self.assertEqual(wrong["refused"], "lease_lost")

    # -- holds ------------------------------------------------------------

    def _claim(self, duration=None, controller=CONTROLLER_A):
        fields = {"state_key": KEY, "purpose": "manual migration",
                  "holder": {"controller_id": controller, "session": "s1"}}
        if duration is not None:
            fields["duration"] = duration
        return self.call("hold-claim", **fields)

    def test_hold_claim_admits_only_the_presenting_operation(self):
        self.enable()
        hold = self._claim()["hold"]
        self.assertRegex(hold["hold_id"], r"^hd-[0-9a-f]{16}$")
        self.assertLessEqual(hold["expires_at"] - hold["claimed_at"], program.HOLD_MAX_SECONDS)
        refused = self.admit(holder=who(controller=CONTROLLER_B))
        self.assertEqual(refused["refused"], "target_held")
        self.assertEqual(refused["hold"]["purpose"], "manual migration")
        nested = self.admit(hold_id=hold["hold_id"])
        self.assertEqual(nested["lease"]["kind"], "hold-nested")
        listing = self.call("list")["targets"][0]
        self.assertEqual(listing["state"], "leased")
        self.assertEqual(listing["hold"]["hold_id"], hold["hold_id"])
        self.call("release", state_key=KEY, lease_id=nested["lease"]["lease_id"],
                  fencing_token=nested["fencing_token"])
        self.assertEqual(self.call("list")["targets"][0]["state"], "held")
        self.assertTrue(self.call("hold-release", state_key=KEY,
                                  hold_id=hold["hold_id"])["released"])
        self.assertTrue(self.admit(holder=who(controller=CONTROLLER_B))["admitted"])

    def test_hold_bounds_ownership_and_break(self):
        self.enable()
        self.assertEqual(self._claim(program.HOLD_MAX_SECONDS + 1)["refused"], "hold_too_long")
        hold = self._claim(60)["hold"]
        self.assertEqual(self._claim()["refused"], "target_held")
        wrong = "hd-" + "0" * 16
        self.assertEqual(self.call("hold-renew", state_key=KEY, hold_id=wrong)["refused"],
                         "hold_not_owned")
        self.assertEqual(self.call("hold-release", state_key=KEY, hold_id=wrong)["refused"],
                         "hold_not_owned")
        self.assertEqual(self.admit(hold_id=wrong)["refused"], "target_held")
        time.sleep(1.1)
        past = self.call("hold-renew", state_key=KEY, hold_id=hold["hold_id"],
                         duration=program.HOLD_MAX_SECONDS)
        self.assertEqual(past["refused"], "hold_renewal_exceeds_maximum")
        self.assertIn("hold", self.call("hold-renew", state_key=KEY, hold_id=hold["hold_id"],
                                        duration=120))
        broken = self.call("hold-break", state_key=KEY, controller_id=CONTROLLER_B,
                           reason="owner unreachable")
        self.assertTrue(broken["broken"])
        history = json.loads(next((self.store / "targets").iterdir()).read_text())["history"]
        entry = history[-1]
        self.assertEqual(entry["event"], "broken_hold")
        self.assertEqual(entry["breaker_controller"], CONTROLLER_B)
        self.assertEqual(entry["reason"], "owner unreachable")
        self.assertTrue(self.admit(holder=who(controller=CONTROLLER_B))["admitted"])

    def test_hold_cannot_be_claimed_over_a_running_operation(self):
        self.enable()
        self.admit()
        self.assertEqual(self._claim(controller=CONTROLLER_B)["refused"], "target_busy")

    def test_expired_hold_frees_the_target(self):
        self.enable()
        self._claim(1)
        time.sleep(1.2)
        self.assertTrue(self.admit(holder=who(controller=CONTROLLER_B))["admitted"])

    # -- build cap and the remote-wide lease ---------------------------------

    def test_build_slots_respect_the_cap_and_follow_their_lease(self):
        self.enable()
        leases = [self.admit(f"london/site{i}/production")["lease"] for i in range(3)]
        for index in range(2):
            self.assertTrue(self.call("build-acquire", state_key=f"london/site{index}/production",
                                      lease_id=leases[index]["lease_id"])["acquired"])
        refused = self.call("build-acquire", state_key="london/site2/production",
                            lease_id=leases[2]["lease_id"])
        self.assertEqual(refused["refused"], "build_cap_reached")
        self.assertEqual(refused["cap"], 2)
        self.assertEqual(len(refused["build_holders"]), 2)
        # Releasing the target lease frees its slot too.
        self.call("release", state_key="london/site0/production",
                  lease_id=leases[0]["lease_id"], fencing_token=1)
        self.assertTrue(self.call("build-acquire", state_key="london/site2/production",
                                  lease_id=leases[2]["lease_id"])["acquired"])
        self.assertTrue(self.call("build-release", state_key="london/site2/production",
                                  lease_id=leases[2]["lease_id"])["released"])
        self.assertEqual(self.call("cap-set", value=3, controller_id=CONTROLLER_B)["build_cap"], 3)
        cap = self.call("cap-get")
        self.assertEqual(cap["build_cap"], 3)
        self.assertEqual(cap["cap_history"][-1]["controller_id"], CONTROLLER_B)
        stranger = self.call("build-acquire", state_key=KEY, lease_id="l-" + "0" * 16)
        self.assertEqual(stranger["refused"], "lease_lost")

    def test_shared_lease_is_single_and_bounded(self):
        self.enable()
        shared = self.call("shared-acquire", state_key=KEY, step="edge", bound=60)["shared_lease"]
        self.assertLessEqual(shared["expires_at"] - shared["acquired_at"], 60)
        busy = self.call("shared-acquire", state_key=OTHER, step="dns")
        self.assertEqual(busy["refused"], "shared_lease_busy")
        self.assertEqual(busy["shared_lease"]["step"], "edge")
        self.assertEqual(self.call("list")["shared_lease"]["target"], KEY)
        self.assertTrue(self.call("shared-release", lease_id=shared["lease_id"])["released"])
        short = self.call("shared-acquire", state_key=OTHER, step="dns", bound=1)
        self.assertIn("shared_lease", short)
        time.sleep(1.2)
        self.assertIn("shared_lease", self.call("shared-acquire", state_key=KEY, step="edge"))

    def test_register_controller_is_idempotent(self):
        self.enable()
        self.call("register-controller", controller_id=CONTROLLER_A)
        self.call("register-controller", controller_id=CONTROLLER_A)
        remote = json.loads((self.store / "remote.json").read_text())
        self.assertEqual(remote["controllers"], [CONTROLLER_A])

    def test_listing_is_bounded_and_flags_truncation(self):
        self.enable()
        for index in range(program.MAX_TARGETS_LISTED + 1):
            self.admit(f"london/site{index}/production")
        listing = self.call("list")
        self.assertEqual(len(listing["targets"]), program.MAX_TARGETS_LISTED)
        self.assertTrue(listing["truncated"])

    def test_corrupt_target_fails_closed_for_that_target_only(self):
        self.enable()
        self.admit()
        path = next((self.store / "targets").iterdir())
        path.write_text("{not json")
        self.assertEqual(self.admit()["refused"], "lease_authority_unavailable")
        self.assertTrue(self.admit(OTHER)["admitted"])
        states = {t["state"] for t in self.call("list")["targets"]}
        self.assertEqual(states, {"unreadable", "leased"})


if __name__ == "__main__":
    unittest.main()

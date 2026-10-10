"""The spec 060 coordination client (T005).

Transport failures, timeouts, invalid output and a runtime without the
``hosting_coordination`` capability all become ``lease_authority_unavailable``
naming the migrate remedy, and no transport detail reaches the error.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.hosting.coordination import client  # noqa: E402
from tests.hosting_coordination_support import TwoControllers  # noqa: E402

KEY = "london/shop/production"


def holder(controller="h-" + "a" * 16):
    return {"operation": "apply", "request_id": "r1", "controller_id": controller,
            "session": "s1"}


class _Canned:
    def __init__(self, stdout="", returncode=0, exc=None):
        self.stdout, self.returncode, self.exc = stdout, returncode, exc
        self.timeouts = []

    def __call__(self, _entry, _command, timeout=30):
        self.timeouts.append(timeout)
        if self.exc is not None:
            raise self.exc
        return subprocess.CompletedProcess(["ssh"], self.returncode, self.stdout, "")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.env = TwoControllers()
        self.addCleanup(self.env.cleanup)
        self.entry = {"name": "london"}
        self.client = client.CoordinationClient(self.entry, self.env.remote)

    def assertUnavailable(self, call, *, secret="secret"):
        with self.assertRaises(client.CoordinationError) as caught:
            call()
        error = caught.exception
        self.assertEqual(error.code, client.LEASE_AUTHORITY_UNAVAILABLE)
        self.assertIn("./sb remote service migrate london", error.remedy)
        self.assertNotIn(secret, str(error))
        self.assertNotIn("192.0.2.1", str(error))
        self.assertIsNone(error.__cause__)  # transport detail is not chained
        return error

    def test_every_call_is_bounded_to_fifteen_seconds(self):
        canned = _Canned('{"ok": true, "action": "capability", "now": 1, "enabled": true}')
        client.CoordinationClient(self.entry, canned).capability()
        self.assertEqual(canned.timeouts, [client.TIMEOUT_SECONDS])
        self.assertEqual(client.TIMEOUT_SECONDS, 15)

    def test_missing_capability_is_lease_authority_unavailable(self):
        self.assertFalse(self.client.capability())
        error = self.assertUnavailable(lambda: self.client.admit(KEY, holder()))
        self.assertEqual(error.reason, "capability_missing")

    def test_ssh_failure_and_timeout_are_unavailable_without_detail(self):
        for exc in (OSError("ssh: connect to host 192.0.2.1 token=secret"),
                    subprocess.TimeoutExpired(["ssh", "token=secret"], 15)):
            with self.subTest(exc=type(exc).__name__):
                stub = client.CoordinationClient(self.entry, _Canned(exc=exc))
                self.assertUnavailable(lambda: stub.list())

    def test_invalid_output_is_rejected(self):
        for stdout, code in (("", 0), ("not json", 0), ('{"ok": false}', 0),
                             ('{"ok": true, "action": "renew", "now": 1}', 0),
                             ('{"ok": true, "action": "list", "now": 1}', 0),
                             ('{"ok": true, "action": "list", "now": 1, "targets": []}', 3),
                             ('[1, 2]', 0)):
            with self.subTest(stdout=stdout, code=code):
                stub = client.CoordinationClient(self.entry, _Canned(stdout, code))
                self.assertUnavailable(lambda: stub.list())

    def test_admitted_lease_shape_is_validated(self):
        bad = ('{"ok": true, "action": "admit", "now": 1, "admitted": true, '
               '"fencing_token": 1, "lease": {"lease_id": "nope"}}')
        stub = client.CoordinationClient(self.entry, _Canned(bad))
        self.assertUnavailable(lambda: stub.admit(KEY, holder()))

    def test_round_trip_against_the_real_program(self):
        self.client.enable()
        self.assertTrue(self.client.capability())
        admitted = self.client.admit(KEY, holder())
        self.assertTrue(admitted["admitted"])
        busy = self.client.admit(KEY, holder("h-" + "b" * 16))
        self.assertEqual(busy["refused"], "target_busy")
        lease = admitted["lease"]
        self.assertFalse(self.client.renew(KEY, lease["lease_id"], 1)["lost"])
        listing = self.client.list()
        self.assertEqual(listing["targets"][0]["state"], "leased")
        self.assertTrue(self.client.release(KEY, lease["lease_id"], 1)["released"])

    def test_invalid_arguments_never_reach_the_remote(self):
        self.client.enable()
        calls = self.env.remote.calls
        with self.assertRaises(ValueError):
            self.client.admit("bad key", holder())
        with self.assertRaises(ValueError):
            self.client.shared_acquire(KEY, "edge", bound=61)
        self.assertEqual(self.env.remote.calls, calls)

    def test_controller_id_is_home_derived(self):
        first = client.controller_id(self.env.homes[0])
        self.assertRegex(first, r"^h-[0-9a-f]{16}$")
        self.assertEqual(first, client.controller_id(self.env.homes[0]))
        self.assertNotEqual(first, client.controller_id(self.env.homes[1]))


if __name__ == "__main__":
    unittest.main()

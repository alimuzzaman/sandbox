"""Container churn between `docker ps`, `docker inspect` and the re-list.

The host port probe counts every container running in either list, keeps a
restart loop's publications, and rescans when a listed container is removed.
The server exec-target snapshot rescans on any vanish or re-list change. Both
refuse other Docker failures and churn that never settles. Synthetic replies only.
"""
from __future__ import annotations

import json
import subprocess
import time
import unittest

from sandbox.hosting import ports
from sandbox.hosting import server_target_selection as selection
from sandbox.hosting.target_resolution import TargetRefusal

A, B, C, D = "a" * 64, "b" * 64, "c" * 64, "d" * 64


def _probe():
    namespace = {}
    exec(compile(ports._PROBE, "probe", "exec"), namespace)
    return namespace


def _item(cid, port):
    return json.dumps({"id": cid, "project": "p", "service": "web",
                       "ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]},
                       "addresses": "172.18.0.2,"})


class PortProbeScanTests(unittest.TestCase):
    def scan(self, listings, inspect, exists=lambda cid: True):
        """listings: successive `docker ps` outputs; inspect(cid) -> (code, out, err)."""
        probe, calls = _probe(), []
        listings = list(listings)

        def attempt(args, payload=None):
            calls.append(args[:2])
            if args[:2] == ["docker", "ps"]:
                return 0, "".join(cid + "\n" for cid in listings.pop(0)), ""
            if args[:3] == ["docker", "inspect", "--format"] and args[3] == "{{json .Id}}":
                cid = args[-1]
                return (0, json.dumps(cid), "") if exists(cid) else (1, "", f"error: no such object: {cid}\n")
            if args[:2] == ["docker", "inspect"]:
                return inspect(args[-1])
            raise AssertionError(args)
        probe["attempt"] = attempt
        return probe, calls

    @staticmethod
    def ok(cid):
        return 0, _item(cid, 8000 + ord(cid[0])), ""

    def test_a_container_vanishing_before_its_inspect_restarts_the_scan(self):
        probe, calls = self.scan([[A, B], [A], [A]], lambda cid: (
            (1, "", f"error: no such object: {cid}\n") if cid == B else self.ok(cid)))
        publications = probe["settled_scan"]()
        self.assertEqual([row["container"] for row in publications], [A])
        self.assertEqual(calls.count(["docker", "ps"]), 3)

    def test_a_restart_loop_leaving_the_list_reserves_its_ports_as_unknown(self):
        # ocp-trial wp-setup in `Restarting (1)` dropped in and out of docker ps.
        probe, calls = self.scan([[A, B], [A]], self.ok)
        publications = {row["container"]: row for row in probe["settled_scan"]()}
        self.assertEqual(sorted(publications), [A, B])
        self.assertEqual(publications[A]["project"], "p")
        self.assertEqual((publications[B]["project"], publications[B]["service"],
                          publications[B]["container_addresses"]), ("unknown:" + B, "unknown", []))
        self.assertEqual(calls.count(["docker", "ps"]), 2)

    def test_a_departed_owner_cannot_vouch_for_its_old_port(self):
        # The expected owner stops after its inspect; a foreign container may
        # take its port and address. Its stale row must refuse, not accept.
        owner = {"project": "p", "service": "web", "container_port": 80}
        probe, _ = self.scan([[A], []], self.ok)
        publications = probe["settled_scan"]()
        port = publications[0]["port"]
        for listeners in ([{"address": "127.0.0.1", "port": port, "container": None}], []):
            with self.subTest(listener=bool(listeners)):
                snapshot = ports.validate_snapshot(self.snapshot(publications, listeners))
                # An ownership refusal, never "stale port observation".
                with self.assertRaisesRegex(ports.PortObservationError, "unknown listener|conflicting Docker owner"):
                    ports.check_pair(snapshot, port, owner)
        # Control: the same container still listed is the port's proven owner.
        probe, _ = self.scan([[A], [A]], self.ok)
        ports.check_pair(ports.validate_snapshot(self.snapshot(probe["settled_scan"](), [])), port, owner)

    def test_a_departed_reservation_and_a_replacement_on_one_port_refuse_the_snapshot(self):
        probe, _ = self.scan([[A], [C]], lambda cid: (0, _item(cid, 9000), ""))
        with self.assertRaisesRegex(ports.PortObservationError, "duplicate Docker bind owner|multiple containers own one TCP port"):
            ports.validate_snapshot(self.snapshot(probe["settled_scan"](), []))

    @staticmethod
    def snapshot(publications, listeners):
        return {"version": 1, "complete": True, "identity": {"target": "t", "uid": 1, "home": "/h"},
                "engine_id": "e", "observed_at": time.time(), "publications": publications,
                "listeners": listeners, "reservations": [], "end": "sandbox.ports.v1.complete"}

    def test_a_container_entering_the_list_is_inspected_too(self):
        probe, calls = self.scan([[A], [A, C]], self.ok)
        publications = probe["settled_scan"]()
        self.assertEqual(sorted(row["container"] for row in publications), [A, C])
        self.assertEqual(calls.count(["docker", "ps"]), 2)

    def test_a_container_entering_and_already_removed_rescans_and_keeps_its_replacement(self):
        # C vanishes before its inspect and D takes its port: D must be counted.
        probe, calls = self.scan([[A], [A, C], [A, D], [A, D]], lambda cid: (
            (1, "", f"error: no such object: {cid}\n") if cid == C else
            (0, _item(cid, 9000), "") if cid == D else self.ok(cid)))
        publications = probe["settled_scan"]()
        self.assertEqual(sorted((row["container"], row["port"]) for row in publications),
                         [(A, 8097), (D, 9000)])
        self.assertEqual(calls.count(["docker", "ps"]), 4)

    def test_a_listed_container_removed_outright_restarts_the_scan(self):
        removed = {B}
        probe, calls = self.scan([[A, B], [A], [A], [A]], self.ok, exists=lambda cid: cid not in removed)
        self.assertEqual([row["container"] for row in probe["settled_scan"]()], [A])
        self.assertEqual(calls.count(["docker", "ps"]), 4)

    def test_any_other_inspect_failure_refuses(self):
        for err in ("Error response from daemon: permission denied\n",
                    "error: no such object: " + C + "\n",  # an id that was not asked about
                    "error: no such object: " + B + "\nwarning: extra\n", ""):
            with self.subTest(err=err):
                probe, _ = self.scan([[A, B]] * 10, lambda cid, err=err: (
                    (1, "", err) if cid == B else self.ok(cid)))
                with self.assertRaisesRegex(RuntimeError, "port observation refused"):
                    probe["settled_scan"]()

    def test_churn_that_never_settles_refuses(self):
        probe, calls = self.scan([[A, B], [A]] * 20, self.ok, exists=lambda cid: cid != B)
        with self.assertRaisesRegex(RuntimeError, "port observation refused"):
            probe["settled_scan"]()
        self.assertEqual(calls.count(["docker", "ps"]), 2 * probe["SCANS"])


class ExecSnapshotScanTests(unittest.TestCase):
    PROJECTION = "projection"

    def run_with(self, listings, inspect):
        listings, calls = list(listings), []

        def run(argv):
            calls.append(argv[:2])
            if argv[:2] == ["docker", "ps"]:
                return subprocess.CompletedProcess(argv, 0, "".join(i + "\n" for i in listings.pop(0)), "")
            if argv[:2] == ["docker", "inspect"]:
                return inspect(argv[4:])
            raise AssertionError(argv)
        return run, calls

    @staticmethod
    def labels(ids):
        return "".join(json.dumps(["proj", "f", "/w", "web", "1"]) + "\n" for _ in ids)

    def test_a_vanished_container_restarts_from_a_fresh_list(self):
        short_a, short_b = A[:12], B[:12]

        def inspect(ids):
            if short_b in ids:
                return subprocess.CompletedProcess(ids, 1, self.labels([short_a]),
                                                   f"Error: No such object: {short_b}\n")
            return subprocess.CompletedProcess(ids, 0, self.labels(ids), "")
        run, calls = self.run_with([[short_a, short_b], [short_a], [short_a]], inspect)
        ids, lines = selection._compose_containers(run, "proj", self.PROJECTION)
        self.assertEqual((ids, len(lines)), ([short_a], 1))
        self.assertEqual(calls.count(["docker", "ps"]), 3)

    def test_other_failures_refuse(self):
        for code, err in ((1, "Error response from daemon: connection refused\n"),
                          (1, "Error: No such object: " + C[:12] + "\n"),
                          (0, "warning\n"), (1, "")):
            with self.subTest(err=err):
                run, _ = self.run_with([[A[:12]]] * 10, lambda ids, code=code, err=err:
                                       subprocess.CompletedProcess(ids, code, "", err))
                with self.assertRaises(TargetRefusal):
                    selection._compose_containers(run, "proj", self.PROJECTION)

    def test_churn_that_never_settles_refuses(self):
        listings = [[A[:12]] if index % 2 == 0 else [A[:12], B[:12]] for index in range(40)]
        run, calls = self.run_with(listings, lambda ids: subprocess.CompletedProcess(
            ids, 0, self.labels(ids), ""))
        with self.assertRaises(TargetRefusal):
            selection._compose_containers(run, "proj", self.PROJECTION)
        self.assertEqual(calls.count(["docker", "ps"]), 2 * selection._SCANS)


if __name__ == "__main__":
    unittest.main()

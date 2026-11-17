"""Container churn between `docker ps`, `docker inspect` and the re-list.

The host port probe counts every container running in either list, keeps a
restart loop's publications, and rescans when one of the applied target's own
containers is removed; other targets' containers are read in one batch and only
claim ports, so a parallel apply elsewhere never restarts or fails the scan.
The server exec-target snapshot rescans on any vanish or re-list change. Both
refuse other Docker failures and churn that never settles. Synthetic replies only.
"""
from __future__ import annotations

import json
import subprocess
import threading
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


def _item(cid, port, project="p"):
    return json.dumps({"id": cid, "project": project, "service": "web",
                       "ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]},
                       "addresses": "172.18.0.2,"})


def _gone(cid):
    return f"error: no such object: {cid}"


class PortProbeScanTests(unittest.TestCase):
    """Own project "p" is strict; project "q" is another target."""

    def scan(self, listings, inspect, exists=lambda cid: True, projects=None):
        """listings: successive `docker ps` id lists; inspect(cid) -> (code, out, err)."""
        probe, calls = _probe(), []
        listings = list(listings)
        projects = projects or {}

        def attempt(args, payload=None, budget=15):
            calls.append(args[:2])
            if args[:2] == ["docker", "ps"]:
                return 0, "".join(json.dumps([cid, projects.get(cid, "p")]) + "\n"
                                  for cid in listings.pop(0)), ""
            if args[:5] == ["docker", "inspect", "--type", "container", "--format"]:
                outs, errs, code = [], [], 0
                for cid in args[6:]:
                    if args[5] == '{"id":{{json .Id}}}':
                        result = (0, json.dumps({"id": cid}), "") if exists(cid) else (1, "", _gone(cid))
                    else:
                        result = inspect(cid)
                    code = code or result[0]
                    if result[1]:
                        outs.append(result[1])
                    if result[2]:
                        errs.append(result[2])
                return code, "".join(o + "\n" for o in outs), "".join(e + "\n" for e in errs)
            raise AssertionError(args)
        probe["attempt"] = attempt
        return probe, calls

    @staticmethod
    def ok(cid):
        return 0, _item(cid, 8000 + ord(cid[0])), ""

    def test_one_batched_inspect_per_list(self):
        probe, calls = self.scan([[A, B, C], [A, B, C]], self.ok)
        self.assertEqual(len(probe["settled_scan"](["p"])), 3)
        self.assertEqual(calls.count(["docker", "inspect"]), 1)

    def test_an_own_container_vanishing_before_its_inspect_restarts_the_scan(self):
        probe, calls = self.scan([[A, B], [A], [A]], lambda cid: (
            (1, "", _gone(cid)) if cid == B else self.ok(cid)))
        publications = probe["settled_scan"](["p"])
        self.assertEqual([row["container"] for row in publications], [A])
        self.assertEqual(calls.count(["docker", "ps"]), 3)

    def test_another_targets_container_vanishing_never_restarts_the_scan(self):
        # 2026-11-17 07:24Z: guided-setup recreated while guided probed.
        probe, calls = self.scan([[A, B], [A]], lambda cid: (
            (1, "", _gone(cid)) if cid == B else self.ok(cid)), projects={B: "q"})
        self.assertEqual([row["container"] for row in probe["settled_scan"](["p"])], [A])
        self.assertEqual(calls.count(["docker", "ps"]), 2)

    def test_another_targets_removed_container_keeps_an_unknown_claim_without_a_rescan(self):
        # Its replacement may bind the port after the re-list: the claim stays.
        probe, calls = self.scan([[A, B], [A]], self.ok, exists=lambda cid: cid != B,
                                 projects={B: "q"})
        publications = {row["container"]: row for row in probe["settled_scan"](["p"])}
        self.assertEqual(sorted(publications), [A, B])
        self.assertEqual((publications[B]["project"], publications[B]["container_addresses"]),
                         ("unknown:" + B, []))
        self.assertEqual(calls.count(["docker", "ps"]), 2)

    def test_not_found_lines_may_come_in_any_order(self):
        missing = {B, C}
        probe, calls = self.scan([[A, B, C], [A]], lambda cid: (
            (1, "", _gone(cid)) if cid in missing else self.ok(cid)), projects={B: "q", C: "q"})
        original = probe["attempt"]

        def reversed_errors(args, payload=None, **kwargs):
            code, out, err = original(args, payload)
            return code, out, "".join(reversed(err.splitlines(keepends=True)))
        probe["attempt"] = reversed_errors
        self.assertEqual([row["container"] for row in probe["settled_scan"](["p"])], [A])

    def test_a_batch_gets_a_budget_that_grows_with_its_size(self):
        probe, _ = self.scan([[A]], self.ok)
        budgets = []

        def attempt(args, payload=None, budget=15):
            budgets.append(budget)
            return 0, "".join(_item(cid, 9000 + i) + "\n" for i, cid in enumerate(args[6:])), ""
        probe["attempt"] = attempt
        ids = [format(i, "064x") for i in range(100)]
        probe["inspect_many"](ids)
        self.assertEqual(budgets, [15 + 0.25 * 100])

    def test_a_restart_loop_leaving_the_list_reserves_its_ports_as_unknown(self):
        # ocp-trial wp-setup in `Restarting (1)` dropped in and out of docker ps.
        for projects in ({}, {B: "q"}):
            with self.subTest(foreign=bool(projects)):
                probe, calls = self.scan([[A, B], [A]], self.ok, projects=projects)
                publications = {row["container"]: row for row in probe["settled_scan"](["p"])}
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
        publications = probe["settled_scan"](["p"])
        port = publications[0]["port"]
        for listeners in ([{"address": "127.0.0.1", "port": port, "container": None}], []):
            with self.subTest(listener=bool(listeners)):
                snapshot = ports.validate_snapshot(self.snapshot(publications, listeners))
                # An ownership refusal, never "stale port observation".
                with self.assertRaisesRegex(ports.PortObservationError, "unknown listener|conflicting Docker owner"):
                    ports.check_pair(snapshot, port, owner)
        # Control: the same container still listed is the port's proven owner.
        probe, _ = self.scan([[A], [A]], self.ok)
        ports.check_pair(ports.validate_snapshot(self.snapshot(probe["settled_scan"](["p"]), [])), port, owner)

    def test_a_departed_reservation_and_a_replacement_refuse_only_their_port(self):
        owner = {"project": "p", "service": "web", "container_port": 80}
        probe, _ = self.scan([[A], [C]], lambda cid: (0, _item(cid, 9000, "q"), ""),
                             projects={A: "q", C: "q"})
        snapshot = ports.validate_snapshot(self.snapshot(probe["settled_scan"](["p"]), []))
        with self.assertRaisesRegex(ports.PortObservationError, "duplicate Docker bind owner|multiple containers own one TCP port"):
            ports.check_pair(snapshot, 9000, owner)
        ports.check_pair(snapshot, 9001, owner)  # an unrelated number stays decidable

    def test_two_own_containers_on_one_port_refuse_the_primary(self):
        owner = {"project": "p", "service": "web", "container_port": 80}
        probe, _ = self.scan([[A, C], [A, C]], lambda cid: (0, _item(cid, 9000), ""))
        snapshot = ports.validate_snapshot(self.snapshot(probe["settled_scan"](["p"]), []))
        with self.assertRaisesRegex(ports.PortObservationError, "duplicate Docker bind owner|multiple containers own one TCP port"):
            ports.primary_ports(snapshot, owner)

    @staticmethod
    def snapshot(publications, listeners):
        return {"version": 1, "complete": True, "identity": {"target": "t", "uid": 1, "home": "/h"},
                "engine_id": "e", "observed_at": time.time(), "publications": publications,
                "listeners": listeners, "reservations": [], "end": "sandbox.ports.v1.complete"}

    def test_a_container_entering_the_list_is_inspected_too(self):
        probe, calls = self.scan([[A], [A, C]], self.ok)
        publications = probe["settled_scan"](["p"])
        self.assertEqual(sorted(row["container"] for row in publications), [A, C])
        self.assertEqual(calls.count(["docker", "ps"]), 2)

    def test_a_container_entering_and_already_removed_rescans_and_keeps_its_replacement(self):
        # C vanishes before its inspect and D takes its port: D must be counted.
        probe, calls = self.scan([[A], [A, C], [A, D], [A, D]], lambda cid: (
            (1, "", _gone(cid)) if cid == C else
            (0, _item(cid, 9000), "") if cid == D else self.ok(cid)))
        publications = probe["settled_scan"](["p"])
        self.assertEqual(sorted((row["container"], row["port"]) for row in publications),
                         [(A, 8097), (D, 9000)])
        self.assertEqual(calls.count(["docker", "ps"]), 4)

    def test_a_listed_own_container_removed_outright_restarts_the_scan(self):
        removed = {B}
        probe, calls = self.scan([[A, B], [A], [A], [A]], self.ok, exists=lambda cid: cid not in removed)
        self.assertEqual([row["container"] for row in probe["settled_scan"](["p"])], [A])
        self.assertEqual(calls.count(["docker", "ps"]), 4)

    def test_any_other_inspect_failure_refuses(self):
        for err in ("Error response from daemon: permission denied",
                    _gone(C),  # an id that was not asked about
                    _gone(B) + "\nwarning: extra", ""):
            for projects in ({}, {B: "q"}):
                with self.subTest(err=err, foreign=bool(projects)):
                    probe, _ = self.scan([[A, B]] * 10, lambda cid, err=err: (
                        (1, "", err) if cid == B else self.ok(cid)), projects=projects)
                    with self.assertRaisesRegex(RuntimeError, "port observation refused"):
                        probe["settled_scan"](["p"])

    def test_own_churn_that_never_settles_refuses(self):
        probe, calls = self.scan([[A, B], [A]] * 20, self.ok, exists=lambda cid: cid != B)
        with self.assertRaisesRegex(RuntimeError, "port observation refused"):
            probe["settled_scan"](["p"])
        self.assertEqual(calls.count(["docker", "ps"]), 2 * probe["SCANS"])

    def test_own_projects_are_required(self):
        probe, _ = self.scan([[A]], self.ok)
        for own in (None, [], [""], "p"):
            with self.subTest(own=own), self.assertRaisesRegex(RuntimeError, "port observation refused"):
                probe["settled_scan"](own)
        with self.assertRaises(ports.PortObservationError):
            ports.probe_command(identity={"target": "t", "uid": 1, "home": "/h"},
                                runtime_dir="/h/runtime", own_projects=[])


class RefusalReasonTests(unittest.TestCase):
    """A failed probe names its stage and kind; nothing else crosses over."""

    def test_the_probe_reports_its_stage_and_never_raw_output(self):
        import os
        import shlex
        identity = {"target": "t", "uid": os.getuid() + 1, "home": "/"}
        argv = shlex.split(ports.probe_command(identity=identity, runtime_dir="/tmp",
                                               own_projects=["p"]))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertEqual(ports.refusal_reason(result.stderr), "identity/invalid")

    def test_the_in_lock_recheck_reports_its_stage_too(self):
        import os
        import shlex
        identity = {"target": "t", "uid": os.getuid() + 1, "home": "/"}
        argv = shlex.split(ports.recheck_command(
            identity=identity, runtime_dir="/tmp", port=18044, engine_id="e",
            owner={"project": "p", "service": "web", "container_port": 80}))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(ports.refusal_reason(result.stderr), "identity/invalid")

    def test_observe_ports_relays_only_a_matching_line(self):
        cases = {
            "sandbox-port-probe: own_containers/unsettled\n": "own_containers/unsettled",
            "noise /home/x secret\nsandbox-port-probe: socket_probe/exit\n": "socket_probe/exit",
            "sandbox-port-probe: port_check/TCP port 20001 has an unknown listener\n":
                "port_check/TCP port 20001 has an unknown listener",
            "sandbox-port-probe: x/../etc/passwd\n": None,
            "Traceback: /home/alim/secret\n": None,
        }
        for stderr, reason in cases.items():
            with self.subTest(stderr=stderr):
                result = subprocess.CompletedProcess([], 3, "", stderr)
                with self.assertRaises(ports.PortObservationError) as caught:
                    ports.observe_ports(lambda *_a, **_k: result,
                                        identity={"target": "t", "uid": 1, "home": "/h"},
                                        runtime_dir="/h/runtime", own_projects=["p"])
                message = str(caught.exception)
                self.assertIn(reason or "reason unavailable", message)
                self.assertNotIn("/home", message)

    def test_a_refusal_inside_the_probe_names_its_kind(self):
        probe = _probe()
        probe["STAGE"][0] = "container_list"
        with self.assertRaisesRegex(RuntimeError, "port observation refused: container_list/exit"):
            probe["fail"]("exit")


class _Docker:
    """A shared fake daemon: two targets each recreate their own containers."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}   # cid -> (project, port)
        self.serial = 0
        self.gone = 0       # not-found replies: proof the scans saw churn

    def start(self, project, port):
        with self.lock:
            self.serial += 1
            cid = format(self.serial, "064x")
            self.running[cid] = (project, port)
            return cid

    def attempt(self, args, payload=None, budget=15):
        time.sleep(0.001)  # yield: interleave the two applies
        with self.lock:
            if args[:2] == ["docker", "ps"]:
                return 0, "".join(json.dumps([cid, project]) + "\n"
                                  for cid, (project, _) in self.running.items()), ""
            outs, errs = [], []
            for cid in args[6:]:
                if cid not in self.running:
                    errs.append(_gone(cid))
                    self.gone += 1
                elif args[5] == '{"id":{{json .Id}}}':
                    outs.append(json.dumps({"id": cid}))
                else:
                    outs.append(_item(cid, self.running[cid][1], self.running[cid][0]))
            return (1 if errs else 0), "".join(o + "\n" for o in outs), "".join(e + "\n" for e in errs)

    def recreate(self, project, port, sidecars):
        """compose_recreate: remove each container, then start a new one."""
        for cid in [c for c, (p, _) in list(self.running.items()) if p == project]:
            with self.lock:
                self.running.pop(cid, None)
            time.sleep(0.004)  # a removal lands inside the other side's scan
        self.start(project, port)
        for index in range(sidecars):
            self.start(project, port + 100 + index)


class ParallelApplyTests(unittest.TestCase):
    """Two applies on different targets of one host, each recreating its stack."""

    def test_two_parallel_applies_both_observe_and_claim_their_ports(self):
        docker = _Docker()
        # Separate port bands: overlapping claims are a different rule (below).
        targets = {"sandbox-host-a-env": 20001, "sandbox-host-b-env": 30001}
        for project, port in targets.items():
            docker.start(project, port)
            for index in range(6):
                docker.start(project, port + 100 + index)
        barrier = threading.Barrier(2)
        results, errors = {}, []

        def apply(project, port):
            probe = _probe()
            probe["attempt"] = docker.attempt
            owner = {"project": project, "service": "web", "container_port": 80}
            try:
                barrier.wait(5)
                for _ in range(5):  # repeated applies keep the other side churning
                    publications = probe["settled_scan"]([project])
                    snapshot = ports.validate_snapshot(PortProbeScanTests.snapshot(publications, []))
                    ports.check_pair(snapshot, port, owner)
                    docker.recreate(project, port, 6)
                results[project] = True
            except Exception as exc:  # pragma: no cover - reported below
                errors.append((project, repr(exc)))

        threads = [threading.Thread(target=apply, args=item) for item in targets.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), sorted(targets))
        self.assertGreater(docker.gone, 0, "the applies never interleaved; nothing was tested")


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

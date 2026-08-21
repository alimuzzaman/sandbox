import json
import unittest


class TestIsolationPreflight(unittest.TestCase):
    def facts(self):
        return {"os_id": "ubuntu", "version_id": "24.04", "systemd_version": 255}

    def test_every_effective_gate_is_required_and_code_presence_does_not_advertise(self):
        from sandbox.isolation.preflight import IsolationPreflight
        preflight = IsolationPreflight(
            facts=self.facts, command_probe=lambda _command: True,
            effective_probe=lambda _gate: True,
        ).inspect()
        self.assertTrue(preflight["ok"])
        self.assertFalse(preflight["adoptable"])
        gates = {item["gate"] for item in preflight["checks"]}
        for required in ("cgroup_v2", "cgroup_delegation", "user_namespaces",
                         "private_network", "nftables", "apparmor_enforcing", "seccomp"):
            self.assertIn(required, gates)

    def test_one_missing_effective_gate_blocks_before_mutation(self):
        from sandbox.isolation.preflight import IsolationPreflight
        result = IsolationPreflight(
            facts=self.facts, command_probe=lambda _command: True,
            effective_probe=lambda gate: gate != "nftables",
        ).inspect()
        self.assertFalse(result["ok"]); self.assertFalse(result["mutated"])
        self.assertEqual(result["reason"]["code"], "isolation_prerequisite_missing")
        self.assertIn("nftables", result["reason"]["missing"])

    def test_wrong_distribution_version_or_missing_binary_blocks(self):
        from sandbox.isolation.preflight import IsolationPreflight
        result = IsolationPreflight(
            facts=lambda: {"os_id": "ubuntu", "version_id": "22.04", "systemd_version": 249},
            command_probe=lambda command: command != "bwrap", effective_probe=lambda _gate: True,
        ).inspect()
        self.assertFalse(result["ok"])
        self.assertIn("platform", result["reason"]["missing"])
        self.assertIn("command:bwrap", result["reason"]["missing"])

    def test_privileged_effective_gates_use_one_strict_batch_per_inspection(self):
        from sandbox.application.context import native_isolation_preflight

        class Process:
            def __init__(self, stdout, returncode=0):
                self.stdout = stdout; self.returncode = returncode; self.calls = []
            def run(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                return type("Result", (), {"returncode": self.returncode,
                                            "stdout": self.stdout})()

        probes = ("cgroup-delegation", "private-network", "nftables", "seccomp")

        def document(states=None):
            states = states or {probe: "ready" for probe in probes}
            rows = [{"ok": states[probe] == "ready", "probe": probe,
                     "state": states[probe]} for probe in probes]
            ok = all(row["ok"] for row in rows)
            return {"schema": "sandbox.native-helper-preflight/v1", "ok": ok,
                    "state": "ready" if ok else "blocked", "probes": rows}

        process = Process(json.dumps(document()))
        preflight = native_isolation_preflight(
            {}, process=process, helper="/fixed/helper", facts=self.facts,
            command_probe=lambda _command: True,
        )
        first = preflight.inspect()
        privileged = {item["gate"]: item["observed"] for item in first["checks"]
                      if item["gate"] in {
                          "cgroup_delegation", "private_network", "nftables", "seccomp",
                      }}
        self.assertEqual(privileged, {
            "cgroup_delegation": True, "private_network": True,
            "nftables": True, "seccomp": True,
        })
        self.assertTrue(all(type(value) is bool for value in privileged.values()))
        self.assertEqual(process.calls, [
            (("sudo", "-n", "/fixed/helper", "preflight-probes"), {"timeout": 20}),
        ])
        preflight.inspect()
        self.assertEqual(len(process.calls), 2)

        states = {probe: "ready" for probe in probes}
        states["nftables"] = "failed"
        process = Process(json.dumps(document(states)), returncode=69)
        result = native_isolation_preflight(
            {}, process=process, helper="/fixed/helper", facts=self.facts,
            command_probe=lambda _command: True,
        ).inspect()
        privileged = {item["gate"]: item["observed"] for item in result["checks"]
                      if item["gate"] in {
                          "cgroup_delegation", "private_network", "nftables", "seccomp",
                      }}
        self.assertEqual(privileged, {
            "cgroup_delegation": True, "private_network": True,
            "nftables": False, "seccomp": True,
        })

    def test_malformed_or_transport_failed_batch_fails_all_privileged_gates(self):
        from sandbox.application.context import _native_helper_preflight_results

        probes = ("cgroup-delegation", "private-network", "nftables", "seccomp")

        def document():
            return {"schema": "sandbox.native-helper-preflight/v1", "ok": True,
                    "state": "ready", "probes": [
                        {"ok": True, "probe": probe, "state": "ready"}
                        for probe in probes
                    ]}

        class Process:
            def __init__(self, stdout, returncode=0, error=None):
                self.stdout = stdout; self.returncode = returncode; self.error = error
            def run(self, _argv, **_kwargs):
                if self.error:
                    raise self.error
                return type("Result", (), {"returncode": self.returncode,
                                            "stdout": self.stdout})()

        malformed = []
        extra = document(); extra["extra"] = True; malformed.append((extra, 0))
        missing = document(); del missing["schema"]; malformed.append((missing, 0))
        missing_probe = document(); missing_probe["probes"].pop()
        malformed.append((missing_probe, 0))
        reordered = document()
        reordered["probes"] = list(reversed(reordered["probes"]))
        malformed.append((reordered, 0))
        duplicate = document()
        duplicate["probes"][1] = dict(duplicate["probes"][0])
        malformed.append((duplicate, 0))
        probe_extra = document(); probe_extra["probes"][0]["extra"] = True
        malformed.append((probe_extra, 0))
        wrong_bool = document(); wrong_bool["probes"][0]["ok"] = 1
        malformed.append((wrong_bool, 0))
        wrong_aggregate_type = document(); wrong_aggregate_type["ok"] = 1
        malformed.append((wrong_aggregate_type, 0))
        bad_state = document()
        bad_state["probes"][0].update(ok=False, state="invalid")
        malformed.append((bad_state, 69))
        bad_state_type = document(); bad_state_type["probes"][0]["state"] = []
        malformed.append((bad_state_type, 0))
        contradiction = document(); contradiction["probes"][0]["ok"] = False
        malformed.append((contradiction, 0))
        aggregate = document(); aggregate.update(ok=False, state="blocked")
        malformed.append((aggregate, 69))
        for value, returncode in malformed:
            with self.subTest(value=value):
                self.assertEqual(
                    set(_native_helper_preflight_results(
                        Process(json.dumps(value), returncode), "/fixed/helper",
                    ).values()),
                    {False},
                )
        for process in (
                Process("not-json"),
                Process(json.dumps(document()) + " trailing"),
                Process(json.dumps(document()), returncode=69),
                Process(json.dumps(document()).encode()),
                Process("", error=OSError("transport")),
                Process("", error=RuntimeError("adapter transport"))):
            with self.subTest(process=process):
                self.assertEqual(
                    set(_native_helper_preflight_results(process, "/fixed/helper").values()),
                    {False},
                )


if __name__ == "__main__": unittest.main()

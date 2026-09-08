import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting.images.activation import private_containment


PROJECT = "lenzora"
TARGET = {"machine_identity": "machine-a", "target_identity": "target-a",
          "daemon_identity": "daemon-a"}


def rows():
    return [
        {"Id": "1" * 64, "Image": "sha256:" + "a" * 64,
         "Config": {"Labels": {"com.docker.compose.project": PROJECT}},
         "State": {"Paused": False, "Restarting": False, "Pid": 123,
                    "Running": True, "Status": "running"},
         "HostConfig": {"RestartPolicy": {"Name": "always", "MaximumRetryCount": 0}},
         "Mounts": [{"Type": "volume", "Name": "lenzora_data",
                     "Destination": "/var/lib/postgresql/data"}]},
        {"Id": "2" * 64, "Image": "sha256:" + "b" * 64,
         "Config": {"Labels": {"com.docker.compose.project": PROJECT}},
         "State": {"Paused": False, "Restarting": False, "Pid": 0,
                    "Running": False, "Status": "exited"},
         "HostConfig": {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
         "Mounts": []},
    ]


class DockerFixture:
    def __init__(self):
        self.rows = rows()
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return b"daemon-a"
        if argv[:2] == ["docker", "ps"]:
            return ("\n".join(sorted(row["Id"] for row in self.rows)) + "\n").encode()
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps(self.rows, sort_keys=True).encode()
        if argv[:3] == ["docker", "update", "--restart=no"]:
            identity = argv[3]
            next(row for row in self.rows if row["Id"] == identity)["HostConfig"]["RestartPolicy"]["Name"] = "no"
            return b""
        if argv[:3] == ["docker", "stop", "--time"]:
            identity = argv[4]
            row = next(row for row in self.rows if row["Id"] == identity)
            row["State"].update(Pid=0, Running=False, Status="exited")
            return b""
        raise AssertionError(f"unexpected docker command: {argv}")


def frame(operation="plan", containers=None):
    return {
        "target": TARGET,
        "compose_project": PROJECT,
        "transaction_digest": "sha256:" + "c" * 64,
        "generation": 0,
        "binding_key": base64.b64encode(b"k" * 32).decode("ascii"),
        "operation": operation,
        "containers": containers or [],
    }


class PrivateContainmentTests(unittest.TestCase):
    def test_unstable_state_returns_closed_reason_without_any_write(self):
        for field, value, code in (('Paused', True, 'container_paused'),
                ('Restarting', True, 'container_restarting'), ('Pid', -1, 'container_state_invalid')):
            with self.subTest(field=field):
                docker = DockerFixture(); docker.rows[0]['State'][field] = value
                self.assertEqual(self._run_main(frame(), docker), {'ok': False, 'code': code})
                self.assertFalse(any(call[1] in {'update', 'stop'} for call in docker.calls))

    def setUp(self):
        self.process_binding = patch.object(
            private_containment, "_process_binding",
            return_value={"pid": 123, "started": 1, "cgroup_digest": "a" * 64})
        self.process_binding.start()
        self.addCleanup(self.process_binding.stop)

    def _run_main(self, value, docker):
        stream = io.StringIO()
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(value).encode()))
        with patch.object(private_containment, "graph_command_port",
                          return_value=(docker, 2**63), create=True), \
                patch("sys.stdin", stdin), patch("sys.stdout", stream):
            private_containment.main()
        return json.loads(stream.getvalue())

    def test_plan_then_apply_rechecks_exact_containers_and_only_disables_restart_and_stops(self):
        docker = DockerFixture()
        expected = private_containment.snapshot(frame(), docker)
        planned = self._run_main(frame("plan", expected), docker)
        self.assertEqual(planned["code"], "planned")
        applied = self._run_main(frame("apply", expected), docker)
        self.assertEqual(applied["code"], "contained")
        writes = [argv for argv in docker.calls if argv[:2] == ["docker", "update"]
                  or argv[:2] == ["docker", "stop"]]
        self.assertEqual(writes, [
            ["docker", "update", "--restart=no", "1" * 64],
            ["docker", "stop", "--time", "30", "1" * 64],
        ])
        self.assertFalse(any(any(word in {"rm", "kill", "volume", "delete"} for word in argv)
                             for argv in docker.calls))
        self.assertEqual([row["container_id"] for row in applied["containers"]], ["1" * 64, "2" * 64])

    def test_apply_refuses_changed_or_foreign_identity_before_any_write(self):
        docker = DockerFixture()
        expected = private_containment.snapshot(frame(), docker)
        forged = [dict(expected[0], binding_digest="sha256:" + "f" * 64), expected[1]]
        result = self._run_main(frame("apply", forged), docker)
        self.assertEqual(result, {"code": "evidence_changed", "ok": False})
        self.assertFalse(any(argv[:2] in (["docker", "update"], ["docker", "stop"])
                             for argv in docker.calls))

        foreign = DockerFixture()
        foreign.rows[1]["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            private_containment.snapshot(frame(), foreign)

    def test_invalid_operation_and_binding_key_are_neutral_refusals(self):
        docker = DockerFixture()
        invalid_operation = frame("delete", [])
        self.assertEqual(self._run_main(invalid_operation, docker),
                         {"code": "evidence_changed", "ok": False})
        invalid_key = frame("plan", [])
        invalid_key["binding_key"] = base64.b64encode(b"short").decode("ascii")
        self.assertEqual(self._run_main(invalid_key, docker),
                         {"code": "evidence_changed", "ok": False})


if __name__ == "__main__":
    unittest.main()

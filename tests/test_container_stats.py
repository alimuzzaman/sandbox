import json
import unittest

from sandbox.services.container_stats import local_container_stats, parse_container_stats
from sandbox.services.process import ProcessResult


class FakeRunner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def run(self, argv, *, cwd=None, env=None, timeout=None):
        self.calls.append((tuple(argv), timeout))
        return self.results.pop(0)


def result(argv, returncode=0, stdout="", stderr=""):
    return ProcessResult(tuple(argv), returncode, stdout, stderr)


class ContainerStatsTests(unittest.TestCase):
    def test_parser_returns_typed_rows_and_partial_malformed_evidence(self):
        output = "\n".join((
            json.dumps({"Name": "sandbox-demo-wp-1", "CPUPerc": "1.25%",
                        "MemUsage": "128.5MiB / 2GiB", "MemPerc": "6.27%", "PIDs": "14"}),
            "not-json",
        ))

        parsed = parse_container_stats(output)

        self.assertEqual(parsed["status"], "partial")
        self.assertEqual(parsed["malformed_count"], 1)
        self.assertEqual(parsed["rows"][0], {
            "name": "sandbox-demo-wp-1", "cpu_percent": 1.25,
            "memory_used_bytes": int(128.5 * 1024**2), "memory_percent": 6.27,
            "pids": 14,
        })

    def test_snapshot_uses_bounded_argv_and_selected_project_containers(self):
        container_id = "a" * 12
        runner = FakeRunner(
            result(("docker", "ps"), stdout=container_id + "\n"),
            result(("docker", "stats"), stdout=json.dumps({
                "Name": "sandbox-demo-db-1", "CPUPerc": "0.00%",
                "MemUsage": "64MiB / 2GiB", "MemPerc": "3.12%", "PIDs": "8",
            }) + "\n"),
        )

        snapshot = local_container_stats("demo", runner=runner, timeout=3)

        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["rows"][0]["pids"], 8)
        self.assertEqual(runner.calls[0], ((
            "docker", "ps", "--filter",
            "label=com.docker.compose.project=sandbox-demo", "--format", "{{.ID}}",
        ), 3))
        self.assertEqual(runner.calls[1][0], (
            "docker", "stats", "--no-stream", "--format", "{{json .}}", container_id,
        ))

    def test_timeout_is_typed_and_non_fatal(self):
        runner = FakeRunner(result(("docker", "ps"), returncode=124,
                                   stderr="process timed out"))

        snapshot = local_container_stats("demo", runner=runner)

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["error"], {"code": "docker_timeout"})

    def test_unavailable_docker_is_typed_and_non_fatal(self):
        runner = FakeRunner(result(("docker", "ps"), returncode=1,
                                   stderr="daemon unavailable"))

        snapshot = local_container_stats("demo", runner=runner)

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["error"], {"code": "docker_unavailable"})
        self.assertNotIn("daemon unavailable", json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()

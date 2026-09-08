"""Linux acceptance canary for the complete candidate-v2 activation graph.

This module is intentionally outside normal test discovery. Run it on the
registered Linux host that already owns the supplied immutable image digest.
The graph is synthetic but uses the real ordered execution driver and private
Docker graph helper. It never pulls or builds an image and never prints the
synthetic secret.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
import time
import unittest
import uuid

from tests.subprocess_support import run_test_process, synthetic_environment


_IMAGE = re.compile(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[0-9a-f]{64}\Z")
_CANARY_SECRET = "synthetic-activation-topology-secret"
_APP_UID = 1000
_APP_GID = 1000
_CONFIGURATION_KEY = b"k" * 32


class _CanaryAdapter:
    """Adapter that routes each durable graph effect through private_graph."""

    def __init__(self, *, document, contract, image_identities, project,
                 project_directory, environment, configuration_key):
        self.document = document
        self.contract = contract
        self.image_identities = image_identities
        self.project = project
        self.project_directory = project_directory
        self.environment = environment
        self.configuration_key = configuration_key
        self.actions: list[tuple[str, str, tuple[str, ...], str | None]] = []
        self.ready_wait_seconds: list[float] = []

    def execute_graph_step_v2(self, *, action, subject, container_identity,
                              timeout_seconds):
        from sandbox.hosting.images.activation.private_graph import execute_private_graph

        self.actions.append((action, subject["kind"], tuple(subject["services"]),
                             container_identity))
        started = time.monotonic()
        result = execute_private_graph(
            source={
                "subject": subject,
                "action": action,
                "container_identity": container_identity,
                "project_name": self.project,
                "project_directory": self.project_directory,
                "input_contract": "candidate-v2",
                "image_identities": self.image_identities,
                "execution_contract": self.contract.as_mapping(),
                "render_digest": self.contract.declarations[0].config_digest,
            },
            document=self.document,
            environment=self.environment,
            configuration_key=self.configuration_key,
            timeout_seconds=timeout_seconds,
        )
        if action == "ready":
            self.ready_wait_seconds.append(time.monotonic() - started)
        return result


class HostingActivationTopologyCanaryTests(unittest.TestCase):
    """Prove cold prerequisite, ordered init, delayed health, and fencing."""

    image: str | None = None
    uid: int = _APP_UID
    gid: int = _APP_GID

    def _docker(self) -> str:
        import sys

        if not sys.platform.startswith("linux"):
            self.fail("topology canary requires Linux")
        docker = shutil.which("docker")
        if not docker:
            self.fail("Docker CLI is unavailable")
        env = synthetic_environment({"PATH": f"{Path(docker).parent}:/usr/bin:/bin"})
        daemon = run_test_process(
            (docker, "version", "--format", "{{.Server.Os}}"), env=env,
            timeout=15, capture_output=True, text=True)
        if daemon.returncode != 0 or daemon.stdout.strip() != "linux":
            self.fail("a Linux Docker daemon is unavailable")
        compose = run_test_process(
            (docker, "compose", "version", "--short"), env=env,
            timeout=15, capture_output=True, text=True)
        if compose.returncode != 0 or not compose.stdout.strip():
            self.fail("Docker Compose is unavailable")
        return docker

    def _env(self, docker: str, *, secret: bool = True) -> dict[str, str]:
        values = {"PATH": f"{Path(docker).parent}:/usr/bin:/bin"}
        if secret:
            # This remains a private child environment. It is never placed in
            # an argv, receipt, assertion message, or captured output.
            values["SANDBOX_ACTIVATION_SECRET_0"] = _CANARY_SECRET
        return dict(synthetic_environment(values))

    def _image_identity(self, docker: str) -> dict[str, str]:
        image = self.image
        if image is None or _IMAGE.fullmatch(image) is None:
            self.fail("--image must be a lower-case immutable repository digest")
        result = run_test_process(
            (docker, "image", "inspect", image), env=self._env(docker, secret=False),
            timeout=20, capture_output=True, text=True)
        if result.returncode != 0:
            self.fail("the requested immutable canary image is not present locally")
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("local image inspection was not valid JSON")
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            self.fail("local image inspection was not singular")
        row = rows[0]
        local_id = row.get("Id")
        if (type(local_id) is not str or _IDENTITY.fullmatch(local_id.removeprefix("sha256:")) is None
                or row.get("Os") != "linux" or row.get("Architecture") != "amd64"
                or row.get("Variant") or type(row.get("RepoDigests")) is not list
                or row["RepoDigests"].count(image) != 1):
            self.fail("local image identity does not match the requested digest")
        return {"image_ref": image, "local_image_id": local_id,
                "config_digest": local_id}

    def _document(self, image: str, *, failing: bool = False) -> dict:
        def service(command: str, *, health: str | None = None) -> dict:
            row = {
                "image": image,
                "pull_policy": "never",
                "entrypoint": ["/bin/sh"],
                "command": ["-ec", command],
                "user": f"{self.uid}:{self.gid}",
                "network_mode": "none",
                "restart": "no",
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges"],
                "labels": {"sandbox.activation-topology-canary": "pending"},
                "secrets": [{"source": "token", "target": "token",
                              "uid": str(self.uid), "gid": str(self.gid),
                              "mode": "0400"}],
            }
            if health is not None:
                row["healthcheck"] = {
                    "test": ["CMD-SHELL", f"test -f {health}"],
                    "interval": "1s", "timeout": "1s", "retries": 20,
                }
            return row

        document = {
            "services": {
                "queue": service(
                    "test \"$(id -u)\" = \"1000\"; test -r /run/secrets/token; "
                    "sleep 2; touch /tmp/queue-ready; exec sleep 300",
                    health="/tmp/queue-ready"),
                "migrate": service(
                    "test \"$(id -u)\" = \"1000\"; test -r /run/secrets/token; exit 0"),
                "storage": service(
                    "test \"$(id -u)\" = \"1000\"; test -r /run/secrets/token; "
                    + ("exit 17" if failing else "exit 0")),
                "topology": service(
                    "test \"$(id -u)\" = \"1000\"; test -r /run/secrets/token; exit 0"),
                "web": service(
                    "test \"$(id -u)\" = \"1000\"; test -r /run/secrets/token; "
                    "sleep 2; touch /tmp/web-ready; exec sleep 300",
                    health="/tmp/web-ready"),
                "worker": service(
                    "test \"$(id -u)\" = \"1000\"; test -r /run/secrets/token; "
                    "sleep 2; touch /tmp/worker-ready; exec sleep 300",
                    health="/tmp/worker-ready"),
            },
            "secrets": {"token": {"environment": "SANDBOX_ACTIVATION_SECRET_0"}},
        }
        document["services"]["migrate"]["depends_on"] = {
            "queue": {"condition": "service_healthy"}}
        document["services"]["storage"]["depends_on"] = {
            "migrate": {"condition": "service_completed_successfully"}}
        document["services"]["topology"]["depends_on"] = {
            "storage": {"condition": "service_completed_successfully"}}
        for name in ("web", "worker"):
            document["services"][name]["depends_on"] = {
                "topology": {"condition": "service_completed_successfully"}}
        return document

    def _contract(self, image: str, image_id: str):
        from sandbox.hosting.images.activation.v2_models import (
            InitDeclarationV2, InitExecutionContractV2, RuntimeExecutionGraphV2,
        )

        target = {"machine_identity": "canary-machine", "target_identity": "canary-target",
                  "daemon_identity": "canary-daemon"}
        snapshot_id = "compose-snapshot/topology-canary-" + uuid.uuid4().hex
        declarations = []
        dependencies = (
            {"service": "migrate", "dependency": "queue", "condition": "service_healthy"},
            {"service": "storage", "dependency": "migrate", "condition": "service_completed_successfully"},
            {"service": "topology", "dependency": "storage", "condition": "service_completed_successfully"},
            {"service": "web", "dependency": "topology", "condition": "service_completed_successfully"},
            {"service": "worker", "dependency": "topology", "condition": "service_completed_successfully"},
        )
        for index, (name, required) in enumerate(
                (("migrate", ("queue",)), ("storage", ("migrate",)),
                 ("topology", ("storage",)))):
            declarations.append(InitDeclarationV2.create(
                index=index, service=name, image="lenzora-worker",
                image_ref=image, config_digest=image_id,
                platform={"os": "linux", "architecture": "amd64"},
                timeout_seconds=120, environment_keys=(),
                dependency_services=required, target=target,
                snapshot_id=snapshot_id, configuration_digest=image_id))
        graph = RuntimeExecutionGraphV2.create(
            prerequisite_groups=(("queue",),),
            initializer_order=("migrate", "storage", "topology"),
            consumer_groups=(("web", "worker"),),
            dependencies=dependencies, readiness_timeout_seconds=120)
        return InitExecutionContractV2.create(
            declarations=tuple(declarations), graph=graph)

    def _candidate(self, root: Path, document: dict) -> Path:
        candidate = root / "candidate"
        candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
        secret = candidate / "secret-0"
        secret.write_bytes(_CANARY_SECRET.encode())
        secret.chmod(0o600)
        effective = candidate / "effective.json"
        effective.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))
        effective.chmod(0o600)
        return candidate

    def _empty(self, docker: str, project: str) -> None:
        result = run_test_process(
            (docker, "ps", "--all", "--quiet",
             "--filter", f"label=sandbox.activation-topology-canary={project}"),
            env=self._env(docker, secret=False), timeout=15,
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "", "canary did not start cold")

    def _cleanup(self, docker: str, project: str) -> None:
        env = self._env(docker, secret=False)
        listed = run_test_process(
            (docker, "ps", "--all", "--quiet", "--no-trunc", "--filter",
             f"label=sandbox.activation-topology-canary={project}"),
            env=env, timeout=15, capture_output=True, text=True)
        self.assertEqual(listed.returncode, 0)
        identities = [value for value in listed.stdout.split() if value]
        self.assertTrue(all(_IDENTITY.fullmatch(value) for value in identities),
                        "cleanup identity was not an exact Docker ID")
        for identity in identities:
            inspected = run_test_process((docker, "inspect", identity), env=env,
                                         timeout=15, capture_output=True, text=True)
            self.assertEqual(inspected.returncode, 0)
            rows = json.loads(inspected.stdout)
            self.assertEqual(len(rows), 1)
            labels = rows[0].get("Config", {}).get("Labels", {})
            self.assertEqual(labels.get("sandbox.activation-topology-canary"), project)
            self.assertEqual(rows[0].get("Mounts") or [], [],
                             "canary unexpectedly created a mount or volume")
            removed = run_test_process((docker, "rm", "--force", identity), env=env,
                                       timeout=20, capture_output=True, text=True)
            self.assertEqual(removed.returncode, 0)
        remaining = run_test_process(
            (docker, "ps", "--all", "--quiet", "--filter",
             f"label=sandbox.activation-topology-canary={project}"),
            env=env, timeout=15, capture_output=True, text=True)
        self.assertEqual(remaining.returncode, 0)
        self.assertEqual(remaining.stdout.strip(), "")
        volumes = run_test_process(
            (docker, "volume", "ls", "--quiet", "--filter",
             f"label=sandbox.activation-topology-canary={project}"),
            env=env, timeout=15, capture_output=True, text=True)
        self.assertEqual(volumes.returncode, 0)
        self.assertEqual(volumes.stdout.strip(), "")

    def _run_graph(self, docker: str, root: Path, *, failing: bool):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2
        from sandbox.hosting.images.activation.models import ActivationContractError

        image = self.image
        assert image is not None
        image_identity = self._image_identity(docker)
        document = self._document(image, failing=failing)
        project = "sandbox-activation-topology-" + uuid.uuid4().hex
        for row in document["services"].values():
            row["labels"]["sandbox.activation-topology-canary"] = project
        candidate_root = root / ("failure" if failing else "success")
        candidate_root.mkdir(mode=0o700)
        candidate = self._candidate(candidate_root, document)
        contract = self._contract(image, image_identity["local_image_id"])
        identities = {name: dict(image_identity)
                      for name in ("queue", "web", "worker")}
        adapter = _CanaryAdapter(
            document=document, contract=contract, image_identities=identities,
            project=project, project_directory=str(candidate),
            environment=self._env(docker), configuration_key=_CONFIGURATION_KEY)
        progress = ExecutionProgressV2.create(
            graph=contract.graph, request_digest="sha256:" + "a" * 64,
            snapshot_digest="sha256:" + "b" * 64)
        self._empty(docker, project)
        saved = []
        try:
            if failing:
                with self.assertRaises(ActivationContractError) as caught:
                    execute_graph_v2(progress=progress, contract=contract,
                                     adapter=adapter, persist=saved.append)
                self.assertIn("init_mismatch", str(caught.exception))
                self.assertTrue(saved)
                retained = saved[-1]
                self.assertTrue(retained.failed)
                self.assertEqual(retained.events[-1]["stage"], "cleaned")
                self.assertEqual(
                    [row[2][0] for row in adapter.actions if row[0] == "start"],
                    ["migrate", "storage"])
                replay = _CanaryAdapter(
                    document=document, contract=contract, image_identities=identities,
                    project=project, project_directory=str(candidate),
                    environment=self._env(docker), configuration_key=_CONFIGURATION_KEY)
                with self.assertRaises(ActivationContractError):
                    execute_graph_v2(progress=retained, contract=contract,
                                     adapter=replay, persist=lambda _value: None)
                self.assertEqual(replay.actions, [])
                return
            result = execute_graph_v2(progress=progress, contract=contract,
                                      adapter=adapter, persist=saved.append)
            self.assertTrue(result.complete)
            self.assertEqual(
                [row[2][0] for row in adapter.actions if row[0] == "start"],
                ["migrate", "storage", "topology"])
            self.assertEqual(adapter.actions[0][0:3],
                             ("replace", "prerequisite", ("queue",)))
            self.assertEqual(adapter.actions[-1][0:3],
                             ("ready", "consumer", ("web", "worker")))
            self.assertTrue(adapter.ready_wait_seconds)
            self.assertTrue(any(seconds >= 1 for seconds in adapter.ready_wait_seconds),
                            "delayed health did not require a bounded readiness wait")
            exited = [event for event in result.events if event["stage"] == "exited"]
            self.assertEqual(len(exited), 3)
            self.assertTrue(all(event["exit_code"] == 0 for event in exited))
        finally:
            self._cleanup(docker, project)

    def test_cold_topology_and_failed_initializer_fence_replay(self):
        docker = self._docker()
        with tempfile.TemporaryDirectory(prefix="sandbox-activation-topology-canary-") as directory:
            root = Path(directory).resolve()
            self._run_graph(docker, root, failing=False)
            self._run_graph(docker, root, failing=True)


if __name__ == "__main__":  # pragma: no cover - explicit acceptance entrypoint
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True,
                        help="already-present immutable repository digest")
    parser.add_argument("--uid", type=int, default=_APP_UID,
                        help="application UID (the canary contract defaults to 1000)")
    parser.add_argument("--gid", type=int, default=_APP_GID,
                        help="application GID (the canary contract defaults to 1000)")
    parsed, unittest_argv = parser.parse_known_args()
    if (not _IMAGE.fullmatch(parsed.image) or parsed.uid != _APP_UID
            or parsed.gid != _APP_GID):
        parser.error("--image must be an immutable digest and UID/GID must be 1000")
    HostingActivationTopologyCanaryTests.image = parsed.image
    HostingActivationTopologyCanaryTests.uid = parsed.uid
    HostingActivationTopologyCanaryTests.gid = parsed.gid
    unittest.main(argv=[__file__, *unittest_argv])

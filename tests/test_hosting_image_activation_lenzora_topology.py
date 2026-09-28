import json
import subprocess
import unittest
from unittest.mock import patch

from tests.fixtures.hosting_image_activation import lenzora_compose_fixture
from tests.test_hosting_image_activation_execution import graph_request_fixture
from tests.test_hosting_image_activation_v2 import (
    FakeEdgeV2, FakeHostStatePort, FakeRuntimeV2, FakeStageRepositoryPort,
    FakeTargetMutationPort, FakeGrantVerifier,
)


class LenzoraGraphRuntime(FakeRuntimeV2):
    """Fixture-local graph transport with explicit receipt failure controls."""

    def __init__(self, proof=None, *, fail_service=None, fail_ack_at=None):
        super().__init__(proof)
        self.actions = []
        self.fail_service = fail_service
        self.fail_ack_at = fail_ack_at

    def execute_graph_step_v2(self, *, action, subject, container_identity,
                              timeout_seconds):
        action_index = len(self.actions)
        self.actions.append((action, subject["kind"], tuple(subject["services"])))
        identity = container_identity
        if subject["kind"] == "initializer" and action == "create":
            identity = "container-" + subject["services"][0]
        if subject["kind"] == "initializer" and identity is None:
            identity = "container-" + subject["services"][0]
        failed = self.fail_service in subject["services"] and action == "wait"
        receipt = {
            "subject_digest": subject["subject_digest"],
            "container_identity": identity if subject["kind"] == "initializer" else None,
            "exit_code": 17 if failed else (0 if action == "wait" else None),
            "terminated": True,
        }
        if action_index == self.fail_ack_at:
            raise OSError("lost graph acknowledgement")
        return receipt


class LenzoraTopologyTests(unittest.TestCase):
    def test_actual_codecs_bind_full_lenzora_graph_and_keep_private_inputs_private(self):
        plan, proof, _legacy, snapshot, grant, request, contract = graph_request_fixture()
        fixture = lenzora_compose_fixture(plan)
        graph = contract.graph

        self.assertEqual(len(plan.policy.persistent_services), 17)
        self.assertEqual(len(graph.persistent_services), 17)
        self.assertEqual(graph.prerequisite_groups, (("lenzora-job-queue",),))
        self.assertEqual(graph.initializer_order, (
            "lenzora-migrate", "lenzora-storage-init",
            "lenzora-job-queue-topology-gate"))
        self.assertEqual(sum(len(group) for group in graph.consumer_groups), 16)
        self.assertEqual(len(contract.declarations), 3)
        self.assertEqual(snapshot.target, proof.target.as_mapping())
        self.assertEqual(grant.compose_snapshot_digest, snapshot.snapshot_digest)
        self.assertEqual(request.compose_snapshot.init_contract, contract)

        dependencies = {(row["service"], row["dependency"], row["condition"])
                        for row in graph.dependencies}
        self.assertIn(("lenzora-job-queue-topology-gate", "lenzora-job-queue",
                      "service_healthy"), dependencies)
        for service in ("lenzora-web", "lenzora-job-worker",
                        "lenzora-webhook-delivery-worker"):
            self.assertEqual(
                fixture["compose"]["services"][service]["secrets"],
                [{"source": "worker-token", "target": "worker-token"}],
            )
        self.assertNotIn("environment", snapshot.init_contract.as_mapping())
        self.assertNotIn("secrets", snapshot.init_contract.as_mapping())

    def test_full_service_runs_queue_three_initializers_then_all_consumers(self):
        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.images.activation.v2_service import ActivationServiceV2

        _plan, proof, _legacy, _snapshot, grant, request, contract = graph_request_fixture()
        host = FakeHostStatePort()
        repository = ActivationRepository(
            host_state_port=host,
            stage_repository=FakeStageRepositoryPort(),
            target_mutation_port=FakeTargetMutationPort(),
        )
        runtime = LenzoraGraphRuntime(proof)

        result = ActivationServiceV2(
            repository=repository, runtime_adapter=runtime,
            edge_adapter=FakeEdgeV2(), rollback_grant_verifier=FakeGrantVerifier(),
            clock=lambda: 100,
        ).execute(
            request, rollback_grant=grant, compose_files=("compose.yml",),
            compose_project="lenzora", edge_route_digest="sha256:" + "a" * 64,
            admission_deadline="2999-01-01T00:00:00Z",
            stage_ledger_authority="feature-050-stage-ledger-v2",
            stage_ledger_revision=1,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(host.state["generation"], 1)
        expected_prefix = [
            ("replace", "prerequisite", ("lenzora-job-queue",)),
            ("ready", "prerequisite", ("lenzora-job-queue",)),
        ]
        self.assertEqual(runtime.actions[:2], expected_prefix)
        self.assertEqual(
            [services[0] for action, kind, services in runtime.actions if action == "start"],
            list(contract.graph.initializer_order),
        )
        self.assertEqual(runtime.actions[-1][0:2], ("ready", "consumer"))
        self.assertEqual(set(runtime.actions[-1][2]), set(contract.graph.persistent_services) -
                         {"lenzora-job-queue"})
        progress = host.state["current"]["execution_evidence"]["progress"]
        self.assertEqual(progress["events"][-1]["stage"], "ready")

    def test_nonzero_initializer_exit_cleans_that_job_and_fences_later_graph_steps(self):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2

        _plan, _proof, _legacy, snapshot, _grant, request, contract = graph_request_fixture()
        progress = ExecutionProgressV2.create(
            graph=contract.graph, request_digest=request.request_digest,
            snapshot_digest=snapshot.snapshot_digest,
        )
        saved = []
        runtime = LenzoraGraphRuntime(fail_service="lenzora-storage-init")

        with self.assertRaises(ValueError):
            execute_graph_v2(
                progress=progress, contract=contract, adapter=runtime,
                persist=saved.append,
            )
        self.assertEqual(
            [services[0] for action, _kind, services in runtime.actions if action == "start"],
            ["lenzora-migrate", "lenzora-storage-init"],
        )
        self.assertEqual(runtime.actions[-1][0], "cleanup")
        self.assertEqual(saved[-1].events[-1]["stage"], "cleaned")
        self.assertTrue(saved[-1].failed)
        self.assertFalse(any(action == "replace" and services != ("lenzora-job-queue",)
                             for action, _kind, services in runtime.actions))
        self.assertNotIn("lenzora-job-queue-topology-gate", {
            service for _action, _kind, services in runtime.actions for service in services
        })

    def test_duplicate_initializer_container_is_refused_without_reuse_or_cleanup(self):
        from sandbox.hosting.images.activation.private_graph import create_init_container

        plan, proof, _legacy, _snapshot, _grant, _request, contract = graph_request_fixture()
        fixture = lenzora_compose_fixture(plan)
        declaration = contract.declarations[0].as_mapping()
        image = next(row for row in proof.observation.images
                     if row.repo_digest == declaration["image_ref"])
        calls = []
        with self.assertRaisesRegex(ValueError, "graph_container_collision"):
            create_init_container(
                document=fixture["compose"], declaration=declaration,
                project="lenzora", project_directory="/private/candidate",
                owner="a" * 64, container_name="sandbox-init-" + "a" * 32,
                image={"Id": image.local_image_id, "Os": "linux",
                       "Architecture": "amd64", "RepoDigests": [image.repo_digest]},
                command=lambda *args, **kwargs: calls.append(args),
                find=lambda _name: ["f" * 64], inspect=lambda _identity: {},
            )
        self.assertEqual(calls, [])

    def test_lost_graph_acknowledgement_never_replays_a_possible_effect(self):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2

        _plan, _proof, _legacy, snapshot, _grant, request, contract = graph_request_fixture()
        initial = ExecutionProgressV2.create(
            graph=contract.graph, request_digest=request.request_digest,
            snapshot_digest=snapshot.snapshot_digest,
        )

        total_actions = 2 + 3 * 5 + 2
        for fail_at in range(total_actions):
            with self.subTest(fail_at=fail_at):
                runtime = LenzoraGraphRuntime(fail_ack_at=fail_at)
                saved = []
                with self.assertRaises(OSError):
                    execute_graph_v2(
                        progress=initial, contract=contract, adapter=runtime,
                        persist=saved.append,
                    )
                self.assertTrue(saved)
                retained = saved[-1]
                replay = LenzoraGraphRuntime(fail_ack_at=-1)
                with self.assertRaises(ValueError):
                    execute_graph_v2(
                        progress=retained, contract=contract, adapter=replay,
                        persist=lambda _value: None,
                    )
                self.assertEqual(replay.actions, [])

    def test_delayed_readiness_polls_without_repeating_compose_up_for_full_consumer_group(self):
        from sandbox.hosting.images.activation.private_graph import execute_private_graph

        plan, _proof, _legacy, _snapshot, _grant, _request, contract = graph_request_fixture()
        fixture = lenzora_compose_fixture(plan)
        services = tuple(contract.graph.consumer_groups[0])
        image_rows = {row["service"]: row for row in plan.as_mapping()["service_image_bindings"]}
        images = {row["name"]: row for row in plan.as_mapping()["images"]}
        proof_images = {row.repo_digest: row for row in _proof.observation.images}
        image_identities = {
            service: {
                "image_ref": images[image_rows[service]["image"]]["image_ref"],
                "config_digest": proof_images[
                    images[image_rows[service]["image"]]["image_ref"]].config_digest,
                "local_image_id": proof_images[
                    images[image_rows[service]["image"]]["image_ref"]].local_image_id,
            }
            for service in services
        }
        graph = {**contract.graph.as_mapping(), "readiness_timeout_seconds": 60}
        source = {
            "subject": {"kind": "consumer", "services": list(services),
                        "subject_digest": "sha256:" + "c" * 64},
            "action": "replace", "container_identity": None,
            "project_name": "lenzora", "project_directory": "/private/candidate",
            "image_identities": image_identities,
            "execution_contract": {"graph": graph},
        }
        calls = []
        inspect_count = {"value": 0}
        config_hashes = {}
        ids = {name: (f"{index + 1:064x}") for index, name in enumerate(services)}

        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["image", "inspect"]:
                ref = argv[-1]
                identity = next(item for item in image_identities.values()
                                if item["image_ref"] == ref)
                return subprocess.CompletedProcess(argv, 0, json.dumps([{
                    "Id": identity["local_image_id"], "Os": "linux",
                    "Architecture": "amd64", "RepoDigests": [identity["image_ref"]],
                }]).encode(), b"")
            if "config" in argv and "--hash" in argv:
                name = argv[-1]
                config_hashes[name] = "a" * 64
                return subprocess.CompletedProcess(argv, 0, f"{name} {'a' * 64}\n".encode(), b"")
            if "up" in argv:
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            if "ps" in argv:
                name = argv[-1]
                return subprocess.CompletedProcess(argv, 0, (ids[name] + "\n").encode(), b"")
            if argv[1] == "inspect":
                identity = argv[-1]
                service = next(name for name, value in ids.items() if value == identity)
                inspect_count["value"] += 1
                status = "healthy" if inspect_count["value"] > len(services) else "starting"
                image = image_identities[service]
                return subprocess.CompletedProcess(argv, 0, json.dumps([{
                    "Id": identity, "Image": image["local_image_id"],
                    "Config": {"Image": image["image_ref"], "Labels": {
                        "com.docker.compose.project": "lenzora",
                        "com.docker.compose.service": service,
                        "com.docker.compose.config-hash": config_hashes[service],
                    }},
                    "State": {"Status": "running", "Running": True,
                              "Health": {"Status": status}},
                }]).encode(), b"")
            self.fail(argv)

        def graph_port(_environment, _timeout):
            return (lambda argv, **_kwargs: run(argv).stdout), float("inf")

        with patch("sandbox.hosting.images.activation.private_graph.graph_command_port", side_effect=graph_port), patch("time.sleep"):
            source["action"] = "replace"
            execute_private_graph(
                source=source, document=fixture["compose"], environment={"PATH": "/synthetic/bin"},
                configuration_key=b"k" * 32, timeout_seconds=60,
            )
            source["action"] = "ready"
            receipt = execute_private_graph(
                source=source, document=fixture["compose"], environment={"PATH": "/synthetic/bin"},
                configuration_key=b"k" * 32, timeout_seconds=60,
            )
        self.assertEqual(len([argv for argv in calls if "up" in argv]), 1)
        self.assertEqual(inspect_count["value"], len(services) * 2)
        self.assertEqual(receipt["subject_digest"], source["subject"]["subject_digest"])


if __name__ == "__main__":
    unittest.main()

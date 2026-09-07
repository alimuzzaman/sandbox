import unittest

from sandbox.hosting.images.activation.models import activation_digest


def graph_request_fixture():
    from sandbox.hosting.images.activation.execution_graph import prepare_execution_contract
    from sandbox.hosting.images.activation.v2_models import PrivateComposeInputSnapshotV2, RollbackCompatibilityGrantV2
    from sandbox.hosting.images.activation.v2_repository import transaction_v2, validate_transaction_v2
    from tests.fixtures.hosting_image_activation import lenzora_compose_fixture
    from tests.test_hosting_image_activation_v2 import artifacts, grant_for, request_for, TARGET
    plan, proof, legacy = artifacts()
    fixture = lenzora_compose_fixture(plan)
    services = {name: {**row, "x-sandbox-environment-keys": sorted(row.get("environment", {})),
                      "depends_on": row.get("depends_on", {})}
                for name, row in fixture["compose"]["services"].items()}
    contract = prepare_execution_contract(plan=plan, services=services, target=TARGET,
        snapshot_id=legacy.snapshot_id, configuration_digest=legacy.configuration_digest,
        initializer_order=fixture["initializer_order"])
    body = legacy.body_mapping(); body.pop("schema_version")
    body["selected_services"] = tuple(body["selected_services"])
    snapshot = PrivateComposeInputSnapshotV2.create(**body, input_contract="candidate-v1", init_contract=contract)
    grant_body = grant_for(plan, proof).body_mapping()
    grant_body["compose_snapshot_digest"] = snapshot.snapshot_digest
    grant = RollbackCompatibilityGrantV2(**grant_body, grant_digest=activation_digest(
        "sandbox.hosting.images.rollback-grant.v2", grant_body))
    request = request_for(plan, proof, snapshot, grant)
    return plan, proof, legacy, snapshot, grant, request, contract


class ExecutionProgressTests(unittest.TestCase):
    def test_transaction_retains_exact_graph_snapshot_and_pre_forward_grant(self):
        from sandbox.hosting.images.activation.execution_graph import prepare_execution_contract
        from sandbox.hosting.images.activation.v2_models import PrivateComposeInputSnapshotV2, RollbackCompatibilityGrantV2
        from sandbox.hosting.images.activation.v2_repository import transaction_v2, validate_transaction_v2
        from tests.fixtures.hosting_image_activation import lenzora_compose_fixture
        from tests.test_hosting_image_activation_v2 import artifacts, grant_for, request_for, TARGET
        plan, proof, legacy, snapshot, grant, request, contract = graph_request_fixture()
        holder = "activation-owner/" + request.request_id
        pin = {"lease_id": "activation-lease/" + "a" * 48, "holder": holder, "phase": "accepted",
               "proof_digest": proof.proof_digest, "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        context = {"target": TARGET, "compose_project": "fixture", "selected_services": list(plan.policy.persistent_services),
                   "compose_snapshot": snapshot.as_mapping(), "compatibility_grant": grant.as_mapping()}
        transaction = transaction_v2(request, holder=holder, proof_pin=pin, recovery_context=context,
                                     prior_generation_digest=grant.prior_generation_digest)
        self.assertEqual(transaction["execution_progress"]["events"], [])
        self.assertEqual(transaction["recovery_context"], context)
        self.assertEqual(validate_transaction_v2(transaction), transaction)
        # The transport binds each graph action to this exact rendered request.
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport, RemoteActivationError
        from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2
        from sandbox.hosting.images.activation.execution_runner import execution_step_subject
        from unittest.mock import Mock
        import json
        transport = RegisteredRemoteActivationTransport(argv_runner=lambda **kwargs: None,
                                                        configuration_binding_key=b"k" * 32)
        transport._compose_selector_v2 = {"snapshot": snapshot.as_mapping(),
            "snapshot_digest": snapshot.snapshot_digest, "private_source": {"kind": "compose_snapshot_v2"},
            "environment": {}, "render_digest": snapshot.configuration_digest}
        transport.bind_execution_v2(request)
        progress = ExecutionProgressV2.from_mapping(transaction["execution_progress"])
        subject = execution_step_subject(progress=progress, contract=contract, index=0)
        transport._invoke = Mock(return_value={"returncode": 0, "terminated": True,
            "stdout": json.dumps({"subject_digest": subject["subject_digest"],
                "container_identity": None, "exit_code": None, "terminated": True})})
        args = dict(action="replace", subject=subject, container_identity=None,
                    timeout_seconds=contract.graph.readiness_timeout_seconds)
        for change in ({"request_digest": "sha256:" + "e" * 64}, {"services": ["foreign"]},
                       {"step_index": True}, {"extra": 1}):
            with self.assertRaises(RemoteActivationError):
                transport.execute_graph_step_v2(**{**args, "subject": {**subject, **change}})
        for change in ({"action": "start"}, {"timeout_seconds": True}, {"container_identity": "foreign"}):
            with self.assertRaises(RemoteActivationError):
                transport.execute_graph_step_v2(**{**args, **change})
        transport._invoke.assert_not_called()
        receipt = transport.execute_graph_step_v2(**args)
        self.assertEqual(receipt["subject_digest"], subject["subject_digest"])
        self.assertEqual(transport._invoke.call_count, 1)
        # Real host argv-runner parsing and program assembly, without SSH effects.
        from sandbox.commands.hosting import _host_image_argv_runner
        from unittest.mock import patch
        import shlex, subprocess
        initializer_index = len(contract.graph.prerequisite_groups)
        init_subject = execution_step_subject(progress=progress, contract=contract, index=initializer_index)
        private = {name: snapshot.as_mapping()[name] for name in (
            "snapshot_id", "snapshot_digest", "provider_revision", "target", "input_contract")}
        provider = {**private, "compose_files": ("/private/candidate/effective.json",),
            "project_directory": "/private/candidate", "project_name": "fixture",
            "environment_file": "/private/candidate/environment", "render_digest": snapshot.configuration_digest}
        private.update(kind="compose_graph_v2", action="start", subject=init_subject,
            container_identity="a" * 64, execution_contract=contract.as_mapping(),
            render_digest=snapshot.configuration_digest,
            image_identities=transport._invoke.call_args.kwargs["private_environment_source"]["image_identities"])
        def ssh(entry, command, **kwargs):
            argv = shlex.split(command)
            compile(argv[2], "private-helper", "exec")
            frame = json.loads(kwargs["input_data"])
            self.assertIn(frame["source"]["subject"], [init_subject, subject])
            self.assertIn("execute_private_graph", argv[2])
            return subprocess.CompletedProcess([], 0, "{}", "")
        runner = _host_image_argv_runner({}, compose_snapshot_provider=provider)
        invocation = dict(argv=("sandbox-activation-execute-graph-v2",), environment={}, private_environment={},
            private_environment_source=private, redact_environment_keys=None,
            timeout_seconds=contract.declarations[0].timeout_seconds, max_output_bytes=1048576)
        with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh) as remote_call:
            self.assertEqual(runner(**invocation)["returncode"], 0)
            self.assertEqual(remote_call.call_count, 1)
            with self.assertRaises(ValueError):
                runner(**{**invocation, "private_environment_source": {**private, "container_identity": "foreign"}})
            self.assertEqual(remote_call.call_count, 1)
            runtime_invocation = {**invocation, "timeout_seconds": contract.graph.readiness_timeout_seconds,
                "private_environment_source": {**private, "subject": subject, "action": "replace", "container_identity": None}}
            self.assertEqual(runner(**runtime_invocation)["returncode"], 0)
            self.assertEqual(remote_call.call_count, 2)
        # Exercise actual service admission through the strict repository codec.
        from tests.test_hosting_image_activation_v2 import FakeRepositoryV2, FakeRuntimeV2, FakeEdgeV2, execute
        admitted = []
        class StrictAdmission(FakeRepositoryV2):
            def accept_v2(self, candidate, **kwargs):
                retained = transaction_v2(candidate, holder=holder, proof_pin=pin,
                    recovery_context=kwargs["recovery_context"],
                    prior_generation_digest=kwargs["prior_generation_digest"])
                admitted.append(retained)
                return "accepted", retained
        runtime = FakeRuntimeV2()
        result = execute(StrictAdmission(), runtime, FakeEdgeV2(), request, grant)
        self.assertEqual(len(admitted), 1, result)
        self.assertEqual(admitted[0]["recovery_context"]["compose_snapshot"], snapshot.as_mapping())
        self.assertEqual(admitted[0]["recovery_context"]["compatibility_grant"], grant.as_mapping())
        self.assertEqual(admitted[0]["execution_progress"]["events"], [])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["code"], "committed")
        altered = {**transaction, "recovery_context": {**context, "compose_snapshot": legacy.as_mapping()}}
        with self.assertRaises(ValueError):
            validate_transaction_v2(altered)

    def test_failed_initializer_retains_exit_allows_cleanup_and_fences_consumers(self):
        from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2
        from sandbox.hosting.images.activation.v2_models import RuntimeExecutionGraphV2
        graph = RuntimeExecutionGraphV2.create(prerequisite_groups=(),
            initializer_order=("migrate",), consumer_groups=(("web",),), dependencies=(
                {"service": "web", "dependency": "migrate", "condition": "service_completed_successfully"},),
            readiness_timeout_seconds=300)
        progress = ExecutionProgressV2.create(graph=graph, request_digest="sha256:" + "a" * 64,
                                             snapshot_digest="sha256:" + "b" * 64)
        subject = "sha256:" + "c" * 64
        progress = progress.append(stage="prepared", subject_digest=subject)
        for stage in ("created", "inspected", "effect_entered"):
            progress = progress.append(stage=stage, subject_digest=subject, container_identity="container-a")
        progress = progress.append(stage="exited", subject_digest=subject,
                                   container_identity="container-a", exit_code=17)
        self.assertTrue(progress.failed)
        self.assertFalse(progress.complete)
        self.assertEqual(progress.next_step[-1], "cleaned")
        progress = ExecutionProgressV2.from_mapping(progress.as_mapping())
        progress = progress.append(stage="cleaned", subject_digest=subject, container_identity="container-a")
        self.assertTrue(progress.possible_effect)
        self.assertTrue(progress.failed)
        self.assertFalse(progress.complete)
        self.assertIsNone(progress.next_step)
        with self.assertRaises(ValueError):
            progress.append(stage="prepared", subject_digest=subject)

    def test_init_exit_is_durable_before_cleanup_and_cannot_skip_or_replay(self):
        from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2
        from sandbox.hosting.images.activation.v2_models import RuntimeExecutionGraphV2
        graph = RuntimeExecutionGraphV2.create(prerequisite_groups=(("queue",),),
            initializer_order=("migrate",), consumer_groups=(("web",),), dependencies=(
                {"service": "web", "dependency": "migrate", "condition": "service_completed_successfully"},),
            readiness_timeout_seconds=300)
        progress = ExecutionProgressV2.create(graph=graph, request_digest="sha256:" + "a" * 64,
                                             snapshot_digest="sha256:" + "b" * 64)
        subject = "sha256:" + "c" * 64
        for stage in ("prepared", "effect_entered", "ready"):
            progress = progress.append(stage=stage, subject_digest=subject)
        self.assertEqual(progress.next_step, (1, "initializer", ("migrate",), "prepared"))
        progress = progress.append(stage="prepared", subject_digest=subject)
        self.assertTrue(progress.possible_effect)
        self.assertFalse(progress.init_effect_entered)
        for stage in ("created", "inspected", "effect_entered"):
            progress = progress.append(stage=stage, subject_digest=subject, container_identity="container-a")
        self.assertTrue(progress.init_effect_entered)
        with self.assertRaises(ValueError):
            progress.append(stage="exited", subject_digest="sha256:" + "d" * 64,
                            container_identity="container-a", exit_code=0)
        with self.assertRaises(ValueError):
            progress.append(stage="cleaned", subject_digest=subject, container_identity="container-a")
        with self.assertRaises(ValueError):
            progress.append(stage="exited", subject_digest=subject, container_identity="foreign", exit_code=0)
        progress = progress.append(stage="exited", subject_digest=subject, container_identity="container-a", exit_code=0)
        self.assertEqual(ExecutionProgressV2.from_mapping(progress.as_mapping()), progress)
        self.assertEqual(progress.next_step[-1], "cleaned")
        progress = progress.append(stage="cleaned", subject_digest=subject, container_identity="container-a")
        with self.assertRaises(ValueError):
            progress.append(stage="cleaned", subject_digest=subject, container_identity="container-a")
        for stage in ("prepared", "effect_entered", "ready"):
            progress = progress.append(stage=stage, subject_digest=subject)
        self.assertTrue(progress.complete)
        self.assertIsNone(progress.next_step)
        raw = progress.as_mapping()
        raw["events"] = raw["events"][:-2] + raw["events"][-1:]
        with self.assertRaises(ValueError):
            ExecutionProgressV2.from_mapping(raw)

import unittest

from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2
from sandbox.hosting.images.activation.v2_models import InitDeclarationV2, InitExecutionContractV2, RuntimeExecutionGraphV2


DIGEST = "sha256:" + "a" * 64


def fixture():
    graph = RuntimeExecutionGraphV2.create(prerequisite_groups=(("queue",),),
        initializer_order=("migrate",), consumer_groups=(("web",),), dependencies=(
            {"service": "web", "dependency": "migrate", "condition": "service_completed_successfully"},),
        readiness_timeout_seconds=300)
    declaration = InitDeclarationV2.create(index=0, service="migrate", image="worker",
        image_ref="ghcr.io/example/worker@" + DIGEST, config_digest=DIGEST,
        platform={"os": "linux", "architecture": "amd64"}, timeout_seconds=60,
        environment_keys=(), dependency_services=(),
        target={"machine_identity": "machine-a", "target_identity": "target-a", "daemon_identity": "daemon-a"},
        snapshot_id="compose-snapshot/fixture", configuration_digest=DIGEST)
    contract = InitExecutionContractV2.create(declarations=(declaration,), graph=graph)
    progress = ExecutionProgressV2.create(graph=graph, request_digest=DIGEST, snapshot_digest=DIGEST)
    return contract, progress


class Port:
    def __init__(self, log, exit_code=0):
        self.log, self.exit_code = log, exit_code

    def execute_graph_step_v2(self, *, action, subject, container_identity, timeout_seconds):
        self.log.append(("effect", action, tuple(subject["services"])))
        return {"subject_digest": subject["subject_digest"],
                "container_identity": "container-a" if subject["kind"] == "initializer" else None,
                "exit_code": self.exit_code if action == "wait" else None,
                "terminated": True}


class ExecutionRunnerTests(unittest.TestCase):
    def run_fixture(self, *, failed_save=None, exit_code=0):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        contract, initial = fixture()
        saved, log = [], []
        def persist(progress):
            log.append(("save", progress.events[-1]["stage"], progress.events[-1]["step_index"]))
            if len(saved) == failed_save:
                raise OSError("synthetic persistence failure")
            saved.append(progress)
        try:
            result = execute_graph_v2(progress=initial, contract=contract, adapter=Port(log, exit_code), persist=persist)
        except (OSError, ValueError):
            result = None
        return result, saved, log

    def test_order_and_exit_saved_before_cleanup(self):
        result, saved, log = self.run_fixture()
        self.assertTrue(result.complete)
        effects = [row[1:] for row in log if row[0] == "effect"]
        self.assertEqual(effects, [("replace", ("queue",)), ("ready", ("queue",)),
            ("create", ("migrate",)), ("inspect", ("migrate",)), ("start", ("migrate",)),
            ("wait", ("migrate",)), ("cleanup", ("migrate",)),
            ("replace", ("web",)), ("ready", ("web",))])
        self.assertLess(log.index(("save", "exited", 1)), log.index(("effect", "cleanup", ("migrate",))))
        self.assertLess(log.index(("save", "effect_entered", 1)), log.index(("effect", "start", ("migrate",))))
        self.assertEqual(len(saved), len(result.sequence))

    def test_every_failed_save_stops_before_any_following_effect(self):
        _, complete, full_log = self.run_fixture()
        for index in range(len(complete)):
            with self.subTest(index=index):
                result, saved, log = self.run_fixture(failed_save=index)
                self.assertIsNone(result)
                self.assertEqual(len(saved), index)
                self.assertEqual(log[-1][0], "save")
                save_positions = [i for i, row in enumerate(full_log) if row[0] == "save"]
                self.assertEqual(log, full_log[:save_positions[index] + 1])

    def test_nonzero_exit_is_retained_and_no_consumer_starts(self):
        result, saved, log = self.run_fixture(exit_code=17)
        self.assertIsNone(result)
        self.assertTrue(saved[-1].failed)
        self.assertEqual(saved[-1].events[-1]["stage"], "cleaned")
        self.assertFalse(any(row[0] == "effect" and row[2] == ("web",) for row in log))

    def test_retained_progress_never_restarts_effects(self):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        contract, progress = fixture()
        progress = progress.append(stage="prepared", subject_digest=DIGEST)
        log = []
        with self.assertRaises(ValueError):
            execute_graph_v2(progress=progress, contract=contract, adapter=Port(log), persist=lambda value: log.append(value))
        self.assertEqual(log, [])

    def test_transport_loss_at_each_action_fences_replay(self):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        for fail_at in range(9):
            with self.subTest(fail_at=fail_at):
                contract, initial = fixture()
                saved, log = [], []
                class LostTransport(Port):
                    def execute_graph_step_v2(self, **kwargs):
                        receipt = super().execute_graph_step_v2(**kwargs)
                        if len(self.log) - 1 == fail_at:
                            raise OSError("synthetic lost acknowledgement")
                        return receipt
                with self.assertRaises(OSError):
                    execute_graph_v2(progress=initial, contract=contract,
                        adapter=LostTransport(log), persist=saved.append)
                self.assertEqual(len(log), fail_at + 1)
                self.assertTrue(saved[-1].possible_effect)
                replay_log = []
                with self.assertRaises(ValueError):
                    execute_graph_v2(progress=saved[-1], contract=contract,
                        adapter=Port(replay_log), persist=lambda row: replay_log.append(row))
                self.assertEqual(replay_log, [])

    def test_unbound_or_unterminated_receipt_never_advances(self):
        from sandbox.hosting.images.activation.execution_runner import execute_graph_v2
        for change in ({"subject_digest": "sha256:" + "b" * 64},
                       {"terminated": False}, {"container_identity": "foreign"},
                       {"unexpected": "value"}, {"exit_code": 0}):
            with self.subTest(change=change):
                contract, initial = fixture()
                log, saved = [], []
                class InvalidReceipt(Port):
                    def execute_graph_step_v2(self, **kwargs):
                        return {**super().execute_graph_step_v2(**kwargs), **change}
                with self.assertRaises(ValueError):
                    execute_graph_v2(progress=initial, contract=contract,
                        adapter=InvalidReceipt(log), persist=saved.append)
                self.assertEqual(len(log), 1)
                self.assertEqual(saved[-1].events[-1]["stage"], "effect_entered")

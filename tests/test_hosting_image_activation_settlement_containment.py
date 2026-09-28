import copy
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from sandbox.hosting.images.activation.settlement_containment import containment
from sandbox.hosting.images.activation.settlement_service import SettlementError
from tests.test_hosting_image_activation_settlement_repository import _state


TARGET = "target-a"
TRANSACTION = "sha256:" + "a" * 64


class Repository:
    def __init__(self):
        self.state = copy.deepcopy(_state())
        self.transactions = []

    def operation_transaction(self, target):
        self.transactions.append(target)
        return nullcontext()

    def snapshot(self, _target):
        return copy.deepcopy(self.state)


class Observer:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def containment(self, *, transaction, generation, containers=None):
        self.calls.append((transaction, generation, containers))
        return {"code": "planned" if containers is None else "contained",
                "containers": self.rows if containers is None else containers}


def args(phase, root: Path, *, request_id="containment-a", plan=None):
    plan_path = root / "containment-plan.json"
    if plan is not None:
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        plan_path.chmod(0o600)
    return SimpleNamespace(
        settlement_phase=phase,
        request_id=request_id,
        expected_generation=0,
        activation_transaction=TRANSACTION,
        settlement_plan=str(plan_path),
    )


class SettlementContainmentTests(unittest.TestCase):
    def test_containment_plan_is_read_only_and_returns_exact_reviewable_rows(self):
        repository = Repository()
        rows = [{"container_id": "1" * 64, "running": True,
                 "restart_policy": {"Name": "always"}}]
        observer = Observer(rows)
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory))
            result = containment(args("containment-plan", Path(directory)), target=TARGET,
                                 repository=repository, observer=observer, store=store)
        self.assertEqual(result["code"], "planned")
        plan = result["plan"]
        self.assertEqual(plan["containers"], rows)
        self.assertTrue(plan["preserve_volumes"])
        self.assertEqual(plan["operation"], "disable-restart-and-stop")
        self.assertEqual(len(observer.calls), 1)
        self.assertEqual(repository.state, _state())

    def test_apply_persists_acceptance_before_effect_and_exact_replay_has_no_second_effect(self):
        repository = Repository()
        rows = [{"container_id": "1" * 64, "running": True,
                 "restart_policy": {"Name": "always"}}]
        observer = Observer(rows)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory)); store = SimpleNamespace(root=root)
            planned = containment(args("containment-plan", root), target=TARGET,
                                  repository=repository, observer=observer, store=store)
            result = containment(args("containment-apply", root, plan=planned["plan"]),
                                 target=TARGET, repository=repository, observer=observer, store=store)
            replay = containment(args("containment-apply", root, plan=planned["plan"]),
                                 target=TARGET, repository=repository, observer=observer, store=store)
            accepted = list((root / "containment").rglob("*-accepted.json"))
            terminal = list((root / "containment").rglob("*-terminal.json"))
            accepted_mode = accepted[0].stat().st_mode & 0o777
            terminal_mode = terminal[0].stat().st_mode & 0o777
        self.assertEqual(result["code"], "contained")
        self.assertEqual(replay, result)
        self.assertEqual(len(observer.calls), 2)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(terminal), 1)
        self.assertEqual(accepted_mode, 0o600)
        self.assertEqual(terminal_mode, 0o600)

    def test_changed_plan_and_oversized_inventory_refuse_before_containment_effect(self):
        repository = Repository()
        observer = Observer([{"container_id": "1" * 64}] * 129)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory)); store = SimpleNamespace(root=root)
            with self.assertRaisesRegex(SettlementError, "artifact_invalid"):
                containment(args("containment-plan", root), target=TARGET,
                            repository=repository, observer=observer, store=store)
            self.assertEqual(len(observer.calls), 1)

            changed = args("containment-apply", root, plan={})
            with self.assertRaisesRegex(SettlementError, "evidence_changed"):
                containment(changed, target=TARGET, repository=repository,
                            observer=Observer([]), store=store)
            self.assertFalse((root / "containment").exists())


if __name__ == "__main__":
    unittest.main()

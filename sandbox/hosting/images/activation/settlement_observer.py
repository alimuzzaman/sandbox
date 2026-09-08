"""Registered-host adapter for a bounded, private settlement inventory."""

import base64
import inspect
import json

from . import private_settlement
from .private_graph import graph_command_port
from .settlement_models import SettlementObservation
from .settlement_service import SettlementError


class SettlementObserver:
    def __init__(self, *, runner, identity_observer, binding_key):
        if type(binding_key) is not bytes or len(binding_key) != 32:
            raise SettlementError("observation_unavailable")
        self.runner = runner
        self.identity_observer = identity_observer
        self.binding_key = binding_key

    def containment(self, *, transaction, generation, containers=None):
        from . import private_containment
        context = transaction["recovery_context"]; target = context["target"]
        expected = {key: target[key] for key in ("machine_identity", "target_identity")}
        if self.identity_observer() != expected: raise SettlementError("evidence_changed")
        frame = {"target": target, "compose_project": context["compose_project"],
            "transaction_digest": transaction["transaction_digest"], "generation": generation,
            "binding_key": base64.b64encode(self.binding_key).decode("ascii"),
            "operation": "plan" if containers is None else "apply", "containers": containers}
        program = inspect.getsource(private_containment) + "\n" + inspect.getsource(graph_command_port) + "\nmain()\n"
        result = self.runner(program=program, input_data=json.dumps(frame, sort_keys=True, separators=(",", ":")),
            timeout_seconds=250, max_output_bytes=65536)
        if type(result) is not dict or result.get("ok") is not True or self.identity_observer() != expected:
            raise SettlementError("observation_unavailable")
        return result

    def observe(self, *, transaction, generation):
        context = transaction["recovery_context"]
        target = context["target"]
        expected = {key: target[key] for key in ("machine_identity", "target_identity")}
        if self.identity_observer() != expected:
            raise SettlementError("evidence_changed")
        frame = {"target": target, "compose_project": context["compose_project"],
            "transaction_digest": transaction["transaction_digest"], "generation": generation,
            "binding_key": base64.b64encode(self.binding_key).decode("ascii")}
        program = inspect.getsource(private_settlement) + "\n" + inspect.getsource(graph_command_port) + "\nmain()\n"
        result = self.runner(program=program,
            input_data=json.dumps(frame, sort_keys=True, separators=(",", ":")),
            timeout_seconds=50, max_output_bytes=65536)
        if type(result) is not dict or result.get("ok") is not True:
            code = result.get("code") if type(result) is dict else None
            raise SettlementError(code if code in {"not_quiescent", "evidence_changed"} else "observation_unavailable")
        if set(result) != {"ok", "observation"} or self.identity_observer() != expected:
            raise SettlementError("evidence_changed")
        raw = result["observation"]
        if type(raw) is not dict:
            raise SettlementError("observation_unavailable")
        for key in ("container_identities", "preserved_identities", "process_identities"):
            if type(raw.get(key)) is not list:
                raise SettlementError("observation_unavailable")
        observation = SettlementObservation.create(**{**raw,
            **{key: tuple(raw[key]) for key in ("container_identities", "preserved_identities", "process_identities")}})
        if (observation.target.as_mapping() != target
                or observation.generation != generation
                or observation.transaction_digest != transaction["transaction_digest"]):
            raise SettlementError("evidence_changed")
        return observation

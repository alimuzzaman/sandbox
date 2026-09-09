"""Registered-host adapter for a bounded, private settlement inventory."""

import base64
import inspect
import json

from . import private_settlement, settlement_diagnostics
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

    @staticmethod
    def _refusal(result, allowed):
        code = result.get("code") if type(result) is dict else None
        code = code if type(code) is str and code in allowed else "observation_unavailable"
        diagnostic = None
        if (type(result) is dict and set(result) == {"ok", "code", "diagnostic"}
                and result["ok"] is False and result["code"] == code):
            diagnostic = settlement_diagnostics.settlement_diagnostic(result["diagnostic"], code)
        return SettlementError(code, diagnostic)

    @staticmethod
    def _identity_changed(sample):
        return SettlementError("evidence_changed", {"schema_version": 1,
            "reason": "target_identity_changed", "subject": "target", "sample": sample})

    def containment(self, *, transaction, generation, containers=None):
        from . import private_containment
        context = transaction["recovery_context"]; target = context["target"]
        expected = {key: target[key] for key in ("machine_identity", "target_identity")}
        if self.identity_observer() != expected: raise self._identity_changed("identity_before")
        frame = {"target": target, "compose_project": context["compose_project"],
            "transaction_digest": transaction["transaction_digest"], "generation": generation,
            "binding_key": base64.b64encode(self.binding_key).decode("ascii"),
            "operation": "plan" if containers is None else "apply", "containers": containers}
        program = inspect.getsource(settlement_diagnostics) + "\n" + inspect.getsource(private_containment) + "\n" + inspect.getsource(graph_command_port) + "\nmain()\n"
        result = self.runner(program=program, input_data=json.dumps(frame, sort_keys=True, separators=(",", ":")),
            timeout_seconds=250, max_output_bytes=65536)
        if type(result) is not dict or result.get("ok") is not True:
            raise self._refusal(result, {'process_owner_unavailable', 'container_paused',
                'container_restarting', 'container_state_invalid', 'evidence_changed'})
        if self.identity_observer() != expected: raise self._identity_changed("identity_after")
        return result

    def observe(self, *, transaction, generation):
        context = transaction["recovery_context"]
        target = context["target"]
        expected = {key: target[key] for key in ("machine_identity", "target_identity")}
        if self.identity_observer() != expected:
            raise self._identity_changed("identity_before")
        frame = {"target": target, "compose_project": context["compose_project"],
            "transaction_digest": transaction["transaction_digest"], "generation": generation,
            "binding_key": base64.b64encode(self.binding_key).decode("ascii")}
        program = inspect.getsource(settlement_diagnostics) + "\n" + inspect.getsource(private_settlement) + "\n" + inspect.getsource(graph_command_port) + "\nmain()\n"
        result = self.runner(program=program,
            input_data=json.dumps(frame, sort_keys=True, separators=(",", ":")),
            timeout_seconds=50, max_output_bytes=65536)
        if type(result) is not dict or result.get("ok") is not True:
            raise self._refusal(result, {"not_quiescent", "evidence_changed"})
        if set(result) != {"ok", "observation"}:
            raise SettlementError("evidence_changed")
        if self.identity_observer() != expected:
            raise self._identity_changed("identity_after")
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

"""Ordered graph driver; persistence acknowledgement precedes every effect.

The transport owns private inspection and finite execution. This driver accepts
only bounded, subject-bound receipts and never resumes possibly entered work.
"""

from .execution_state import ExecutionProgressV2
from .models import ActivationContractError, _closed, _text, activation_digest
from .v2_models import InitExecutionContractV2


def execution_step_subject(*, progress, contract, index):
    if (type(progress) is not ExecutionProgressV2 or type(contract) is not InitExecutionContractV2
            or contract.graph != progress.graph or type(index) is not int
            or not 0 <= index < len(progress.steps)):
        raise ActivationContractError("init_mismatch")
    kind, services = progress.steps[index]
    declarations = {row.service: row for row in contract.declarations}
    declaration = declarations[services[0]] if kind == "initializer" else None
    body = {"request_digest": progress.request_digest, "snapshot_digest": progress.snapshot_digest,
            "graph_digest": progress.graph.graph_digest, "contract_digest": contract.contract_digest,
            "step_index": index, "kind": kind, "services": list(services),
            "declaration_digest": declaration.declaration_digest if declaration else None}
    return {**body, "subject_digest": activation_digest("sandbox.hosting.images.execution-step.v2", body)}


def execute_graph_v2(*, progress, contract, adapter, persist):
    if (type(progress) is not ExecutionProgressV2 or type(contract) is not InitExecutionContractV2
            or contract.graph != progress.graph):
        raise ActivationContractError("init_mismatch")
    if progress.events:
        raise ActivationContractError("effect_unknown")
    declarations = {row.service: row for row in contract.declarations}

    def save(stage, subject, container=None, exit_code=None):
        nonlocal progress
        candidate = progress.append(stage=stage, subject_digest=subject["subject_digest"],
                                    container_identity=container, exit_code=exit_code)
        # A lost acknowledgement aborts the driver. It cannot repeat the write
        # or advance the effect using a speculative in-memory state.
        persist(candidate)
        progress = candidate

    def invoke(action, subject, container, timeout):
        row = _closed(adapter.execute_graph_step_v2(action=action, subject=subject,
            container_identity=container, timeout_seconds=timeout), frozenset({
                "subject_digest", "container_identity", "exit_code", "terminated"}))
        if row["subject_digest"] != subject["subject_digest"] or row["terminated"] is not True:
            raise ActivationContractError("effect_unknown")
        if subject["kind"] == "initializer":
            _text(row["container_identity"], identity=True)
            if container is not None and row["container_identity"] != container:
                raise ActivationContractError("init_uncertain")
        elif row["container_identity"] is not None:
            raise ActivationContractError("runtime_mismatch")
        if action == "wait":
            if type(row["exit_code"]) is not int or not 0 <= row["exit_code"] <= 255:
                raise ActivationContractError("init_uncertain")
        elif row["exit_code"] is not None:
            raise ActivationContractError("init_uncertain")
        return row

    for index, (kind, services) in enumerate(progress.steps):
        declaration = declarations[services[0]] if kind == "initializer" else None
        subject = execution_step_subject(progress=progress, contract=contract, index=index)
        save("prepared", subject)
        if declaration is None:
            save("effect_entered", subject)
            invoke("replace", subject, None, progress.graph.readiness_timeout_seconds)
            invoke("ready", subject, None, progress.graph.readiness_timeout_seconds)
            save("ready", subject)
            continue
        timeout = declaration.timeout_seconds
        container = invoke("create", subject, None, timeout)["container_identity"]
        save("created", subject, container)
        invoke("inspect", subject, container, timeout)
        save("inspected", subject, container)
        save("effect_entered", subject, container)
        invoke("start", subject, container, timeout)
        exit_code = invoke("wait", subject, container, timeout)["exit_code"]
        save("exited", subject, container, exit_code)
        invoke("cleanup", subject, container, timeout)
        save("cleaned", subject, container)
        if exit_code != 0:
            raise ActivationContractError("init_mismatch")
    return progress

"""Derive a closed execution graph from the private helper's public projection."""

from .models import ActivationContractError
from .v2_models import InitDeclarationV2, InitExecutionContractV2, RuntimeExecutionGraphV2
from ..plan_set import validate_verified_image_plan_set


def prepare_execution_contract(*, plan, services: dict, target: dict, snapshot_id: str,
        configuration_digest: str, initializer_order: tuple[str, ...],
        init_timeout_seconds: int = 300, readiness_timeout_seconds: int = 300) -> InitExecutionContractV2:
    validate_verified_image_plan_set(plan)
    persistent = set(plan.policy.persistent_services)
    initializers = set(plan.policy.one_shot_services)
    expected = persistent | initializers
    if (type(services) is not dict or set(services) != expected
            or type(initializer_order) is not tuple or set(initializer_order) != initializers
            or len(initializer_order) != len(initializers)):
        raise ActivationContractError("init_mismatch")
    images = {image.name: image for image in plan.receipt.images}
    bindings = dict(plan.policy.service_image_bindings)
    edges = []
    dependencies = {}
    for name in sorted(expected):
        row = services[name]
        image = images[bindings[name]]
        if (type(row) is not dict or row.get("image") != image.image_ref
                or row.get("build") is not None or row.get("pull_policy") not in (None, "never")
                or row.get("platform") not in (None, "linux/amd64")
                or type(row.get("depends_on")) is not dict):
            raise ActivationContractError("init_mismatch")
        dependencies[name] = set(row["depends_on"])
        if not dependencies[name] <= expected:
            raise ActivationContractError("init_mismatch")
        for dependency, condition in sorted(row["depends_on"].items()):
            if type(condition) is not dict or set(condition) != {"condition"}:
                raise ActivationContractError("init_mismatch")
            edges.append({"service": name, "dependency": dependency, "condition": condition["condition"]})

    prerequisites = set().union(*(dependencies[name] & persistent for name in initializers)) if initializers else set()
    while True:
        expanded = prerequisites | set().union(*(dependencies[name] for name in prerequisites)) if prerequisites else set()
        if expanded & initializers:
            raise ActivationContractError("init_mismatch")
        if expanded == prerequisites:
            break
        prerequisites = expanded

    def groups(members):
        pending = set(members)
        result = []
        while pending:
            ready = tuple(sorted(name for name in pending if not (dependencies[name] & pending)))
            if not ready:
                raise ActivationContractError("init_mismatch")
            result.append(ready)
            pending.difference_update(ready)
        return tuple(result)

    graph = RuntimeExecutionGraphV2.create(prerequisite_groups=groups(prerequisites),
        initializer_order=initializer_order, consumer_groups=groups(persistent - prerequisites),
        dependencies=tuple(edges), readiness_timeout_seconds=readiness_timeout_seconds)
    declarations = []
    for index, name in enumerate(initializer_order):
        image = images[bindings[name]]
        keys = services[name].get("x-sandbox-environment-keys")
        if type(keys) is not list:
            raise ActivationContractError("init_mismatch")
        declarations.append(InitDeclarationV2.create(index=index, service=name,
            image=image.name, image_ref=image.image_ref, config_digest=image.config_digest,
            platform={"os": "linux", "architecture": "amd64"}, timeout_seconds=init_timeout_seconds,
            environment_keys=tuple(keys), dependency_services=tuple(sorted(dependencies[name])),
            target=target, snapshot_id=snapshot_id, configuration_digest=configuration_digest))
    return InitExecutionContractV2.create(declarations=tuple(declarations), graph=graph)

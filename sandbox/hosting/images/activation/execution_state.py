"""Append-only graph evidence. Initializer exit precedes owned cleanup."""

from dataclasses import dataclass

from .models import ActivationContractError, _closed, _digest, _text, activation_digest
from .v2_models import RuntimeExecutionGraphV2


_RUNTIME_STAGES = ("prepared", "effect_entered", "ready")
_INIT_STAGES = ("prepared", "created", "inspected", "effect_entered", "exited", "cleaned")


@dataclass(frozen=True, slots=True)
class ExecutionProgressV2:
    graph: RuntimeExecutionGraphV2
    request_digest: str
    snapshot_digest: str
    events: tuple[dict, ...]
    progress_digest: str

    @property
    def steps(self):
        return (*(("prerequisite", group) for group in self.graph.prerequisite_groups),
                *(("initializer", (name,)) for name in self.graph.initializer_order),
                *(("consumer", group) for group in self.graph.consumer_groups))

    @property
    def sequence(self):
        return tuple((index, kind, services, stage) for index, (kind, services) in enumerate(self.steps)
                     for stage in (_INIT_STAGES if kind == "initializer" else _RUNTIME_STAGES))

    @property
    def next_step(self):
        sequence = self.sequence
        if self.failed and self.events[-1]["stage"] == "cleaned":
            return None
        return sequence[len(self.events)] if len(self.events) < len(sequence) else None

    @property
    def failed(self):
        return any(event["stage"] == "exited" and event["exit_code"] != 0 for event in self.events)

    @property
    def complete(self):
        return not self.failed and self.next_step is None

    @property
    def possible_effect(self):
        # Preparation precedes container/network creation. A crash there must
        # never be reclassified as no effect from empty running containers.
        return bool(self.events)

    @property
    def init_effect_entered(self):
        return any(self.steps[event["step_index"]][0] == "initializer"
                   and event["stage"] == "effect_entered" for event in self.events)

    @property
    def root_digest(self):
        return activation_digest("sandbox.hosting.images.execution-root.v2", {
            "graph_digest": self.graph.graph_digest, "request_digest": self.request_digest,
            "snapshot_digest": self.snapshot_digest})

    def __post_init__(self):
        if type(self.graph) is not RuntimeExecutionGraphV2 or type(self.events) is not tuple:
            raise ActivationContractError("init_mismatch")
        for value in (self.request_digest, self.snapshot_digest, self.progress_digest):
            _digest(value)
        sequence = self.sequence
        if len(self.events) > len(sequence):
            raise ActivationContractError("init_mismatch")
        previous = self.root_digest
        containers = {}
        subjects = {}
        safe = []
        failed_index = None
        for event, expected in zip(self.events, sequence):
            row = _closed(event, frozenset({"step_index", "stage", "subject_digest", "container_identity",
                                           "exit_code", "previous_digest", "event_digest"}))
            index, kind, _services, stage = expected
            if failed_index is not None and (index != failed_index or stage != "cleaned"):
                raise ActivationContractError("init_mismatch")
            if type(row["step_index"]) is not int or row["step_index"] != index or row["stage"] != stage:
                raise ActivationContractError("init_mismatch")
            _digest(row["subject_digest"])
            subjects.setdefault(index, row["subject_digest"])
            if subjects[index] != row["subject_digest"]:
                raise ActivationContractError("init_mismatch")
            if kind == "initializer" and stage != "prepared":
                _text(row["container_identity"], identity=True)
                containers.setdefault(index, row["container_identity"])
                if containers[index] != row["container_identity"]:
                    raise ActivationContractError("init_mismatch")
            elif row["container_identity"] is not None:
                raise ActivationContractError("init_mismatch")
            if stage == "exited":
                if type(row["exit_code"]) is not int or not 0 <= row["exit_code"] <= 255:
                    raise ActivationContractError("init_mismatch")
                if row["exit_code"] != 0:
                    failed_index = index
            elif row["exit_code"] is not None:
                raise ActivationContractError("init_mismatch")
            body = {name: value for name, value in row.items() if name != "event_digest"}
            if row["previous_digest"] != previous or row["event_digest"] != activation_digest(
                    "sandbox.hosting.images.execution-event.v2", body):
                raise ActivationContractError("init_mismatch")
            previous = row["event_digest"]
            safe.append(dict(row))
        object.__setattr__(self, "events", tuple(safe))
        if self.progress_digest != activation_digest("sandbox.hosting.images.execution-progress.v2", self.body_mapping()):
            raise ActivationContractError("init_mismatch")

    def body_mapping(self):
        return {"graph": self.graph.as_mapping(), "request_digest": self.request_digest,
                "snapshot_digest": self.snapshot_digest, "events": [dict(event) for event in self.events]}

    def as_mapping(self):
        return {**self.body_mapping(), "progress_digest": self.progress_digest}

    @classmethod
    def create(cls, *, graph, request_digest, snapshot_digest):
        body = {"graph": graph.as_mapping(), "request_digest": request_digest,
                "snapshot_digest": snapshot_digest, "events": []}
        return cls(graph, request_digest, snapshot_digest, (), activation_digest(
            "sandbox.hosting.images.execution-progress.v2", body))

    @classmethod
    def from_mapping(cls, value):
        raw = _closed(value, frozenset({"graph", "request_digest", "snapshot_digest", "events", "progress_digest"}))
        if type(raw["events"]) is not list:
            raise ActivationContractError("init_mismatch")
        return cls(RuntimeExecutionGraphV2.from_mapping(raw["graph"]), raw["request_digest"],
                   raw["snapshot_digest"], tuple(raw["events"]), raw["progress_digest"])

    def append(self, *, stage, subject_digest, container_identity=None, exit_code=None):
        if self.next_step is None or self.next_step[-1] != stage:
            raise ActivationContractError("init_mismatch")
        row = {"step_index": self.next_step[0], "stage": stage, "subject_digest": subject_digest,
               "container_identity": container_identity, "exit_code": exit_code,
               "previous_digest": self.events[-1]["event_digest"] if self.events else self.root_digest}
        event = {**row, "event_digest": activation_digest("sandbox.hosting.images.execution-event.v2", row)}
        body = {**self.body_mapping(), "events": [*self.events, event]}
        return self.from_mapping({**body, "progress_digest": activation_digest(
            "sandbox.hosting.images.execution-progress.v2", body)})

"""Delivery diagnostics through an explicitly injected shared service."""
from __future__ import annotations

from sandbox.delivery.service import inspect_with_factory


_service_factory = None
_trace_factory = None


def configure(*, delivery_service_factory, trace_service_factory=None):
    global _service_factory, _trace_factory
    _service_factory = delivery_service_factory
    _trace_factory = trace_service_factory


def delivery_inspect(project_dir: str, remote: str | None = None, environment: str | None = None,
                     label: str | None = None, operation_id: str | None = None,
                     request_id: str | None = None, observe: bool = False,
                     limit: int | None = None, cursor: str | None = None,
                     trace_id: str | None = None, trace_request_id: str | None = None,
                     mutation_id: str | None = None) -> dict:
    """Inspect this controller's retained outcomes; ok means query serviced.

    Select exactly one environment or label. Current observation is opt-in and
    cannot be combined with a continuation cursor. No recovery or work starts.
    """
    if trace_id is not None or trace_request_id is not None or mutation_id is not None:
        return _trace('inspect', project_dir=project_dir, remote=remote, environment=environment,
                      label=label, operation_id=operation_id, request_id=request_id, observe=observe,
                      limit=limit, cursor=cursor, trace_id=trace_id,
                      trace_request_id=trace_request_id, mutation_id=mutation_id)
    return inspect_with_factory(_service_factory, project_dir=project_dir, remote=remote,
                                environment=environment, label=label, operation_id=operation_id,
                                request_id=request_id, observe=observe, limit=10 if limit is None else limit, cursor=cursor)


def _trace(action, **values):
    from sandbox.delivery.trace_service import trace_with_factory
    return trace_with_factory(_trace_factory, action, **values)


def delivery_trace_capabilities() -> dict:
    """Read executing-controller trace support without creating state."""
    return _trace('trace-capabilities')


def delivery_trace_start(project_dir: str, trace_request_id: str, input: dict) -> dict:
    """Retain a diagnostic invocation. This does not start deployment work."""
    return _trace('trace-start', project_dir=project_dir, trace_request_id=trace_request_id, input_json=input)


def delivery_trace_record(project_dir: str, trace_id: str, mutation_id: str,
                          expected_sequence: int, input: dict) -> dict:
    """Record bounded invocation evidence under the original trace identity."""
    return _trace('trace-record', project_dir=project_dir, trace_id=trace_id,
                  mutation_id=mutation_id, expected_sequence=expected_sequence, input_json=input)


def delivery_trace_owner_status(project_dir: str, producer: str, parent_request_id: str,
                                publication_id: str | None = None) -> dict:
    """Read the retained parent and original publication receipt without effects."""
    return _trace('trace-owner-status', project_dir=project_dir, producer=producer,
                  parent_request_id=parent_request_id, publication_id=publication_id)


def delivery_trace_owner_record(project_dir: str, producer: str, parent_request_id: str,
                                publication_id: str, expected_sequence: int, input: dict) -> dict:
    """Publish diagnostic worker evidence; original workload authority is unchanged."""
    return _trace('trace-owner-record', project_dir=project_dir, producer=producer,
                  parent_request_id=parent_request_id, publication_id=publication_id,
                  expected_sequence=expected_sequence, input_json=input)


def register(server, dependencies):
    configure(delivery_service_factory=dependencies.require("delivery_service_factory"),
              trace_service_factory=dependencies.require("trace_service_factory"))
    for tool in (delivery_inspect, delivery_trace_capabilities, delivery_trace_start,
                 delivery_trace_record, delivery_trace_owner_status, delivery_trace_owner_record):
        server.tool()(tool)

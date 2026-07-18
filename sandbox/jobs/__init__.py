"""Runtime-neutral durable job contracts and mechanisms."""

from .manifest import JobComponentRegistry, JobComponentSpec

__all__ = ["JobComponentRegistry", "JobComponentSpec"]

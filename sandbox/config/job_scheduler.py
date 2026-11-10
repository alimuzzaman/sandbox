"""Pure durable-job scheduler policy normalization.

The scheduler runs on the host that owns the job registry, so its slot limit
and resource floors are machine configuration of that host: the
``jobs.scheduler`` block of its own ``sandbox.yml`` / ``sandbox.local.yml``.
This module loads nothing and inspects nothing; it turns one raw block into a
validated :class:`JobSchedulerPolicy` or refuses it with a stable code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


MAX_PARALLEL_LIMIT = 32
MIN_FREE_MEMORY_FLOOR_MB = 512
MIN_FREE_DISK_FLOOR_MB = 2048
# Upper bounds only catch unit mistakes (a value meant as bytes); they are far
# above any host this runs on.
MAX_FREE_MEMORY_MB = 1024 * 1024
MAX_FREE_DISK_MB = 64 * 1024 * 1024


class JobSchedulerConfigError(ValueError):
    """A ``jobs.scheduler`` setting was rejected; ``code`` is stable."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class JobSchedulerPolicy:
    max_parallel: int = 2
    min_free_memory_mb: int = MIN_FREE_MEMORY_FLOOR_MB
    min_free_disk_mb: int = MIN_FREE_DISK_FLOOR_MB

    def as_mapping(self) -> dict[str, int]:
        return asdict(self)


DEFAULT_POLICY = JobSchedulerPolicy()
_KEYS = frozenset(DEFAULT_POLICY.as_mapping())


def _integer(raw: Mapping[str, Any], name: str, low: int, high: int, code: str) -> int:
    value = raw.get(name, getattr(DEFAULT_POLICY, name))
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise JobSchedulerConfigError(
            f"jobs.scheduler.{name} must be an integer from {low} to {high}", code)
    return value


def normalize_job_scheduler(raw: Any) -> JobSchedulerPolicy:
    """Validate one raw ``jobs.scheduler`` block; ``None`` means defaults."""
    if raw is None:
        return DEFAULT_POLICY
    if not isinstance(raw, Mapping):
        raise JobSchedulerConfigError(
            "jobs.scheduler must be an object", "job_policy_invalid")
    unknown = sorted(set(raw) - _KEYS, key=repr)
    if unknown:
        raise JobSchedulerConfigError(
            f"jobs.scheduler has unknown key: {unknown[0]!r}", "job_policy_unknown_key")
    return JobSchedulerPolicy(
        max_parallel=_integer(raw, "max_parallel", 1, MAX_PARALLEL_LIMIT,
                              "job_policy_invalid_max_parallel"),
        min_free_memory_mb=_integer(raw, "min_free_memory_mb", MIN_FREE_MEMORY_FLOOR_MB,
                                    MAX_FREE_MEMORY_MB, "job_policy_invalid_floor"),
        min_free_disk_mb=_integer(raw, "min_free_disk_mb", MIN_FREE_DISK_FLOOR_MB,
                                  MAX_FREE_DISK_MB, "job_policy_invalid_floor"),
    )


def raw_job_scheduler(result: Mapping) -> Any:
    """Return the raw ``jobs.scheduler`` block of a machine config, or None."""
    if "jobs" not in result:
        return None
    jobs = result["jobs"]
    if not isinstance(jobs, Mapping):
        raise JobSchedulerConfigError("jobs must be an object", "job_policy_invalid")
    return jobs.get("scheduler")


def machine_job_scheduler_provider(result: Mapping) -> JobSchedulerPolicy:
    """Machine-config provider for ``jobs.scheduler``."""
    return normalize_job_scheduler(raw_job_scheduler(result))


__all__ = (
    "DEFAULT_POLICY", "JobSchedulerConfigError", "JobSchedulerPolicy",
    "MAX_PARALLEL_LIMIT", "machine_job_scheduler_provider",
    "normalize_job_scheduler", "raw_job_scheduler",
)

"""Common project runtime, execution-profile, and output-profile schema."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from collections.abc import Mapping

from sandbox.jobs.models import ExecutionProfile, OutputProfile


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RUNTIME_KEYS = {
    "default", "remote", "workspace", "executionProfile", "outputProfile",
    "maxParallel", "retentionDays", "executionProfiles", "outputProfiles",
    "testPlans", "workspaces",
}
_EXECUTION_PROFILE_DECLARED = "_executionProfileDeclared"


class _NormalizedRuntimePolicy(dict):
    """Private carrier for normalization provenance, never an input schema."""

BUILTIN_EXECUTION_PROFILES = {
    "exec": {"timeoutSeconds": 900, "stallSeconds": 300, "cancelGraceSeconds": 20,
             "cancelOnStall": False, "cleanup": "retain"},
    "unit": {"timeoutSeconds": 1800, "stallSeconds": 300, "cancelGraceSeconds": 20,
             "cancelOnStall": False, "cleanup": "retain"},
    "integration": {"timeoutSeconds": 3600, "stallSeconds": 600, "cancelGraceSeconds": 30,
                    "cancelOnStall": False, "cleanup": "retain"},
    "e2e": {"timeoutSeconds": 14400, "stallSeconds": 900, "cancelGraceSeconds": 60,
            "cancelOnStall": False, "cleanup": "retain"},
    "ci": {"timeoutSeconds": 14400, "stallSeconds": 900, "cancelGraceSeconds": 60,
           "cancelOnStall": False, "cleanup": "retain"},
    "overall": {"timeoutSeconds": 21600, "stallSeconds": 1200, "cancelGraceSeconds": 60,
                "cancelOnStall": False, "cleanup": "retain"},
    "overnight": {"timeoutSeconds": 86400, "stallSeconds": 3600, "cancelGraceSeconds": 120,
                  "cancelOnStall": False, "cleanup": "retain"},
}

BUILTIN_OUTPUT_PROFILES = {
    "full": {"mode": "full"},
    "smart": {"mode": "smart", "heartbeatSeconds": 30, "maxBytes": 65536,
              "maxEvents": 500, "deduplicate": True},
    "errors": {"mode": "errors", "before": 2, "after": 5},
    "sampled": {"mode": "sampled", "everyLines": 20, "heartbeatSeconds": 30},
    "quiet": {"mode": "quiet"},
}


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE.fullmatch(value):
        raise ValueError(f"runtime {label} is invalid")
    return value


def _whole(value: object, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"runtime {label} must be between {low} and {high}")
    return value


def _execution(name: str, value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"execution profile {name!r} must be an object")
    allowed = {"timeoutSeconds", "stallSeconds", "cancelGraceSeconds", "cancelOnStall", "cleanup"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"execution profile {name!r} has unknown keys: {sorted(unknown)}")
    if "timeoutSeconds" not in value:
        raise ValueError(f"execution profile {name!r} requires timeoutSeconds")
    profile = ExecutionProfile(
        name, value["timeoutSeconds"], value.get("stallSeconds", 300),
        value.get("cancelGraceSeconds", 20), value.get("cancelOnStall", False),
        value.get("cleanup", "retain"),
    )
    return {
        "timeoutSeconds": profile.timeout_seconds,
        "stallSeconds": profile.stall_seconds,
        "cancelGraceSeconds": profile.cancel_grace_seconds,
        "cancelOnStall": profile.cancel_on_stall,
        "cleanup": profile.cleanup,
    }


def _output(name: str, value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"output profile {name!r} must be an object")
    aliases = {
        "mode": "mode", "everyLines": "every_lines", "everyEvents": "every_events",
        "everySeconds": "every_seconds", "include": "include", "exclude": "exclude",
        "before": "before", "after": "after", "deduplicate": "deduplicate",
        "timestamps": "timestamps", "streamPrefixes": "stream_prefixes",
        "heartbeatSeconds": "heartbeat_seconds", "maxBytes": "max_bytes",
        "maxEvents": "max_events",
    }
    unknown = set(value) - set(aliases)
    if unknown:
        raise ValueError(f"output profile {name!r} has unknown keys: {sorted(unknown)}")
    kwargs = {aliases[key]: tuple(item) if key in {"include", "exclude"}
              and not isinstance(item, tuple) else item for key, item in value.items()}
    profile = OutputProfile(name, **kwargs)
    result = {key: value[key] for key in value}
    result.setdefault("mode", profile.mode)
    return result


def _validate_plans(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("runtime testPlans must be an object")
    result = {}
    for name, plan in value.items():
        _name(name, "test plan name")
        if (not isinstance(plan, Mapping) or set(plan) - {"executionProfile", "outputProfile", "maxParallel", "steps"}
                or not isinstance(plan.get("steps"), list) or not plan["steps"]):
            raise ValueError(f"test plan {name!r} requires non-empty steps")
        if "executionProfile" in plan:
            _name(plan["executionProfile"], "test plan execution profile")
        if "outputProfile" in plan:
            _name(plan["outputProfile"], "test plan output profile")
        if "maxParallel" in plan:
            _whole(plan["maxParallel"], "test plan maxParallel", 1, 64)
        step_ids = set()
        for step in plan["steps"]:
            if (not isinstance(step, Mapping) or set(step) - {"id", "argv", "needs", "parallelSafe", "workspace", "artifacts"}
                    or not isinstance(step.get("id"), str) or not isinstance(step.get("argv"), list)
                    or not step["argv"]):
                raise ValueError(f"test plan {name!r} has an invalid step")
            _name(step["id"], "test plan step id")
            if step["id"] in step_ids or any(not isinstance(item, str) or not item for item in step["argv"]):
                raise ValueError(f"test plan {name!r} has an invalid step")
            step_ids.add(step["id"])
            if "needs" in step and (not isinstance(step["needs"], list)
                                    or any(not isinstance(item, str) for item in step["needs"])):
                raise ValueError(f"test plan {name!r} has an invalid step")
            if "parallelSafe" in step and not isinstance(step["parallelSafe"], bool):
                raise ValueError(f"test plan {name!r} has an invalid step")
            if "workspace" in step:
                _name(step["workspace"], "test plan workspace")
            if "artifacts" in step and (not isinstance(step["artifacts"], list)
                                        or any(not isinstance(item, str) or not item for item in step["artifacts"])):
                raise ValueError(f"test plan {name!r} has an invalid step")
        result[name] = copy.deepcopy(dict(plan))
    return result


def _validate_workspaces(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("runtime workspaces must be an object")
    result = {}
    allowed = {"persistent", "allowParallelSafe", "executionProfile", "outputProfile"}
    for name, policy in value.items():
        _name(name, "workspace name")
        if not isinstance(policy, Mapping) or set(policy) - allowed:
            raise ValueError(f"workspace {name!r} policy is invalid")
        result[name] = copy.deepcopy(dict(policy))
    return result


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    """Fully resolved, serializable execution policy for one durable job.

    Resolution happens at the caller's target boundary.  Remote controllers
    receive this immutable value rather than re-reading their own project
    policy, which may be at a different revision from the staged checkout.
    """

    execution_profile: str
    deadline_seconds: int
    deadline_source: str
    deadline_reminder: str | None
    stall_seconds: int
    cancel_grace_seconds: int
    cancel_on_stall: bool
    cleanup_policy: str
    provenance: dict[str, str]

    def as_dict(self) -> dict:
        return {
            "execution_profile": self.execution_profile,
            "deadline_seconds": self.deadline_seconds,
            "deadline_source": self.deadline_source,
            "deadline_reminder": self.deadline_reminder,
            "stall_seconds": self.stall_seconds,
            "cancel_grace_seconds": self.cancel_grace_seconds,
            "cancel_on_stall": self.cancel_on_stall,
            "cleanup_policy": self.cleanup_policy,
            "provenance": dict(self.provenance),
        }


def resolve_execution_policy(runtime_policy: object = None, *, workspace: str | None = None,
                             execution_profile: object = None, timeout_seconds: object = None,
                             stall_seconds: object = None, cancel_grace_seconds: object = None,
                             cancel_on_stall: object = None, cleanup_policy: object = None,
                             fallback_profile: str = "exec") -> ResolvedExecutionPolicy:
    """Resolve one policy with explicit > workspace > project > operation precedence.

    ``None`` is the only absence sentinel.  In particular, an explicit false
    ``cancel_on_stall`` is a real caller choice and must not be replaced by a
    profile's true value.
    """
    # Normalization keeps the public default (``exec``) for compatibility,
    # but callers that already normalized a project policy must retain whether
    # that value was actually declared.  Otherwise the injected default would
    # incorrectly outrank an operation-specific fallback profile.
    if isinstance(runtime_policy, _NormalizedRuntimePolicy):
        runtime = runtime_policy
    else:
        runtime = normalize_runtime_policy(runtime_policy)
    selected_workspace = workspace if workspace is not None else runtime["workspace"]
    if not isinstance(selected_workspace, str):
        raise ValueError("runtime workspace is invalid")
    workspace_policy = runtime["workspaces"].get(selected_workspace, {})
    if not isinstance(workspace_policy, Mapping):
        raise ValueError("runtime workspace policy is invalid")

    if execution_profile is not None:
        profile_name, profile_source = _name(execution_profile, "execution profile"), "explicit"
    elif workspace_policy.get("executionProfile") is not None:
        profile_name, profile_source = _name(workspace_policy["executionProfile"], "workspace execution profile"), "workspace"
    elif runtime[_EXECUTION_PROFILE_DECLARED]:
        profile_name, profile_source = runtime["executionProfile"], "project"
    else:
        profile_name, profile_source = fallback_profile, "operation"
    profile = runtime["executionProfiles"].get(profile_name)
    if not isinstance(profile, Mapping):
        raise ValueError(f"unknown execution profile {profile_name!r}")

    def choose(name: str, explicit: object, profile_key: str, validator):
        if explicit is not None:
            return validator(explicit), "explicit"
        return validator(profile[profile_key]), f"profile:{profile_source}"

    deadline, deadline_value_source = choose(
        "timeout", timeout_seconds, "timeoutSeconds",
        lambda value: _whole(value, "execution timeout", 1, 604800),
    )
    stall, stall_source = choose(
        "stall", stall_seconds, "stallSeconds",
        lambda value: _whole(value, "stall timeout", 1, 604800),
    )
    grace, grace_source = choose(
        "cancel grace", cancel_grace_seconds, "cancelGraceSeconds",
        lambda value: _whole(value, "cancellation grace", 1, 600),
    )
    if cancel_on_stall is not None:
        if not isinstance(cancel_on_stall, bool):
            raise ValueError("cancel on stall must be boolean")
        cancel, cancel_source = cancel_on_stall, "explicit"
    else:
        cancel, cancel_source = profile["cancelOnStall"], f"profile:{profile_source}"
    if cleanup_policy is not None:
        if cleanup_policy not in {"retain", "always", "on-success", "ephemeral"}:
            raise ValueError("cleanup policy is invalid")
        cleanup, cleanup_source = cleanup_policy, "explicit"
    else:
        cleanup, cleanup_source = profile["cleanup"], f"profile:{profile_source}"
    deadline_source = "explicit" if timeout_seconds is not None else f"profile:{profile_name}"
    return ResolvedExecutionPolicy(
        execution_profile=profile_name, deadline_seconds=deadline,
        deadline_source=deadline_source,
        deadline_reminder=None if timeout_seconds is not None else (
            f"deadline supplied by {deadline_source}; pass an explicit timeout to override it"),
        stall_seconds=stall, cancel_grace_seconds=grace, cancel_on_stall=cancel,
        cleanup_policy=cleanup,
        provenance={
            "execution_profile": profile_source,
            "deadline": deadline_value_source,
            "stall": stall_source,
            "cancel_grace": grace_source,
            "cancel_on_stall": cancel_source,
            "cleanup": cleanup_source,
        },
    )


def execution_policy_from_wire(value: object) -> ResolvedExecutionPolicy:
    """Validate a previously resolved policy without consulting local config."""
    if not isinstance(value, Mapping) or set(value) != {
            "execution_profile", "deadline_seconds", "deadline_source", "deadline_reminder",
            "stall_seconds", "cancel_grace_seconds", "cancel_on_stall", "cleanup_policy", "provenance"}:
        raise ValueError("execution policy wire value is invalid")
    profile = _name(value["execution_profile"], "execution profile")
    source = value["deadline_source"]
    reminder = value["deadline_reminder"]
    provenance = value["provenance"]
    if (not isinstance(source, str) or not source or
            (reminder is not None and not isinstance(reminder, str)) or
            not isinstance(provenance, Mapping) or
            set(provenance) != {"execution_profile", "deadline", "stall", "cancel_grace", "cancel_on_stall", "cleanup"} or
            any(not isinstance(item, str) or not item for item in provenance.values())):
        raise ValueError("execution policy wire value is invalid")
    cancel_on_stall = value["cancel_on_stall"]
    cleanup_policy = value["cleanup_policy"]
    if not isinstance(cancel_on_stall, bool):
        raise ValueError("cancel on stall must be boolean")
    if cleanup_policy not in {"retain", "always", "on-success", "ephemeral"}:
        raise ValueError("cleanup policy is invalid")
    return ResolvedExecutionPolicy(
        execution_profile=profile,
        deadline_seconds=_whole(value["deadline_seconds"], "execution timeout", 1, 604800),
        deadline_source=source, deadline_reminder=reminder,
        stall_seconds=_whole(value["stall_seconds"], "stall timeout", 1, 604800),
        cancel_grace_seconds=_whole(value["cancel_grace_seconds"], "cancellation grace", 1, 600),
        cancel_on_stall=cancel_on_stall,
        cleanup_policy=cleanup_policy,
        provenance=dict(provenance),
    )


def normalize_runtime_policy(value: object = None) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("runtime configuration must be an object")
    is_normalized = isinstance(value, _NormalizedRuntimePolicy)
    if _EXECUTION_PROFILE_DECLARED in value and not is_normalized:
        raise ValueError("runtime configuration has an internal execution profile marker")
    unknown = set(value) - _RUNTIME_KEYS - ({_EXECUTION_PROFILE_DECLARED} if is_normalized else set())
    if unknown:
        raise ValueError(f"runtime configuration has unknown keys: {sorted(unknown)}")
    execution_profile_declared = value.get(_EXECUTION_PROFILE_DECLARED, "executionProfile" in value)
    if not isinstance(execution_profile_declared, bool):
        raise ValueError("runtime execution profile declaration is invalid")
    default = value.get("default", "local")
    if default not in {"local", "remote"}:
        raise ValueError("runtime default must be local or remote")
    remote = value.get("remote")
    if remote is not None:
        remote = _name(remote, "remote")
    if default == "remote" and not remote:
        raise ValueError("runtime remote is required when default is remote")
    workspace = _name(value.get("workspace", "default"), "workspace")
    executions = {name: _execution(name, profile)
                  for name, profile in BUILTIN_EXECUTION_PROFILES.items()}
    custom_executions = value.get("executionProfiles", {})
    if not isinstance(custom_executions, Mapping):
        raise ValueError("runtime executionProfiles must be an object")
    for name, profile in custom_executions.items():
        executions[_name(name, "execution profile name")] = _execution(name, profile)
    outputs = {name: _output(name, profile) for name, profile in BUILTIN_OUTPUT_PROFILES.items()}
    custom_outputs = value.get("outputProfiles", {})
    if not isinstance(custom_outputs, Mapping):
        raise ValueError("runtime outputProfiles must be an object")
    for name, profile in custom_outputs.items():
        outputs[_name(name, "output profile name")] = _output(name, profile)
    execution_name = _name(value.get("executionProfile", "exec"), "execution profile")
    output_name = _name(value.get("outputProfile", "smart"), "output profile")
    if execution_name not in executions or output_name not in outputs:
        raise ValueError("runtime selected profile is not defined")
    plans = _validate_plans(value.get("testPlans"))
    for name, plan in plans.items():
        if plan.get("executionProfile", execution_name) not in executions:
            raise ValueError(f"test plan {name!r} references an unknown execution profile")
        if plan.get("outputProfile", output_name) not in outputs:
            raise ValueError(f"test plan {name!r} references an unknown output profile")
    workspaces = _validate_workspaces(value.get("workspaces"))
    for name, policy in workspaces.items():
        if (policy.get("executionProfile") is not None
                and _name(policy["executionProfile"], "workspace execution profile") not in executions):
            raise ValueError(f"workspace {name!r} references an unknown execution profile")
        if (policy.get("outputProfile") is not None
                and _name(policy["outputProfile"], "workspace output profile") not in outputs):
            raise ValueError(f"workspace {name!r} references an unknown output profile")
    return _NormalizedRuntimePolicy({
        "default": default, "remote": remote, "workspace": workspace,
        _EXECUTION_PROFILE_DECLARED: execution_profile_declared,
        "executionProfile": execution_name, "outputProfile": output_name,
        "maxParallel": _whole(value.get("maxParallel", 4), "maxParallel", 1, 64),
        "retentionDays": _whole(value.get("retentionDays", 7), "retentionDays", 1, 365),
        "executionProfiles": executions, "outputProfiles": outputs,
        "testPlans": plans,
        "workspaces": workspaces,
    })

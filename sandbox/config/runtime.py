"""Common project runtime, execution-profile, and output-profile schema."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping

from sandbox.jobs.models import ExecutionProfile, OutputProfile


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RUNTIME_KEYS = {
    "default", "remote", "workspace", "executionProfile", "outputProfile",
    "maxParallel", "retentionDays", "executionProfiles", "outputProfiles",
    "testPlans", "workspaces",
}

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


def normalize_runtime_policy(value: object = None) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("runtime configuration must be an object")
    unknown = set(value) - _RUNTIME_KEYS
    if unknown:
        raise ValueError(f"runtime configuration has unknown keys: {sorted(unknown)}")
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
    return {
        "default": default, "remote": remote, "workspace": workspace,
        "executionProfile": execution_name, "outputProfile": output_name,
        "maxParallel": _whole(value.get("maxParallel", 4), "maxParallel", 1, 64),
        "retentionDays": _whole(value.get("retentionDays", 7), "retentionDays", 1, 365),
        "executionProfiles": executions, "outputProfiles": outputs,
        "testPlans": plans,
        "workspaces": _validate_workspaces(value.get("workspaces")),
    }

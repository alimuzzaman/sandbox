"""Normalize WordPress backend selection without conflating job runtime policy."""

from __future__ import annotations

from collections.abc import Mapping
import copy


_KEYS = {"mode", "adapter", "php", "database", "webServer", "resources", "egress"}
_MODES = {"compose": "compose", "incumbent-native": "incumbent_native",
          "managed-native": "managed_native"}


def raw_wordpress_runtime_layer(document):
    if not isinstance(document, Mapping):
        raise ValueError("project configuration must be an object")
    value = document.get("wordpressRuntime", {})
    if value is None: value = {}
    if not isinstance(value, Mapping):
        raise ValueError("wordpressRuntime must be an object")
    return copy.deepcopy(dict(value))


def _validate(layer, label):
    if not isinstance(layer, Mapping):
        raise ValueError(f"wordpress runtime {label} must be an object")
    unknown = set(layer) - _KEYS
    if unknown:
        raise ValueError(f"wordpressRuntime has unknown keys: {sorted(unknown)}")
    result = copy.deepcopy(dict(layer))
    if "mode" in result and result["mode"] not in _MODES:
        raise ValueError("wordpressRuntime mode must be compose, incumbent-native, or managed-native")
    for key in ("adapter", "php", "database", "webServer"):
        if key in result and (not isinstance(result[key], str) or not result[key]
                              or any(ord(char) < 32 or ord(char) == 127 for char in result[key])):
            raise ValueError(f"wordpressRuntime {key} is invalid")
    if "resources" in result and not isinstance(result["resources"], Mapping):
        raise ValueError("wordpressRuntime resources must be an object")
    if "egress" in result and not isinstance(result["egress"], list):
        raise ValueError("wordpressRuntime egress must be a list")
    return result


def normalize_wordpress_runtime(result):
    raw = result.get("_wordpress_runtime_raw", {})
    if not isinstance(raw, Mapping):
        raise ValueError("wordpress runtime provenance must be an object")
    project = _validate(raw.get("project", {}), "project requirement")
    machine = _validate(raw.get("machine_override", {}), "machine override")
    selected = {**project, **machine}
    requested = selected.get("mode", "compose")
    requested_mode = _MODES[requested]
    machine_mode = machine.get("mode")
    if requested_mode != "compose" and machine_mode is None:
        mode, source, explicit = "compose", "project_requirement", False
        reason = "explicit_selection_required"
    else:
        mode = _MODES[machine_mode] if machine_mode is not None else "compose"
        source = "machine_override" if machine else "default"
        explicit = bool(machine)
        reason = "selected"
    adapter = selected.get("adapter")
    if mode == "compose":
        adapter = "compose"
    elif not adapter:
        raise ValueError("native wordpressRuntime requires adapter")
    return {
        "mode": mode, "adapter": adapter, "source": source, "explicit": explicit,
        "requestedMode": requested_mode, "reason": reason,
        "php": selected.get("php"), "database": selected.get("database"),
        "webServer": selected.get("webServer"),
        "resources": copy.deepcopy(selected.get("resources", {})),
        "egress": copy.deepcopy(selected.get("egress", [])),
        "projectRequirements": project,
    }

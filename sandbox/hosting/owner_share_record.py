"""Public applied sharing facts. These records select targets; they issue no grant."""
from __future__ import annotations

import copy
import hashlib
import json
import re


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def target_digest(validated: dict) -> str:
    """Bind WP paths/services and routes into the actual gate configuration."""
    return digest({"project": validated["project"], "environment": validated["environment"],
        "routes": validated["routes"], "autologin": validated.get("autologin"),
        "basic_auth": bool(validated.get("basic_auth")),
        "compose": {key: validated["compose"][key] for key in ("service", "background_services")
                    if key in validated["compose"]}})


def build(validated: dict, stack_record: dict | None) -> dict | None:
    from sandbox.hosting import login_service
    declaration = validated.get("login_service")
    record = stack_record or {}
    if not declaration or not declaration.get("share"):
        return None
    # runtime_config explicitly excludes credential values and credential names.
    config = login_service.runtime_config(declaration, target_digest=target_digest(validated))
    return parse({"schema": 1, "copy_id": record.get("copy_id"),
        "target_digest": target_digest(validated),
        "uses": list(record.get("uses") or []), "gate_config_digest": digest(config),
        "validated": {"project": validated["project"], "environment": validated["environment"],
            "routes": copy.deepcopy(validated["routes"]),
            "compose": {key: copy.deepcopy(validated["compose"][key])
                        for key in ("service", "background_services") if key in validated["compose"]},
            "basic_auth": bool(validated.get("basic_auth")),
            "autologin": copy.deepcopy(validated.get("autologin")),
            "login_service": {key: copy.deepcopy(declaration[key]) for key in
                ("enabled_until", "allowed_host_suffixes", "session_ttl_seconds", "entries", "share")}}})


def parse(value: object) -> dict:
    if (type(value) is not dict or set(value) != {"schema", "copy_id", "uses", "gate_config_digest", "target_digest", "validated"}
            or type(value.get("schema")) is not int or value["schema"] != 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("gate_config_digest")))
            or value.get("copy_id") is not None and not re.fullmatch(r"[0-9a-f]{8}", str(value["copy_id"]))
            or type(value.get("uses")) is not list or len(value["uses"]) > 4
            or any(type(item) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", item) for item in value["uses"])
            or len(set(value["uses"])) != len(value["uses"])):
        raise ValueError("applied share facts unavailable")
    facts = value["validated"]
    if (type(facts) is not dict or set(facts) != {"project", "environment", "routes", "compose", "basic_auth", "autologin", "login_service"}
            or type(facts["login_service"]) is not dict
            or set(facts["login_service"]) != {"enabled_until", "allowed_host_suffixes", "session_ttl_seconds", "entries", "share"}
            or type(facts["compose"]) is not dict or set(facts["compose"]) - {"service", "background_services"}
            or type(facts["routes"]) is not list or not 1 <= len(facts["routes"]) <= 64):
        raise ValueError("applied share facts unavailable")
    # Bound the private declaration as well as the outer record. It is not a
    # replacement manifest: only apply's validated projection writes this field.
    encoded = json.dumps(value, sort_keys=True)
    if len(encoded.encode()) > 49152:
        raise ValueError("applied share facts exceed bound")
    if value["target_digest"] != target_digest(facts):
        raise ValueError("applied share target digest differs")
    return json.loads(encoded)

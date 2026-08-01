from __future__ import annotations

from pathlib import Path
import re

from .descriptors import _load_mapping
from .domains import raw_domain_layer
from .wordpress_runtime import raw_wordpress_runtime_layer

_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DEFAULT_RESOURCES = {"cpus": 2.0, "memoryMB": 4096, "pids": 512}


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def _safe_text(value: object) -> bool:
    return isinstance(value, str) and not any(ord(char) < 32 or ord(char) == 127 for char in value)


class ComposeSchemaProvider:
    """Normalize the small, explicit Compose project descriptor."""

    def resolve(self, root: Path, *, label: str | None = None) -> dict:
        if label is not None and (not isinstance(label, str) or not _SAFE_LABEL.fullmatch(label)):
            raise ValueError("compose configuration label is invalid")
        config_path = next((root / name for name in ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml") if (root / name).exists()), None)
        if config_path is None:
            raise ValueError("generic Compose project requires sandbox.config.json or sandbox.config.yml")
        document = _load_mapping(config_path)
        project_domains = raw_domain_layer(document)
        project_runtime = raw_wordpress_runtime_layer(document)
        machine_domains = {}
        machine_runtime = {}
        override = next((root / name for name in (
            "sandbox.config.override.json", "sandbox.config.override.yml",
            "sandbox.config.override.yaml",
        ) if (root / name).exists()), None)
        if override is not None:
            override_doc = _load_mapping(override)
            document["compose"] = {**document.get("compose", {}), **override_doc.get("compose", {})}
            document["runtime"] = {**document.get("runtime", {}), **override_doc.get("runtime", {})}
            machine_domains.update(raw_domain_layer(override_doc))
            machine_runtime.update(raw_wordpress_runtime_layer(override_doc))
        if label:
            override = next((root / f"sandbox.config.{label}{suffix}" for suffix in (".json", ".yml", ".yaml") if (root / f"sandbox.config.{label}{suffix}").exists()), None)
            if override is not None:
                override_doc = _load_mapping(override)
                document["compose"] = {**document.get("compose", {}), **override_doc.get("compose", {})}
                document["runtime"] = {**document.get("runtime", {}), **override_doc.get("runtime", {})}
                machine_domains.update(raw_domain_layer(override_doc))
                machine_runtime.update(raw_wordpress_runtime_layer(override_doc))
        compose = document.get("compose")
        if not isinstance(compose, dict):
            raise ValueError("compose project requires a compose descriptor")
        filename = compose.get("file")
        service = compose.get("service")
        port = compose.get("internal_port")
        health_path = compose.get("health_path")
        if not isinstance(filename, str) or not filename or not _safe_text(filename):
            raise ValueError("compose file is required")
        path = root / filename
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("compose file must stay within the project root") from exc
        if not isinstance(service, str) or not _SAFE_SERVICE.fullmatch(service):
            raise ValueError("compose service is required")
        if not _valid_port(port):
            raise ValueError("compose internal_port must be a valid port")
        if (not isinstance(health_path, str) or not health_path.startswith("/") or
                not _safe_text(health_path)):
            raise ValueError("compose health_path must start with /")
        http_port = compose.get("http_port")
        if http_port is not None and not _valid_port(http_port):
            raise ValueError("compose http_port must be a valid port")
        startup_timeout = compose.get("startupTimeoutSeconds", 120)
        if (isinstance(startup_timeout, bool) or not isinstance(startup_timeout, int)
                or not 30 <= startup_timeout <= 3600):
            raise ValueError("compose startupTimeoutSeconds must be between 30 and 3600")
        recreate_on_ensure = compose.get("recreateOnEnsure", False)
        if not isinstance(recreate_on_ensure, bool):
            raise ValueError("compose recreateOnEnsure must be a boolean")
        resources = compose.get("resources", {})
        if resources is None:
            resources = {}
        if not isinstance(resources, dict) or set(resources) - set(_DEFAULT_RESOURCES):
            raise ValueError("compose resources must contain only cpus, memoryMB, and pids")
        cpus = resources.get("cpus", _DEFAULT_RESOURCES["cpus"])
        memory_mb = resources.get("memoryMB", _DEFAULT_RESOURCES["memoryMB"])
        pids = resources.get("pids", _DEFAULT_RESOURCES["pids"])
        if (isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or not 0.25 <= cpus <= 64):
            raise ValueError("compose resources.cpus must be between 0.25 and 64")
        if (isinstance(memory_mb, bool) or not isinstance(memory_mb, int) or not 128 <= memory_mb <= 262144):
            raise ValueError("compose resources.memoryMB must be between 128 and 262144")
        if (isinstance(pids, bool) or not isinstance(pids, int) or not 32 <= pids <= 65536):
            raise ValueError("compose resources.pids must be between 32 and 65536")
        tests = document.get("tests", {})
        if not isinstance(tests, dict):
            raise ValueError("compose tests must be an object")
        modes = tests.get("modes", {})
        if not isinstance(modes, dict):
            raise ValueError("compose tests.modes must be an object")
        normalized_modes = {}
        for name, command in modes.items():
            if not isinstance(name, str) or not _SAFE_LABEL.fullmatch(name):
                raise ValueError("compose test mode name is invalid")
            argv = command.get("argv") if isinstance(command, dict) else None
            if not isinstance(argv, list) or not argv or any(not _safe_text(item) or not item for item in argv):
                raise ValueError(f"compose test mode {name!r} requires a non-empty argv list")
            normalized_modes[name] = {"argv": list(argv)}
        return {"kind": "compose", "framework": document.get("framework") or document.get("preset"),
                "compose_file": str(path), "service": service,
                "internal_port": port, "health_path": health_path,
                "http_port": http_port,
                "startup_timeout_seconds": startup_timeout,
                "recreate_on_ensure": recreate_on_ensure,
                "resources": {"cpus": float(cpus), "memoryMB": memory_mb, "pids": pids},
                "tests": {"modes": normalized_modes},
                "display_name": root.name, "label": label or "default",
                "runtime": document.get("runtime"),
                "_domains_raw": {"project": project_domains,
                                 "machine_override": machine_domains},
                "_wordpress_runtime_raw": {"project": project_runtime,
                                            "machine_override": machine_runtime},
                "root": str(root), "source": config_path.name}

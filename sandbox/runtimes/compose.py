from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from .base import OperationRequest, OperationResult, RuntimeDependencies


_SAFE = re.compile(r"[^a-z0-9-]+")
_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


class ComposeAdapter:
    """Framework-neutral local Compose runtime.

    PHP, Node, Laravel/Sail, Astro, and Docker-native projects all use this
    adapter. The repository declares the Compose file, public service, port,
    and health path; the adapter never executes discovered package scripts.
    """

    adapter_id = "compose"
    kinds = ("compose",)

    def __init__(self, dependencies: RuntimeDependencies, registry: Any, *, timeout: float = 120.0):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("Compose timeout must be a finite positive number")
        self.dependencies = dependencies
        self.registry = registry
        self.timeout = timeout
        self.capabilities = frozenset({"ensure", "status", "start", "stop", "logs", "exec", "apply", "destroy", "open"})

    @staticmethod
    def _runtime_id(root: str, label: str, taken: set[str]) -> str:
        base = _SAFE.sub("-", Path(root).name.lower()).strip("-") or "project"
        suffix = "" if label == "default" else f"-{_SAFE.sub('-', label.lower()).strip('-')}"
        candidate = f"{base}{suffix}"[:48].strip("-") or "project"
        if candidate not in taken:
            return candidate
        digest = hashlib.sha256(f"{Path(root).resolve()}\0{label}".encode()).hexdigest()[:8]
        return f"{candidate[:39].rstrip('-')}-{digest}"

    def _descriptor(self, request: OperationRequest) -> dict[str, Any]:
        descriptor = self.registry.load_project_config(request.project_root, label=request.label)
        if not isinstance(descriptor, dict) or descriptor.get("kind") != "compose":
            raise ValueError("project is not configured as a generic Compose project")
        try:
            compose_file = Path(descriptor["compose_file"]).resolve()
            root = (Path(descriptor["root"]).resolve() if descriptor.get("root")
                    else Path(request.project_root).resolve())
            compose_file.relative_to(root)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError("Compose descriptor paths are invalid") from exc
        if not root.is_dir():
            raise ValueError("Compose project root does not exist")
        if not compose_file.is_file():
            raise ValueError(f"Compose file does not exist: {compose_file}")
        service = descriptor.get("service")
        if not isinstance(service, str) or not _SAFE_SERVICE.fullmatch(service):
            raise ValueError("Compose service name is invalid")
        if not _valid_port(descriptor.get("internal_port")):
            raise ValueError("Compose internal port is invalid")
        health_path = descriptor.get("health_path")
        if (not isinstance(health_path, str) or not health_path.startswith("/") or
                not health_path or any(ord(char) < 32 or ord(char) == 127 for char in health_path)):
            raise ValueError("Compose health path is invalid")
        if "http_port" in descriptor and descriptor["http_port"] is not None and not _valid_port(descriptor["http_port"]):
            raise ValueError("Compose HTTP port is invalid")
        return {**descriptor, "root": str(root), "compose_file": str(compose_file)}

    def _record(self, request: OperationRequest, **fields: Any) -> dict[str, Any] | None:
        return self.registry.registry_get(request.project_root, label=request.label)

    def _artifact_dir(self, runtime_id: str) -> Path:
        base = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox"))
        path = base / "runtime" / "projects" / runtime_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _compose(self, descriptor: dict[str, Any], runtime_id: str, *args: str):
        return self.dependencies.process.run(
            ["docker", "compose", "--project-name", f"sandbox-{runtime_id}",
             "--project-directory", descriptor["root"], "--file", descriptor["compose_file"], *args],
            cwd=descriptor["root"], timeout=self.timeout,
        )

    def _overlay(self, descriptor: dict[str, Any], runtime_id: str, http_port: int) -> Path:
        path = self._artifact_dir(runtime_id) / "sandbox.override.yaml"
        service = descriptor["service"]
        resources = descriptor.get("resources") or {"cpus": 2.0, "memoryMB": 4096, "pids": 512}
        path.write_text(
            "services:\n"
            f"  {service}:\n"
            "    ports:\n"
            f"      - \"127.0.0.1:{http_port}:{int(descriptor['internal_port'])}\"\n"
            f"    cpus: \"{float(resources['cpus']):g}\"\n"
            f"    mem_limit: \"{int(resources['memoryMB'])}m\"\n"
            f"    pids_limit: {int(resources['pids'])}\n"
        )
        return path

    def _compose_args(self, descriptor: dict[str, Any], runtime_id: str, *args: str) -> list[str]:
        overlay = self._overlay(descriptor, runtime_id, int(self._record_port(descriptor, runtime_id)))
        return ["--file", descriptor["compose_file"], "--file", str(overlay), *args]

    def _record_port(self, descriptor: dict[str, Any], runtime_id: str) -> int:
        record = self.registry.registry_find_instance(runtime_id) or {}
        if record.get("http_port"):
            return int(record["http_port"])
        preferred = descriptor.get("http_port")
        return int(self.dependencies.ports.allocate(int(preferred)) if preferred else self.dependencies.ports.allocate())

    def _operation_timeout(self, request: OperationRequest) -> float:
        """Return the bounded deadline supplied by a durable execution job."""
        value = request.arguments.get("timeout")
        if value is None:
            return self.timeout
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value <= 0):
            raise ValueError("generic Compose execution timeout must be a finite positive number")
        return float(value)

    def invoke(self, request: OperationRequest) -> OperationResult:
        descriptor = self._descriptor(request)
        op = request.operation
        record = self._record(request)
        taken = {str(e.get("instance")) for e in self.registry.registry_all().values() if e.get("instance")}
        runtime_id = str(record.get("instance")) if record else self._runtime_id(descriptor["root"], request.label, taken)
        http_port = int(record.get("http_port")) if record and record.get("http_port") else self._record_port(descriptor, runtime_id)
        overlay = self._overlay(descriptor, runtime_id, http_port)
        project_args = ["--project-name", f"sandbox-{runtime_id}", "--project-directory", descriptor["root"], "--file", descriptor["compose_file"], "--file", str(overlay)]
        service = descriptor["service"]

        if op == "ensure":
            config = self.dependencies.process.run(["docker", "compose", *project_args, "config", "--services"], cwd=descriptor["root"], timeout=30)
            if config.returncode != 0 or service not in config.stdout.split():
                raise ValueError(f"declared Compose service {service!r} was not found")
            started = self.dependencies.process.run(["docker", "compose", *project_args, "up", "-d", service], cwd=descriptor["root"], timeout=self.timeout)
            if started.returncode != 0:
                raise RuntimeError(started.stderr or "Compose failed to start")
            url = f"http://127.0.0.1:{http_port}"
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.dependencies.http.probe(url + descriptor["health_path"], timeout=2):
                    data = {"instance": runtime_id, "root": descriptor["root"], "label": request.label, "kind": "compose", "adapter": self.adapter_id, "service": service, "http_port": http_port, "url": url, "health_path": descriptor["health_path"], "framework": descriptor.get("framework"), "status": "ready"}
                    stored = {key: value for key, value in data.items() if key != "root"}
                    self.registry.registry_put(descriptor["root"], **stored)
                    return OperationResult(True, op, descriptor["root"], "compose", data)
                time.sleep(0.1)
            raise RuntimeError("generic Compose service did not pass its health check")

        if op == "status":
            result = self.dependencies.process.run(["docker", "compose", *project_args, "ps", "--format", "json"], cwd=descriptor["root"], timeout=30)
            data = {"instance": runtime_id, "root": descriptor["root"], "label": request.label, "kind": "compose", "adapter": self.adapter_id, "service": service, "http_port": http_port, "url": f"http://127.0.0.1:{http_port}", "status": "ready" if result.returncode == 0 else "error", "compose": result.stdout[-10000:]}
            return OperationResult(result.returncode == 0, op, descriptor["root"], "compose", data)

        commands = {"start": ["start", service], "stop": ["stop", service], "logs": ["logs", "--no-color", service], "apply": ["up", "-d", "--force-recreate", service], "destroy": ["down"]}
        if op == "exec":
            command = request.arguments.get("argv")
            if (not isinstance(command, (list, tuple)) or not command or
                    any(not isinstance(x, str) or not x or "\x00" in x for x in command)):
                raise ValueError("generic exec requires a non-empty argv list")
            commands[op] = ["exec", "-T", service, *command]
        if op not in commands:
            raise ValueError(f"unsupported Compose operation: {op}")
        result = self.dependencies.process.run(
            ["docker", "compose", *project_args, *commands[op]], cwd=descriptor["root"],
            timeout=self._operation_timeout(request) if op == "exec" else self.timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or f"Compose {op} failed")
        data = dict(record or {}, instance=runtime_id, root=descriptor["root"], label=request.label, kind="compose", adapter=self.adapter_id, service=service, http_port=http_port, url=f"http://127.0.0.1:{http_port}", status="stopped" if op in {"stop", "destroy"} else "ready", output=result.stdout[-10000:])
        if op == "destroy":
            self.registry.registry_remove(descriptor["root"], label=request.label)
            # Registry removal is the source-of-truth mutation; remove the
            # corresponding aggregate-proxy route immediately afterward. The
            # proxy facade owns Caddy regeneration/reload, so the adapter does
            # not duplicate Caddy policy or use a destructive Compose flag.
            domain = (record or {}).get("domain")
            if domain:
                self.dependencies.proxy.remove(str(domain))
            self._artifact_dir(runtime_id).mkdir(parents=True, exist_ok=True)
        else:
            stored = {key: value for key, value in data.items() if key != "root"}
            self.registry.registry_put(descriptor["root"], **stored)
        return OperationResult(True, op, descriptor["root"], "compose", data)

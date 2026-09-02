from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from sandbox.config.instance_lifecycle import normalize_instance_lifecycle

from .base import OperationRequest, OperationResult, RuntimeDependencies


_SAFE = re.compile(r"[^a-z0-9-]+")
_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SAFE_RUNTIME_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_WORKSPACE_FAMILY_MARKER = re.compile(r"-workspace-[0-9a-f]{14}")

# ``BoundedProcessRunner`` already enforces this limit for the normal
# dependency used by the adapter.  Keep the limit at this seam as well so a
# custom process dependency cannot turn an adapter result (or a durable job
# receipt carrying it) into an unbounded payload.  Preserve both edges when a
# caller supplies more than the bounded amount: the beginning usually carries
# the command/setup context while the end usually carries the assertion and
# exit summary that explains a failed test.
_MAX_EXEC_OUTPUT = 1_048_576
_EXEC_OUTPUT_TRUNCATION_MARKER = "\n...[output truncated]...\n"
_MISSING_NETWORK = re.compile(
    r"\bnetwork\b[^\r\n]{0,240}\bnot found\b", re.IGNORECASE,
)


def _bounded_exec_output(value: object) -> str:
    """Return one bounded, edge-preserving execution stream."""
    text = value if isinstance(value, str) else str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    marker = _EXEC_OUTPUT_TRUNCATION_MARKER.encode("utf-8")
    if len(encoded) <= _MAX_EXEC_OUTPUT:
        return text
    if _MAX_EXEC_OUTPUT < len(marker):
        return encoded[:_MAX_EXEC_OUTPUT].decode(errors="ignore")
    available = _MAX_EXEC_OUTPUT - len(marker)
    head = available // 2
    tail = available - head
    # Ignore only incomplete UTF-8 endpoints; replacing them can expand the
    # byte count beyond the declared bound.
    return (encoded[:head] + marker + encoded[-tail:]).decode(errors="ignore")


def _missing_network_failure(stdout: object, stderr: object) -> bool:
    """Classify one bounded Compose missing-network diagnostic."""
    evidence = "\n".join(
        value[-4096:] for value in (stdout, stderr)
        if isinstance(value, str) and value
    )
    return bool(_MISSING_NETWORK.search(evidence))


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def node_store_family_id(runtime_id: str) -> str:
    """Return one exact, collision-preserving Compose node-store family."""
    if not isinstance(runtime_id, str) or not _SAFE_RUNTIME_ID.fullmatch(runtime_id):
        raise ValueError("Compose runtime id is invalid for node-store family")
    markers = tuple(_WORKSPACE_FAMILY_MARKER.finditer(runtime_id))
    if not markers:
        return _node_store_family_component(runtime_id)
    if len(markers) != 1 or markers[0].end() != len(runtime_id):
        return runtime_id
    marker = markers[0]
    family = runtime_id[:marker.start()] + runtime_id[marker.end():]
    if not family or not _SAFE_RUNTIME_ID.fullmatch(family):
        raise ValueError("Compose node-store family is ambiguous")
    return _node_store_family_component(family)


def _node_store_family_component(runtime_id: str) -> str:
    """Fit one canonical runtime identity before the fixed workspace marker."""
    if len(runtime_id) <= 38:
        return runtime_id
    digest = hashlib.sha256(runtime_id.encode()).hexdigest()[:8]
    return f"{runtime_id[:29].rstrip('-')}-{digest}"


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
        self.capabilities = frozenset({
            "ensure", "status", "start", "stop", "resume", "suspend", "logs",
            "exec", "apply", "destroy", "open",
        })

    @staticmethod
    def _runtime_id(root: str, label: str, taken: set[str], *,
                    source_family: str | None = None,
                    require_source_family: bool = False) -> str:
        root_path = Path(root).resolve(strict=False)
        base = _SAFE.sub("-", root_path.name.lower()).strip("-") or "project"
        suffix = "" if label == "default" else f"-{_SAFE.sub('-', label.lower()).strip('-')}"
        markers = tuple(_WORKSPACE_FAMILY_MARKER.finditer(base))
        workspace_marker = None
        source_root = root_path
        if len(markers) == 1 and markers[0].end() == len(base):
            workspace_marker = markers[0].group(0)
            source_base = base[:markers[0].start()]
            if source_base:
                base = source_base
                source_root = root_path.parent / source_base
        candidate = f"{base}{suffix}"[:48].strip("-") or "project"
        digest = hashlib.sha256(
            f"{source_root.resolve(strict=False)}\0{label}".encode()
        ).hexdigest()[:8]
        collision = f"{candidate[:39].rstrip('-')}-{digest}"
        if workspace_marker is not None:
            plain = f"{_node_store_family_component(candidate)}{workspace_marker}"
            collided = f"{_node_store_family_component(collision)}{workspace_marker}"
            if source_family is not None:
                if (not _SAFE_RUNTIME_ID.fullmatch(source_family) or
                        _WORKSPACE_FAMILY_MARKER.search(source_family)):
                    raise ValueError("registered Compose source family is invalid")
                return f"{_node_store_family_component(source_family)}{workspace_marker}"
            if require_source_family:
                raise ValueError(
                    "opted-in Compose workspace requires a registered source family"
                )
            if collided in taken or collision in taken:
                return collided
            if plain in taken:
                return plain
            if candidate in taken:
                # A bare set cannot prove that the occupied candidate belongs
                # to this workspace's canonical source. Preserve isolation by
                # using the path-bound collision identity. The invoke path
                # supplies a registry-pinned source family when one exists.
                return collided
            return plain
        if candidate not in taken:
            return candidate
        return collision

    def _descriptor(self, request: OperationRequest) -> dict[str, Any]:
        config_file = request.arguments.get("config_file")
        if config_file is None:
            descriptor = self.registry.load_project_config(
                request.project_root, label=request.label,
            )
        else:
            descriptor = self.registry.load_project_config(
                request.project_root, label=request.label, config_file=config_file,
            )
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
        startup_timeout = descriptor.get("startup_timeout_seconds", self.timeout)
        if "startup_timeout_seconds" in descriptor and (
                isinstance(startup_timeout, bool) or not isinstance(startup_timeout, (int, float))
                or not math.isfinite(startup_timeout) or not 30 <= startup_timeout <= 3600):
            raise ValueError("Compose startup timeout is invalid")
        recreate_on_ensure = descriptor.get("recreate_on_ensure", False)
        if not isinstance(recreate_on_ensure, bool):
            raise ValueError("Compose recreate-on-ensure setting is invalid")
        lifecycle = normalize_instance_lifecycle(descriptor.get("instanceLifecycle"))
        return {**descriptor, "root": str(root), "compose_file": str(compose_file),
                "startup_timeout_seconds": float(startup_timeout),
                "recreate_on_ensure": recreate_on_ensure,
                "instanceLifecycle": lifecycle}

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
        content = (
            "services:\n"
            f"  {service}:\n"
            "    ports:\n"
            f"      - \"127.0.0.1:{http_port}:{int(descriptor['internal_port'])}\"\n"
            f"    cpus: \"{float(resources['cpus']):g}\"\n"
            f"    mem_limit: \"{int(resources['memoryMB'])}m\"\n"
            f"    pids_limit: {int(resources['pids'])}\n"
        )
        if descriptor.get("node_store") is True:
            family = node_store_family_id(runtime_id)
            volume = f"sandbox-nodestore-{family}"
            modules = f"/sandbox-node/node_modules/{runtime_id}"
            content += (
                "    volumes:\n"
                f"      - \"{volume}:/sandbox-node\"\n"
                "    environment:\n"
                "      SANDBOX_NODE_STORE: /sandbox-node/store\n"
                f"      SANDBOX_NODE_MODULES: {modules}\n"
                "      npm_config_store_dir: /sandbox-node/store\n"
                "volumes:\n"
                f"  {volume}:\n"
                f"    name: {volume}\n"
            )
        path.write_text(content)
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
        registry_records = tuple(self.registry.registry_all().values())
        taken = {str(e.get("instance")) for e in registry_records if e.get("instance")}
        source_family = None
        root_path = Path(descriptor["root"]).resolve(strict=False)
        root_markers = tuple(_WORKSPACE_FAMILY_MARKER.finditer(root_path.name.lower()))
        if len(root_markers) == 1 and root_markers[0].end() == len(root_path.name):
            source_root = root_path.with_name(root_path.name[:root_markers[0].start()])
            matches = {
                str(entry.get("instance"))
                for entry in registry_records
                if entry.get("instance")
                and entry.get("label", "default") == request.label
                and Path(str(entry.get("root", ""))).resolve(strict=False) == source_root
            }
            if len(matches) > 1:
                raise ValueError("registered Compose source family is ambiguous")
            if matches:
                source_family = matches.pop()
        runtime_id = (
            str(record.get("instance")) if record else
            self._runtime_id(
                descriptor["root"], request.label, taken,
                source_family=source_family,
                require_source_family=descriptor.get("node_store") is True,
            )
        )
        if descriptor.get("node_store") is True and len(root_markers) == 1:
            if source_family is None:
                raise ValueError(
                    "opted-in Compose workspace requires a registered source family"
                )
            if node_store_family_id(runtime_id) != node_store_family_id(source_family):
                raise ValueError(
                    "registered Compose workspace family does not match its source"
                )
        http_port = int(record.get("http_port")) if record and record.get("http_port") else self._record_port(descriptor, runtime_id)
        overlay = self._overlay(descriptor, runtime_id, http_port)
        project_args = ["--project-name", f"sandbox-{runtime_id}", "--project-directory", descriptor["root"], "--file", descriptor["compose_file"], "--file", str(overlay)]
        service = descriptor["service"]

        if op in {"resume", "suspend"} and record is None:
            raise ValueError(f"Compose {op} requires a provisioned instance")
        if op == "suspend" and descriptor["instanceLifecycle"]["mode"] != "idle_stop":
            raise ValueError("Compose suspend requires instanceLifecycle.mode idle_stop")

        if op == "ensure":
            config = self.dependencies.process.run(["docker", "compose", *project_args, "config", "--services"], cwd=descriptor["root"], timeout=30)
            if config.returncode != 0 or service not in config.stdout.split():
                raise ValueError(f"declared Compose service {service!r} was not found")
            up = ["docker", "compose", *project_args, "up", "-d"]
            if descriptor["recreate_on_ensure"]:
                up.append("--force-recreate")
            started = self.dependencies.process.run([*up, service], cwd=descriptor["root"], timeout=self.timeout)
            if started.returncode != 0:
                raise RuntimeError(started.stderr or "Compose failed to start")
            url = f"http://127.0.0.1:{http_port}"
            deadline = time.monotonic() + descriptor["startup_timeout_seconds"]
            while time.monotonic() < deadline:
                if self.dependencies.http.probe(url + descriptor["health_path"], timeout=2):
                    data = {"instance": runtime_id, "root": descriptor["root"], "label": request.label, "kind": "compose", "adapter": self.adapter_id, "service": service, "http_port": http_port, "url": url, "health_path": descriptor["health_path"], "framework": descriptor.get("framework"), "status": "ready", "instanceLifecycle": descriptor["instanceLifecycle"], "lifecycleState": "ready"}
                    stored = {key: value for key, value in data.items() if key != "root"}
                    self.registry.registry_put(descriptor["root"], **stored)
                    return OperationResult(True, op, descriptor["root"], "compose", data)
                time.sleep(0.1)
            logs = self.dependencies.process.run(
                ["docker", "compose", *project_args, "logs", "--no-color", "--tail", "100", service],
                cwd=descriptor["root"], timeout=30,
            )
            detail = "\n".join(part.strip() for part in (logs.stderr, logs.stdout) if part.strip())[-4096:]
            message = "generic Compose service did not pass its health check"
            raise RuntimeError(f"{message}:\n{detail}" if detail else message)

        if op == "status":
            result = self.dependencies.process.run(["docker", "compose", *project_args, "ps", "--format", "json"], cwd=descriptor["root"], timeout=30)
            compose_output = result.stdout[-10000:]
            states = []
            for line in compose_output.splitlines():
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict):
                    states.append(item)
            service_state = next(
                (item.get("State") for item in states
                 if item.get("Service") == service),
                None,
            )
            if result.returncode != 0:
                status = "error"
            elif service_state is None:
                # Preserve the historical ready result for older Compose
                # implementations that return non-JSON output, while an
                # explicit service row is authoritative when available.
                status = "ready" if not states else "stopped"
            else:
                status = "ready" if service_state == "running" else "stopped"
            lifecycle_state = (
                "ready" if status == "ready" else
                "asleep" if status == "stopped" and descriptor["instanceLifecycle"]["mode"] == "idle_stop" else
                "stopped" if status == "stopped" else
                "error"
            )
            data = {"instance": runtime_id, "root": descriptor["root"], "label": request.label, "kind": "compose", "adapter": self.adapter_id, "service": service, "http_port": http_port, "url": f"http://127.0.0.1:{http_port}", "status": status, "lifecycleState": lifecycle_state, "instanceLifecycle": descriptor["instanceLifecycle"], "compose": compose_output, "observation": {"source": "compose", "freshness": "live"}}
            return OperationResult(result.returncode == 0, op, descriptor["root"], "compose", data)

        commands = {
            "start": ["start", service],
            "stop": ["stop", service],
            # These names are deliberately narrower lifecycle contracts. They
            # preserve the Compose project and volumes; unlike ensure/destroy,
            # they never reconcile files or remove containers.
            "resume": ["start"],
            "suspend": ["stop", "--timeout", str(descriptor["instanceLifecycle"]["stopGraceSeconds"])],
            "logs": ["logs", "--no-color", service],
            "apply": ["up", "-d", "--force-recreate", service],
            "destroy": ["down"],
        }
        if op == "exec":
            command = request.arguments.get("argv")
            if (not isinstance(command, (list, tuple)) or not command or
                    any(not isinstance(x, str) or not x or "\x00" in x for x in command)):
                raise ValueError("generic exec requires a non-empty argv list")
            commands[op] = ["exec", "-T", service, *command]
        if op not in commands:
            raise ValueError(f"unsupported Compose operation: {op}")
        operation_timeout = (
            max(self.timeout, descriptor["instanceLifecycle"]["stopGraceSeconds"] + 10)
            if op == "suspend" else self.timeout
        )
        result = self.dependencies.process.run(
            ["docker", "compose", *project_args, *commands[op]], cwd=descriptor["root"],
            timeout=self._operation_timeout(request) if op == "exec" else operation_timeout,
        )
        if result.returncode != 0:
            if op == "exec":
                # Keep execution failures in the transport-neutral result
                # contract.  Raising here used to merge stdout and stderr into
                # a 4 KiB exception tail; the caller (and, for remote runs,
                # the outer durable supervisor) could no longer distinguish
                # test output from Compose diagnostics or recover the child
                # exit status.  A failed exec is observational and therefore
                # must not write a ready/stopped registry record.
                return OperationResult(
                    False, op, descriptor["root"], "compose",
                    {
                        "instance": runtime_id,
                        "root": descriptor["root"],
                        "label": request.label,
                        "kind": "compose",
                        "adapter": self.adapter_id,
                        "service": service,
                        "http_port": http_port,
                        "url": f"http://127.0.0.1:{http_port}",
                        "status": "error",
                        "stdout": _bounded_exec_output(result.stdout),
                        "stderr": _bounded_exec_output(result.stderr),
                        "exit_code": int(result.returncode),
                        "reason": {"code": "compose_exec_failed"},
                    },
                )
            if op == "start" and _missing_network_failure(result.stdout, result.stderr):
                recovery = (
                    f"./sb down --instance {runtime_id} && "
                    f"./sb up --instance {runtime_id}"
                )
                return OperationResult(
                    False, op, descriptor["root"], "compose",
                    {
                        "instance": runtime_id,
                        "status": "error",
                        "lifecycleState": "error",
                        "mutated": False,
                        "error": {
                            "code": "stale_container_network",
                            "message": (
                                "the managed Docker network is missing; no containers "
                                "or volumes were removed"
                            ),
                        },
                        "recovery": {"command": recovery},
                    },
                )
            detail = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
            raise RuntimeError(detail or f"Compose {op} failed")
        if op == "resume":
            url = f"http://127.0.0.1:{http_port}"
            deadline = time.monotonic() + descriptor["instanceLifecycle"]["wakeTimeoutSeconds"]
            while time.monotonic() < deadline:
                if self.dependencies.http.probe(url + descriptor["health_path"], timeout=2):
                    break
                time.sleep(0.1)
            else:
                data = dict(
                    record or {}, instance=runtime_id, root=descriptor["root"],
                    label=request.label, kind="compose", adapter=self.adapter_id,
                    service=service, http_port=http_port, url=url, status="error",
                    lifecycleState="error", instanceLifecycle=descriptor["instanceLifecycle"],
                    output=result.stdout[-10000:], mutated=True,
                    reason={"code": "resume_readiness_failed"},
                )
                return OperationResult(False, op, descriptor["root"], "compose", data)
        lifecycle_state = "asleep" if op in {"stop", "suspend"} and descriptor["instanceLifecycle"]["mode"] == "idle_stop" else "stopped" if op in {"stop", "suspend", "destroy"} else "ready"
        data = dict(record or {}, instance=runtime_id, root=descriptor["root"], label=request.label, kind="compose", adapter=self.adapter_id, service=service, http_port=http_port, url=f"http://127.0.0.1:{http_port}", status="stopped" if op in {"stop", "suspend", "destroy"} else "ready", lifecycleState=lifecycle_state, instanceLifecycle=descriptor["instanceLifecycle"], output=result.stdout[-10000:])
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

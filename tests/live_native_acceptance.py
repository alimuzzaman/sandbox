#!/usr/bin/env python3
"""Live-only Ubuntu acceptance harness for the managed-native runtime.

The harness deliberately uses the public ``./sb`` command surface.  It never
imports a runtime adapter, changes an evidence gate, or invokes systemd,
machinectl, Docker, nftables, or a database client directly.  Run it only on a
disposable Ubuntu 24.04 proof host after the reviewed interactive package
transaction.  One invocation proves one nginx/Apache pairing; run it again
with the servers reversed.

Every child command has a deadline and retained output is bounded.  A failed
probe is evidence, not a reason to skip later probes: the harness records the
failure, checks sibling and controller liveness, and continues to cleanup.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SB = REPOSITORY_ROOT / "sb"
MAX_CAPTURE_BYTES = 16 * 1024
MAX_EVENTS = 384
_GIT_REVISION_LENGTH = 40
# The fixed helper batch pays one sudo/controller round trip while preserving
# the four isolated kernel-effect probes, so the acceptance bound is 3 seconds.
PREFLIGHT_LIMIT_SECONDS = 3.0
# Status remains independently bounded.
STATUS_LIMIT_SECONDS = 3.0
WARM_START_LIMIT_SECONDS = 20.0
# A cold provision builds a fixed-size image, debootstraps a root filesystem and
# installs the web/PHP/database packages inside it. That is minutes of work and is
# not what the 20-second bound measures -- that bound is for a WARM ensure against an
# instance that already exists. The flat 180-second child deadline had never been
# exercised against a real cold build and killed every one of them.
COLD_PROVISION_DEADLINE_SECONDS = 2400
PROOF_CANDIDATE = "ubuntu-24.04-systemd-255"
TERMINAL_JOBS = {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}
ENTRY_PATHS = (
    "web", "cron", "wordpress_cli", "exec", "composer", "plugin_activation",
    "durable_job", "phpunit",
)
RESOURCE_PROBES = (
    "cpu", "memory", "pids", "runtime", "disk", "inodes", "fds",
    "connections", "io",
)


def _bounded(value: str, limit: int = MAX_CAPTURE_BYTES) -> dict[str, Any]:
    raw = value.encode("utf-8", "replace")
    clipped = raw[:limit]
    return {
        "text": clipped.decode("utf-8", "replace"),
        "bytes": len(raw),
        "truncated": len(raw) > limit,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _last_json(value: str) -> dict[str, Any] | None:
    for line in reversed(value.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_lines(value: str) -> list[dict[str, Any]]:
    values = []
    for line in value.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            values.append(parsed)
    return values


def _nested(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _enable_proof_candidate(environment: dict[str, str]) -> str:
    existing = environment.get("SANDBOX_NATIVE_PROOF_CANDIDATE")
    if existing is not None and existing != PROOF_CANDIDATE:
        raise ValueError("conflicting SANDBOX_NATIVE_PROOF_CANDIDATE is set")
    environment["SANDBOX_NATIVE_PROOF_CANDIDATE"] = PROOF_CANDIDATE
    return PROOF_CANDIDATE


def _candidate_result(event: dict[str, Any]) -> bool:
    payload = event.get("json") or {}
    candidate = payload.get("proof_candidate")
    adoptable = payload.get("adoptable")
    if candidate is None and isinstance(payload.get("runtime"), dict):
        candidate = payload["runtime"].get("proof_candidate")
        adoptable = payload["runtime"].get("adoptable")
    return candidate is True and adoptable is False


def _source_identity(root: Path = REPOSITORY_ROOT, *, run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Capture a bounded, non-secret identity for a live-proof artifact.

    A proof result without an exact revision cannot later establish what code
    was measured.  The worktree state is included rather than assumed clean so
    an operator can distinguish a committed source tree from a local patch.
    """
    def git(*args: str):
        return run(
            ("git", "-C", str(root), *args), capture_output=True, text=True,
            timeout=5, check=False,
        )

    try:
        revision_result = git("rev-parse", "HEAD")
        revision = str(revision_result.stdout or "").strip()
        status_result = git("status", "--porcelain=v1")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not capture proof source identity") from exc
    if revision_result.returncode != 0 or len(revision) != _GIT_REVISION_LENGTH \
            or any(char not in "0123456789abcdef" for char in revision.lower()):
        raise RuntimeError("proof source revision is unavailable")
    if status_result.returncode != 0:
        raise RuntimeError("proof source worktree state is unavailable")
    return {
        "revision": revision.lower(),
        "worktree_clean": not bool(str(status_result.stdout or "").strip()),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _durable_job_ids(events: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """Extract recorded durable-job IDs without trusting arbitrary event text."""
    identifiers = set()
    for event in events:
        payload = event.get("json") if isinstance(event, dict) else None
        identifier = payload.get("job_id") if isinstance(payload, dict) else None
        if isinstance(identifier, str) and identifier:
            identifiers.add(identifier)
    return tuple(sorted(identifiers))


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[:1] == (".git",):
            continue
        digest.update(str(relative).encode())
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"F")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


def _json_from_output(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("json")
    if isinstance(payload, dict):
        for key in ("stdout", "output", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, str):
                parsed = _last_json(candidate)
                if parsed is not None:
                    return parsed
    return _last_json(_nested(event, "stdout", "text") or "")


def _output_text(event: dict[str, Any]) -> str:
    payload = event.get("json")
    if isinstance(payload, dict):
        for key in ("stdout", "output", "data"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return _nested(event, "stdout", "text") or ""


class HostMessageQueue:
    """Real host SysV IPC sentinel, removed with compare-free IPC_RMID."""

    IPC_CREAT = 0o1000
    IPC_EXCL = 0o2000
    IPC_RMID = 0

    def __init__(self):
        library = ctypes.util.find_library("c")
        if not library:
            raise RuntimeError("host libc is unavailable for the IPC sentinel")
        self.libc = ctypes.CDLL(library, use_errno=True)
        self.libc.msgget.argtypes = (ctypes.c_int, ctypes.c_int)
        self.libc.msgget.restype = ctypes.c_int
        self.libc.msgctl.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
        self.libc.msgctl.restype = ctypes.c_int
        self.key = 0x61000000 | (os.getpid() & 0x00ffffff)
        self.identifier = self.libc.msgget(
            self.key, self.IPC_CREAT | self.IPC_EXCL | 0o600,
        )
        if self.identifier < 0:
            error = ctypes.get_errno()
            raise OSError(error, "could not create exclusive host IPC sentinel")

    def close(self) -> None:
        if self.identifier >= 0:
            if self.libc.msgctl(self.identifier, self.IPC_RMID, None) != 0:
                error = ctypes.get_errno()
                raise OSError(error, "could not remove host IPC sentinel")
            self.identifier = -1


class HostTcpSentinel:
    """Harness-owned live listener on the observed host-veth address."""

    def __init__(self, address: str):
        self.address = str(ipaddress.ip_address(address))
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.address, 0))
        self._socket.listen(8)
        self._socket.settimeout(.2)
        self.port = int(self._socket.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, name="native-host-veth-sentinel",
                                        daemon=True)
        self._thread.start()
        with socket.create_connection((self.address, self.port), timeout=1):
            self.active = True

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _peer = self._socket.accept()
            except (TimeoutError, OSError):
                continue
            connection.close()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2)


@dataclass(frozen=True)
class Project:
    root: Path
    label: str
    web_server: str
    runtime_config: Path


class SbRunner:
    """Bounded recorder for the public Sandbox CLI."""

    def __init__(self, *, events: list[dict[str, Any]], run: Callable[..., Any] = subprocess.run):
        self.events = events
        self._run = run

    def call(
        self,
        project: Project | None,
        *args: str,
        timeout: int = 30,
        operation: str | None = None,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        if len(self.events) >= MAX_EVENTS:
            raise RuntimeError("live evidence event ceiling exceeded")
        argv = [str(SB), *map(str, args)]
        started = time.monotonic()
        try:
            result = self._run(
                argv, cwd=str(project.root if project else REPOSITORY_ROOT),
                input=stdin, text=True, capture_output=True, timeout=timeout,
                check=False,
            )
            returncode = int(result.returncode)
            stdout, stderr = str(result.stdout or ""), str(result.stderr or "")
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            timed_out = True
        elapsed = round(time.monotonic() - started, 3)
        parsed = _last_json(stdout)
        event = {
            "operation": operation or str(args[0]),
            "command": ["./sb", *map(str, args)],
            "project": str(project.root) if project else None,
            "label": project.label if project else None,
            "timeout_seconds": timeout,
            "elapsed_seconds": elapsed,
            "returncode": returncode,
            "timed_out": timed_out,
            "stdout": _bounded(stdout),
            "stderr": _bounded(stderr),
            "json": parsed,
        }
        self.events.append(event)
        return event


class RuntimeConfigGrant:
    """Temporarily reconcile an exact grant through a gitignored runtime config."""

    def __init__(self, project: Project):
        self.project = project
        self.original = project.runtime_config.read_bytes()
        self.document = json.loads(self.original)
        runtime = self.document.get("wordpressRuntime")
        if not isinstance(runtime, dict):
            raise ValueError(f"{project.runtime_config} has no wordpressRuntime object")
        selected = (runtime.get("mode"), runtime.get("adapter"), runtime.get("webServer"))
        if selected != ("managed-native", "ubuntu-nspawn", project.web_server):
            raise ValueError(f"{project.runtime_config} does not select the requested managed runtime")

    def set(self, grants: list[dict[str, Any]]) -> None:
        self.document["wordpressRuntime"]["egress"] = grants
        encoded = (json.dumps(self.document, indent=2, sort_keys=True) + "\n").encode()
        temporary = self.project.runtime_config.with_name(self.project.runtime_config.name + ".acceptance.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, self.project.runtime_config)

    def set_runtime_seconds(self, seconds: int) -> None:
        self.document = json.loads(self.original)
        runtime = self.document["wordpressRuntime"]
        resources = runtime.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("runtime proof config has no resources object")
        resources["runtime_seconds"] = seconds
        runtime["egress"] = []
        encoded = (json.dumps(self.document, indent=2, sort_keys=True) + "\n").encode()
        temporary = self.project.runtime_config.with_name(self.project.runtime_config.name + ".acceptance.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, self.project.runtime_config)

    def restore(self) -> None:
        temporary = self.project.runtime_config.with_name(self.project.runtime_config.name + ".acceptance.tmp")
        temporary.write_bytes(self.original)
        os.replace(temporary, self.project.runtime_config)


def _project_args(project: Project) -> list[str]:
    return ["--project-dir", str(project.root), "--label", project.label]


def _native(
    runner: SbRunner, project: Project, action: str, *, operation: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    return runner.call(
        project, "native", action, *_project_args(project), "--json",
        timeout=timeout, operation=operation or f"native_{action}",
    )


def _ensure(runner: SbRunner, project: Project, *, operation: str) -> dict[str, Any]:
    return runner.call(
        project, "ensure", *_project_args(project), "--json",
        timeout=COLD_PROVISION_DEADLINE_SECONDS, operation=operation,
    )


def _apply(runner: SbRunner, project: Project, *, operation: str) -> dict[str, Any]:
    return runner.call(
        project, "apply", *_project_args(project), "--json",
        timeout=180, operation=operation,
    )


def _exec(
    runner: SbRunner, project: Project, argv: Iterable[str], *, operation: str,
    timeout: int = 30,
) -> dict[str, Any]:
    return runner.call(
        project, "exec", "--json", "--timeout", str(timeout), "--", *tuple(argv),
        timeout=timeout + 10, operation=operation,
    )


def _wp(runner: SbRunner, project: Project, argv: Iterable[str], *, operation: str) -> dict[str, Any]:
    return runner.call(
        project, "wp", "--label", project.label, "--", *tuple(argv),
        timeout=45, operation=operation,
    )


def _is_ok(event: dict[str, Any]) -> bool:
    payload = event.get("json")
    return event.get("returncode") == 0 and (not isinstance(payload, dict) or payload.get("ok", True) is True)


def _machine(status: dict[str, Any]) -> str | None:
    payload = status.get("json") or {}
    return _nested(payload, "backend", "machine") or _nested(payload, "backend", "backend", "machine")


def _backend(status: dict[str, Any]) -> dict[str, Any]:
    payload = status.get("json") or {}
    value = payload.get("backend")
    if isinstance(value, dict) and isinstance(value.get("backend"), dict):
        value = value["backend"]
    return value if isinstance(value, dict) else {}


def _observed_network_targets(
    primary_backend: dict[str, Any], sibling_backend: dict[str, Any],
) -> tuple[str, str, int]:
    guest = ipaddress.ip_address(str(primary_backend.get("address") or ""))
    sibling = ipaddress.ip_address(str(sibling_backend.get("address") or ""))
    sibling_port = int(sibling_backend.get("port") or 0)
    if guest.version != 4 or sibling.version != 4 or int(guest) < 1 or sibling_port <= 0:
        raise ValueError("managed backends have no observed IPv4 guest/listener identity")
    # Managed allocation is an observed /30 point-to-point pair: the backend
    # is the second usable address and its host veth peer is immediately prior.
    return str(ipaddress.ip_address(int(guest) - 1)), str(sibling), sibling_port


def _liveness(runner: SbRunner, primary: Project, sibling: Project, name: str) -> bool:
    primary_status = _native(runner, primary, "status", operation=f"{name}_primary_status")
    sibling_status = _native(runner, sibling, "status", operation=f"{name}_sibling_status")
    controller = runner.call(None, "native", "support", "--json", timeout=3,
                             operation=f"{name}_controller_liveness")
    return _is_ok(primary_status) and _is_ok(sibling_status) and _is_ok(controller)


def _concurrent_peer_liveness(runner: SbRunner, sibling: Project, name: str) -> bool:
    sibling_status = _native(runner, sibling, "status", operation=f"{name}_sibling_status")
    controller = runner.call(None, "native", "support", "--json", timeout=3,
                             operation=f"{name}_controller_liveness")
    return _is_ok(sibling_status) and _is_ok(controller)


def _wait_job(runner: SbRunner, project: Project, job_id: str, operation: str, timeout: int = 45) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = runner.call(project, "job-status", job_id, "--json", timeout=3,
                             operation=f"{operation}_status")
        lifecycle = _nested(status, "json", "lifecycle")
        if lifecycle in TERMINAL_JOBS:
            break
        time.sleep(.2)
    output = runner.call(
        project, "job-output", job_id, "--max-bytes", str(MAX_CAPTURE_BYTES), "--json",
        timeout=5, operation=f"{operation}_output",
    )
    return status, output


def _start_job(runner: SbRunner, project: Project, argv: Iterable[str], operation: str, timeout: int = 30) -> dict[str, Any]:
    return runner.call(
        project, "job-start", "--project-dir", str(project.root), "--local",
        "--timeout", str(timeout), "--json", "--", *tuple(argv),
        timeout=10, operation=operation,
    )


def _boundary_command(
    host_pid: int, sibling_pid: int | None, sibling_root: Path,
    host_ipc: int, host_veth: str, host_veth_port: int,
    sibling_address: str, sibling_port: int,
    sibling_ipc: int | None = None,
) -> tuple[str, ...]:
    return (
        "/usr/bin/php", "/workspace/native-boundary-proof.php", "boundary",
        "--host-pid", str(host_pid), "--sibling-pid", str(sibling_pid or -1),
        "--sibling-root", str(sibling_root), "--host-home", str(Path.home()),
        "--host-ipc", str(host_ipc), "--sibling-ipc", str(sibling_ipc or -1),
        "--host-veth", host_veth, "--host-veth-port", str(host_veth_port),
        "--sibling-address", sibling_address,
        "--sibling-port", str(sibling_port),
    )


def _boundary_ok(body: dict[str, Any]) -> bool:
    denied_fields = (
        "source_write", "symlink_escape", "sibling_source_read", "host_home_read",
        "host_control_read", "host_process_visible", "host_process_signal",
        "sibling_process_visible", "sibling_process_signal", "host_ipc_visible",
        "sibling_ipc_visible", "device_open", "control_socket_open",
        "raw_socket", "new_user_namespace", "metadata_reachable",
        "private_reachable", "host_veth_reachable", "sibling_address_reachable",
        "public_reachable", "credential_read",
    )
    try:
        observed_targets = (
            ipaddress.ip_address(str(body.get("host_veth_target") or "")),
            ipaddress.ip_address(str(body.get("sibling_address_target") or "")),
        )
    except ValueError:
        return False
    return bool(body) and all(target.version == 4 for target in observed_targets) \
        and isinstance(body.get("host_veth_port"), int) and body["host_veth_port"] > 0 \
        and all(body.get(field) is False for field in denied_fields) \
        and body.get("instance_db_socket") is True and body.get("effective_uid") == 33


def _entry_path_matrix(
    runner: SbRunner, primary: Project, sibling: Project, backend: dict[str, Any],
    sibling_backend: dict[str, Any],
    host_ipc: int, host_veth_port: int,
    sibling_pid: int | None, sibling_ipc: int | None,
) -> dict[str, bool]:
    host_veth, sibling_address, sibling_port = _observed_network_targets(
        backend, sibling_backend,
    )
    command = _boundary_command(
        os.getpid(), sibling_pid, sibling.root, host_ipc, host_veth,
        host_veth_port, sibling_address, sibling_port, sibling_ipc,
    )
    checks: dict[str, bool] = {}

    context = json.dumps({
        "host_pid": os.getpid(), "sibling_pid": sibling_pid or -1,
        "sibling_root": str(sibling.root), "host_home": str(Path.home()),
        "host_ipc": host_ipc, "sibling_ipc": sibling_ipc or -1,
        "host_veth": host_veth, "host_veth_port": host_veth_port,
        "sibling_address": sibling_address,
        "sibling_port": sibling_port,
    }, sort_keys=True, separators=(",", ":"))
    context_event = _wp(
        runner, primary,
        ("option", "update", "sandbox_native_proof_context", context, "--format=json"),
        operation="entry_context_seed",
    )
    checks["context_seed"] = _is_ok(context_event)
    context_path = primary.root / ".sandbox-native-proof-context.json"
    descriptor = os.open(
        context_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(context.encode())
        handle.flush()
        os.fsync(handle.fileno())
    checks["readonly_context_seed"] = (
        context_path.stat().st_mode & 0o777 == 0o444
        and json.loads(context_path.read_text()) == json.loads(context)
    )

    wp = _wp(runner, primary, ("eval-file", "/workspace/native-boundary-proof.php", *command[2:]),
             operation="entry_wordpress_cli")
    checks["wordpress_cli"] = _boundary_ok(_json_from_output(wp) or {})

    direct = _exec(runner, primary, command, operation="entry_exec")
    checks["exec"] = _boundary_ok(_json_from_output(direct) or {})

    composer = _exec(
        runner, primary,
        ("/usr/bin/composer", "run", "isolation-proof", "--working-dir=/workspace",
         "--no-interaction", "--", *command[2:]),
        operation="entry_composer", timeout=45,
    )
    checks["composer"] = _boundary_ok(_json_from_output(composer) or {})

    deactivate = _wp(runner, primary, ("plugin", "deactivate", "sandbox-native-proof"),
                     operation="plugin_deactivate")
    activate = _wp(runner, primary, ("plugin", "activate", "sandbox-native-proof"),
                   operation="entry_plugin_activation")
    activation = _wp(runner, primary, ("option", "get", "sandbox_native_proof_activation_result", "--format=json"),
                     operation="plugin_activation_result")
    checks["plugin_activation"] = _is_ok(deactivate) and _is_ok(activate) \
        and _boundary_ok(_json_from_output(activation) or {})

    _wp(runner, primary, ("option", "delete", "sandbox_native_proof_cron_result"),
        operation="cron_result_clear")
    cron_seed = _wp(runner, primary, ("eval", "wp_schedule_single_event(time(), 'sandbox_native_proof_cron');"),
                    operation="cron_seed")
    cron_result = None
    cron_deadline = time.monotonic() + 330
    while time.monotonic() < cron_deadline:
        candidate = _wp(
            runner, primary,
            ("option", "get", "sandbox_native_proof_cron_result", "--format=json"),
            operation="entry_cron_daemon_poll",
        )
        if _boundary_ok(_json_from_output(candidate) or {}):
            cron_result = candidate
            break
        time.sleep(5)
    checks["cron"] = _is_ok(cron_seed) and cron_result is not None

    address, port = backend.get("address"), backend.get("port")
    if address and port:
        url = f"http://{address}:{port}/?sandbox-native-proof=1"
        web = runner.call(primary, "visit", url, "--timeout", "10", timeout=20, operation="entry_web")
        visit_report = _last_json(_nested(web, "stdout", "text") or "") or {}
        title = visit_report.get("title")
        web_body = {}
        if isinstance(title, str) and title.startswith("sandbox-native-proof:"):
            try:
                web_body = json.loads(base64.b64decode(title.split(":", 1)[1], validate=True))
            except (ValueError, json.JSONDecodeError):
                web_body = {}
        checks["web"] = web.get("returncode") == 0 and _boundary_ok(web_body)
    else:
        checks["web"] = False

    job = _start_job(runner, primary, command, "entry_durable_job", timeout=30)
    job_id = _nested(job, "json", "job_id")
    if isinstance(job_id, str):
        job_status, job_output = _wait_job(runner, primary, job_id, "entry_durable_job")
        checks["durable_job"] = _nested(job_status, "json", "lifecycle") == "succeeded" \
            and _boundary_ok(_json_from_output(job_output) or {})
    else:
        checks["durable_job"] = False

    phpunit = runner.call(
        primary, "test", "--project-dir", str(primary.root), "--label", primary.label,
        "unit", "--json", timeout=180, operation="entry_phpunit",
    )
    checks["phpunit"] = phpunit.get("returncode") == 0
    context_path.unlink(missing_ok=True)
    checks["readonly_context_removed"] = not context_path.exists()
    return checks


def _resource_evidence(resource: str, event: dict[str, Any], observer: dict[str, Any]) -> bool:
    values = _json_lines(_output_text(event))
    started = next((item for item in values
                    if item.get("phase") == "started" and item.get("resource") == resource), None)
    result = next((item for item in reversed(values)
                   if item.get("phase") == "result" and item.get("resource") == resource), None)
    observed = _json_from_output(observer) or {}
    if not started:
        return False
    if resource == "cpu":
        return bool(result and started.get("cpu_max") not in {None, "max"}
                    and result.get("nr_throttled_delta", 0) > 0
                    and result.get("throttled_usec_delta", 0) > 0)
    if resource == "memory":
        before = started.get("memory_events") or {}
        after = observed.get("memory_events") or {}
        return (event.get("returncode") != 0
                and isinstance(started.get("memory_max"), int)
                and after.get("oom_kill", 0) > before.get("oom_kill", 0))
    if resource == "pids":
        return bool(result and started.get("pcntl") is True
                    and result.get("forked", 0) > 0
                    and result.get("fork_failures", 0) > 0
                    and result.get("pids_max_events_delta", 0) > 0)
    if resource == "disk":
        return bool(result and result.get("path") == "/var/lib/sandbox/tmp"
                    and result.get("write_failed") is True
                    and result.get("bytes_written", 0) > 0)
    if resource == "inodes":
        return bool(result and result.get("path") == "/var/lib/sandbox/tmp"
                    and result.get("create_failed") is True
                    and result.get("created", 0) > 0
                    and result.get("inodes_consumed", 0) > 0)
    if resource == "fds":
        return bool(result and isinstance(started.get("fd_soft_limit"), int)
                    and result.get("open_failed") is True
                    and result.get("opened", 0) >= max(1, started["fd_soft_limit"] - 64))
    if resource == "connections":
        return bool(result and started.get("backend_connected") is True
                    and started.get("backend_port", 0) > 0
                    and started.get("connection_limit", 0) > 0
                    and result.get("connection_failed") is True
                    and result.get("held_connections", 0) >= started["connection_limit"]
                    and result.get("held_connections", 0) <= started["connection_limit"] * 8)
    if resource == "io":
        return bool(result and result.get("path") == "/var/lib/sandbox/tmp"
                    and started.get("io_weight") is not None
                    and result.get("write_bytes_delta", 0) > 0)
    return False


def _io_concurrent_evidence(
    *, started: dict[str, Any] | None, progress: dict[str, Any] | None,
    lifecycle_during_probe: str | None, peer_live: bool,
    terminal_lifecycle: str | None, cancelled: bool, cleaned: bool,
    expected_weight: int,
) -> bool:
    weight = str((started or {}).get("io_weight") or "")
    delta = (progress or {}).get("write_bytes_delta")
    return bool(
        started and started.get("phase") == "started" and expected_weight > 0
        and str(expected_weight) in weight
        and isinstance(delta, int) and not isinstance(delta, bool) and delta > 0
        and lifecycle_during_probe == "running" and peer_live
        and terminal_lifecycle in {"cancelled", "interrupted"}
        and cancelled and cleaned
    )


def _io_durable_probe(
    runner: SbRunner, primary: Project, sibling: Project, *, expected_weight: int,
) -> bool:
    job = _start_job(
        runner, primary,
        ("/usr/bin/php", "/workspace/native-boundary-proof.php", "resource", "io",
         "--duration", "60"),
        "resource_io_durable_start", timeout=75,
    )
    job_id = _nested(job, "json", "job_id")
    if not isinstance(job_id, str):
        return False
    started = progress = None
    lifecycle_during_probe = None
    peer_live = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = runner.call(primary, "job-status", job_id, "--json", timeout=3,
                             operation="resource_io_concurrent_status")
        output = runner.call(
            primary, "job-output", job_id, "--max-bytes", str(MAX_CAPTURE_BYTES), "--json",
            timeout=3, operation="resource_io_concurrent_output",
        )
        values = _json_lines(_nested(output, "json", "data") or "")
        started = started or next((item for item in values
                                   if item.get("phase") == "started"
                                   and item.get("resource") == "io"), None)
        candidates = [item for item in values if item.get("phase") == "progress"
                      and item.get("resource") == "io"]
        if candidates:
            progress = candidates[-1]
        lifecycle = _nested(status, "json", "lifecycle")
        if started and progress and lifecycle == "running":
            lifecycle_during_probe = lifecycle
            peer_live = _concurrent_peer_liveness(runner, sibling, "during_io")
            break
        if lifecycle in TERMINAL_JOBS:
            break
        time.sleep(.5)
    cancel = runner.call(primary, "job-cancel", job_id, "--json", timeout=5,
                         operation="resource_io_cancel")
    terminal, _output = _wait_job(runner, primary, job_id, "resource_io_terminal", timeout=15)
    cleanup = runner.call(
        primary, "job-cleanup", job_id, "--logs", "--artifacts", "--metrics",
        "--confirm", "--json", timeout=10, operation="resource_io_cleanup",
    )
    scratch_cleanup = _exec(
        runner, primary,
        ("/usr/bin/php", "-r",
         "$p='/var/lib/sandbox/tmp/sandbox-native-io-durable';"
         "if(file_exists($p)&&!unlink($p)){exit(2);}exit(file_exists($p)?3:0);"),
        operation="resource_io_scratch_cleanup", timeout=10,
    )
    return _io_concurrent_evidence(
        started=started, progress=progress,
        lifecycle_during_probe=lifecycle_during_probe, peer_live=peer_live,
        terminal_lifecycle=_nested(terminal, "json", "lifecycle"),
        cancelled=_is_ok(cancel), cleaned=_is_ok(cleanup) and _is_ok(scratch_cleanup),
        expected_weight=expected_weight,
    )


def _resource_matrix(
    runner: SbRunner, primary: Project, sibling: Project, backend: dict[str, Any],
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    backend_port = int(backend.get("port") or 0)
    backend_address = str(backend.get("address") or "")
    runtime = json.loads(primary.runtime_config.read_text()).get("wordpressRuntime", {})
    connection_limit = int((runtime.get("resources") or {}).get("connections") or 0)
    io_weight = int((runtime.get("resources") or {}).get("io_weight") or 0)
    for resource in (item for item in RESOURCE_PROBES if item not in {"runtime", "io"}):
        timeout = 240 if resource in {"disk", "inodes"} else 60
        event = _exec(
            runner, primary,
            ("/usr/bin/php", "/workspace/native-boundary-proof.php", "resource", resource,
             "--backend-address", backend_address, "--backend-port", str(backend_port),
             "--connection-limit", str(connection_limit)),
            operation=f"resource_{resource}", timeout=timeout,
        )
        observer = _exec(
            runner, primary,
            ("/usr/bin/php", "/workspace/native-boundary-proof.php", "resource-observe", resource),
            operation=f"resource_{resource}_observe", timeout=15,
        )
        checks[resource] = _resource_evidence(resource, event, observer) \
            and _liveness(runner, primary, sibling, f"after_{resource}")
    checks["io"] = _io_durable_probe(
        runner, primary, sibling, expected_weight=io_weight,
    )
    return checks


def _runtime_limit_proof(
    runner: SbRunner, primary: Project, sibling: Project, config: RuntimeConfigGrant,
) -> tuple[bool, dict[str, Any]]:
    declared = 20
    initial_cleanup = _native(
        runner, primary, "cleanup", operation="runtime_limit_initial_cleanup",
    )
    if not _cleanup_check(initial_cleanup, "complete"):
        return False, initial_cleanup
    config.set_runtime_seconds(declared)
    ensured = _ensure(runner, primary, operation="runtime_limit_ensure")
    if not _is_ok(ensured):
        return False, ensured
    event = _exec(
        runner, primary,
        ("/usr/bin/php", "/workspace/native-boundary-proof.php", "resource", "runtime",
         "--declared-runtime-seconds", str(declared)),
        operation="resource_runtime_declared_machine_limit", timeout=45,
    )
    lines = _json_lines(_output_text(event))
    started = next((item for item in lines if item.get("phase") == "started"
                    and item.get("resource") == "runtime"), None)
    status = _native(runner, primary, "status", operation="runtime_limit_expired_status")
    sibling_status = _native(runner, sibling, "status", operation="runtime_limit_sibling_status")
    controller = runner.call(None, "native", "support", "--json", timeout=3,
                             operation="runtime_limit_controller_liveness")
    elapsed = event.get("elapsed_seconds", 0)
    isolated_exit = _nested(event, "json", "exit_code")
    ok = bool(
        started and started.get("declared_runtime_seconds") == declared
        and isolated_exit == 124 and not event.get("timed_out")
        and declared - 5 <= elapsed <= declared + 15
        and _is_ok(status) and _is_ok(sibling_status) and _is_ok(controller)
    )
    cleanup = _native(runner, primary, "cleanup", operation="runtime_limit_cleanup")
    return ok and _cleanup_check(cleanup, "complete"), cleanup


def _grant_matrix(runner: SbRunner, primary: Project, config: RuntimeConfigGrant) -> dict[str, bool]:
    grant = {
        "grant_id": "example-https", "kind": "hostname_https",
        "destinations": ["example.com"], "ports": [443],
        "expires_at": "2027-08-01T00:00:00Z",
    }
    probe = ("/usr/bin/curl", "--silent", "--show-error", "--max-time", "8")
    config.set([])
    revoke = _apply(runner, primary, operation="grant_initial_revoke")
    denied = _exec(runner, primary, (*probe, "https://example.com/"), operation="grant_revoked_probe", timeout=12)
    denied_plugin = _wp(
        runner, primary,
        ("eval", "echo wp_json_encode(sandbox_native_proof_observe());"),
        operation="grant_revoked_plugin_probe",
    )
    config.set([grant])
    add = _apply(runner, primary, operation="grant_exact_add")
    exact = _exec(runner, primary, (*probe, "https://example.com/"), operation="grant_exact_probe", timeout=12)
    direct = _exec(runner, primary, (*probe, "--noproxy", "*", "https://example.com/"), operation="grant_bypass_probe", timeout=12)
    wrong = _exec(runner, primary, (*probe, "https://www.iana.org/"), operation="grant_wrong_probe", timeout=12)
    exact_plugin = _wp(
        runner, primary,
        ("eval", "echo wp_json_encode(sandbox_native_proof_observe());"),
        operation="grant_exact_plugin_probe",
    )
    config.set([])
    remove = _apply(runner, primary, operation="grant_second_revoke")
    denied_again = _exec(runner, primary, (*probe, "https://example.com/"), operation="grant_second_revoked_probe", timeout=12)
    denied_again_plugin = _wp(
        runner, primary,
        ("eval", "echo wp_json_encode(sandbox_native_proof_observe());"),
        operation="grant_second_revoked_plugin_probe",
    )
    denied_plugin_body = _json_from_output(denied_plugin) or {}
    exact_plugin_body = _json_from_output(exact_plugin) or {}
    denied_again_plugin_body = _json_from_output(denied_again_plugin) or {}
    return {
        "default_deny": _is_ok(revoke) and not _is_ok(denied)
            and denied_plugin_body.get("public_reachable") is False,
        "exact_grant": _is_ok(add) and _is_ok(exact)
            and exact_plugin_body.get("public_reachable") is True,
        "no_bypass": not _is_ok(direct),
        "other_public_denied": not _is_ok(wrong),
        "revocation": _is_ok(remove) and not _is_ok(denied_again)
            and denied_again_plugin_body.get("public_reachable") is False,
    }


def _cleanup_check(event: dict[str, Any], expectation: str) -> bool:
    payload = event.get("json") or {}
    state = payload.get("state")
    reason = _nested(payload, "reason", "code")
    if expectation == "complete":
        return payload.get("ok") is True and state in {"ready", "absent"}
    fault = payload.get("acceptance_fault")
    if not isinstance(fault, dict) or fault.get("owner_match") is not True \
            or fault.get("retained_state_verified") is not True \
            or fault.get("restored") is not True or fault.get("retry_ok") is not True:
        return False
    if expectation == "drift":
        return payload.get("ok") is False and state == "cleanup_incomplete" \
            and reason == "isolation_drift" and fault.get("kind") == "owned_drift"
    return payload.get("ok") is False and state == "cleanup_incomplete" \
        and reason == "runtime_cleanup_unavailable" \
        and fault.get("kind") == "runtime_observer_unavailable"


def _validate_project(root: str, label: str, server: str, config: str) -> Project:
    project_root = Path(root).expanduser().resolve(strict=True)
    runtime_config = Path(config).expanduser().resolve(strict=True)
    if not runtime_config.is_file() or runtime_config.parent != project_root:
        raise ValueError("runtime config must be an existing file directly inside its project root")
    if label != "default":
        raise ValueError("live durable-job proof currently requires separate projects with the default label")
    return Project(project_root, label, server, runtime_config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--label", default="default")
    parser.add_argument("--web-server", choices=("nginx", "apache"), required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--sibling-project-dir", required=True)
    parser.add_argument("--sibling-label", default="default")
    parser.add_argument("--sibling-web-server", choices=("nginx", "apache"), required=True)
    parser.add_argument("--sibling-runtime-config", required=True)
    parser.add_argument("--cleanup-expectation", choices=("complete", "drift", "unavailable"), default="complete")
    parser.add_argument("--evidence-out", required=True)
    parser.add_argument("--confirm-disposable-host", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_disposable_host:
        parser.error("--confirm-disposable-host is required for destructive exhaustion probes")
    candidate_authority = _enable_proof_candidate(os.environ)

    primary = _validate_project(args.project_dir, args.label, args.web_server, args.runtime_config)
    sibling = _validate_project(
        args.sibling_project_dir, args.sibling_label, args.sibling_web_server,
        args.sibling_runtime_config,
    )
    output = Path(args.evidence_out).expanduser().resolve()
    if output.exists():
        raise ValueError("evidence output already exists; proof records are append-never")
    if output.is_relative_to(primary.root) or output.is_relative_to(sibling.root):
        raise ValueError("evidence output must be outside both read-only proof projects")
    if primary.root == sibling.root:
        raise ValueError("a real sibling requires a distinct canonical project root")
    if primary.web_server == sibling.web_server:
        raise ValueError("one proof run must exercise nginx and Apache coexistence")

    events: list[dict[str, Any]] = []
    runner = SbRunner(events=events)
    primary_config = RuntimeConfigGrant(primary)
    sibling_config = RuntimeConfigGrant(sibling)
    checks: dict[str, Any] = {"entry_paths": {}, "resources": {}, "grants": {}}
    before_primary = hashlib.sha256(primary_config.original).hexdigest()
    before_sibling = hashlib.sha256(sibling_config.original).hexdigest()
    source_before_primary = _tree_digest(primary.root)
    source_before_sibling = _tree_digest(sibling.root)
    cleanup_primary = cleanup_sibling = repeated_primary = None
    preflight_before = None
    host_baseline_before = host_baseline_after = None
    host_tcp = None
    host_sentinel = Path("/tmp") / f"sandbox-native-host-sentinel-{os.getpid()}"
    escape_link = primary.root / ".native-host-escape"
    if host_sentinel.exists() or escape_link.exists() or escape_link.is_symlink():
        raise ValueError("hostile-proof sentinel paths must be absent before the run")
    host_ipc = None
    try:
        host_ipc = HostMessageQueue()
        host_sentinel.write_bytes(os.urandom(32))
        escape_link.symlink_to(host_sentinel)
    except Exception:
        if escape_link.is_symlink():
            escape_link.unlink()
        host_sentinel.unlink(missing_ok=True)
        if host_ipc is not None:
            host_ipc.close()
        raise

    try:
        # Every hostile entry path begins from the canonical empty grant set.
        primary_config.set([])
        sibling_config.set([])
        preflight_before = runner.call(None, "native", "preflight", "--project-dir", str(primary.root), "--json",
                                       timeout=int(PREFLIGHT_LIMIT_SECONDS) + 2, operation="preflight_before")
        sibling_preflight = runner.call(None, "native", "preflight", "--project-dir", str(sibling.root), "--json",
                                        timeout=int(PREFLIGHT_LIMIT_SECONDS) + 2, operation="sibling_preflight_before")
        checks["preflight_timing"] = (
            _is_ok(preflight_before) and _is_ok(sibling_preflight)
            and preflight_before["elapsed_seconds"] <= PREFLIGHT_LIMIT_SECONDS
            and sibling_preflight["elapsed_seconds"] <= PREFLIGHT_LIMIT_SECONDS
        )
        host_baseline_before = _native(
            runner, primary, "baseline", operation="host_baseline_before", timeout=130,
        )
        checks["foreign_data_sentinel_seeded"] = (
            _nested(host_baseline_before, "json", "baseline", "data", "sentinel", "present")
            is True
        )

        cold_primary = _ensure(runner, primary, operation="cold_ensure_primary")
        cold_sibling = _ensure(runner, sibling, operation="cold_ensure_sibling")
        status_primary = _native(runner, primary, "status", operation="status_primary")
        status_sibling = _native(runner, sibling, "status", operation="status_sibling")
        checks["ensure_both"] = _is_ok(cold_primary) and _is_ok(cold_sibling)
        checks["status_timing"] = (
            _is_ok(status_primary) and _is_ok(status_sibling)
            and status_primary["elapsed_seconds"] <= STATUS_LIMIT_SECONDS
            and status_sibling["elapsed_seconds"] <= STATUS_LIMIT_SECONDS
        )
        checks["proof_candidate_truthful"] = (
            _candidate_result(status_primary) and _candidate_result(status_sibling)
        )
        checks["real_sibling"] = bool(
            _machine(status_primary) and _machine(status_sibling)
            and _machine(status_primary) != _machine(status_sibling)
        )
        checks["server_pair"] = {
            primary.web_server, sibling.web_server,
        } == {"nginx", "apache"}
        host_veth, sibling_address, sibling_port = _observed_network_targets(
            _backend(status_primary), _backend(status_sibling),
        )
        host_tcp = HostTcpSentinel(host_veth)
        checks["host_veth_sentinel_active"] = host_tcp.active

        sibling_hold = _start_job(
            runner, sibling,
            ("/usr/bin/php", "/workspace/native-boundary-proof.php", "hold", "30"),
            "sibling_process_ipc_hold", timeout=40,
        )
        sibling_job_id = _nested(sibling_hold, "json", "job_id")
        sibling_pid = None
        sibling_ipc = None
        if isinstance(sibling_job_id, str):
            time.sleep(.5)
            held = runner.call(
                sibling, "job-output", sibling_job_id, "--max-bytes", "4096", "--json",
                timeout=3, operation="sibling_process_ipc_identity",
            )
            hold_identity = _json_from_output(held) or {}
            sibling_pid = hold_identity.get("pid")
            sibling_ipc = hold_identity.get("ipc_key")

        boundary = _exec(
            runner, primary,
            _boundary_command(
                os.getpid(), sibling_pid, sibling.root, host_ipc.key,
                host_veth, host_tcp.port, sibling_address, sibling_port, sibling_ipc,
            ),
            operation="hostile_boundary_full", timeout=30,
        )
        boundary_body = _json_from_output(boundary) or {}
        checks["hostile_boundary"] = _boundary_ok(boundary_body)

        checks["entry_paths"] = _entry_path_matrix(
            runner, primary, sibling, _backend(status_primary), _backend(status_sibling),
            host_ipc.key, host_tcp.port,
            sibling_pid, sibling_ipc,
        )
        checks["resources"] = _resource_matrix(
            runner, primary, sibling, _backend(status_primary),
        )
        checks["grants"] = _grant_matrix(runner, primary, primary_config)

        primary_config.restore()
        sibling_config.restore()
        restored_primary = _apply(runner, primary, operation="restore_primary_descriptor")
        restored_sibling = _apply(runner, sibling, operation="restore_sibling_descriptor")
        warm_primary = _ensure(runner, primary, operation="warm_ensure_primary")
        warm_sibling = _ensure(runner, sibling, operation="warm_ensure_sibling")
        checks["descriptor_restored"] = _is_ok(restored_primary) and _is_ok(restored_sibling)
        checks["warm_start_timing"] = (
            _is_ok(warm_primary) and _is_ok(warm_sibling)
            and warm_primary["elapsed_seconds"] <= WARM_START_LIMIT_SECONDS
            and warm_sibling["elapsed_seconds"] <= WARM_START_LIMIT_SECONDS
        )
        checks["warm_converged"] = all(
            (_nested(event, "json", "mutated") is False) for event in (warm_primary, warm_sibling)
        )
        if args.cleanup_expectation == "complete":
            checks["resources"]["runtime"], _runtime_cleanup = _runtime_limit_proof(
                runner, primary, sibling, primary_config,
            )
        else:
            # The current CLI has no owner/digest-bound acceptance fault
            # injector. Never let an arbitrary cleanup error masquerade as
            # drift/unavailability evidence.
            checks["cleanup_fault_injection_available"] = False
    except Exception as exc:
        checks["harness_exception"] = False
        events.append({
            "operation": "harness_exception", "type": type(exc).__name__,
            "message": _bounded(str(exc), 2048),
        })
    finally:
        # Restore machine-local desired state before any cleanup attempt.
        for config in (primary_config, sibling_config):
            try:
                config.restore()
            except OSError as exc:
                events.append({"operation": "config_restore_failed", "message": _bounded(str(exc), 2048)})
        try:
            if escape_link.is_symlink():
                escape_link.unlink()
            if host_sentinel.exists():
                host_sentinel.unlink()
            (primary.root / ".sandbox-native-proof-context.json").unlink(missing_ok=True)
            if host_tcp is not None:
                host_tcp.close()
        except OSError as exc:
            events.append({"operation": "sentinel_cleanup_failed", "message": _bounded(str(exc), 2048)})
        cleanup_primary = _native(runner, primary, "cleanup", operation="cleanup_primary")
        cleanup_sibling = _native(runner, sibling, "cleanup", operation="cleanup_sibling")
        repeated_primary = _native(runner, primary, "cleanup", operation="cleanup_primary_repeated")
        try:
            host_ipc.close()
        except OSError as exc:
            events.append({"operation": "host_ipc_cleanup_failed", "message": _bounded(str(exc), 2048)})

    preflight_after = runner.call(None, "native", "preflight", "--project-dir", str(primary.root), "--json",
                                  timeout=3, operation="preflight_after")
    host_baseline_after = _native(
        runner, primary, "baseline", operation="host_baseline_after", timeout=130,
    )
    checks["cleanup_primary"] = _cleanup_check(cleanup_primary, args.cleanup_expectation)
    checks["cleanup_sibling"] = _cleanup_check(cleanup_sibling, args.cleanup_expectation)
    checks["cleanup_repeated"] = _cleanup_check(repeated_primary, args.cleanup_expectation)
    before_host_digest = _nested(host_baseline_before or {}, "json", "digest")
    after_host_digest = _nested(host_baseline_after, "json", "digest")
    checks["host_preflight_stable"] = (
        _nested(preflight_before or {}, "json") == _nested(preflight_after, "json")
    )
    checks["foreign_host_service_baseline"] = (
        isinstance(before_host_digest, str) and len(before_host_digest) == 64
        and before_host_digest == after_host_digest
    )
    checks["config_restored"] = (
        hashlib.sha256(primary.runtime_config.read_bytes()).hexdigest() == before_primary
        and hashlib.sha256(sibling.runtime_config.read_bytes()).hexdigest() == before_sibling
    )
    checks["source_diff_clean"] = (
        _tree_digest(primary.root) == source_before_primary
        and _tree_digest(sibling.root) == source_before_sibling
    )
    checks["bounded_evidence"] = all(
        event.get("stdout", {}).get("bytes", 0) >= 0 and event.get("stderr", {}).get("bytes", 0) >= 0
        for event in events if isinstance(event.get("stdout"), dict)
    ) and len(events) <= MAX_EVENTS

    def truthy(value: Any) -> bool:
        if isinstance(value, dict):
            return bool(value) and all(truthy(item) for item in value.values())
        return value is True

    report = {
        "schema": "sandbox.native-live-acceptance/v1",
        "ok": truthy(checks),
        "provenance": {
            "source": _source_identity(),
            "durable_job_ids": _durable_job_ids(events),
        },
        "matrix": {
            "primary": {"project": str(primary.root), "label": primary.label, "web_server": primary.web_server},
            "sibling": {"project": str(sibling.root), "label": sibling.label, "web_server": sibling.web_server},
            "cleanup_expectation": args.cleanup_expectation,
            "proof_candidate": candidate_authority,
            "adoptable": False,
        },
        "thresholds_seconds": {
            "preflight": PREFLIGHT_LIMIT_SECONDS,
            "status": STATUS_LIMIT_SECONDS,
            "warm_start": WARM_START_LIMIT_SECONDS,
        },
        "checks": checks,
        "events": events,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, output)
    print(json.dumps({"ok": report["ok"], "evidence": str(output), "checks": checks}, sort_keys=True))
    return 0 if report["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())

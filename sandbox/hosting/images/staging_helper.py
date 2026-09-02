"""Measured fixed remote helper for one exact GHCR image staging operation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

FIXED_ENTRY = "sandbox-image-stage-helper-v1"
MAX_STAGE_FRAME_BYTES = 1024 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024
TOPOLOGY_LABEL = "org.sandbox.application-topology.v1"
_cancelled = False


def canonical_bytes(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_STAGE_FRAME_BYTES: raise ValueError("protocol_invalid")
    return encoded


def staging_digest(domain: str, value: object) -> str:
    return "sha256:" + hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_bytes(value)).hexdigest()


def _signal(_number, _frame) -> None:
    global _cancelled
    _cancelled = True


def _read_frame(stream, maximum: int) -> bytes:
    size_raw = stream.read(4)
    if len(size_raw) != 4: raise ValueError("protocol_invalid")
    size = int.from_bytes(size_raw, "big")
    if not 1 <= size <= maximum: raise ValueError("protocol_invalid")
    payload = stream.read(size)
    if len(payload) != size: raise ValueError("protocol_invalid")
    return payload


def _closed_plan(value: object) -> dict:
    fields = {"schema_version", "unit_name", "repository", "repository_qualified_digest",
              "manifest_digest", "config_digest", "platform", "topology",
              "target", "request_id", "helper"}
    if type(value) is not dict or set(value) != fields or value["schema_version"] != 1:
        raise ValueError("protocol_invalid")
    if not isinstance(value["repository_qualified_digest"], str) \
            or ":latest" in value["repository_qualified_digest"] \
            or "@sha256:" not in value["repository_qualified_digest"]:
        raise ValueError("protocol_invalid")
    helper = value["helper"]
    if type(helper) is not dict or set(helper) != {"artifact_digest", "entry",
            "runtime_revision", "capability_revision"} or helper["entry"] != FIXED_ENTRY \
            or not isinstance(helper["runtime_revision"], str) \
            or not isinstance(helper["capability_revision"], str):
        raise ValueError("protocol_invalid")
    measured = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if helper["artifact_digest"] != measured:
        raise ValueError("capability_mismatch")
    return value


def _cgroup_identity(unit_name: str) -> str:
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise ValueError("capability_mismatch")
    lines = Path("/proc/self/cgroup").read_text().splitlines()
    unified = next((line.split("::", 1)[1] for line in lines if line.startswith("0::")), None)
    if not unified or unit_name not in unified or ".." in unified:
        raise ValueError("process_unproven")
    return unified


def _verify_workspace_parent(run_root: Path = Path("/run/sandbox-image-stage"), *,
                             mountinfo_text: str | None = None,
                             required_uid: int = 0) -> Path:
    """Prove the credential workspace is volatile before asking for bytes."""
    try:
        parent = os.lstat(run_root.parent)
        if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode) \
                or parent.st_uid != required_uid:
            raise ValueError("capability_mismatch")
        mount_lines = (mountinfo_text if mountinfo_text is not None
                       else Path("/proc/self/mountinfo").read_text()).splitlines()
        candidates = []
        for line in mount_lines:
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            fields = left.split(); after = right.split()
            if len(fields) >= 5 and after:
                mount_point = fields[4].replace("\\040", " ")
                if str(run_root) == mount_point or str(run_root).startswith(mount_point.rstrip("/") + "/"):
                    candidates.append((len(mount_point), mount_point, after[0]))
        if not candidates or max(candidates)[2] != "tmpfs":
            raise ValueError("capability_mismatch")
        try:
            existing = os.lstat(run_root)
        except FileNotFoundError:
            os.mkdir(run_root, 0o700)
            existing = os.lstat(run_root)
            parent_fd = os.open(run_root.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try: os.fsync(parent_fd)
            finally: os.close(parent_fd)
        if not stat.S_ISDIR(existing.st_mode) or stat.S_ISLNK(existing.st_mode) \
                or existing.st_uid != required_uid or stat.S_IMODE(existing.st_mode) != 0o700:
            raise ValueError("capability_mismatch")
    except (OSError, UnicodeError):
        raise ValueError("capability_mismatch") from None
    return run_root


def _run(argv: tuple[str, ...], *, environment: dict[str, str], input_data: bytes | None = None,
         timeout: int = 300) -> subprocess.CompletedProcess:
    if _cancelled: raise ValueError("cancelled")
    return subprocess.run(argv, input=input_data, capture_output=True, env=environment,
                          timeout=timeout, check=False)


def _anonymous_denied(repository: str, manifest_digest: str) -> bool:
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{repository}/manifests/{manifest_digest}",
        headers={"Accept": "application/vnd.oci.image.manifest.v1+json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status in {401, 403}
    except urllib.error.HTTPError as exc:
        return exc.code in {401, 403}
    except (urllib.error.URLError, TimeoutError):
        raise ValueError("registry_observation_failed") from None


def execute(plan: dict, credential: bytes, *, run_root: Path | None = None,
            runner=_run, anonymous_probe=_anonymous_denied,
            cgroup_identity=_cgroup_identity, machine_epoch_reader=None,
            remover=shutil.rmtree) -> dict:
    plan = _closed_plan(plan)
    if type(credential) is not bytes or not credential or len(credential) > MAX_CREDENTIAL_BYTES:
        raise ValueError("broker_unavailable")
    cgroup = cgroup_identity(plan["unit_name"])
    run_root = _verify_workspace_parent() if run_root is None else run_root
    machine_epoch_reader = machine_epoch_reader or (
        lambda: Path("/etc/machine-id").read_text().strip())
    workspace = Path(tempfile.mkdtemp(prefix="operation-", dir=run_root))
    os.chmod(workspace, 0o700)
    environment = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C",
                   "HOME": str(workspace), "DOCKER_CONFIG": str(workspace / "docker")}
    code = "staged"
    observation = None
    try:
        denied = anonymous_probe(plan["repository"], plan["manifest_digest"])
        if not denied: raise ValueError("observation_invalid")
        login = runner(("docker", "login", "ghcr.io", "--username", "sandbox-broker",
                      "--password-stdin"), environment=environment,
                     input_data=credential + b"\n", timeout=30)
        if login.returncode != 0: raise ValueError("broker_unavailable")
        pull = runner(("docker", "pull", plan["repository_qualified_digest"]),
                    environment=environment, timeout=600)
        if pull.returncode != 0: raise ValueError("pull_failed")
        # Credential material is gone before identity observation or any result frame.
        remover(workspace / "docker", ignore_errors=False)
        cleanup_complete = not (workspace / "docker").exists()
        if not cleanup_complete: raise ValueError("cleanup_unproven")
        target_start = machine_epoch_reader()
        epoch_start = runner(("docker", "info", "--format", "{{.ID}}"), environment=environment,
                           timeout=15)
        inspect = runner(("docker", "image", "inspect", plan["repository_qualified_digest"],
                        "--format", "{{json .}}"), environment=environment, timeout=30)
        epoch_end = runner(("docker", "info", "--format", "{{.ID}}"),
                         environment=environment, timeout=15)
        if any(item.returncode != 0 for item in (epoch_start, inspect, epoch_end)):
            raise ValueError("observation_invalid")
        target_end = machine_epoch_reader()
        start = epoch_start.stdout.decode().strip(); end = epoch_end.stdout.decode().strip()
        if not start or start != end or not target_start or target_start != target_end \
                or target_start != plan["target"]["machine_identity"] \
                or start != plan["target"]["daemon_identity"]:
            raise ValueError("observation_invalid")
        raw = json.loads(inspect.stdout)
        repo_digests = raw.get("RepoDigests")
        if type(repo_digests) is not list or repo_digests.count(plan["repository_qualified_digest"]) != 1:
            raise ValueError("observation_invalid")
        # Docker's immutable image ID is the sha256 digest of the image config
        # JSON. Bind it as the config digest while retaining a separate local
        # image-ID field in the observation/proof contract.
        config_digest = raw.get("Id")
        if config_digest != plan["config_digest"]:
            raise ValueError("observation_invalid")
        platform = {"os": raw.get("Os"), "architecture": raw.get("Architecture")}
        if raw.get("Variant"): platform["variant"] = raw["Variant"]
        if platform != plan["platform"]: raise ValueError("observation_invalid")
        labels = raw.get("Config", {}).get("Labels")
        if type(labels) is not dict or type(labels.get(TOPOLOGY_LABEL)) is not str:
            raise ValueError("observation_invalid")
        try:
            observed_topology = json.loads(labels[TOPOLOGY_LABEL])
        except (TypeError, json.JSONDecodeError):
            raise ValueError("observation_invalid") from None
        if observed_topology != plan["topology"]:
            raise ValueError("observation_invalid")
        topology_digest = staging_digest(
            "sandbox.hosting.images.topology.v1", observed_topology)
        registry = {"anonymous_exact_manifest": "denied",
                    "authenticated_exact_manifest": "succeeded"}
        registry["observation_digest"] = staging_digest(
            "sandbox.hosting.images.registry-observation.v1", registry)
        observation = {"target_epoch_start": target_start, "target_epoch_end": target_end,
            "daemon_epoch_start": start, "daemon_epoch_end": end, "target": plan["target"],
            "repository": plan["repository"], "repo_digest": plan["repository_qualified_digest"],
            "config_digest": config_digest, "platform": platform, "local_image_id": raw.get("Id"),
            "topology_digest": topology_digest, "observed_topology": observed_topology, **registry}
        observation["observation_id"] = staging_digest(
            "sandbox.hosting.images.local-observation.v1", observation)
    except Exception as exc:
        code = str(exc) if str(exc) in {"capability_mismatch", "process_unproven",
            "registry_observation_failed", "observation_invalid", "broker_unavailable",
            "pull_failed", "cleanup_unproven", "cancelled"} else "helper_failed"
    finally:
        try: remover(workspace)
        except FileNotFoundError: pass
        except OSError: code = "cleanup_unproven"
        cleanup_complete = not workspace.exists()
        credential = b""
    payload = {"process": {"unit_name": plan["unit_name"], "cgroup": cgroup,
                            "delegated": False, "escape_allowed": False},
               "cleanup": {"complete": cleanup_complete}}
    if observation is not None and cleanup_complete and code == "staged":
        payload["observation"] = observation
        return {"schema_version": 1, "ok": True, "code": "staged", "payload": payload}
    if not cleanup_complete: code = "cleanup_unproven"
    return {"schema_version": 1, "ok": False, "code": code, "payload": payload}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    response = None
    try:
        if argv != [FIXED_ENTRY]: raise ValueError("protocol_invalid")
        plan = json.loads(_read_frame(sys.stdin.buffer, MAX_STAGE_FRAME_BYTES))
        # This handshake proves the measured helper is already inside its
        # transient cgroup before the broker resolves credential bytes.
        _closed_plan(plan)
        _cgroup_identity(plan["unit_name"])
        _verify_workspace_parent()
        sys.stdout.buffer.write(b"READY\n"); sys.stdout.buffer.flush()
        credential = _read_frame(sys.stdin.buffer, MAX_CREDENTIAL_BYTES)
        if sys.stdin.buffer.read(1): raise ValueError("protocol_invalid")
        response = execute(plan, credential)
    except Exception as exc:
        code = str(exc) if str(exc) in {"protocol_invalid", "capability_mismatch",
            "process_unproven", "registry_observation_failed", "observation_invalid",
            "broker_unavailable", "pull_failed", "cleanup_unproven", "cancelled"} else "helper_failed"
        response = {"schema_version": 1, "ok": False, "code": code,
                    "payload": {"cleanup": {"complete": code == "protocol_invalid"}}}
    output = canonical_bytes(response)
    sys.stdout.buffer.write(output); sys.stdout.buffer.flush()
    # A closed negative frame is a successful protocol exchange. Transport and
    # service classify the operation from the frame, not arbitrary stderr/exit text.
    return 0


for _number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(_number, _signal)

if __name__ == "__main__":
    raise SystemExit(main())

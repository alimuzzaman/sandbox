"""Measured fixed remote helper for one exact GHCR image staging operation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

FIXED_ENTRY = "sandbox-image-stage-helper-v1"
FIXED_ENTRY_V2 = "sandbox-image-stage-helper-v2"
MAX_STAGE_FRAME_BYTES = 1024 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024
TOPOLOGY_LABEL = "org.sandbox.application-topology.v1"
_cancelled = False
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(
    r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+\Z")
_SERVICE = re.compile(r"[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
V2_CAPABILITY_REVISION = "systemd-cgroup-v2-batch-stage-v2"


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


def _closed_plan_v2(value: object) -> dict:
    fields = {"schema_version", "unit_name", "plan_set_digest", "images",
              "service_image_bindings", "target", "request_id", "helper"}
    if type(value) is not dict or set(value) != fields \
            or type(value["schema_version"]) is not int or value["schema_version"] != 2 \
            or type(value["images"]) is not list or len(value["images"]) != 3 \
            or type(value["service_image_bindings"]) is not list:
        raise ValueError("protocol_invalid")
    if type(value["plan_set_digest"]) is not str \
            or _DIGEST.fullmatch(value["plan_set_digest"]) is None:
        raise ValueError("protocol_invalid")
    if type(value["unit_name"]) is not str \
            or re.fullmatch(r"sandbox-image-stage-[0-9a-f]{32}\.service",
                            value["unit_name"]) is None \
            or type(value["request_id"]) is not str \
            or _IDENTITY.fullmatch(value["request_id"]) is None:
        raise ValueError("protocol_invalid")
    target = value["target"]
    if type(target) is not dict or set(target) != {
            "machine_identity", "target_identity", "daemon_identity"} \
            or any(type(item) is not str or _IDENTITY.fullmatch(item) is None
                   for item in target.values()):
        raise ValueError("protocol_invalid")
    names = []
    for image in value["images"]:
        if type(image) is not dict or set(image) != {
                "name", "repository", "repository_qualified_digest", "manifest_digest",
                "config_digest", "platform"}:
            raise ValueError("protocol_invalid")
        if image["name"] not in {"queue", "web", "worker"} \
                or type(image["repository"]) is not str \
                or _REPOSITORY.fullmatch(image["repository"]) is None \
                or type(image["manifest_digest"]) is not str \
                or _DIGEST.fullmatch(image["manifest_digest"]) is None \
                or type(image["config_digest"]) is not str \
                or _DIGEST.fullmatch(image["config_digest"]) is None \
                or not isinstance(image["repository_qualified_digest"], str) \
                or "@sha256:" not in image["repository_qualified_digest"] \
                or ":latest" in image["repository_qualified_digest"] \
                or image["repository_qualified_digest"] != (
                    f'{image["repository"]}@{image["manifest_digest"]}') \
                or image["platform"] != "linux/amd64":
            raise ValueError("protocol_invalid")
        names.append(image["name"])
    if names != ["queue", "web", "worker"]:
        raise ValueError("protocol_invalid")
    bindings = value["service_image_bindings"]
    if not bindings or len(bindings) > 64 \
            or any(type(row) is not dict or set(row) != {"service", "image", "image_ref"}
                   or type(row["service"]) is not str
                   or _SERVICE.fullmatch(row["service"]) is None for row in bindings) \
            or bindings != sorted(bindings, key=lambda row: row["service"]) \
            or len({row["service"] for row in bindings}) != len(bindings):
        raise ValueError("protocol_invalid")
    by_name = {item["name"]: item for item in value["images"]}
    if {row["image"] for row in bindings} != set(by_name) \
            or any(row["image"] not in by_name
           or row["image_ref"] != by_name[row["image"]]["repository_qualified_digest"]
           for row in bindings):
        raise ValueError("protocol_invalid")
    helper = value["helper"]
    if type(helper) is not dict or set(helper) != {"artifact_digest", "entry",
            "runtime_revision", "capability_revision"} \
            or helper["entry"] != FIXED_ENTRY_V2 \
            or helper["capability_revision"] != V2_CAPABILITY_REVISION \
            or type(helper["runtime_revision"]) is not str \
            or re.fullmatch(r"[0-9a-f]{40}", helper["runtime_revision"]) is None \
            or type(helper["artifact_digest"]) is not str \
            or _DIGEST.fullmatch(helper["artifact_digest"]) is None:
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
    uid = os.geteuid()
    expected = (f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/"
                f"{unit_name}")
    if unified != expected:
        raise ValueError("process_unproven")
    return unified


def _verify_workspace_parent(run_root: Path | None = None, *,
                             mountinfo_text: str | None = None,
                             required_uid: int | None = None) -> Path:
    """Prove the credential workspace is volatile before asking for bytes."""
    required_uid = os.geteuid() if required_uid is None else required_uid
    if run_root is None:
        run_root = Path("/run/user") / str(required_uid) / "sandbox-image-stage"
    try:
        parent = os.lstat(run_root.parent)
        if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode) \
                or parent.st_uid != required_uid or stat.S_IMODE(parent.st_mode) != 0o700:
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
        parent_fd = os.open(run_root.parent,
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW)
        try:
            try:
                child_fd = os.open(run_root.name,
                                   os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
                                   dir_fd=parent_fd)
            except FileNotFoundError:
                os.mkdir(run_root.name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
                child_fd = os.open(run_root.name,
                                   os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
                                   dir_fd=parent_fd)
            try:
                existing = os.fstat(child_fd)
                if (not stat.S_ISDIR(existing.st_mode) or existing.st_uid != required_uid
                        or stat.S_IMODE(existing.st_mode) != 0o700):
                    raise ValueError("capability_mismatch")
            finally:
                os.close(child_fd)
        finally:
            os.close(parent_fd)
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


def execute_v2(plan: dict, credential: bytes, *, run_root: Path | None = None,
               runner=_run, anonymous_probe=_anonymous_denied,
               cgroup_identity=_cgroup_identity, machine_epoch_reader=None,
               remover=shutil.rmtree) -> dict:
    """Pull and observe the whole plan set in one measured process and lease."""
    plan = _closed_plan_v2(plan)
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
    code = "staged"; observation = None
    try:
        for image in plan["images"]:
            if not anonymous_probe(image["repository"], image["manifest_digest"]):
                raise ValueError("observation_invalid")
        login = runner(("docker", "login", "ghcr.io", "--username", "sandbox-broker",
                        "--password-stdin"), environment=environment,
                       input_data=credential + b"\n", timeout=30)
        if login.returncode != 0: raise ValueError("broker_unavailable")
        target_start = machine_epoch_reader()
        daemon_start_result = runner(("docker", "info", "--format", "{{.ID}}"),
                                     environment=environment, timeout=15)
        if daemon_start_result.returncode != 0: raise ValueError("observation_invalid")
        daemon_start = daemon_start_result.stdout.decode().strip()
        for image in plan["images"]:
            pull = runner(("docker", "pull", image["repository_qualified_digest"]),
                          environment=environment, timeout=600)
            if pull.returncode != 0: raise ValueError("pull_failed")
        # Remove the one credential workspace before any image inspection/result.
        remover(workspace / "docker", ignore_errors=False)
        if (workspace / "docker").exists(): raise ValueError("cleanup_unproven")
        observations = []
        for image in plan["images"]:
            inspect = runner(("docker", "image", "inspect",
                image["repository_qualified_digest"], "--format", "{{json .}}"),
                environment=environment, timeout=30)
            if inspect.returncode != 0: raise ValueError("observation_invalid")
            raw = json.loads(inspect.stdout)
            if type(raw.get("RepoDigests")) is not list \
                    or raw["RepoDigests"].count(image["repository_qualified_digest"]) != 1 \
                    or raw.get("Id") != image["config_digest"]:
                raise ValueError("observation_invalid")
            platform = f'{raw.get("Os")}/{raw.get("Architecture")}'
            if raw.get("Variant"): platform += f'/{raw["Variant"]}'
            if platform != image["platform"]: raise ValueError("observation_invalid")
            observations.append({"name": image["name"], "repository": image["repository"],
                "repo_digest": image["repository_qualified_digest"],
                "config_digest": raw["Id"], "platform": platform,
                "local_image_id": raw["Id"], "anonymous_exact_manifest": "denied",
                "authenticated_exact_manifest": "succeeded"})
        daemon_end_result = runner(("docker", "info", "--format", "{{.ID}}"),
                                   environment=environment, timeout=15)
        target_end = machine_epoch_reader()
        if daemon_end_result.returncode != 0: raise ValueError("observation_invalid")
        daemon_end = daemon_end_result.stdout.decode().strip()
        if not target_start or target_start != target_end \
                or target_start != plan["target"]["machine_identity"] \
                or not daemon_start or daemon_start != daemon_end \
                or daemon_start != plan["target"]["daemon_identity"]:
            raise ValueError("observation_invalid")
        body = {"target_epoch_start": target_start, "target_epoch_end": target_end,
                "daemon_epoch_start": daemon_start, "daemon_epoch_end": daemon_end,
                "target": plan["target"], "images": observations}
        observation = {**body, "observation_digest": staging_digest(
            "sandbox.hosting.images.batch-observation.v2", body)}
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
        return {"schema_version": 2, "ok": True, "code": "staged", "payload": payload}
    if not cleanup_complete: code = "cleanup_unproven"
    return {"schema_version": 2, "ok": False, "code": code, "payload": payload}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    response = None
    response_version = 2 if argv == [FIXED_ENTRY_V2] else 1
    try:
        if argv not in ([FIXED_ENTRY], [FIXED_ENTRY_V2]): raise ValueError("protocol_invalid")
        plan = json.loads(_read_frame(sys.stdin.buffer, MAX_STAGE_FRAME_BYTES))
        # This handshake proves the measured helper is already inside its
        # transient cgroup before the broker resolves credential bytes.
        if plan.get("schema_version") == 1 and argv == [FIXED_ENTRY]: _closed_plan(plan)
        elif plan.get("schema_version") == 2 and argv == [FIXED_ENTRY_V2]: _closed_plan_v2(plan)
        else: raise ValueError("protocol_invalid")
        _cgroup_identity(plan["unit_name"])
        _verify_workspace_parent()
        sys.stdout.buffer.write(b"READY\n"); sys.stdout.buffer.flush()
        credential = _read_frame(sys.stdin.buffer, MAX_CREDENTIAL_BYTES)
        if sys.stdin.buffer.read(1): raise ValueError("protocol_invalid")
        response = execute(plan, credential) if plan["schema_version"] == 1 \
            else execute_v2(plan, credential)
    except Exception as exc:
        code = str(exc) if str(exc) in {"protocol_invalid", "capability_mismatch",
            "process_unproven", "registry_observation_failed", "observation_invalid",
            "broker_unavailable", "pull_failed", "cleanup_unproven", "cancelled"} else "helper_failed"
        response = {"schema_version": response_version, "ok": False, "code": code,
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

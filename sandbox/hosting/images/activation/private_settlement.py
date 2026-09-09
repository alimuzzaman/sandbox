"""Read-only Linux incident inventory. Only keyed identities leave this helper."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import time


if "settlement_refusal" not in globals():
    from sandbox.hosting.images.activation.settlement_diagnostics import settlement_refusal, settlement_diagnostic


_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
_HELPER_MARKERS = (b"compose_graph_v2", b"compose_replace_v2", b"run_init_action",
                   b"sandbox-feature-051", b"activation-inputs")


def _bytes(path, maximum):
    with open(path, "rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("observation_unavailable")
    return value


def _process(pid):
    root = Path("/proc") / str(pid)
    value = _bytes(root / "stat", 8192)
    tail = value[value.rfind(b")") + 2:].split()
    if len(tail) < 20:
        raise ValueError("observation_unavailable")
    return {"pid": pid, "parent": int(tail[1]), "started": int(tail[19]),
            "command": _bytes(root / "cmdline", 1024 * 1024)}


def _process_epoch(project, identities, *, deadline, data_markers=(), sample="first"):
    excluded = set()
    pid = os.getpid()
    for _ in range(64):
        excluded.add(pid)
        if pid <= 1:
            break
        pid = _process(pid)["parent"]
    else:
        raise ValueError("observation_unavailable")
    pids = sorted(int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit())
    if len(pids) > 32768:
        raise ValueError("observation_unavailable")
    daemon = []
    needles = (project.encode(), *(identity.encode() for identity in identities),
               *(value.encode() for value in data_markers))
    for pid in pids:
        if time.monotonic() >= deadline:
            raise ValueError("observation_unavailable")
        if pid in excluded:
            continue
        try:
            row = _process(pid)
        except FileNotFoundError:
            continue
        command = row.pop("command")
        argv = command.split(b"\0")
        if argv and os.path.basename(argv[0]) == b"dockerd":
            daemon.append(row)
        if command and (any(part in command for part in needles)
                        or any(part in command for part in _HELPER_MARKERS)):
            raise settlement_refusal("not_quiescent", "helper_activity_present", "helper_activity", sample)
    if len(daemon) != 1:
        raise ValueError("observation_unavailable")
    boot = _bytes("/proc/sys/kernel/random/boot_id", 64).strip().decode("ascii")
    if re.fullmatch(r"[0-9a-f-]{36}", boot) is None:
        raise ValueError("observation_unavailable")
    return {"boot": boot, "daemon": daemon[0]}


def _path_identity(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("observation_unavailable")
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("observation_unavailable")
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError("observation_unavailable")
    return {"path": str(path), **{name: getattr(info, name) for name in
        ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_mtime_ns", "st_ctime_ns")}}


def _layer_identity(row, storage):
    if type(row.get("Image")) is not str or _DIGEST.fullmatch(row["Image"]) is None:
        raise ValueError("observation_unavailable")
    graph = row.get("GraphDriver")
    if type(graph) is dict and graph.get("Name") == storage["driver"] and type(graph.get("Data")) is dict:
        return {"container": row["Id"], "image": row["Image"], "graph": graph}
    # Docker's containerd store has no legacy GraphDriver paths. Its supported
    # inspect identity is the daemon-scoped immutable container ID plus image
    # descriptor and snapshotter. Never read containerd's private state files.
    descriptor = row.get("ImageManifestDescriptor")
    if (graph is not None or storage["driver"] != "overlayfs"
            or ["driver-type", "io.containerd.snapshotter.v1"] not in storage["status"]
            or row.get("Driver") != "overlayfs" or type(descriptor) is not dict
            or descriptor.get("mediaType") not in {
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json"}
            or type(descriptor.get("digest")) is not str or _DIGEST.fullmatch(descriptor["digest"]) is None
            or type(descriptor.get("size")) is not int or not 0 < descriptor["size"] <= 16 * 1024 * 1024
            or type(row.get("Created")) is not str or not 1 <= len(row["Created"]) <= 128):
        raise ValueError("observation_unavailable")
    platform = descriptor.get("platform")
    if (type(platform) is not dict or not {"os", "architecture"} <= set(platform)
            or set(platform) - {"os", "architecture", "variant"} or platform["os"] != "linux"
            or any(type(value) is not str or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,31}", value) is None
                   for value in platform.values())):
        raise ValueError("observation_unavailable")
    manifest = {key: descriptor[key] for key in ("mediaType", "digest", "size", "platform")}
    return {"container": row["Id"], "image": row["Image"], "created": row["Created"],
            "snapshotter": storage, "manifest": manifest}


def inventory(frame, command, *, deadline):
    if type(frame) is not dict or set(frame) != {
            "target", "compose_project", "transaction_digest", "generation", "binding_key"}:
        raise ValueError("observation_unavailable")
    target = frame["target"]
    if (type(target) is not dict or set(target) != {
            "machine_identity", "target_identity", "daemon_identity"}
            or any(type(value) is not str or not value or len(value) > 256 for value in target.values())
            or type(frame["generation"]) is not int or frame["generation"] < 0
            or type(frame["compose_project"]) is not str
            or _PROJECT.fullmatch(frame["compose_project"]) is None
            or type(frame["transaction_digest"]) is not str
            or _DIGEST.fullmatch(frame["transaction_digest"]) is None):
        raise ValueError("observation_unavailable")
    key = base64.b64decode(frame["binding_key"], validate=True)
    if len(key) != 32:
        raise ValueError("observation_unavailable")
    project = frame["compose_project"]
    def digest(label, value):
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hmac.new(key, b"sandbox-settlement-" + label.encode() + b"\0" + body,
                                     hashlib.sha256).hexdigest()
    def daemon(sample="first"):
        template = '{"ID":{{json .ID}},"Driver":{{json .Driver}},"DriverStatus":{{json .DriverStatus}}}'
        value = json.loads(command(["docker", "info", "--format", template], max_output_bytes=16384))
        if type(value) is not dict or value.get("ID") != target["daemon_identity"]:
            raise settlement_refusal("evidence_changed", "daemon_identity_changed", "daemon", sample)
        if (type(value.get("Driver")) is not str or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value["Driver"]) is None
                or type(value.get("DriverStatus")) is not list or len(value["DriverStatus"]) > 32
                or any(type(row) is not list or len(row) != 2 or any(
                    type(part) is not str or len(part) > 4096 for part in row)
                    for row in value["DriverStatus"])):
            raise ValueError("observation_unavailable")
        return {"driver": value["Driver"], "status": value["DriverStatus"]}
    def container_ids():
        value = command(["docker", "ps", "-aq", "--no-trunc", "--filter",
                         "label=com.docker.compose.project=" + project], max_output_bytes=16384)
        values = sorted(value.decode("ascii").split())
        if len(values) > 128 or len(values) != len(set(values)) or any(_HEX.fullmatch(v) is None for v in values):
            raise ValueError("observation_unavailable")
        return values
    storage = daemon()
    identities = container_ids()
    epoch = _process_epoch(project, identities, deadline=deadline)
    rows = json.loads(command(["docker", "inspect", *identities], max_output_bytes=8 * 1024 * 1024)) if identities else []
    if type(rows) is not list or sorted(row.get("Id", "") for row in rows) != identities:
        raise ValueError("observation_unavailable")
    def project_volumes():
        return set(command(["docker", "volume", "ls", "-q", "--filter",
            "label=com.docker.compose.project=" + project], max_output_bytes=65536).decode().split())
    labelled_volumes = project_volumes()
    volume_names = set(labelled_volumes)
    preserved = []
    container_metadata = []
    bind_paths = set()
    path_identities = {}
    def path_identity(path):
        observed = _path_identity(path)
        if path in path_identities and path_identities[path] != observed:
            raise ValueError("evidence_changed")
        path_identities[path] = observed
        return observed
    for row in sorted(rows, key=lambda value: value["Id"]):
        state = row["State"]
        if row.get("Config", {}).get("Labels", {}).get("com.docker.compose.project") != project:
            raise ValueError("not_quiescent")
        if (state.get("Status") not in {"created", "exited"}
                or state.get("Running") is not False or state.get("Restarting") is not False
                or state.get("Paused") is not False or type(state.get("Pid")) is not int or state["Pid"] != 0):
            # Preserve refusal for malformed/contradictory state without
            # presenting it as proof that a container is running.
            pid = state.get("Pid")
            flags = tuple(state.get(key) for key in ("Running", "Restarting", "Paused"))
            coherent = (all(type(flag) is bool for flag in flags) and type(pid) is int and (
                flags == (True, False, False) and pid > 0 and state.get("Status") == "running"
                or flags == (True, False, True) and pid > 0 and state.get("Status") == "paused"
                or flags == (True, True, False) and pid == 0 and state.get("Status") == "restarting"))
            if coherent:
                raise settlement_refusal("not_quiescent", "container_not_stopped", "owned_container", "first", row["Id"])
            raise ValueError("not_quiescent")
        if row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") not in {"", "no"}:
            if row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") in {"always", "unless-stopped", "on-failure"}:
                raise settlement_refusal("not_quiescent", "container_restart_enabled", "owned_container", "first", row["Id"])
            raise ValueError("not_quiescent")
        mounts = row.get("Mounts")
        if type(mounts) is not list or len(mounts) > 128:
            raise ValueError("observation_unavailable")
        for mount in mounts:
            if mount.get("Type") == "volume":
                volume_names.add(mount["Name"])
            elif mount.get("Type") == "bind":
                bind_paths.add(mount["Source"])
                preserved.append(digest("bind", {"mount": mount, "identity": path_identity(mount["Source"])}))
            elif mount.get("Type") != "tmpfs":
                raise ValueError("observation_unavailable")
        preserved.append(digest("layer", _layer_identity(row, storage)))
        # Hash private metadata; environment values and host paths never become output.
        container_metadata.append(digest("container", row))
    if len(volume_names) > 256 or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", name) is None for name in volume_names):
        raise ValueError("observation_unavailable")
    volumes = json.loads(command(["docker", "volume", "inspect", *sorted(volume_names)],
        max_output_bytes=2 * 1024 * 1024)) if volume_names else []
    if type(volumes) is not list or sorted(row.get("Name", "") for row in volumes) != sorted(volume_names):
        raise ValueError("observation_unavailable")
    for row in volumes:
        if row.get("Driver") != "local" or row.get("Options"):
            raise ValueError("observation_unavailable")
        preserved.append(digest("volume", {"metadata": row, "identity": path_identity(row["Mountpoint"])}))
    # A foreign running consumer can keep changing the same data after the
    # incident's own containers stop. Its private configuration stays here.
    running = sorted(command(["docker", "ps", "-q", "--no-trunc"], max_output_bytes=131072).decode().split())
    if len(running) > 1024 or any(_HEX.fullmatch(value) is None for value in running):
        raise ValueError("observation_unavailable")
    for offset in range(0, len(running), 32):
        consumers = json.loads(command(["docker", "inspect", *running[offset:offset + 32]],
            max_output_bytes=8 * 1024 * 1024))
        if type(consumers) is not list or sorted(row.get("Id", "") for row in consumers) != running[offset:offset + 32]:
            raise ValueError("observation_unavailable")
        for consumer in consumers:
            for mount in consumer.get("Mounts", []):
                if mount.get("Type") == "volume" and mount.get("Name") in volume_names:
                    raise settlement_refusal("not_quiescent", "retained_data_consumer_running", "data_consumer", "first")
                if mount.get("Type") == "bind":
                    source = mount.get("Source")
                    if type(source) is not str or not Path(source).is_absolute():
                        raise ValueError("observation_unavailable")
                    if any(os.path.commonpath((source, path)) in {source, path} for path in bind_paths):
                        raise settlement_refusal("not_quiescent", "retained_data_consumer_running", "data_consumer", "first")
    if container_ids() != identities:
        raise settlement_refusal("evidence_changed", "container_set_changed", "container_set", "comparison")
    if _process_epoch(project, identities, deadline=deadline,
            data_markers=tuple(sorted(volume_names | bind_paths)), sample="second") != epoch:
        raise ValueError("evidence_changed")
    final_rows = json.loads(command(["docker", "inspect", *identities], max_output_bytes=8 * 1024 * 1024)) if identities else []
    final_volumes = json.loads(command(["docker", "volume", "inspect", *sorted(volume_names)],
        max_output_bytes=2 * 1024 * 1024)) if volume_names else []
    final_running = sorted(command(["docker", "ps", "-q", "--no-trunc"], max_output_bytes=131072).decode().split())
    if (final_rows != rows or final_volumes != volumes or project_volumes() != labelled_volumes
            or final_running != running or any(_path_identity(path) != identity
                for path, identity in path_identities.items()) or daemon("second") != storage):
        raise ValueError("evidence_changed")
    return {"target": target, "transaction_digest": frame["transaction_digest"],
        "generation": frame["generation"], "runtime_epoch": target["daemon_identity"],
        "container_identities": identities, "preserved_identities": sorted(set(preserved)),
        "inventory_digest": digest("inventory", {"epoch": epoch, "containers": container_metadata,
            "preserved": sorted(set(preserved))}), "process_identities": [], "quiescent": True}


def main():
    import sys
    try:
        payload = sys.stdin.buffer.read(16385)
        if len(payload) > 16384:
            raise ValueError("observation_unavailable")
        command, deadline = graph_command_port(_ENV, 45)
        result = {"ok": True, "observation": inventory(json.loads(payload), command, deadline=deadline)}
    except Exception as exc:
        code = str(exc)
        result = {"ok": False, "code": code if code in {
            "not_quiescent", "evidence_changed"} else "observation_unavailable"}
        diagnostic = settlement_diagnostic(getattr(exc, "diagnostic", None), result["code"])
        if diagnostic is not None:
            result["diagnostic"] = diagnostic
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")

#!/usr/bin/env python3
"""Fixed-verb privileged boundary for managed-native host objects."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile


MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
STAGING_ROOT = Path("/var/lib/sandbox/native/staging")
POLICY_ROOT = Path("/etc/sandbox/native/policies")
INSTALL_PATH = Path("/usr/local/libexec/sandbox-native-helper")
POLICY_KEYS = {"policy_version", "machine_id", "uid_map", "root_image",
               "read_only_mounts", "writable_mounts", "network", "syscalls",
               "devices", "resources", "credentials", "digest"}


def fail(message, code=65):
    print(f"native-helper: {message}", file=sys.stderr); raise SystemExit(code)


def require_root():
    if os.geteuid() != 0: fail("this verb requires root", 77)


def machine(value):
    if not MACHINE.fullmatch(value): fail("invalid machine id")
    return value


def digest_value(value):
    if not re.fullmatch(r"[a-f0-9]{64}", value): fail("invalid policy digest")
    return value


def canonical_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_schema(value, machine_id):
    if set(value) != POLICY_KEYS or value.get("policy_version") != 1:
        fail("policy schema is invalid")
    if value.get("machine_id") != machine_id:
        fail("policy identity mismatch")
    for key in ("uid_map", "root_image", "network", "syscalls", "resources"):
        if not isinstance(value.get(key), dict): fail(f"policy {key} is invalid")
    for key in ("read_only_mounts", "writable_mounts", "devices", "credentials"):
        if not isinstance(value.get(key), list): fail(f"policy {key} is invalid")
    image_spec = value["root_image"]
    if set(image_spec) != {"path", "bytes", "inodes"}:
        fail("policy root image is invalid")
    image = image_spec.get("path")
    expected_image = f"/var/lib/sandbox/native/instances/{machine_id}/root.img"
    if image != expected_image: fail("policy image path is outside its fixed root")
    if value["network"].get("egress") != "deny": fail("policy must default-deny egress")
    uid_map = value["uid_map"]
    if (set(uid_map) != {"base", "count"} or uid_map.get("count") != 65536 or
            isinstance(uid_map.get("base"), bool) or not isinstance(uid_map.get("base"), int)
            or uid_map["base"] < 65536 or uid_map["base"] % 65536):
        fail("policy private UID map is invalid")
    project_root = None
    for mount in value["read_only_mounts"]:
        if not isinstance(mount, dict) or set(mount) != {"source", "target"}:
            fail("policy read-only mount is invalid")
        if not isinstance(mount["source"], str) or not isinstance(mount["target"], str):
            fail("policy read-only mount is invalid")
        source = Path(mount["source"])
        if mount["target"] != "/workspace" or project_root is not None:
            fail("policy must expose exactly one project root")
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            fail("policy project root is unavailable")
        project_root = source.resolve(strict=True)
    if project_root is None: fail("policy project root is required")
    state_root = Path(f"/var/lib/sandbox/native/instances/{machine_id}/state")
    allowed_targets = ("/workspace/", "/var/lib/sandbox/", "/var/log/sandbox/")
    for mount in value["writable_mounts"]:
        if not isinstance(mount, dict) or set(mount) != {"source", "target"}:
            fail("policy writable mount is invalid")
        if not isinstance(mount["source"], str) or not isinstance(mount["target"], str):
            fail("policy writable mount is invalid")
        source = Path(mount["source"])
        if not source.is_absolute() or source.is_symlink() or not source.exists():
            fail("policy writable source is unavailable")
        resolved = source.resolve(strict=True)
        raw_target = mount["target"]
        target_path = PurePosixPath(raw_target)
        if (not target_path.is_absolute() or ".." in target_path.parts or
                str(target_path) != raw_target.rstrip("/")):
            fail("policy writable target is forbidden")
        target = str(target_path).rstrip("/") + "/"
        if not target.startswith(allowed_targets): fail("policy writable target is forbidden")
        if not (resolved.is_relative_to(project_root) or resolved.is_relative_to(state_root)):
            fail("policy writable source escapes owned roots")
    network = value["network"]
    if set(network) != {"egress", "veth", "host_address", "guest_address",
                       "default_route"}:
        fail("policy point-to-point network is invalid")
    try:
        host = ipaddress.ip_interface(network["host_address"])
        guest = ipaddress.ip_interface(network["guest_address"])
    except (KeyError, ValueError): fail("policy point-to-point network is invalid")
    pool = ipaddress.ip_network("10.203.0.0/16")
    if (host.network != guest.network or host.network.prefixlen != 30 or host.ip == guest.ip
            or not host.ip in pool or not guest.ip in pool or network.get("default_route") is not False
            or not re.fullmatch(r"ve-[a-z0-9-]{1,12}", str(network.get("veth", "")))):
        fail("policy point-to-point network is invalid")
    if (set(value["syscalls"]) != {"no_new_privileges", "seccomp"} or
            value["syscalls"].get("no_new_privileges") is not True or
            value["syscalls"].get("seccomp") != "managed-v1"):
        fail("policy must enforce no-new-privileges")
    if value["devices"]:
        fail("policy device access must remain closed")
    required_resources = {"cpu_percent", "memory_bytes", "pids", "runtime_seconds",
                          "disk_bytes", "inodes", "fds", "connections", "io_weight"}
    ranges = {"cpu_percent": (10, 6400), "memory_bytes": (128 * 1024**2, 256 * 1024**3),
              "pids": (32, 65536), "runtime_seconds": (1, 86400),
              "disk_bytes": (1024**3, 1024**4), "inodes": (10000, 10000000),
              "fds": (128, 1048576), "connections": (16, 65535),
              "io_weight": (1, 10000)}
    resources = value["resources"]
    if set(resources) != required_resources or any(
            isinstance(resources[key], bool) or not isinstance(resources[key], int) or
            not minimum <= resources[key] <= maximum
            for key, (minimum, maximum) in ranges.items()):
        fail("policy resource limits are invalid")
    if (image_spec["bytes"] != resources["disk_bytes"] or
            image_spec["inodes"] != resources["inodes"]):
        fail("policy image and resource limits disagree")
    if any(not isinstance(ref, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,127}", ref)
           for ref in value["credentials"]):
        fail("policy credential reference is invalid")


def checked_policy(path_value, machine_id, *, applied=False):
    path = Path(path_value)
    try: details = path.lstat()
    except OSError: fail("policy file is unavailable")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        fail("policy must be a regular non-symlink")
    expected_root = POLICY_ROOT if applied else STAGING_ROOT
    try: canonical = path.resolve(strict=True); canonical.relative_to(expected_root)
    except (OSError, ValueError): fail("policy path is outside its fixed root")
    expected = expected_root / f"{machine_id}.json"
    if canonical != expected: fail("policy path does not match machine identity")
    expected_uid = 0 if applied else int(os.environ.get("SUDO_UID", os.getuid()))
    if details.st_uid != expected_uid: fail("policy owner mismatch")
    if details.st_mode & 0o022: fail("policy must not be group/world writable")
    if details.st_size > 1024 * 1024: fail("policy is too large")
    try: value = json.loads(canonical.read_text())
    except (OSError, json.JSONDecodeError): fail("policy JSON is invalid")
    if not isinstance(value, dict): fail("policy schema is invalid")
    validate_schema(value, machine_id)
    supplied = value.get("digest"); basis = {key: val for key, val in value.items() if key != "digest"}
    if not isinstance(supplied, str) or supplied != canonical_digest(basis):
        fail("policy digest mismatch")
    return canonical, value


def atomic_install(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def applied_policy(machine_id, digest):
    path, value = checked_policy(POLICY_ROOT / f"{machine_id}.json", machine_id,
                                 applied=True)
    if value["digest"] != digest_value(digest): fail("applied policy digest changed")
    return path, value


def run_fixed(argv, message):
    result = subprocess.run(tuple(argv), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=120, check=False, close_fds=True)
    if result.returncode != 0: fail(message)
    return result


def image_paths(machine_id):
    instance = Path("/var/lib/sandbox/native/instances") / machine_id
    mountpoint = Path("/var/lib/sandbox/native/mounts") / machine_id
    return instance, instance / "root.img", mountpoint


def image_create(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    instance, image, _mountpoint = image_paths(machine_id)
    if instance.exists() and (instance.is_symlink() or not instance.is_dir()):
        fail("native instance root is foreign")
    instance.mkdir(parents=True, exist_ok=True, mode=0o700)
    if image.exists() or image.is_symlink(): fail("native image already exists")
    spec = policy["root_image"]
    size = spec.get("bytes"); inodes = spec.get("inodes")
    if (isinstance(size, bool) or not isinstance(size, int) or
            not 1024**3 <= size <= 1024**4): fail("native image size is invalid")
    if (isinstance(inodes, bool) or not isinstance(inodes, int) or
            not 10000 <= inodes <= 10000000): fail("native image inode limit is invalid")
    try:
        run_fixed(("truncate", "--size", str(size), str(image)), "image allocation failed")
        run_fixed(("mkfs.ext4", "-F", "-q", "-m", "0", "-N", str(inodes), str(image)),
                  "image format failed")
        os.chown(image, 0, 0); os.chmod(image, 0o600)
    except BaseException:
        if image.exists() and not image.is_symlink(): image.unlink()
        raise


def image_mount(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    _instance, image, mountpoint = image_paths(machine_id)
    if not image.is_file() or image.is_symlink() or image.stat().st_uid != 0:
        fail("owned native image is unavailable")
    if mountpoint.exists() and (mountpoint.is_symlink() or not mountpoint.is_dir()):
        fail("native mountpoint is foreign")
    mountpoint.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.path.ismount(mountpoint): fail("native image is already mounted")
    run_fixed(("mount", "-o", "loop,nodev,nosuid,noatime", str(image), str(mountpoint)),
              "native image mount failed")
    if not os.path.ismount(mountpoint): fail("native image mount was not observed")


def image_unmount(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    _instance, image, mountpoint = image_paths(machine_id)
    if mountpoint.is_symlink(): fail("native mountpoint is foreign")
    if not os.path.ismount(mountpoint): return
    backing = run_fixed(("findmnt", "-n", "-o", "FSROOT", "--target", str(mountpoint)),
                        "native mount ownership could not be observed")
    # A loop-mounted ext4 image must expose filesystem root '/'. Other values
    # indicate an unexpected bind/subvolume and are never unmounted by Sandbox.
    if backing.stdout.strip() != "/": fail("native mount ownership is ambiguous")
    loops = run_fixed(("losetup", "-j", str(image)), "native loop ownership unavailable")
    if not loops.stdout.strip(): fail("native mount is not backed by the owned image")
    run_fixed(("umount", str(mountpoint)), "native image unmount failed")
    try: mountpoint.rmdir()
    except OSError: pass


def image_remove(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    _instance, image, mountpoint = image_paths(machine_id)
    if os.path.ismount(mountpoint): fail("native image remains mounted")
    if not image.exists(): return
    details = image.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != 0:
        fail("native image ownership changed")
    image.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sandbox-native-helper")
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check-policy"); check.add_argument("machine"); check.add_argument("path")
    install = sub.add_parser("install")
    apply_policy = sub.add_parser("policy-install"); apply_policy.add_argument("machine"); apply_policy.add_argument("path")
    status = sub.add_parser("policy-status"); status.add_argument("machine")
    for name in ("image-create", "image-mount", "image-unmount", "image-remove"):
        action = sub.add_parser(name); action.add_argument("machine"); action.add_argument("digest")
    args = parser.parse_args(argv)
    if args.verb == "check-policy":
        identity = machine(args.machine); checked_policy(args.path, identity); print("policy-ok")
    elif args.verb == "install":
        require_root(); atomic_install(Path(__file__).resolve(), INSTALL_PATH)
        os.chmod(INSTALL_PATH, 0o755)
    elif args.verb == "policy-install":
        require_root(); identity = machine(args.machine)
        source, value = checked_policy(args.path, identity)
        # Re-read and re-hash immediately before the root-owned copy closes the staging race.
        if canonical_digest({k: v for k, v in json.loads(source.read_text()).items()
                             if k != "digest"}) != value["digest"]:
            fail("policy changed during validation")
        atomic_install(source, POLICY_ROOT / f"{identity}.json")
    elif args.verb == "policy-status":
        require_root(); identity = machine(args.machine)
        _path, value = checked_policy(POLICY_ROOT / f"{identity}.json", identity, applied=True)
        print(json.dumps({"machine_id": identity, "digest": value["digest"]}, sort_keys=True))
    elif args.verb in {"image-create", "image-mount", "image-unmount", "image-remove"}:
        require_root(); identity = machine(args.machine)
        {"image-create": image_create, "image-mount": image_mount,
         "image-unmount": image_unmount, "image-remove": image_remove}[args.verb](
             identity, args.digest)


if __name__ == "__main__": main()

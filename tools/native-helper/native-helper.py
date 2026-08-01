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
import time


MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
STAGING_ROOT = Path("/var/lib/sandbox/native/staging")
POLICY_ROOT = Path("/etc/sandbox/native/policies")
INSTALL_PATH = Path("/usr/local/libexec/sandbox-native-helper")
APPARMOR_ROOT = Path("/etc/apparmor.d")
POLICY_KEYS = {"policy_version", "machine_id", "uid_map", "root_image",
               "read_only_mounts", "writable_mounts", "network", "syscalls",
               "devices", "resources", "credentials", "digest"}
FIXED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


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
        if (not source.is_absolute() or source.is_symlink() or not source.is_dir() or
                ":" in mount["source"] or any(ord(char) < 32 for char in mount["source"])):
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
        if (not source.is_absolute() or source.is_symlink() or not source.exists() or
                ":" in mount["source"] or any(ord(char) < 32 for char in mount["source"])):
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
                       "default_route", "ingress_port", "grants"}:
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
    ingress_port = network.get("ingress_port")
    if (isinstance(ingress_port, bool) or not isinstance(ingress_port, int) or
            not 1024 <= ingress_port <= 65535 or not isinstance(network.get("grants"), list)):
        fail("policy point-to-point network is invalid")
    for grant in network["grants"]:
        if (not isinstance(grant, dict) or
                set(grant) != {"grant_id", "destinations", "ports", "expires_at", "revoked"} or
                not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(grant.get("grant_id", ""))) or
                not isinstance(grant.get("destinations"), list) or
                not isinstance(grant.get("ports"), list) or
                not isinstance(grant.get("expires_at"), str) or
                grant.get("revoked") not in {True, False}):
            fail("policy egress grant is invalid")
        if any(isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
               for port in grant["ports"]):
            fail("policy egress grant is invalid")
        for destination in grant["destinations"]:
            try: destination_network = ipaddress.ip_network(destination, strict=False)
            except (TypeError, ValueError): fail("policy egress grant is invalid")
            if (destination_network.version != 4 or destination_network.is_private or
                    destination_network.is_loopback or destination_network.is_link_local or
                    destination_network.is_multicast or destination_network.is_unspecified):
                fail("policy egress grant is invalid")
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


def _read_checked_policy(path_value, machine_id, *, applied=False):
    path = Path(path_value)
    expected_root = POLICY_ROOT if applied else STAGING_ROOT
    expected = expected_root / f"{machine_id}.json"
    try:
        if path.absolute() != expected.absolute():
            fail("policy path does not match machine identity")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        fail("policy file is unavailable")
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            fail("policy must be a regular non-symlink")
        expected_uid = 0 if applied else int(os.environ.get("SUDO_UID", os.getuid()))
        if details.st_uid != expected_uid: fail("policy owner mismatch")
        if details.st_mode & 0o022: fail("policy must not be group/world writable")
        if details.st_size > 1024 * 1024: fail("policy is too large")
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk: break
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) != details.st_size: fail("policy changed during validation")
    finally:
        os.close(descriptor)
    try: value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError): fail("policy JSON is invalid")
    if not isinstance(value, dict): fail("policy schema is invalid")
    validate_schema(value, machine_id)
    supplied = value.get("digest"); basis = {key: val for key, val in value.items() if key != "digest"}
    if not isinstance(supplied, str) or supplied != canonical_digest(basis):
        fail("policy digest mismatch")
    return expected, value, payload


def checked_policy(path_value, machine_id, *, applied=False):
    path, value, _payload = _read_checked_policy(path_value, machine_id, applied=applied)
    return path, value


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


def atomic_install_bytes(payload, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(payload); output.flush(); os.fsync(output.fileno())
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


def run_fixed(argv, message, *, input_text=None):
    result = subprocess.run(tuple(argv), stdin=subprocess.DEVNULL if input_text is None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            input=input_text, text=True, timeout=120, check=False,
                            close_fds=True, env=FIXED_ENVIRONMENT)
    if result.returncode != 0: fail(message)
    return result


def run_optional(argv, *, input_text=None):
    return subprocess.run(tuple(argv), stdin=subprocess.DEVNULL if input_text is None else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          input=input_text, text=True, timeout=30, check=False,
                          close_fds=True, env=FIXED_ENVIRONMENT)


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


def network_names(machine_id):
    return "sb_" + machine_id[3:], f"sandbox-native:{machine_id}"


def network_marker(machine_id, digest):
    return f"sandbox-native:{machine_id}:{digest}"


def machine_leader(machine_id):
    result = run_optional(("machinectl", "show", machine_id, "--property=Leader", "--value"))
    try: leader = int((result.stdout or "").strip())
    except ValueError: fail("native machine leader is unavailable")
    if result.returncode != 0 or leader <= 1 or not Path(f"/proc/{leader}/ns/net").exists():
        fail("native machine leader is unavailable")
    return leader


def observed_link(veth):
    result = run_optional(("ip", "-j", "link", "show", "dev", veth))
    if result.returncode != 0: return None
    try: values = json.loads(result.stdout or "[]")
    except json.JSONDecodeError: fail("native veth observation is invalid")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        fail("native veth observation is invalid")
    return values[0]


def observed_nft_table(table):
    result = run_optional(("nft", "-j", "list", "table", "inet", table))
    if result.returncode != 0: return None
    try: document = json.loads(result.stdout or "{}")
    except json.JSONDecodeError: fail("native nft observation is invalid")
    for item in document.get("nftables", ()) if isinstance(document, dict) else ():
        row = item.get("table") if isinstance(item, dict) else None
        if isinstance(row, dict) and row.get("family") == "inet" and row.get("name") == table:
            return row
    fail("native nft observation is invalid")


def network_apply(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]
    if any(not grant["revoked"] for grant in network["grants"]):
        fail("native egress grants require the isolated broker")
    table, alias_prefix = network_names(machine_id)
    marker = network_marker(machine_id, digest)
    veth = network["veth"]
    link = observed_link(veth)
    if link is None: fail("native veth is unavailable")
    observed_alias = link.get("ifalias", "")
    if observed_alias and observed_alias != alias_prefix:
        fail("native veth ownership is foreign")
    if observed_nft_table(table) is not None:
        fail("native nft table already exists")
    run_fixed(("ip", "link", "set", "dev", veth, "alias", alias_prefix),
              "native veth ownership could not be marked")
    run_fixed(("ip", "address", "replace", network["host_address"], "dev", veth),
              "native host veth address failed")
    guest = str(ipaddress.ip_interface(network["guest_address"]).ip)
    leader = machine_leader(machine_id)
    namespace = ("nsenter", "--target", str(leader), "--net", "--")
    run_fixed((*namespace, "ip", "address", "flush", "dev", "host0", "scope", "global"),
              "native guest address reset failed")
    run_fixed((*namespace, "ip", "route", "flush", "table", "main"),
              "native guest route reset failed")
    run_fixed((*namespace, "ip", "address", "replace", network["guest_address"],
               "dev", "host0"), "native guest veth address failed")
    run_fixed((*namespace, "ip", "link", "set", "dev", "host0", "up"),
              "native guest veth activation failed")
    run_fixed((*namespace, "ip", "link", "set", "dev", "lo", "up"),
              "native guest loopback activation failed")
    script = "\n".join((
        f'add table inet {table} {{ comment "{marker}"; }}',
        f"add chain inet {table} input {{ type filter hook input priority filter; policy accept; }}",
        f"add chain inet {table} forward {{ type filter hook forward priority filter; policy accept; }}",
        f'add rule inet {table} input iifname "{veth}" ip saddr {guest} ct state established,related accept',
        f'add rule inet {table} input iifname "{veth}" counter drop',
        f'add rule inet {table} forward iifname "{veth}" counter drop',
    )) + "\n"
    try:
        run_fixed(("nft", "-f", "-"), "native nft policy failed", input_text=script)
    except BaseException:
        run_optional(("ip", "address", "del", network["host_address"], "dev", veth))
        raise


def network_status(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]; table, alias_prefix = network_names(machine_id)
    link = observed_link(network["veth"]); nft_table = observed_nft_table(table)
    ok = bool(link and link.get("ifalias") == alias_prefix and nft_table and
              nft_table.get("comment") == network_marker(machine_id, digest))
    print(json.dumps({"machine_id": machine_id, "policy_digest": digest,
                      "veth": network["veth"], "table": table, "ok": ok}, sort_keys=True))
    if not ok: raise SystemExit(69)


def network_remove(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]; table, alias_prefix = network_names(machine_id)
    nft_table = observed_nft_table(table)
    if nft_table is not None:
        if nft_table.get("comment") != network_marker(machine_id, digest):
            fail("native nft ownership changed")
        run_fixed(("nft", "delete", "table", "inet", table),
                  "native nft table removal failed")
    link = observed_link(network["veth"])
    if link is not None:
        if link.get("ifalias") != alias_prefix: fail("native veth ownership changed")
        run_optional(("ip", "address", "del", network["host_address"],
                      "dev", network["veth"]))


def machine_names(machine_id):
    return f"sandbox-native-{machine_id}.service", f"sandbox-native-{machine_id}"


def compile_apparmor_profile(machine_id, policy_digest):
    profile = f"sandbox-native-{machine_id}"
    return f"""#include <tunables/global>

# Sandbox policy {policy_digest}
profile {profile} flags=(attach_disconnected,mediate_deleted) {{
  #include <abstractions/base>
  capability,
  network,
  mount,
  remount,
  umount,
  pivot_root,
  ptrace,
  signal,
  dbus,
  userns,
  /** rwklm,
  /** ix,
  /usr/lib/systemd/systemd cx -> guest,
  /lib/systemd/systemd cx -> guest,
  /sbin/init cx -> guest,

  profile guest flags=(attach_disconnected,mediate_deleted) {{
    #include <abstractions/base>
    capability audit_write,
    capability chown,
    capability dac_override,
    capability fowner,
    capability fsetid,
    capability kill,
    capability net_bind_service,
    capability setfcap,
    capability setgid,
    capability setpcap,
    capability setuid,
    capability sys_chroot,
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    dbus,
    /** rwklm,
    /** ix,
  }}
}}
"""


def apparmor_loaded(machine_id):
    profile = f"sandbox-native-{machine_id}"
    try:
        names = {line.split(" ", 1)[0]
                 for line in Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines()}
        return profile in names and profile + "//guest" in names
    except OSError:
        return False


def apparmor_install(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    destination = APPARMOR_ROOT / f"sandbox-native-{machine_id}"
    payload = compile_apparmor_profile(machine_id, digest).encode()
    if destination.exists() or destination.is_symlink():
        details = destination.lstat()
        if (stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or
                details.st_uid != 0 or destination.read_bytes() != payload):
            fail("native AppArmor profile ownership changed")
    else:
        atomic_install_bytes(payload, destination)
    run_fixed(("apparmor_parser", "--replace", "--skip-cache", str(destination)),
              "native AppArmor profile load failed")
    if not apparmor_loaded(machine_id): fail("native AppArmor profiles were not observed")


def apparmor_status(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    ok = apparmor_loaded(machine_id)
    print(json.dumps({"machine_id": machine_id, "policy_digest": digest,
                      "profile": f"sandbox-native-{machine_id}", "ok": ok}, sort_keys=True))
    if not ok: raise SystemExit(69)


def apparmor_remove(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    unit, _profile = machine_names(machine_id)
    if unit_description(unit): fail("native machine must stop before AppArmor removal")
    destination = APPARMOR_ROOT / f"sandbox-native-{machine_id}"
    if not destination.exists():
        if apparmor_loaded(machine_id): fail("native AppArmor profile file is missing")
        return
    payload = compile_apparmor_profile(machine_id, digest).encode()
    details = destination.lstat()
    if (stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or
            details.st_uid != 0 or destination.read_bytes() != payload):
        fail("native AppArmor profile ownership changed")
    run_fixed(("apparmor_parser", "--remove", "--skip-cache", str(destination)),
              "native AppArmor profile removal failed")
    destination.unlink()


def unit_description(unit):
    result = run_optional(("systemctl", "show", unit, "--property=Description", "--value"))
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def machine_command(policy):
    machine_id = policy["machine_id"]; digest = policy["digest"]
    unit, profile = machine_names(machine_id)
    resources = policy["resources"]
    description = f"Sandbox native {machine_id} policy {digest}"
    properties = (
        f"Description={description}", "KillMode=mixed", "DevicePolicy=closed",
        "DeviceAllow=/dev/null rw", "DeviceAllow=/dev/zero rw",
        "DeviceAllow=/dev/full rw", "DeviceAllow=/dev/random r",
        "DeviceAllow=/dev/urandom r", "DeviceAllow=/dev/loop-control rw",
        "DeviceAllow=block-loop rwm",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
        "LockPersonality=yes", "RestrictSUIDSGID=yes", "NoNewPrivileges=yes",
        f"AppArmorProfile={profile}", f"CPUQuota={resources['cpu_percent']}%",
        f"MemoryMax={resources['memory_bytes']}", "MemorySwapMax=0",
        f"TasksMax={resources['pids']}", f"RuntimeMaxSec={resources['runtime_seconds']}",
        f"LimitNOFILE={resources['fds']}", f"IOWeight={resources['io_weight']}",
    )
    dropped = ("CAP_AUDIT_CONTROL", "CAP_DAC_READ_SEARCH", "CAP_IPC_OWNER", "CAP_LEASE",
               "CAP_LINUX_IMMUTABLE", "CAP_MKNOD", "CAP_NET_ADMIN", "CAP_NET_BROADCAST",
               "CAP_NET_RAW", "CAP_SYS_ADMIN", "CAP_SYS_BOOT", "CAP_SYS_NICE",
               "CAP_SYS_PTRACE", "CAP_SYS_RESOURCE", "CAP_SYS_TTY_CONFIG")
    nspawn = ["/usr/bin/systemd-nspawn", "--quiet", "--boot", "--keep-unit",
              "--register=yes", "--settings=no", f"--machine={machine_id}",
              f"--image={policy['root_image']['path']}",
              f"--private-users={policy['uid_map']['base']}:{policy['uid_map']['count']}",
              "--private-users-ownership=map",
              "--private-network",
              f"--network-veth-extra={policy['network']['veth']}:host0",
              "--resolv-conf=off", "--link-journal=no-host", "--no-new-privileges=yes",
              "--drop-capability=" + ",".join(dropped),
              "--system-call-filter=@system-service ~@mount ~@raw-io ~@reboot ~@swap"]
    for mount in policy["read_only_mounts"]:
        nspawn.append(f"--bind-ro={mount['source']}:{mount['target']}:norbind")
    for mount in policy["writable_mounts"]:
        nspawn.append(f"--bind={mount['source']}:{mount['target']}:norbind")
    command = ["systemd-run", "--no-block", "--collect", f"--unit={unit}",
               "--service-type=notify"]
    command.extend(f"--property={value}" for value in properties)
    command.extend(nspawn)
    return tuple(command), description


def machine_start_minimal(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    unit, _profile = machine_names(machine_id)
    if not apparmor_loaded(machine_id): fail("native AppArmor profile is not loaded")
    _instance, image, mountpoint = image_paths(machine_id)
    if not image.is_file() or image.is_symlink() or image.stat().st_uid != 0:
        fail("owned native image is unavailable")
    if os.path.ismount(mountpoint): fail("native image remains mounted for provisioning")
    if unit_description(unit): fail("native machine unit already exists")
    machine = run_optional(("machinectl", "show", machine_id))
    if machine.returncode == 0: fail("native machine identity already exists")
    command, description = machine_command(policy)
    run_fixed(command, "native machine start failed")
    for _attempt in range(100):
        if unit_description(unit) == description and observed_link(policy["network"]["veth"]):
            return
        time.sleep(0.1)
    fail("native machine did not expose its owned unit and veth")


def machine_status(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    unit, _profile = machine_names(machine_id)
    expected = f"Sandbox native {machine_id} policy {digest}"
    active = run_optional(("systemctl", "is-active", unit))
    machine = run_optional(("machinectl", "show", machine_id))
    ok = (unit_description(unit) == expected and active.returncode == 0 and
          (active.stdout or "").strip() == "active" and machine.returncode == 0)
    print(json.dumps({"machine_id": machine_id, "policy_digest": digest,
                      "unit": unit, "ok": ok}, sort_keys=True))
    if not ok: raise SystemExit(69)


def machine_stop(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    unit, _profile = machine_names(machine_id)
    description = unit_description(unit)
    if not description: return
    if description != f"Sandbox native {machine_id} policy {digest}":
        fail("native machine unit ownership changed")
    run_fixed(("systemctl", "stop", unit), "native machine stop failed")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sandbox-native-helper")
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check-policy"); check.add_argument("machine"); check.add_argument("path")
    install = sub.add_parser("install")
    apply_policy = sub.add_parser("policy-install"); apply_policy.add_argument("machine"); apply_policy.add_argument("path")
    status = sub.add_parser("policy-status"); status.add_argument("machine")
    for name in ("image-create", "image-mount", "image-unmount", "image-remove",
                 "network-apply", "network-status", "network-remove",
                 "machine-start-minimal", "machine-status", "machine-stop",
                 "apparmor-install", "apparmor-status", "apparmor-remove"):
        action = sub.add_parser(name); action.add_argument("machine"); action.add_argument("digest")
    args = parser.parse_args(argv)
    if args.verb == "check-policy":
        identity = machine(args.machine); checked_policy(args.path, identity); print("policy-ok")
    elif args.verb == "install":
        require_root(); atomic_install(Path(__file__).resolve(), INSTALL_PATH)
        os.chmod(INSTALL_PATH, 0o755)
    elif args.verb == "policy-install":
        require_root(); identity = machine(args.machine)
        _source, _value, payload = _read_checked_policy(args.path, identity)
        # Install the exact bytes read from the validated O_NOFOLLOW descriptor;
        # never reopen the user-controlled staging pathname.
        atomic_install_bytes(payload, POLICY_ROOT / f"{identity}.json")
    elif args.verb == "policy-status":
        require_root(); identity = machine(args.machine)
        _path, value = checked_policy(POLICY_ROOT / f"{identity}.json", identity, applied=True)
        print(json.dumps({"machine_id": identity, "digest": value["digest"]}, sort_keys=True))
    elif args.verb in {"image-create", "image-mount", "image-unmount", "image-remove",
                      "network-apply", "network-status", "network-remove",
                      "machine-start-minimal", "machine-status", "machine-stop",
                      "apparmor-install", "apparmor-status", "apparmor-remove"}:
        require_root(); identity = machine(args.machine)
        {"image-create": image_create, "image-mount": image_mount,
         "image-unmount": image_unmount, "image-remove": image_remove,
         "network-apply": network_apply, "network-status": network_status,
         "network-remove": network_remove, "machine-start-minimal": machine_start_minimal,
         "machine-status": machine_status, "machine-stop": machine_stop,
         "apparmor-install": apparmor_install, "apparmor-status": apparmor_status,
         "apparmor-remove": apparmor_remove}[args.verb](
             identity, args.digest)


if __name__ == "__main__": main()

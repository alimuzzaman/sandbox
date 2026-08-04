#!/usr/bin/env python3
"""Fixed-verb privileged boundary for managed-native host objects."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
import pwd
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time


MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
STAGING_ROOT = Path("/var/lib/sandbox/native/staging")
INJECTED_ROOT = Path("/var/lib/sandbox/native/injected")
RUNTIME_ROOT = Path("/run/sandbox-native")
GUEST_CREDENTIAL_SOURCE_ROOT = "/run/sandbox-native-credentials"
GUEST_CREDENTIAL_TARGET_ROOT = "/run/credentials/sandbox"
POLICY_ROOT = Path("/etc/sandbox/native/policies")
POLICY_OWNER_ROOT = Path("/etc/sandbox/native/owners")
NETWORK_STATE_ROOT = Path("/etc/sandbox/native/networks")
INSTALL_PATH = Path("/usr/local/libexec/sandbox-native-helper")
BROKER_SOURCE = Path(__file__).with_name("native-egress-broker.py")
BROKER_INSTALL_PATH = Path("/usr/local/libexec/sandbox-native-egress-broker")
EGRESS_ROOT = Path("/etc/sandbox/native/egress")
GRANT_ROOT = Path("/etc/sandbox/native/grants")
GRANT_LOCK_ROOT = Path("/run/lock/sandbox-native")
FOREIGN_DATA_SENTINEL = Path("/var/lib/mysql/.sandbox-native-coexistence-sentinel")
GRANT_AUTHORITY = "staged-v1"
ABSENT_GRANT_DIGEST = "0" * 64
BROKER_PORT = 18443
APPARMOR_ROOT = Path("/etc/apparmor.d")
POLICY_KEYS = {"policy_version", "machine_id", "uid_map", "root_image",
               "read_only_mounts", "writable_mounts", "network", "syscalls",
               "devices", "resources", "credentials", "digest"}
FIXED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
OFFICIAL_APT_SOURCE = Path("/etc/apt/sources.list.d/ubuntu.sources")
OFFICIAL_APT_URIS = {"http://archive.ubuntu.com/ubuntu", "http://security.ubuntu.com/ubuntu",
                     "https://archive.ubuntu.com/ubuntu", "https://security.ubuntu.com/ubuntu"}
HOST_PACKAGE_ROOTS = ("systemd-container", "bubblewrap", "nftables", "debootstrap", "e2fsprogs")
IMAGE_PACKAGE_ROOTS = {"php8.3-fpm", "php8.3-cli", "php8.3-mysql", "php8.3-curl",
                       "php8.3-gd", "php8.3-mbstring", "php8.3-xml", "php8.3-zip",
                       "php8.3-intl", "php8.3-opcache", "mariadb-server",
                       "mariadb-client", "cron", "ca-certificates", "curl", "unzip",
                       "git", "composer", "bubblewrap", "iproute2", "util-linux"}
WORDPRESS_URL = "https://wordpress.org/wordpress-6.8.2.tar.gz"
WORDPRESS_SHA256 = "d85a72e392bfe866816b3c2ebc6a44699072aa50cc3a620f1c4ed2f13b645e2b"
WP_CLI_URL = "https://github.com/wp-cli/wp-cli/releases/download/v2.12.0/wp-cli-2.12.0.phar"
WP_CLI_SHA256 = "ce34ddd838f7351d6759068d09793f26755463b4a4610a5a5c0a97b68220d85c"
PHPUNIT_URL = "https://phar.phpunit.de/phpunit-9.6.34.phar"
PHPUNIT_SHA256 = "e7264ae61fe58a487c2bd741905b85940d8fbc2b32cf4a279949b6d9a172a06a"
PHPUNIT_PATH = "/usr/local/libexec/sandbox-phpunit.phar"
EXECUTION_ENV_ALLOWLIST = {"PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME",
                           "WP_ENVIRONMENT_TYPE", "XDEBUG_TRIGGER", "HTTP_PROXY", "HTTPS_PROXY"}
EXECUTION_WRITABLE_TARGETS = {"/var/www/html", "/var/lib/sandbox", "/var/log/sandbox",
                              "/run/mysqld"}
PERSISTENT_WRITABLE_TARGETS = EXECUTION_WRITABLE_TARGETS | {"/run/php"}
GRANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
GRANT_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
HOSTNAME = re.compile(
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
FORBIDDEN_IPV4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
REQUIRED_NETWORK_RULES = (
    "guest_host_established", "guest_host_drop", "ingress",
    "host_guest_drop", "guest_forward_drop", "guest_forward_reply_drop",
)
BROKER_NETWORK_RULES = ("egress_broker_request", "egress_broker_reply")
CREDENTIAL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PROBE_TOKEN = re.compile(r"^[a-f0-9]{16}$")
LOGIN_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


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


def utc_now():
    return datetime.now(timezone.utc)


def parse_expiry(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        fail("policy egress grant expiry is invalid")
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: fail("policy egress grant expiry is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("policy egress grant expiry must include a timezone")
    return parsed.astimezone(timezone.utc)


def public_ipv4_network(value):
    try: network = ipaddress.ip_network(value, strict=True)
    except (TypeError, ValueError): fail("policy egress grant destination is invalid")
    if (network.version != 4 or not network.network_address.is_global or
            not network.broadcast_address.is_global or
            any(network.overlaps(blocked) for blocked in FORBIDDEN_IPV4)):
        fail("policy egress grant destination must be an exact public IPv4 CIDR")
    return network


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
    legacy_network = set(network) == {"egress", "veth", "host_address", "guest_address",
                                      "default_route", "ingress_port", "grants"}
    delegated_network = set(network) == {"egress", "veth", "host_address", "guest_address",
                                         "default_route", "ingress_port", "grant_authority"}
    if not (legacy_network or delegated_network):
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
            not 1024 <= ingress_port <= 65535):
        fail("policy point-to-point network is invalid")
    if delegated_network and network.get("grant_authority") != GRANT_AUTHORITY:
        fail("policy grant authority is invalid")
    if legacy_network and not isinstance(network.get("grants"), list):
        fail("policy point-to-point network is invalid")
    grant_ids = set()
    for grant in network.get("grants", ()):
        if (not isinstance(grant, dict) or
                set(grant) != {"grant_id", "owner", "kind", "destinations", "ports",
                               "expires_at", "revoked"} or
                not GRANT_ID.fullmatch(str(grant.get("grant_id", ""))) or
                not GRANT_OWNER.fullmatch(str(grant.get("owner", ""))) or
                grant.get("owner") != machine_id or
                grant.get("grant_id") in grant_ids or
                grant.get("kind") not in {"public_cidr_tcp", "hostname_https"} or
                not isinstance(grant.get("destinations"), list) or
                not grant.get("destinations") or
                not isinstance(grant.get("ports"), list) or
                not grant.get("ports") or
                not isinstance(grant.get("expires_at"), str) or
                not isinstance(grant.get("revoked"), bool)):
            fail("policy egress grant is invalid")
        grant_ids.add(grant["grant_id"])
        if any(isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
               for port in grant["ports"]):
            fail("policy egress grant is invalid")
        if (len(set(grant["ports"])) != len(grant["ports"]) or
                len(set(grant["destinations"])) != len(grant["destinations"])):
            fail("policy egress grant is invalid")
        expiry = parse_expiry(grant["expires_at"])
        if not grant["revoked"] and expiry <= utc_now():
            fail("policy active egress grant is expired")
        if grant["kind"] == "hostname_https":
            if grant["ports"] != [443] or any(
                    not isinstance(destination, str) or not HOSTNAME.fullmatch(destination)
                    or destination != destination.lower().rstrip(".")
                    for destination in grant["destinations"]):
                fail("policy hostname HTTPS grant is invalid")
        else:
            for destination in grant["destinations"]: public_ipv4_network(destination)
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
              "fds": (128, 1048576), "connections": (16, 20000),
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


def invoking_uid():
    raw = os.environ.get("SUDO_UID")
    if raw is None or not raw.isdigit():
        fail("native helper requires an authenticated sudo caller", 77)
    value = int(raw)
    if value <= 0: fail("native helper caller is invalid", 77)
    return value


def project_source_identity(policy, uid):
    source = Path(policy["read_only_mounts"][0]["source"])
    try:
        resolved = source.resolve(strict=True)
        details = resolved.stat()
        home = Path(pwd.getpwuid(uid).pw_dir).resolve(strict=True)
    except (KeyError, OSError):
        fail("policy project root ownership is unavailable")
    if (source.absolute() != resolved or resolved in {Path("/"), home}
            or details.st_uid != uid or not stat.S_ISDIR(details.st_mode)):
        fail("policy project root is not an owner-controlled scoped directory")
    return {"owner_uid": uid, "source": str(resolved),
            "source_dev": details.st_dev, "source_ino": details.st_ino}


def policy_owner_path(machine_id):
    return POLICY_OWNER_ROOT / f"{machine_id}.json"


def exact_privileged_file(path, payload):
    """Return whether a fixed helper-owned file exists with these exact bytes."""
    if not os.path.lexists(path):
        return False
    try:
        details = path.lstat()
        observed = path.read_bytes()
    except OSError:
        fail("native privileged ownership record is unavailable")
    if (not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)
            or details.st_nlink != 1 or details.st_uid != os.geteuid()
            or details.st_mode & 0o077 or observed != payload):
        fail("native privileged ownership collision")
    return True


def read_policy_owner(machine_id, policy):
    path = policy_owner_path(machine_id)
    try:
        details = path.lstat(); payload = path.read_bytes(); value = json.loads(payload)
    except (OSError, json.JSONDecodeError):
        fail("native policy owner record is unavailable")
    keys = {"machine_id", "policy_digest", "owner_uid", "source",
            "source_dev", "source_ino"}
    if (not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)
            or details.st_uid != os.geteuid() or details.st_mode & 0o077 or set(value) != keys
            or value["machine_id"] != machine_id
            or value["policy_digest"] != policy["digest"]
            or value["owner_uid"] != invoking_uid()):
        fail("native policy caller ownership changed", 77)
    observed = project_source_identity(policy, value["owner_uid"])
    if any(value[key] != observed[key] for key in observed):
        fail("native policy project source identity changed")
    return value


def read_partial_policy_owner(machine_id, digest):
    path = policy_owner_path(machine_id)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        fail("native policy owner record is unavailable")
    keys = {"machine_id", "policy_digest", "owner_uid", "source",
            "source_dev", "source_ino"}
    if (not isinstance(value, dict) or set(value) != keys
            or value.get("machine_id") != machine_id
            or value.get("policy_digest") != digest
            or value.get("owner_uid") != invoking_uid()):
        fail("native policy caller ownership changed", 77)
    expected = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    exact_privileged_file(path, expected)
    try:
        source = Path(value["source"])
        resolved = source.resolve(strict=True)
        details = resolved.stat()
    except (OSError, TypeError):
        fail("native policy project source identity changed")
    if (source.absolute() != resolved or details.st_uid != value["owner_uid"]
            or not stat.S_ISDIR(details.st_mode) or details.st_dev != value["source_dev"]
            or details.st_ino != value["source_ino"]):
        fail("native policy project source identity changed")
    return value


def install_policy_pair(machine_id, value, payload):
    """Install or repair only an exact policy/owner pair.

    Either half may survive a crash. Exact helper-created halves are completed;
    unsafe or drifted halves remain untouched and fail closed.
    """
    owner = {"machine_id": machine_id, "policy_digest": value["digest"],
             **project_source_identity(value, invoking_uid())}
    owner_payload = (json.dumps(owner, sort_keys=True, separators=(",", ":")) + "\n").encode()
    destination = POLICY_ROOT / f"{machine_id}.json"
    owner_destination = policy_owner_path(machine_id)
    policy_exists = exact_privileged_file(destination, payload)
    owner_exists = exact_privileged_file(owner_destination, owner_payload)
    if policy_exists and owner_exists:
        return
    if policy_exists:
        atomic_install_bytes(owner_payload, owner_destination)
        exact_privileged_file(owner_destination, owner_payload)
        return
    if owner_exists:
        atomic_install_bytes(payload, destination)
        exact_privileged_file(destination, payload)
        return
    try:
        atomic_install_bytes(owner_payload, owner_destination)
        exact_privileged_file(owner_destination, owner_payload)
        atomic_install_bytes(payload, destination)
        exact_privileged_file(destination, payload)
    except BaseException:
        # If the policy write did not complete, retire only the exact owner half
        # created by this transaction. A completed exact pair remains retryable.
        if not os.path.lexists(destination):
            try:
                if exact_privileged_file(owner_destination, owner_payload):
                    owner_destination.unlink()
            except (OSError, SystemExit):
                pass
        raise


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
    read_policy_owner(machine_id, value)
    return path, value


def run_fixed(argv, message, *, input_text=None, timeout=120, environment=None):
    result = subprocess.run(tuple(argv), stdin=subprocess.DEVNULL if input_text is None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            input=input_text, text=True, timeout=timeout, check=False,
                            close_fds=True, env=environment or FIXED_ENVIRONMENT)
    if result.returncode != 0:
        # Include the tail of the command's own output. A fixed sentence alone
        # made a missing package, a network refusal, and a policy rejection
        # indistinguishable to the operator.
        detail = ((result.stderr or "").strip() or (result.stdout or "").strip())
        if detail:
            detail = detail if len(detail) <= 600 else "…" + detail[-599:]
            fail(f"{message}: {detail}")
        fail(message)
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


def ensure_root_directory(path, mode):
    if path.exists() or path.is_symlink():
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != 0:
            fail(f"owned directory {path} is foreign")
    else:
        path.mkdir(parents=True, mode=mode)
    os.chown(path, 0, 0); os.chmod(path, mode)


def ensure_user_directory(path, uid, mode=0o700):
    if path.exists() or path.is_symlink():
        details = path.lstat()
        if (stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or
                details.st_uid != uid or details.st_mode & 0o077):
            fail(f"owned directory {path} is foreign")
    else:
        path.mkdir(parents=True, mode=mode)
    os.chown(path, uid, uid); os.chmod(path, mode)


def read_install_plan(path_value, digest):
    digest = digest_value(digest); uid = int(os.environ.get("SUDO_UID", os.getuid()))
    expected = STAGING_ROOT / f"install-{uid}-{digest}.json"
    path = Path(path_value)
    if path.absolute() != expected.absolute(): fail("install plan path is outside its fixed root")
    try: descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError: fail("install plan is unavailable")
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != uid or
                details.st_mode & 0o022 or details.st_size > 4 * 1024 * 1024):
            fail("install plan ownership is invalid")
        payload = b""
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk: break
            payload += chunk
        if len(payload) != details.st_size: fail("install plan changed during validation")
    finally: os.close(descriptor)
    try: value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError): fail("install plan JSON is invalid")
    keys = {"matrix_id", "host_packages", "image_packages", "sources", "service_effects",
            "owned_roots", "privilege_actions", "simulation_digest"}
    if not isinstance(value, dict) or set(value) != keys: fail("install plan schema is invalid")
    if value["matrix_id"] != "ubuntu-24.04-systemd-255": fail("install plan matrix is invalid")
    basis = {key: item for key, item in value.items() if key != "simulation_digest"}
    if value["simulation_digest"] != digest or canonical_digest(basis) != digest:
        fail("install plan digest mismatch")
    for key in ("host_packages", "image_packages", "sources", "service_effects",
                "owned_roots", "privilege_actions"):
        if not isinstance(value[key], list): fail("install plan schema is invalid")
    version_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,255}")
    for scope in ("host", "image"):
        for row in value[f"{scope}_packages"]:
            if (not isinstance(row, dict) or not isinstance(row.get("name"), str) or
                    not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", row["name"]) or
                    not isinstance(row.get("version"), str) or
                    not version_pattern.fullmatch(row["version"]) or
                    row.get("scope") != scope or row.get("action") not in {"install", "keep"}):
                fail("install package row is invalid")
    host_versions = {row["name"]: row["version"] for row in value["host_packages"]}
    if set(HOST_PACKAGE_ROOTS) - set(host_versions): fail("host package roots are incomplete")
    image_names = {row["name"] for row in value["image_packages"]}
    if (not IMAGE_PACKAGE_ROOTS <= image_names or
            len({"nginx", "apache2"} & image_names) != 1):
        fail("image package roots are incomplete")
    for source in value["sources"]:
        suites = source.get("suite", "").split() if isinstance(source, dict) else ()
        if (not isinstance(source, dict) or source.get("uri") not in OFFICIAL_APT_URIS or
                source.get("signed") is not True or not suites or
                any(suite != "noble" and not suite.startswith("noble-") for suite in suites) or
                source.get("kind", "archive") != "archive"):
            fail("install source is not an official signed Noble archive")
    if value["service_effects"] != [{"scope": "image", "policy_rc_d": "deny-service-start"}]:
        fail("install service effects are invalid")
    if value["owned_roots"] != ["/var/lib/sandbox/native", "/etc/sandbox/native"]:
        fail("install owned roots are invalid")
    if value["privilege_actions"] != ["policy-install", "image-create", "image-bootstrap"]:
        fail("install privilege actions are invalid")
    return value, host_versions


def host_packages_apply(path_value, digest):
    _plan, versions = read_install_plan(path_value, digest)
    try: source_text = OFFICIAL_APT_SOURCE.read_text()
    except OSError: fail("official Ubuntu APT source is unavailable")
    configured_uris = [token for line in source_text.splitlines() if line.startswith("URIs:")
                       for token in line.split(":", 1)[1].split()]
    if not configured_uris or any(uri not in OFFICIAL_APT_URIS for uri in configured_uris):
        fail("configured Ubuntu APT source is not official")
    source_options = ("-o", f"Dir::Etc::sourcelist={OFFICIAL_APT_SOURCE}",
                      "-o", "Dir::Etc::sourceparts=-", "-o", "APT::Get::List-Cleanup=0")
    packages = tuple(f"{name}={versions[name]}" for name in HOST_PACKAGE_ROOTS)
    base = ("apt-get", *source_options, "--no-install-recommends")
    run_fixed((*base, "--simulate", "install", *packages),
              "host package re-simulation failed", timeout=120)
    environment = {**FIXED_ENVIRONMENT, "DEBIAN_FRONTEND": "noninteractive"}
    run_fixed((*base, "--yes", "install", *packages), "host package installation failed",
              timeout=900, environment=environment)


def rootfs_path(root, relative, *, allow_final_symlink=False):
    if not isinstance(relative, str) or not relative.startswith("/") or ".." in Path(relative).parts:
        fail("native rootfs path is invalid")
    root = root.resolve(strict=True); destination = root / relative.lstrip("/")
    current = root
    for part in Path(relative).parent.parts:
        if part in {"/", ""}: continue
        current = current / part
        if current.exists() or current.is_symlink():
            details = current.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                fail("native rootfs path escapes the image")
        else:
            current.mkdir(mode=0o755)
    if destination.is_symlink() and not allow_final_symlink:
        fail("native rootfs path is a symlink")
    return destination


def write_rootfs(root, relative, payload, mode=0o644):
    destination = rootfs_path(root, relative)
    atomic_install_bytes(payload.encode() if isinstance(payload, str) else payload, destination)
    os.chmod(destination, mode)


def mask_rootfs_unit(root, unit):
    if not re.fullmatch(r"[a-zA-Z0-9@_.-]+\.(?:service|socket)", unit):
        fail("native unit is invalid")
    destination = rootfs_path(root, f"/etc/systemd/system/{unit}", allow_final_symlink=True)
    if destination.exists() and not destination.is_symlink():
        fail("native service mask collides with a file")
    if destination.is_symlink():
        if os.readlink(destination) != "/dev/null": fail("native service mask is foreign")
        return
    destination.symlink_to("/dev/null")


def remove_rootfs_entry(root, relative):
    destination = rootfs_path(root, relative, allow_final_symlink=True)
    if not destination.exists() and not destination.is_symlink(): return
    details = destination.lstat()
    if stat.S_ISDIR(details.st_mode): fail("native rootfs removal target is a directory")
    destination.unlink()


def _persistent_payload(command, writable_targets):
    argv = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--clearenv",
            "--unshare-user", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--ro-bind", "/", "/"]
    for target in sorted(PERSISTENT_WRITABLE_TARGETS | set(writable_targets)):
        argv.extend(("--bind", target, target))
    argv.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--tmpfs", "/run/credentials", "--dir", "/run/credentials/sandbox",
                 "--tmpfs", GUEST_CREDENTIAL_SOURCE_ROOT,
                 "--tmpfs", "/run/systemd", "--tmpfs", "/run/dbus",
                 "--chdir", "/workspace", "--cap-drop", "ALL", "--uid", "33", "--gid", "33",
                 "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                 "--setenv", "HOME", "/var/www", "--setenv", "USER", "www-data",
                 "--setenv", "LOGNAME", "www-data", "--", *command))
    return "#!/bin/sh\nset -eu\nexec " + shlex.join(argv) + "\n"


def compile_service_files(guest, connections, runtime_seconds, web_server, backend_port,
                          writable_targets=()):
    php_children = max(2, min(32, connections // 4))
    common_php = (
        "[sandbox]\nuser = www-data\ngroup = www-data\n"
        "listen = /run/php/sandbox.sock\nlisten.owner = www-data\nlisten.group = www-data\n"
        f"pm = dynamic\npm.max_children = {php_children}\npm.start_servers = 2\n"
        "pm.min_spare_servers = 1\npm.max_spare_servers = 4\nclear_env = yes\n"
        "security.limit_extensions = .php\ncatch_workers_output = yes\n"
        f"request_terminate_timeout = {runtime_seconds}s\n"
        "php_admin_value[upload_tmp_dir] = /var/lib/sandbox/tmp\n"
        "php_admin_value[session.save_path] = /var/lib/sandbox/sessions\n"
        "php_admin_value[open_basedir] = /var/www/html:/workspace:/var/lib/sandbox:/tmp:/usr/share/php\n"
    )
    if web_server == "nginx":
        web_path = "/etc/nginx/sites-enabled/sandbox.conf"
        nginx = ("user www-data;\nworker_processes 1;\npid /run/nginx.pid;\n"
                 "error_log /var/log/nginx/error.log;\n"
                 f"events {{ worker_connections {connections}; }}\n"
                 "http {\n    include /etc/nginx/mime.types;\n"
                 "    default_type application/octet-stream;\n"
                 "    access_log /var/log/nginx/access.log;\n"
                 "    sendfile on;\n    keepalive_timeout 15;\n"
                 "    include /etc/nginx/sites-enabled/*;\n}\n")
        web = (f"server {{\n    listen {guest}:{backend_port} default_server;\n"
               "    server_name _;\n    root /var/www/html;\n    index index.php;\n"
               "    client_max_body_size 64m;\n"
               "    location / { try_files $uri $uri/ /index.php?$args; }\n"
               "    location ~ \\.php$ { try_files $uri =404; include fastcgi_params; "
               "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; "
               "fastcgi_pass unix:/run/php/sandbox.sock; }\n"
               "    location ~ /\\. { deny all; }\n}\n")
        units = ("mariadb.service", "php8.3-fpm.service", "nginx.service", "cron.service")
    else:
        web_path = "/etc/apache2/sites-enabled/000-sandbox.conf"
        apache_limits = ("KeepAlive Off\n<IfModule mpm_prefork_module>\n"
                         "    StartServers 1\n    MinSpareServers 1\n"
                         f"    MaxSpareServers {min(10, connections)}\n"
                         f"    ServerLimit {connections}\n"
                         f"    MaxRequestWorkers {connections}\n"
                         "    MaxConnectionsPerChild 10000\n</IfModule>\n")
        web = (f"Listen {guest}:{backend_port}\n<VirtualHost {guest}:{backend_port}>\n"
               "    DocumentRoot /var/www/html\n    DirectoryIndex index.php\n"
               "    <Directory /var/www/html>\n        Options FollowSymLinks\n"
               "        AllowOverride All\n        Require all granted\n"
               "    </Directory>\n"
               "    <FilesMatch \\.php$>\n"
               "        SetHandler \"proxy:unix:/run/php/sandbox.sock|fcgi://localhost/\"\n"
               "    </FilesMatch>\n</VirtualHost>\n")
        units = ("mariadb.service", "php8.3-fpm.service", "apache2.service", "cron.service")
    database = ("[mysqld]\nskip-networking=1\nskip-name-resolve=1\nlocal-infile=0\n"
                f"socket=/run/mysqld/mysqld.sock\nmax_connections={connections}\n")
    php_command = ("/usr/sbin/php-fpm8.3", "--nodaemonize", "--force-stderr",
                   "--fpm-config", "/etc/php/8.3/fpm/php-fpm.conf")
    cron_command = ("/usr/bin/timeout", "--signal=TERM", "--kill-after=5s",
                    f"{runtime_seconds}s", "/usr/local/bin/wp", "cron", "event", "run", "--due-now",
                    "--path=/var/www/html")
    files = {web_path: web, "/etc/php/8.3/fpm/pool.d/sandbox.conf": common_php,
             "/etc/mysql/mariadb.conf.d/90-sandbox.cnf": database,
             "/usr/local/libexec/sandbox-php-fpm":
             _persistent_payload(php_command, writable_targets),
             "/usr/local/libexec/sandbox-wordpress-cron":
             _persistent_payload(cron_command, writable_targets),
             "/etc/systemd/system/php8.3-fpm.service.d/sandbox-isolation.conf":
             "[Service]\nType=simple\nNoNewPrivileges=yes\nExecStartPre=/usr/bin/install -d -o www-data -g www-data -m 0770 /run/php\nExecStart=\nExecStart=/usr/local/libexec/sandbox-php-fpm\n",
             "/etc/cron.d/sandbox-wordpress":
             "*/5 * * * * root /usr/local/libexec/sandbox-wordpress-cron >/dev/null 2>&1\n",
             # Declared writable targets under /run must exist from boot, not
             # from the moment their service happens to start. /run is a tmpfs,
             # so without this the isolation probe -- and any command run before
             # the database is up -- dies in bwrap with "Can't find source path
             # /run/mysqld".
             "/etc/tmpfiles.d/sandbox-runtime-dirs.conf":
             "d /run/mysqld 0755 mysql mysql -\nd /run/php 0770 www-data www-data -\n"}
    # NoNewPrivileges lives on the guest's own units, not on the machine: on the
    # machine it blocks the AppArmor transition into the tighter `//guest`
    # profile, and the guest init can never exec. Every untrusted execution path
    # inside the guest is one of these services, so the flag still covers them.
    for unit in units:
        files[f"/etc/systemd/system/{unit}.d/sandbox-no-new-privileges.conf"] = (
            "[Service]\nNoNewPrivileges=yes\n"
            # The guest profile grants sys_admin so PID 1 can mount its API
            # filesystems; no service that runs project code may keep it.
            "CapabilityBoundingSet=~CAP_SYS_ADMIN CAP_SYS_PTRACE CAP_SYS_MODULE "
            "CAP_SYS_RAWIO CAP_SYS_BOOT CAP_MKNOD\n"
            "RestrictNamespaces=yes\nProtectKernelTunables=yes\n"
        )
    if web_server == "nginx":
        files["/etc/nginx/nginx.conf"] = nginx
    else:
        files["/etc/apache2/conf-enabled/sandbox-limits.conf"] = apache_limits
    return files, units


def _namespaced_config_test(mountpoint, *command):
    """Run a web-server config test in a private netns holding every local address.

    The machine's veth address exists only once the machine runs, but nginx and
    Apache bind their listen sockets during a config test. `ip_nonlocal_bind`
    inside a throwaway namespace lets the test bind any address without touching
    the host's networking.
    """
    script = ("ip link set dev lo up; sysctl -q -w net.ipv4.ip_nonlocal_bind=1; "
              "sysctl -q -w net.ipv6.ip_nonlocal_bind=1; "
              + " ".join(("exec", "chroot", str(mountpoint), *command)))
    return ("unshare", "--net", "--", "/bin/sh", "-c", script)


def image_configure(machine_id, policy_digest, web_server, service_digest):
    _policy_path, policy = applied_policy(machine_id, policy_digest)
    if web_server not in {"nginx", "apache"}: fail("native web server is invalid")
    _instance, image, mountpoint = image_paths(machine_id)
    if not os.path.ismount(mountpoint): fail("native image is not mounted for configuration")
    loops = run_fixed(("losetup", "-j", str(image)), "native loop ownership unavailable")
    if not loops.stdout.strip(): fail("native configuration target is not the owned image")
    bootstrap = mountpoint / "etc/sandbox-native/bootstrap.json"
    if not bootstrap.is_file() or bootstrap.is_symlink(): fail("native image is not bootstrapped")
    try: bootstrap_value = json.loads(bootstrap.read_text())
    except (OSError, json.JSONDecodeError): fail("native bootstrap marker is invalid")
    if (bootstrap_value.get("machine_id") != machine_id or
            bootstrap_value.get("policy_digest") != policy_digest or
            bootstrap_value.get("web_server") != web_server):
        fail("native bootstrap marker changed")
    guest = str(ipaddress.ip_interface(policy["network"]["guest_address"]).ip)
    files, units = compile_service_files(guest, policy["resources"]["connections"],
                                         policy["resources"]["runtime_seconds"],
                                         web_server, policy["network"]["ingress_port"],
                                         tuple(item["target"] for item in policy["writable_mounts"]))
    observed_digest = canonical_digest(files)
    if service_digest != observed_digest: fail("native service configuration digest mismatch")
    marker_value = {"machine_id": machine_id, "policy_digest": policy_digest,
                    "service_digest": service_digest, "web_server": web_server,
                    "units": list(units)}
    marker = mountpoint / "etc/sandbox-native/services.json"
    if marker.exists():
        try: observed = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError): fail("native service marker is invalid")
        if observed != marker_value: fail("native service marker changed")
        return
    for relative, content in files.items(): write_rootfs(mountpoint, relative, content)
    os.chmod(rootfs_path(mountpoint, "/etc/cron.d/sandbox-wordpress"), 0o644)
    for executable in ("/usr/local/libexec/sandbox-php-fpm",
                       "/usr/local/libexec/sandbox-wordpress-cron"):
        os.chmod(rootfs_path(mountpoint, executable), 0o750)
    remove_rootfs_entry(mountpoint, "/etc/php/8.3/fpm/pool.d/www.conf")
    rootfs_path(mountpoint, "/var/www/html").mkdir(parents=True, exist_ok=True)
    plugin_link = rootfs_path(
        mountpoint, "/var/www/html/wp-content/plugins/sandbox-project",
        allow_final_symlink=True,
    )
    if plugin_link.exists() or plugin_link.is_symlink():
        if not plugin_link.is_symlink() or os.readlink(plugin_link) != "/workspace":
            fail("native project plugin link changed")
    else:
        plugin_link.parent.mkdir(parents=True, exist_ok=True)
        plugin_link.symlink_to("/workspace")
    for directory in ("/var/www/html", "/var/lib/sandbox/tmp", "/var/lib/sandbox/sessions"):
        run_fixed(("chroot", str(mountpoint), "/usr/bin/chown", "-R", "www-data:www-data",
                   directory), "native service ownership configuration failed")
    run_fixed(("chroot", str(mountpoint), "/usr/sbin/php-fpm8.3", "--test"),
              "native PHP-FPM configuration failed")
    if web_server == "nginx":
        remove_rootfs_entry(mountpoint, "/etc/nginx/sites-enabled/default")
        # `nginx -t` binds its listen sockets, and the machine's address does
        # not exist in the host namespace at image-configure time, so the test
        # failed with "Cannot assign requested address" on every provision.
        # Validate inside a throwaway network namespace that owns the address,
        # which is exactly the context the service will run in.
        run_fixed(_namespaced_config_test(mountpoint, "/usr/sbin/nginx", "-t"),
                  "native nginx configuration failed")
    else:
        remove_rootfs_entry(mountpoint, "/etc/apache2/sites-enabled/000-default.conf")
        run_fixed(("chroot", str(mountpoint), "/usr/sbin/a2dismod", "mpm_event"),
                  "native Apache event MPM disable failed")
        run_fixed(("chroot", str(mountpoint), "/usr/sbin/a2enmod", "mpm_prefork"),
                  "native Apache prefork MPM enable failed")
        run_fixed(("chroot", str(mountpoint), "/usr/sbin/a2enmod",
                   "proxy", "proxy_fcgi", "rewrite", "setenvif"),
                  "native Apache module configuration failed")
        run_fixed(_namespaced_config_test(mountpoint, "/usr/sbin/apache2ctl", "configtest"),
                  "native Apache configuration failed")
    write_rootfs(mountpoint, "/etc/sandbox-native/services.json",
                 json.dumps(marker_value, sort_keys=True) + "\n", 0o600)
    database_script = """#!/bin/sh
set -eu
umask 077
case "${1-}:${2-}:${3-}" in
  *[!a-z0-9_:]*|'') exit 65 ;;
esac
production=$1
tests=$2
database_user=$3
credential=/run/sandbox-native-credentials/db-credential
test -f "$credential" || exit 66
password=$(cat "$credential")
case "$password" in
  ''|*[!A-Za-z0-9_-]*) exit 65 ;;
esac
sql=$(mktemp /run/sandbox-db-bootstrap.XXXXXX)
trap 'rm -f "$sql"' EXIT HUP INT TERM
printf "CREATE DATABASE IF NOT EXISTS %s;\\nCREATE DATABASE IF NOT EXISTS %s;\\nCREATE USER IF NOT EXISTS '%s'@'localhost' IDENTIFIED BY '%s';\\nALTER USER '%s'@'localhost' IDENTIFIED BY '%s';\\nGRANT ALL ON %s.* TO '%s'@'localhost';\\nGRANT ALL ON %s.* TO '%s'@'localhost';\\nFLUSH PRIVILEGES;\\n" \\
  "$production" "$tests" "$database_user" "$password" "$database_user" "$password" \\
  "$production" "$database_user" "$tests" "$database_user" >"$sql"
unset password
/usr/bin/mariadb --protocol=socket --socket=/run/mysqld/mysqld.sock <"$sql" >/dev/null
"""
    write_rootfs(mountpoint, "/usr/local/libexec/sandbox-db-bootstrap",
                 database_script, 0o700)
    wordpress_script = """#!/bin/sh
set -eu
umask 077
credential=/run/credentials/sandbox/db-credential
test -f "$credential" || exit 66
cd /var/www/html
if test ! -f wp-config.php; then
  cat "$credential" | /usr/local/bin/wp config create --path=/var/www/html --dbname="$1" --dbuser="$2" --dbhost=localhost --prompt=dbpass --skip-check --quiet
fi
if ! /usr/local/bin/wp core is-installed --path=/var/www/html --quiet; then
  cat "$credential" | /usr/local/bin/wp core install --path=/var/www/html --url=http://sandbox.invalid --title=Sandbox --admin_user=admin --admin_email=admin@example.invalid --prompt=admin_password --skip-email --quiet
fi
/usr/local/bin/wp config set WP_PROXY_HOST "$3" --path=/var/www/html --quiet
/usr/local/bin/wp config set WP_PROXY_PORT "$4" --path=/var/www/html --raw --quiet
/usr/local/bin/wp config set WP_PROXY_BYPASS_HOSTS 'localhost,127.0.0.1' --path=/var/www/html --quiet
/usr/local/bin/wp plugin activate sandbox-project --path=/var/www/html --quiet
"""
    write_rootfs(mountpoint, "/usr/local/libexec/sandbox-wordpress-bootstrap",
                 wordpress_script, 0o755)


def install_wordpress_artifacts(mountpoint):
    """Download fixed releases, verify bytes, and extract without remote installers."""
    mountpoint = Path(mountpoint).resolve(strict=True)
    marker = rootfs_path(mountpoint, "/etc/sandbox-native/artifacts.json")
    expected = {"wordpress": {"url": WORDPRESS_URL, "sha256": WORDPRESS_SHA256},
                "wp_cli": {"url": WP_CLI_URL, "sha256": WP_CLI_SHA256},
                "phpunit": {"url": PHPUNIT_URL, "sha256": PHPUNIT_SHA256}}
    wp_cli = rootfs_path(mountpoint, "/usr/local/bin/wp")
    phpunit = rootfs_path(mountpoint, PHPUNIT_PATH)
    if marker.exists():
        try: observed = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError): fail("native artifact marker is invalid")
        installed = ((wp_cli, WP_CLI_SHA256, 0o755),
                     (phpunit, PHPUNIT_SHA256, 0o555))
        if observed != expected or any(
                not path.is_file() or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
                or path.stat().st_uid != 0 or path.stat().st_gid != 0
                or stat.S_IMODE(path.stat().st_mode) != mode
                for path, digest, mode in installed):
            fail("native artifact state changed")
        return
    artifact_root = rootfs_path(mountpoint, "/var/lib/sandbox/artifacts")
    artifact_root.mkdir(parents=True, exist_ok=True)
    wordpress_archive = artifact_root / "wordpress.tar.gz"
    wp_cli_download = artifact_root / "wp-cli.phar"
    phpunit_download = artifact_root / "phpunit.phar"
    for url, destination, digest in (
            (WORDPRESS_URL, wordpress_archive, WORDPRESS_SHA256),
            (WP_CLI_URL, wp_cli_download, WP_CLI_SHA256),
            (PHPUNIT_URL, phpunit_download, PHPUNIT_SHA256)):
        relative = "/" + str(destination.relative_to(mountpoint))
        run_fixed(("chroot", str(mountpoint), "/usr/bin/curl", "--fail", "--silent",
                   "--show-error", "--location", "--proto", "=https", "--tlsv1.2",
                   "--output", relative, url), "native artifact download failed", timeout=300)
        digest_observed = hashlib.sha256(destination.read_bytes()).hexdigest()
        if digest_observed != digest:
            fail("native artifact digest mismatch")
    document_root = rootfs_path(mountpoint, "/var/www/html")
    document_root.mkdir(parents=True, exist_ok=True)
    run_fixed(("tar", "--extract", "--gzip", "--file", str(wordpress_archive),
               "--directory", str(document_root), "--strip-components=1",
               "--no-same-owner", "--no-same-permissions"),
              "native WordPress extraction failed", timeout=300)
    wp_cli.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wp_cli_download, wp_cli)
    os.chown(wp_cli, 0, 0); os.chmod(wp_cli, 0o755)
    phpunit.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(phpunit_download, phpunit)
    os.chown(phpunit, 0, 0); os.chmod(phpunit, 0o555)
    wordpress_archive.unlink(); wp_cli_download.unlink(); phpunit_download.unlink()
    write_rootfs(mountpoint, "/etc/sandbox-native/artifacts.json",
                 json.dumps(expected, sort_keys=True) + "\n", 0o600)


def image_bootstrap(machine_id, policy_digest, plan_path, plan_digest, web_server):
    _policy_path, policy = applied_policy(machine_id, policy_digest)
    plan, _host_versions = read_install_plan(plan_path, plan_digest)
    if web_server not in {"nginx", "apache"}: fail("native web server is invalid")
    image_names = {row["name"] for row in plan["image_packages"]}
    expected_web = "nginx" if web_server == "nginx" else "apache2"
    if expected_web not in image_names or ({"nginx", "apache2"} - {expected_web}) & image_names:
        fail("native image package variant is invalid")
    _instance, image, mountpoint = image_paths(machine_id)
    if not os.path.ismount(mountpoint): fail("native image is not mounted for bootstrap")
    loops = run_fixed(("losetup", "-j", str(image)), "native loop ownership unavailable")
    if not loops.stdout.strip(): fail("native bootstrap target is not the owned image")
    marker = mountpoint / "etc/sandbox-native/bootstrap.json"
    expected_marker = {"machine_id": machine_id, "policy_digest": policy_digest,
                       "package_digest": plan_digest, "web_server": web_server,
                       "matrix_id": "ubuntu-24.04-systemd-255"}
    if marker.exists():
        try: observed_marker = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError): fail("native bootstrap marker is invalid")
        if observed_marker != expected_marker: fail("native bootstrap marker changed")
        return
    entries = [item.name for item in mountpoint.iterdir() if item.name != "lost+found"]
    if entries: fail("native bootstrap image is not empty")
    archive = next((source["uri"] for source in plan["sources"]
                    if "security.ubuntu.com" not in source["uri"]), None)
    if archive is None: fail("native bootstrap archive is unavailable")
    keyring = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
    run_fixed(("debootstrap", "--variant=minbase",
               "--components=main,universe,restricted,multiverse",
               f"--keyring={keyring}", "noble", str(mountpoint), archive),
              "native Noble bootstrap failed", timeout=1800)
    components_allowed = {"main", "universe", "restricted", "multiverse"}
    source_stanzas = []
    for source in plan["sources"]:
        components = source.get("components", "main universe").split()
        if not components or any(item not in components_allowed for item in components):
            fail("native image source components are invalid")
        source_stanzas.append(
            "Types: deb\n"
            f"URIs: {source['uri']}\n"
            f"Suites: {source['suite']}\n"
            f"Components: {' '.join(components)}\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        )
    write_rootfs(mountpoint, "/etc/apt/sources.list.d/ubuntu.sources",
                 "\n".join(source_stanzas))
    write_rootfs(mountpoint, "/usr/sbin/policy-rc.d", "#!/bin/sh\nexit 101\n", 0o755)
    write_rootfs(mountpoint, "/etc/resolv.conf", Path("/etc/resolv.conf").read_text())
    write_rootfs(mountpoint, "/etc/hostname", machine_id + "\n")
    write_rootfs(mountpoint, "/etc/hosts",
                 f"127.0.0.1 localhost\n127.0.1.1 {machine_id}\n::1 localhost\n")
    for unit in ("systemd-networkd.service", "systemd-networkd.socket",
                 "systemd-resolved.service", "nginx.service", "apache2.service",
                 "php8.3-fpm.service", "mariadb.service", "mysql.service", "cron.service"):
        mask_rootfs_unit(mountpoint, unit)
    package_specs = tuple(f"{row['name']}={row['version']}" for row in plan["image_packages"])
    apt = ("chroot", str(mountpoint), "/usr/bin/apt-get", "--no-install-recommends")
    environment = {**FIXED_ENVIRONMENT, "DEBIAN_FRONTEND": "noninteractive"}
    run_fixed((*apt, "update"), "native image APT metadata refresh failed",
              timeout=900, environment=environment)
    run_fixed((*apt, "--yes", "--allow-downgrades", "install", *package_specs),
              "native image package installation failed", timeout=1800,
              environment=environment)
    bwrap = rootfs_path(mountpoint, "/usr/bin/bwrap")
    if not bwrap.is_file() or bwrap.is_symlink() or bwrap.stat().st_uid != 0:
        fail("native image bubblewrap binary is invalid")
    # Project payloads run as www-data and cannot invoke the setup transition
    # with attacker-controlled bwrap arguments. Only root-owned gateways can.
    os.chmod(bwrap, 0o750)
    install_wordpress_artifacts(mountpoint)
    write_rootfs(mountpoint, "/etc/resolv.conf", "# DNS disabled; use an explicit egress broker grant.\n")
    write_rootfs(mountpoint, "/etc/machine-id", "")
    for directory, mode in (("/var/lib/sandbox", 0o700), ("/var/log/sandbox", 0o700),
                            ("/var/lib/sandbox/tmp", 0o1770),
                            ("/var/lib/sandbox/sessions", 0o1770),
                            ("/run/php", 0o770)):
        path = rootfs_path(mountpoint, directory); path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, mode)
    write_rootfs(mountpoint, "/etc/sandbox-native/bootstrap.json",
                 json.dumps(expected_marker, sort_keys=True) + "\n", 0o600)


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
    # `nodev` blocks the device nodes debootstrap creates while populating the
    # rootfs (its very first step is a /dev/null probe), so bootstrap failed on
    # every host with "cannot create .../test-dev-null: Permission denied". The
    # guest's device access is governed by the machine's cgroup DeviceAllow
    # policy and nspawn, not by this mount flag; `nosuid` still stands.
    run_fixed(("mount", "-o", "loop,nosuid,noatime", str(image), str(mountpoint)),
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


def expected_network_chains():
    return {name: {"type": "filter", "hook": name, "prio": 0, "policy": "accept"}
            for name in ("input", "output", "forward")}


NETWORK_RECORD_VERSION = 2
# Fields whose meaning does not depend on how a rule is spelled. They bind the
# record to this machine, this policy and this firewall shape, and are what an
# older record is still trusted for after a rendering change.
NETWORK_RECORD_IDENTITY = ("machine_id", "policy_digest", "grant_digest",
                           "marker", "network_digest", "chains")


def network_record_matches(record, desired):
    """Whether an ownership record still describes the desired network.

    Rule digests are a rendering of the rules, and a rendering can change: the
    ct-state canonicalisation changed exactly once and would otherwise have
    stranded every network written before it, with cleanup refusing to remove
    resources it still owned. A record from an older version is compared on the
    fields that do not depend on rendering, and the host is then compared
    against the rules the policy asks for rather than the ones the record
    happens to spell.
    """
    if not isinstance(record, dict):
        return False
    if record.get("version") == desired.get("version"):
        return record == desired
    return all(record.get(key) == desired.get(key) for key in NETWORK_RECORD_IDENTITY)


def desired_network_state(machine_id, policy_digest, network, *, broker=False,
                          grant_digest=ABSENT_GRANT_DIGEST):
    basis = {"version": NETWORK_RECORD_VERSION,
             "machine_id": machine_id, "policy_digest": policy_digest,
             "grant_digest": grant_digest,
             "marker": network_marker(machine_id, policy_digest),
             "network_digest": canonical_digest(network),
             "chains": expected_network_chains(),
             "rules": [list(item) for item in expected_network_rules(
                 network, broker=broker)]}
    return {**basis, "digest": canonical_digest(basis)}


def network_state_record(machine_id):
    path = NETWORK_STATE_ROOT / f"{machine_id}.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        fail("native network ownership record is unavailable")
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or
                details.st_mode & 0o077 or not 1 <= details.st_size <= 65536):
            fail("native network ownership record is invalid")
        payload = b""
        while len(payload) <= 65536:
            chunk = os.read(descriptor, 65536)
            if not chunk: break
            payload += chunk
        if len(payload) != details.st_size:
            fail("native network ownership record changed")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native network ownership record is invalid")
    keys = {"version", "machine_id", "policy_digest", "grant_digest", "marker", "network_digest",
            "chains", "rules", "digest"}
    if (not isinstance(value, dict) or set(value) != keys or value.get("version") != 1 or
            value.get("machine_id") != machine_id or
            not isinstance(value.get("policy_digest"), str) or
            not re.fullmatch(r"[a-f0-9]{64}", str(value.get("grant_digest", ""))) or
            not isinstance(value.get("marker"), str) or
            not isinstance(value.get("network_digest"), str) or
            not isinstance(value.get("chains"), dict) or
            not isinstance(value.get("rules"), list)):
        fail("native network ownership record is invalid")
    basis = {key: item for key, item in value.items() if key != "digest"}
    if value["digest"] != canonical_digest(basis):
        fail("native network ownership record digest changed")
    return value


def write_network_state(record):
    ensure_root_directory(NETWORK_STATE_ROOT, 0o755)
    atomic_install_bytes((json.dumps(record, sort_keys=True,
                                     separators=(",", ":")) + "\n").encode(),
                         NETWORK_STATE_ROOT / f"{record['machine_id']}.json")


def record_rule_tuple(record):
    try:
        return tuple((str(item[0]), str(item[1])) for item in record["rules"]
                     if isinstance(item, list) and len(item) == 2)
    except (KeyError, TypeError):
        return ()


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


def observed_nft_state(table):
    result = run_optional(("nft", "-j", "list", "table", "inet", table))
    if result.returncode != 0: return None
    try: document = json.loads(result.stdout or "{}")
    except json.JSONDecodeError: fail("native nft observation is invalid")
    if not isinstance(document, dict) or not isinstance(document.get("nftables"), list):
        fail("native nft observation is invalid")
    table_row = None; chains = {}; rules = []; counters = {}
    for item in document["nftables"]:
        if not isinstance(item, dict): fail("native nft observation is invalid")
        row = item.get("table")
        if isinstance(row, dict) and row.get("family") == "inet" and row.get("name") == table:
            table_row = row
        chain = item.get("chain")
        if isinstance(chain, dict) and chain.get("family") == "inet" and chain.get("table") == table:
            name = chain.get("name")
            if not isinstance(name, str): fail("native nft observation is invalid")
            if name in chains: fail("native nft observation is invalid")
            chains[name] = {"type": chain.get("type"), "hook": chain.get("hook"),
                            "prio": chain.get("prio"), "policy": chain.get("policy")}
        rule = item.get("rule")
        if isinstance(rule, dict) and rule.get("family") == "inet" and rule.get("table") == table:
            comment = rule.get("comment")
            if (not isinstance(comment, str) or comment in counters or
                    comment not in (*REQUIRED_NETWORK_RULES, *BROKER_NETWORK_RULES)):
                fail("native nft rule identity is invalid")
            expressions = rule.get("expr")
            if not isinstance(expressions, list): fail("native nft observation is invalid")
            counter = None
            for expression in expressions:
                if isinstance(expression, dict) and isinstance(expression.get("counter"), dict):
                    counter = expression["counter"]; break
            if (not isinstance(counter, dict) or
                    isinstance(counter.get("packets"), bool) or
                    not isinstance(counter.get("packets"), int) or
                    isinstance(counter.get("bytes"), bool) or
                    not isinstance(counter.get("bytes"), int)):
                fail("native nft counter observation is invalid")
            counters[comment] = {"packets": counter["packets"], "bytes": counter["bytes"]}
            rules.append((comment, canonical_digest({
                "chain": rule.get("chain"),
                "expr": normalize_nft_value(expressions),
            })))
    if table_row is None: fail("native nft observation is invalid")
    # A tuple, because every consumer compares this against the tuple that
    # `expected_network_rules` and `record_rule_tuple` return, and a list never
    # equals a tuple however identical the contents. That comparison could not
    # succeed on any real host: the rules matched exactly and cleanup still
    # refused the network as changed ownership.
    return {"table": table_row, "chains": chains, "rules": tuple(rules),
            "counters": counters}


def _sorted_set_items(items):
    return sorted((normalize_nft_value(item) for item in items),
                  key=lambda item: json.dumps(item, sort_keys=True))


def _canonical_match_right(value):
    """Render a match's right operand the same way whoever produced it did.

    We build an anonymous set as `{"set": [...]}`; nft echoes the same set back
    as a bare list. Both denote one membership test, so they must digest
    identically -- otherwise an untouched host reports drift on exactly the rules
    that use a set. On the proof host that was `guest_host_established` and
    `ingress`, the only two rules matching on a ct-state set: their rules were
    byte-for-byte what the policy asked for, and cleanup refused them as changed
    ownership anyway. Order is not meaningful in a set, so both forms sort.
    """
    if isinstance(value, dict) and set(value) == {"set"} and isinstance(value["set"], list):
        return {"set": _sorted_set_items(value["set"])}
    if isinstance(value, list):
        return {"set": _sorted_set_items(value)}
    return normalize_nft_value(value)


def normalize_nft_value(value):
    """Canonicalize nft JSON while retaining all authorization semantics."""
    if isinstance(value, list):
        return [normalize_nft_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"counter"} and isinstance(value["counter"], dict):
        return {"counter": {}}
    if set(value) == {"match"} and isinstance(value["match"], dict):
        match = {key: normalize_nft_value(item) for key, item in value["match"].items()}
        if "right" in value["match"]:
            match["right"] = _canonical_match_right(value["match"]["right"])
            # nft renders equality against an anonymous set as `in`, and the two
            # are the same membership test. Only `==` is rewritten; `!=` keeps
            # its own meaning and must never be folded into this.
            if isinstance(match["right"], dict) and set(match["right"]) == {"set"} \
                    and match.get("op") == "==":
                match["op"] = "in"
        return {"match": match}
    result = {key: normalize_nft_value(item) for key, item in value.items()}
    if set(result) == {"set"} and isinstance(result["set"], list):
        result["set"] = sorted(result["set"], key=lambda item: json.dumps(item, sort_keys=True))
    return result


def expected_network_rules(network, *, broker=False):
    guest = str(ipaddress.ip_interface(network["guest_address"]).ip)
    veth = network["veth"]

    def match(left, right):
        return {"match": {"op": "==", "left": left, "right": right}}

    meta_iif = {"meta": {"key": "iifname"}}
    meta_oif = {"meta": {"key": "oifname"}}
    saddr = {"payload": {"protocol": "ip", "field": "saddr"}}
    daddr = {"payload": {"protocol": "ip", "field": "daddr"}}
    dport = {"payload": {"protocol": "tcp", "field": "dport"}}
    ct_state = {"ct": {"key": "state"}}
    counter = {"counter": {}}
    accept = {"accept": None}
    drop = {"drop": None}
    rows = {
        "guest_host_established": ("input", [
            match(meta_iif, veth), match(saddr, guest),
            match(ct_state, {"set": ["established", "related"]}), counter, accept,
        ]),
        "guest_host_drop": ("input", [match(meta_iif, veth), counter, drop]),
        "ingress": ("output", [
            match(meta_oif, veth), match(daddr, guest),
            match(dport, network["ingress_port"]),
            match(ct_state, {"set": ["new", "established"]}), counter, accept,
        ]),
        "host_guest_drop": ("output", [match(meta_oif, veth), counter, drop]),
        "guest_forward_drop": ("forward", [match(meta_iif, veth), counter, drop]),
        "guest_forward_reply_drop": ("forward", [match(meta_oif, veth), counter, drop]),
    }
    if broker:
        host = str(ipaddress.ip_interface(network["host_address"]).ip)
        request = ("input", [
            match(meta_iif, veth), match(saddr, guest), match(daddr, host),
            match(dport, BROKER_PORT), match(ct_state, "new"), counter, accept,
        ])
        source_port = {"payload": {"protocol": "tcp", "field": "sport"}}
        reply = ("output", [
            match(meta_oif, veth), match(saddr, host), match(daddr, guest),
            match(source_port, BROKER_PORT),
            match(ct_state, {"set": ["established", "related"]}), counter, accept,
        ])
        rows = {
            "guest_host_established": rows["guest_host_established"],
            "egress_broker_request": request,
            "guest_host_drop": rows["guest_host_drop"],
            "ingress": rows["ingress"],
            "egress_broker_reply": reply,
            "host_guest_drop": rows["host_guest_drop"],
            "guest_forward_drop": rows["guest_forward_drop"],
            "guest_forward_reply_drop": rows["guest_forward_reply_drop"],
        }
    return tuple((name, canonical_digest({
                     "chain": chain, "expr": normalize_nft_value(expressions),
                 }))
                 for name, (chain, expressions) in rows.items())


def observed_guest_network(machine_id):
    leader = machine_leader(machine_id)
    namespace = ("nsenter", "--target", str(leader), "--net", "--")
    address = run_optional((*namespace, "ip", "-j", "address", "show", "dev", "host0"))
    routes = run_optional((*namespace, "ip", "-j", "route", "show", "table", "main"))
    if address.returncode != 0 or routes.returncode != 0:
        fail("native guest network observation failed")
    try:
        address_rows = json.loads(address.stdout or "[]")
        route_rows = json.loads(routes.stdout or "[]")
    except json.JSONDecodeError: fail("native guest network observation is invalid")
    if (not isinstance(address_rows, list) or len(address_rows) != 1 or
            not isinstance(address_rows[0], dict) or not isinstance(route_rows, list)):
        fail("native guest network observation is invalid")
    global_ipv4 = [item for item in address_rows[0].get("addr_info", ())
                   if isinstance(item, dict) and item.get("family") == "inet" and
                   item.get("scope") == "global"]
    if len(global_ipv4) != 1: fail("native guest IPv4 observation is invalid")
    item = global_ipv4[0]
    try: guest_address = str(ipaddress.ip_interface(
        f"{item['local']}/{int(item['prefixlen'])}"))
    except (KeyError, TypeError, ValueError): fail("native guest IPv4 observation is invalid")
    destinations = []
    for route in route_rows:
        if not isinstance(route, dict): fail("native guest route observation is invalid")
        destination = route.get("dst", "default")
        if not isinstance(destination, str): fail("native guest route observation is invalid")
        destinations.append(destination)
    return {"guest_address": guest_address, "default_route": "default" in destinations,
            "routes": sorted(destinations)}


def active_egress_grants(network):
    return tuple(grant for grant in network.get("grants", ()) if not grant["revoked"])


def validate_grant_list(grants, machine_id, *, allow_expired=False):
    if not isinstance(grants, list):
        fail("native grant document grants are invalid")
    ids = set()
    for grant in grants:
        if (not isinstance(grant, dict) or
                set(grant) != {"grant_id", "owner", "kind", "destinations", "ports",
                               "expires_at", "revoked"} or
                not GRANT_ID.fullmatch(str(grant.get("grant_id", ""))) or
                grant.get("owner") != machine_id or grant.get("grant_id") in ids or
                grant.get("kind") not in {"public_cidr_tcp", "hostname_https"} or
                not isinstance(grant.get("destinations"), list) or
                not grant.get("destinations") or
                not isinstance(grant.get("ports"), list) or not grant.get("ports") or
                not isinstance(grant.get("expires_at"), str) or
                not isinstance(grant.get("revoked"), bool)):
            fail("native grant document grant is invalid")
        ids.add(grant["grant_id"])
        if (len(set(grant["ports"])) != len(grant["ports"]) or
                len(set(grant["destinations"])) != len(grant["destinations"]) or
                any(isinstance(port, bool) or not isinstance(port, int) or
                    not 1 <= port <= 65535 for port in grant["ports"])):
            fail("native grant document grant is invalid")
        expiry = parse_expiry(grant["expires_at"])
        if not allow_expired and not grant["revoked"] and expiry <= utc_now():
            fail("native active grant is expired")
        if grant["kind"] == "hostname_https":
            if (grant["ports"] != [443] or any(
                    not isinstance(destination, str) or not HOSTNAME.fullmatch(destination) or
                    destination != destination.lower().rstrip(".")
                    for destination in grant["destinations"])):
                fail("native hostname HTTPS grant is invalid")
        else:
            for destination in grant["destinations"]:
                public_ipv4_network(destination)
    return grants


def grant_document(machine_id, base_policy_digest, grants):
    basis = {"version": 1, "machine_id": machine_id,
             "base_policy_digest": base_policy_digest,
             "grant_authority": GRANT_AUTHORITY, "grants": grants}
    return {**basis, "grant_digest": canonical_digest(basis)}


def _validate_grant_document(value, machine_id, base_policy_digest, desired_digest, *,
                             allow_expired=False):
    keys = {"version", "machine_id", "base_policy_digest", "grant_authority",
            "grants", "grant_digest"}
    if (not isinstance(value, dict) or set(value) != keys or value.get("version") != 1 or
            value.get("machine_id") != machine_id or
            value.get("base_policy_digest") != base_policy_digest or
            value.get("grant_authority") != GRANT_AUTHORITY or
            value.get("grant_digest") != desired_digest):
        fail("native grant document schema is invalid")
    validate_grant_list(value["grants"], machine_id, allow_expired=allow_expired)
    basis = {key: item for key, item in value.items() if key != "grant_digest"}
    if canonical_digest(basis) != desired_digest:
        fail("native grant document digest mismatch")
    return value


def _read_grant_file(path, machine_id, base_policy_digest, grant_digest, owner_uid, *,
                     allow_expired=False):
    try: descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError: fail("native grant document is unavailable")
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != owner_uid or
                details.st_mode & 0o077 or not 1 <= details.st_size <= 1024 * 1024):
            fail("native grant document ownership is invalid")
        payload = b""
        while len(payload) <= 1024 * 1024:
            chunk = os.read(descriptor, 65536)
            if not chunk: break
            payload += chunk
        if len(payload) != details.st_size:
            fail("native grant document changed")
    finally: os.close(descriptor)
    try: value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native grant document JSON is invalid")
    if isinstance(value, dict):
        base_policy_digest = base_policy_digest or value.get("base_policy_digest")
        grant_digest = grant_digest or value.get("grant_digest")
    if (not re.fullmatch(r"[a-f0-9]{64}", str(base_policy_digest or "")) or
            not re.fullmatch(r"[a-f0-9]{64}", str(grant_digest or ""))):
        fail("native grant document digest is invalid")
    return _validate_grant_document(
        value, machine_id, base_policy_digest, grant_digest,
        allow_expired=allow_expired), payload


def installed_grant_record(machine_id, base_policy_digest=None):
    path = GRANT_ROOT / f"{machine_id}.json"
    try: path.lstat()
    except FileNotFoundError: return None
    except OSError: fail("native installed grant document is unavailable")
    value, _payload = _read_grant_file(path, machine_id, base_policy_digest, None, 0,
                                      allow_expired=True)
    return value


@contextmanager
def grant_machine_lock(machine_id):
    ensure_root_directory(GRANT_LOCK_ROOT, 0o755)
    path = GRANT_LOCK_ROOT / f"{machine_id}.lock"
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                             0o600)
    except OSError:
        fail("native grant lock is unavailable")
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or
                details.st_mode & 0o077):
            fail("native grant lock ownership is invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def observed_forbidden_networks():
    result = {str(value) for value in FORBIDDEN_IPV4}
    addresses = run_optional(("ip", "-j", "address", "show"))
    if addresses.returncode != 0:
        fail("native host address observation failed")
    try:
        rows = json.loads(addresses.stdout or "[]")
    except json.JSONDecodeError:
        fail("native host address observation is invalid")
    if not isinstance(rows, list):
        fail("native host address observation is invalid")
    for row in rows:
        if not isinstance(row, dict):
            fail("native host address observation is invalid")
        for item in row.get("addr_info", ()):
            if not isinstance(item, dict):
                fail("native host address observation is invalid")
            if item.get("family") == "inet":
                try: result.add(str(ipaddress.ip_network(f"{item['local']}/32")))
                except (KeyError, ValueError): fail("native host address observation is invalid")
    try:
        resolvers = Path("/etc/resolv.conf").read_text().splitlines()
    except OSError:
        resolvers = ()
    for line in resolvers:
        fields = line.split()
        if len(fields) == 2 and fields[0] == "nameserver":
            try:
                address = ipaddress.ip_address(fields[1])
            except ValueError:
                continue
            if address.version == 4:
                result.add(f"{address}/32")
    return tuple(sorted(result))


def resolve_public_hostname(hostname, forbidden):
    try:
        rows = socket.getaddrinfo(hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        fail("native egress hostname resolution failed")
    blocked = tuple(ipaddress.ip_network(item, strict=False) for item in forbidden)
    values = []
    for row in rows:
        try: address = ipaddress.ip_address(row[4][0])
        except (IndexError, TypeError, ValueError): fail("native egress resolution is invalid")
        public_ipv4_network(f"{address}/32")
        if any(address in network for network in blocked):
            fail("native egress hostname resolves to a host or control address")
        values.append(str(address))
    result = sorted(set(values))
    if not result:
        fail("native egress hostname resolution is empty")
    return result


def build_egress_config(machine_id, digest, network, connection_limit, *,
                        grants=None, grant_digest=None):
    if (isinstance(connection_limit, bool) or not isinstance(connection_limit, int) or
            not 16 <= connection_limit <= 20000):
        fail("native egress connection ceiling is invalid")
    forbidden = observed_forbidden_networks()
    configured = []
    source_grants = active_egress_grants(network) if grants is None else tuple(
        grant for grant in grants if not grant["revoked"])
    for grant in source_grants:
        pins = {}
        if grant["kind"] == "hostname_https":
            for hostname in grant["destinations"]:
                pins[hostname] = resolve_public_hostname(hostname, forbidden)
        else:
            blocked = tuple(ipaddress.ip_network(item, strict=False) for item in forbidden)
            for destination in grant["destinations"]:
                network_value = public_ipv4_network(destination)
                if any(network_value.overlaps(item) for item in blocked):
                    fail("native fixed egress grant overlaps a host or control address")
        configured.append({"grant_id": grant["grant_id"], "kind": grant["kind"],
                       "destinations": list(grant["destinations"]),
                       "ports": list(grant["ports"]),
                       "expires_at": grant["expires_at"], "pins": pins})
    if not configured:
        fail("native egress activation requires an active grant")
    if grant_digest is None:
        grant_digest = canonical_digest({"grants": list(source_grants)})
    digest_value(grant_digest)
    basis = {"version": 1, "machine_id": machine_id, "policy_digest": digest,
             "grant_digest": grant_digest,
             "host_address": str(ipaddress.ip_interface(network["host_address"]).ip),
             "guest_address": str(ipaddress.ip_interface(network["guest_address"]).ip),
             "interface": network["veth"], "port": BROKER_PORT,
             "connection_limit": connection_limit,
             "forbidden": list(forbidden), "grants": configured}
    return {**basis, "config_digest": canonical_digest(basis)}


def egress_config_record(machine_id):
    path = EGRESS_ROOT / f"{machine_id}.json"
    try: descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError: return None
    except OSError: fail("native egress configuration is unavailable")
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or
                details.st_mode & 0o077 or not 1 <= details.st_size <= 1024 * 1024):
            fail("native egress configuration is invalid")
        payload = b""
        while len(payload) <= 1024 * 1024:
            chunk = os.read(descriptor, 65536)
            if not chunk: break
            payload += chunk
        if len(payload) != details.st_size: fail("native egress configuration changed")
    finally: os.close(descriptor)
    try: value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError): fail("native egress configuration is invalid")
    if (not isinstance(value, dict) or value.get("machine_id") != machine_id or
            not re.fullmatch(r"[a-f0-9]{64}", str(value.get("policy_digest", ""))) or
            not re.fullmatch(r"[a-f0-9]{64}", str(value.get("grant_digest", ""))) or
            not re.fullmatch(r"[a-f0-9]{64}", str(value.get("config_digest", "")))):
        fail("native egress configuration is invalid")
    basis = {key: item for key, item in value.items() if key != "config_digest"}
    if value["config_digest"] != canonical_digest(basis):
        fail("native egress configuration digest changed")
    return value


def write_egress_config(value):
    ensure_root_directory(EGRESS_ROOT, 0o755)
    atomic_install_bytes((json.dumps(value, sort_keys=True,
                                     separators=(",", ":")) + "\n").encode(),
                         EGRESS_ROOT / f"{value['machine_id']}.json")


def egress_names(machine_id):
    suffix = machine_id[3:]
    return (f"sandbox-native-egress-{suffix}.service",
            f"sandbox-native-egress-{suffix}",
            Path("/run") / f"sandbox-native-egress-{suffix}" / "control.sock")


def egress_description(config):
    return (f"Sandbox native egress {config['machine_id']} policy "
            f"{config['policy_digest']} grants {config['grant_digest']} "
            f"config {config['config_digest']}")


def query_egress_status(config):
    _unit, _runtime, control_path = egress_names(config["machine_id"])
    try:
        details = control_path.lstat()
        parent = control_path.parent.lstat()
    except OSError:
        return None
    if (not stat.S_ISSOCK(details.st_mode) or details.st_mode & 0o077 or
            not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077):
        return None
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    payload = b""
    try:
        client.settimeout(2)
        client.connect(str(control_path)); client.sendall(b"status\n")
        while len(payload) <= 65536:
            chunk = client.recv(65536)
            if not chunk: break
            payload += chunk
    except OSError:
        return None
    finally: client.close()
    if len(payload) > 65536: return None
    try: value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError): return None
    expected_grants = {grant["grant_id"] for grant in config["grants"]}
    if (not isinstance(value, dict) or value.get("ok") is not True or
            value.get("machine_id") != config["machine_id"] or
            value.get("policy_digest") != config["policy_digest"] or
            value.get("grant_digest") != config["grant_digest"] or
            value.get("config_digest") != config["config_digest"] or
            value.get("connection_limit") != config["connection_limit"] or
            value.get("expired") != [] or
            not isinstance(value.get("grants"), dict) or
            set(value["grants"]) != expected_grants or
            value.get("listener") != {"address": config["host_address"],
                                      "port": config["port"],
                                      "interface": config["interface"]}):
        return None
    for counter in value["grants"].values():
        if (not isinstance(counter, dict) or
                set(counter) != {"accepted", "rejected", "bytes", "active"} or
                any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                    for item in counter.values())):
            return None
    return value


def stop_owned_egress(machine_id):
    unit, _runtime, _control = egress_names(machine_id)
    prior = egress_config_record(machine_id)
    description = unit_description(unit)
    if description:
        if prior is None or description != egress_description(prior):
            fail("native egress unit ownership is unproven")
        run_fixed(("systemctl", "stop", unit), "native egress stop before reconcile failed")
    return prior


def start_egress_config(config):
    machine_id = config["machine_id"]
    unit, runtime, control_path = egress_names(machine_id)
    write_egress_config(config)
    allowed = {f"{config['host_address']}/32", f"{config['guest_address']}/32"}
    for grant in config["grants"]:
        if grant["kind"] == "public_cidr_tcp": allowed.update(grant["destinations"])
        else:
            for values in grant["pins"].values():
                allowed.update(f"{value}/32" for value in values)
    command = ["systemd-run", "--no-block", "--collect", f"--unit={unit}",
               "--service-type=exec", f"--description={egress_description(config)}",
               "--property=DynamicUser=yes", f"--property=RuntimeDirectory={runtime}",
               "--property=RuntimeDirectoryMode=0700", "--property=UMask=0077",
               "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
               "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
               "--property=PrivateDevices=yes", "--property=ProtectKernelTunables=yes",
               "--property=ProtectKernelModules=yes", "--property=ProtectControlGroups=yes",
               "--property=RestrictSUIDSGID=yes", "--property=LockPersonality=yes",
               "--property=MemoryDenyWriteExecute=yes", "--property=RestrictNamespaces=yes",
               "--property=ProtectClock=yes", "--property=ProtectHostname=yes",
               "--property=ProtectProc=invisible", "--property=ProcSubset=pid",
               "--property=RestrictRealtime=yes", "--property=RemoveIPC=yes",
               "--property=RestrictAddressFamilies=AF_UNIX AF_INET",
               "--property=CapabilityBoundingSet=CAP_NET_RAW",
               "--property=AmbientCapabilities=CAP_NET_RAW",
               "--property=SocketBindDeny=any",
               f"--property=SocketBindAllow=ipv4:tcp:{BROKER_PORT}",
               "--property=IPAddressDeny=any", "--property=MemoryMax=134217728",
               f"--property=TasksMax={config['connection_limit'] + 8}",
               f"--property=LoadCredential=egress.json:{EGRESS_ROOT / (machine_id + '.json')}"]
    command.extend(f"--property=IPAddressAllow={value}" for value in sorted(allowed))
    command.extend((str(BROKER_INSTALL_PATH), str(control_path)))
    run_fixed(tuple(command), "native egress broker start failed")
    for _attempt in range(30):
        if (unit_description(unit) == egress_description(config) and
                query_egress_status(config) is not None):
            return
        time.sleep(0.1)
    if unit_description(unit) == egress_description(config):
        run_optional(("systemctl", "stop", unit))
    fail("native egress broker did not expose exact status")


def egress_apply(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    config = build_egress_config(machine_id, digest, policy["network"],
                                 policy["resources"]["connections"])
    prior = egress_config_record(machine_id)
    unit, _runtime, _control = egress_names(machine_id)
    if (prior == config and unit_description(unit) == egress_description(config) and
            query_egress_status(config) is not None):
        return
    stop_owned_egress(machine_id)
    start_egress_config(config)


def egress_status(machine_id, digest):
    digest_value(digest)
    config = egress_config_record(machine_id)
    unit, _runtime, _control = egress_names(machine_id)
    observed = query_egress_status(config) if config is not None else None
    ok = bool(config and config["policy_digest"] == digest and observed and
              unit_description(unit) == egress_description(config))
    print(json.dumps({"ok": ok, "machine_id": machine_id, "policy_digest": digest,
                      "grant_digest": config.get("grant_digest") if config else None,
                      "config_digest": config.get("config_digest") if config else None,
                      "grants": observed.get("grants", {}) if observed else {}}, sort_keys=True))
    if not ok: raise SystemExit(69)


def egress_remove(machine_id, digest):
    digest_value(digest)
    config = egress_config_record(machine_id)
    unit, _runtime, _control = egress_names(machine_id)
    description = unit_description(unit)
    if description:
        if config is None or description != egress_description(config):
            fail("native egress unit ownership changed")
        run_fixed(("systemctl", "stop", unit), "native egress broker stop failed")
    if config is not None:
        try: (EGRESS_ROOT / f"{machine_id}.json").unlink()
        except OSError: fail("native egress configuration removal failed")


def network_nft_statements(table, marker, network, *, broker=False, replace=False):
    veth = network["veth"]
    host = str(ipaddress.ip_interface(network["host_address"]).ip)
    guest = str(ipaddress.ip_interface(network["guest_address"]).ip)
    statements = []
    if replace: statements.append(f"delete table inet {table}")
    statements.extend((
        f'add table inet {table} {{ comment "{marker}"; }}',
        f"add chain inet {table} input {{ type filter hook input priority filter; policy accept; }}",
        f"add chain inet {table} output {{ type filter hook output priority filter; policy accept; }}",
        f"add chain inet {table} forward {{ type filter hook forward priority filter; policy accept; }}",
        f'add rule inet {table} input iifname "{veth}" ip saddr {guest} '
        f'ct state established,related counter accept comment "guest_host_established"',
    ))
    if broker:
        statements.append(
            f'add rule inet {table} input iifname "{veth}" ip saddr {guest} '
            f'ip daddr {host} tcp dport {BROKER_PORT} ct state new counter accept '
            f'comment "egress_broker_request"')
    statements.extend((
        f'add rule inet {table} input iifname "{veth}" counter drop comment "guest_host_drop"',
        f'add rule inet {table} output oifname "{veth}" ip daddr {guest} '
        f'tcp dport {network["ingress_port"]} ct state new,established '
        f'counter accept comment "ingress"',
    ))
    if broker:
        statements.append(
            f'add rule inet {table} output oifname "{veth}" ip saddr {host} '
            f'ip daddr {guest} tcp sport {BROKER_PORT} ct state established,related '
            f'counter accept comment "egress_broker_reply"')
    statements.extend((
        f'add rule inet {table} output oifname "{veth}" counter drop comment "host_guest_drop"',
        f'add rule inet {table} forward iifname "{veth}" '
        f'counter drop comment "guest_forward_drop"',
        f'add rule inet {table} forward oifname "{veth}" '
        f'counter drop comment "guest_forward_reply_drop"',
    ))
    return statements


def nft_state_matches_record(observed, record):
    names = {item[0] for item in record_rule_tuple(record)}
    return bool(observed and observed["chains"] == record["chains"] and
                observed["rules"] == record_rule_tuple(record) and
                set(observed["counters"]) == names)


def network_apply(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]
    table, alias_prefix = network_names(machine_id)
    marker = network_marker(machine_id, digest)
    veth = network["veth"]
    link = observed_link(veth)
    if link is None: fail("native veth is unavailable")
    observed_alias = link.get("ifalias", "")
    if observed_alias and observed_alias != alias_prefix:
        fail("native veth ownership is foreign")
    existing_table = observed_nft_table(table)
    prior_record = network_state_record(machine_id)
    grant_record = installed_grant_record(machine_id, digest) \
        if network.get("grant_authority") == GRANT_AUTHORITY else None
    grants = grant_record["grants"] if grant_record else []
    grant_digest = grant_record["grant_digest"] if grant_record else ABSENT_GRANT_DIGEST
    broker = any(not grant["revoked"] for grant in grants)
    if broker:
        config = egress_config_record(machine_id)
        unit, _runtime, _control = egress_names(machine_id)
        if (config is None or config.get("grant_digest") != grant_digest or
                unit_description(unit) != egress_description(config) or
                query_egress_status(config) is None):
            fail("native egress broker proof is unavailable")
    desired_record = desired_network_state(
        machine_id, digest, network, broker=broker, grant_digest=grant_digest)
    unrecorded_exact = False
    if existing_table is not None:
        if prior_record is None:
            if existing_table.get("comment") != desired_record["marker"]:
                fail("native nft table ownership is unproven")
            partial_state = observed_nft_state(table)
            if not nft_state_matches_record(partial_state, desired_record):
                fail("native nft table ownership is unproven")
            unrecorded_exact = True
        elif existing_table.get("comment") != prior_record["marker"]:
            fail("native nft table ownership is unproven")
        existing_state = observed_nft_state(table)
        if prior_record is not None and not nft_state_matches_record(existing_state, prior_record):
            fail("native nft owned state drifted")
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
    statements = network_nft_statements(
        table, marker, network, broker=broker, replace=existing_table is not None and
        prior_record != desired_record,
    )
    script = "\n".join(statements) + "\n"
    try:
        if existing_table is None or (not unrecorded_exact and prior_record != desired_record):
            run_fixed(("nft", "-f", "-"), "native nft policy failed", input_text=script)
    except BaseException:
        run_optional(("ip", "address", "del", network["host_address"], "dev", veth))
        raise
    try:
        write_network_state(desired_record)
    except BaseException:
        # A state write can fail after nft accepted the exact policy. Recover a
        # newly created table by exact comparison, or restore the prior exact
        # policy for updates. Unknown/drifted tables are never touched.
        current_table = observed_nft_table(table)
        current_state = observed_nft_state(table) if current_table is not None else None
        current_record = network_state_record(machine_id)
        current_exact = bool(
            current_table is not None
            and current_table.get("comment") == desired_record["marker"]
            and nft_state_matches_record(current_state, desired_record)
        )
        if current_record == desired_record and current_exact:
            raise
        if current_exact and prior_record is None:
            removed = run_optional(("nft", "delete", "table", "inet", table))
            if removed.returncode == 0 and observed_nft_table(table) is None:
                run_optional(("ip", "address", "del", network["host_address"], "dev", veth))
        elif current_exact and prior_record is not None:
            prior_broker = any(
                name in BROKER_NETWORK_RULES for name, _rule in record_rule_tuple(prior_record)
            )
            rollback_script = "\n".join(network_nft_statements(
                table, prior_record["marker"], network,
                broker=prior_broker, replace=True,
            )) + "\n"
            run_fixed(("nft", "-f", "-"), "native nft rollback failed",
                      input_text=rollback_script)
        raise


def network_grants_apply(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]
    if not active_egress_grants(network):
        fail("native egress firewall activation requires an active grant")
    config = egress_config_record(machine_id)
    unit, _runtime, _control = egress_names(machine_id)
    if (config is None or config["policy_digest"] != digest or
            unit_description(unit) != egress_description(config) or
            query_egress_status(config) is None):
        fail("native egress broker proof is unavailable")
    table, _alias_prefix = network_names(machine_id)
    marker = network_marker(machine_id, digest)
    existing_table = observed_nft_table(table)
    prior_record = network_state_record(machine_id)
    if (existing_table is None or prior_record is None or
            existing_table.get("comment") != prior_record["marker"] or
            not nft_state_matches_record(observed_nft_state(table), prior_record)):
        fail("native baseline nft ownership is unproven")
    desired = desired_network_state(machine_id, digest, network, broker=True)
    if prior_record == desired:
        return
    script = "\n".join(network_nft_statements(
        table, marker, network, broker=True, replace=True,
    )) + "\n"
    run_fixed(("nft", "-f", "-"), "native egress firewall activation failed",
              input_text=script)
    write_network_state(desired)


def _replace_and_observe_network(machine_id, policy_digest, network, *, broker,
                                 grant_digest):
    table, _alias = network_names(machine_id)
    marker = network_marker(machine_id, policy_digest)
    script = "\n".join(network_nft_statements(
        table, marker, network, broker=broker, replace=True,
    )) + "\n"
    run_fixed(("nft", "-f", "-"), "native grant firewall reconcile failed",
              input_text=script)
    observed = observed_nft_state(table)
    desired = desired_network_state(machine_id, policy_digest, network,
                                    broker=broker, grant_digest=grant_digest)
    if not observed or not nft_state_matches_record(observed, desired):
        fail("native grant firewall observation failed")
    return desired


def grant_reconcile(machine_id, base_policy_digest, expected_grant_digest,
                    desired_grant_digest):
    expected_grant_digest = digest_value(expected_grant_digest)
    desired_grant_digest = digest_value(desired_grant_digest)
    _path, policy = applied_policy(machine_id, base_policy_digest)
    network = policy["network"]
    if (network.get("grant_authority") != GRANT_AUTHORITY or "grants" in network):
        fail("native base policy does not delegate grants")
    uid = invoking_uid()
    staged_path = STAGING_ROOT / f"grants-{uid}-{desired_grant_digest}.json"
    desired, desired_payload = _read_grant_file(
        staged_path, machine_id, base_policy_digest, desired_grant_digest, uid)
    desired_active = tuple(grant for grant in desired["grants"] if not grant["revoked"])
    table, _alias = network_names(machine_id)
    with grant_machine_lock(machine_id):
        current = installed_grant_record(machine_id, base_policy_digest)
        current_digest = current["grant_digest"] if current else ABSENT_GRANT_DIGEST
        if current_digest != expected_grant_digest:
            fail("native grant compare-and-swap failed", 73)
        record = network_state_record(machine_id)
        nft = observed_nft_state(table)
        if (record is None or record.get("policy_digest") != base_policy_digest or
                record.get("grant_digest") != current_digest or nft is None or
                nft.get("table", {}).get("comment") != network_marker(
                    machine_id, base_policy_digest) or
                not nft_state_matches_record(nft, record)):
            fail("native grant baseline ownership is unproven")
        current_payload = None
        if current is not None:
            current_payload = (json.dumps(current, sort_keys=True,
                                          separators=(",", ":")) + "\n").encode()
        desired_installed = False
        final_record = None
        try:
            # Close the only guest-to-host exception atomically before touching
            # the old broker. This also makes revocation close first; stopping
            # the old unit then terminates every accepted connection.
            baseline = _replace_and_observe_network(
                machine_id, base_policy_digest, network, broker=False,
                grant_digest=current_digest)
            stop_owned_egress(machine_id)
            config_path = EGRESS_ROOT / f"{machine_id}.json"
            if desired_active:
                config = build_egress_config(
                    machine_id, base_policy_digest, network,
                    policy["resources"]["connections"],
                    grants=desired["grants"], grant_digest=desired_grant_digest)
                start_egress_config(config)
                unit, _runtime, _control = egress_names(machine_id)
                if (unit_description(unit) != egress_description(config) or
                        query_egress_status(config) is None):
                    fail("native desired egress broker proof is unavailable")
                final_record = _replace_and_observe_network(
                    machine_id, base_policy_digest, network, broker=True,
                    grant_digest=desired_grant_digest)
            else:
                if config_path.exists():
                    try: config_path.unlink()
                    except OSError: fail("native empty grant configuration cleanup failed")
                final_record = desired_network_state(
                    machine_id, base_policy_digest, network, broker=False,
                    grant_digest=desired_grant_digest)
                # The closed baseline was produced with the previous CAS digest;
                # its nft authorization is identical, but bind the final record
                # to the explicit empty/revoked desired set.
                if not nft_state_matches_record(observed_nft_state(table), final_record):
                    fail("native empty grant firewall observation failed")
            ensure_root_directory(GRANT_ROOT, 0o755)
            atomic_install_bytes(desired_payload, GRANT_ROOT / f"{machine_id}.json")
            desired_installed = True
            write_network_state(final_record)
        except BaseException:
            # Any failed transaction converges to closed before returning. Never
            # preserve a broker exception without matching durable state.
            rollback_record = None
            try:
                rollback_record = _replace_and_observe_network(
                    machine_id, base_policy_digest, network, broker=False,
                    grant_digest=current_digest)
            except BaseException:
                pass
            # Keep this independent from nft repair: if the firewall mechanism
            # itself has failed, killing the broker still closes the capability
            # and all active connections.
            try:
                stop_owned_egress(machine_id)
            except BaseException:
                pass
            try:
                if rollback_record is not None:
                    write_network_state(rollback_record)
            except BaseException:
                pass
            try:
                destination = GRANT_ROOT / f"{machine_id}.json"
                if desired_installed:
                    if current_payload is None:
                        destination.unlink(missing_ok=True)
                    else:
                        atomic_install_bytes(current_payload, destination)
            except BaseException:
                pass
            raise
        try: staged_path.unlink()
        except FileNotFoundError: pass
        except OSError: pass
        print(json.dumps({"ok": True, "machine_id": machine_id,
                          "base_policy_digest": base_policy_digest,
                          "expected_grant_digest": expected_grant_digest,
                          "grant_digest": desired_grant_digest,
                          "active_grants": sorted(grant["grant_id"]
                                                  for grant in desired_active)},
                         sort_keys=True, separators=(",", ":")))


def network_status(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]; table, alias_prefix = network_names(machine_id)
    link = observed_link(network["veth"]); nft_state = observed_nft_state(table)
    guest = observed_guest_network(machine_id)
    record = network_state_record(machine_id)
    expected_chains = expected_network_chains()
    expected_routes = [str(ipaddress.ip_interface(network["guest_address"]).network)]
    grant_record = installed_grant_record(machine_id, digest) \
        if network.get("grant_authority") == GRANT_AUTHORITY else None
    grants = grant_record["grants"] if grant_record else list(network.get("grants", ()))
    grant_digest = grant_record["grant_digest"] if grant_record else ABSENT_GRANT_DIGEST
    broker = any(not grant["revoked"] for grant in grants)
    egress = egress_config_record(machine_id)
    egress_observed = query_egress_status(egress) if broker and egress is not None else None
    egress_unit, _egress_runtime, _egress_control = egress_names(machine_id)
    expected_rule_names = {name for name, _digest in expected_network_rules(
        network, broker=broker)}
    ok = bool(network_record_matches(record, desired_network_state(
                  machine_id, digest, network, broker=broker,
                  grant_digest=grant_digest)) and
              link and link.get("ifalias") == alias_prefix and nft_state and
              nft_state["table"].get("comment") == network_marker(machine_id, digest) and
              nft_state["chains"] == expected_chains and
              nft_state["rules"] == expected_network_rules(network, broker=broker) and
              set(nft_state["counters"]) == expected_rule_names and
              ((not broker and egress is None and not unit_description(egress_unit)) or
               (broker and egress["policy_digest"] == digest and
                egress["grant_digest"] == grant_digest and egress_observed and
                unit_description(egress_unit) == egress_description(egress))) and
              guest["guest_address"] == network["guest_address"] and
              guest["default_route"] is False and guest["routes"] == expected_routes)
    print(json.dumps({"machine_id": machine_id, "policy_digest": digest,
                      "grant_digest": grant_digest,
                      "veth": network["veth"], "table": table, "ok": ok,
                      "default_route": guest["default_route"],
                      "guest_address": guest["guest_address"],
                      "routes": guest["routes"],
                      "counters": nft_state["counters"] if nft_state else {},
                      "grant_counters": egress_observed.get("grants", {})
                      if egress_observed else {}}, sort_keys=True))
    if not ok: raise SystemExit(69)


def network_remove(machine_id, digest):
    _path, policy = applied_policy(machine_id, digest)
    network = policy["network"]; table, alias_prefix = network_names(machine_id)
    record = network_state_record(machine_id)
    grant_record = installed_grant_record(machine_id, digest) \
        if network.get("grant_authority") == GRANT_AUTHORITY else None
    grant_digest = grant_record["grant_digest"] if grant_record else ABSENT_GRANT_DIGEST
    if grant_record and any(not grant["revoked"] for grant in grant_record["grants"]):
        fail("native active grants must be revoked before network removal")
    desired = desired_network_state(machine_id, digest, network,
                                    grant_digest=grant_digest)
    if record is not None and not network_record_matches(record, desired):
        fail("native network ownership record changed")
    nft_table = observed_nft_table(table)
    if nft_table is not None:
        if record is None or nft_table.get("comment") != record["marker"]:
            fail("native nft ownership changed")
        run_fixed(("nft", "delete", "table", "inet", table),
                  "native nft table removal failed")
    link = observed_link(network["veth"])
    if link is not None:
        if link.get("ifalias") != alias_prefix: fail("native veth ownership changed")
        run_optional(("ip", "address", "del", network["host_address"],
                      "dev", network["veth"]))
    path = NETWORK_STATE_ROOT / f"{machine_id}.json"
    if record is not None:
        try: path.unlink()
        except OSError: fail("native network ownership record removal failed")
    if grant_record is not None:
        try: (GRANT_ROOT / f"{machine_id}.json").unlink()
        except OSError: fail("native grant record removal failed")


def guest_command(machine_id, argv):
    """Argv that runs `argv` inside the machine and returns its real result.

    `machinectl shell` allocates a PTY through logind and does NOT propagate the
    command's exit status; measured on a live host it also dropped output
    intermittently, so a failing probe was indistinguishable from a silent one.
    `systemd-run --pipe --wait` returns the command's own status and output.
    """
    return ("systemd-run", f"--machine={machine_id}", "--pipe", "--wait",
            "--quiet", "--collect", *tuple(argv))


def guest_run(machine_id, argv, message, *, timeout=120):
    machine(machine_id)
    return run_fixed(guest_command(machine_id, argv), message, timeout=timeout)


def guest_json(machine_id, path, message):
    if path != "/etc/sandbox-native/services.json":
        fail("native guest marker path is invalid")
    result = guest_run(machine_id, ("/usr/bin/cat", path), message)
    try:
        value = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        fail(message)
    if not isinstance(value, dict):
        fail(message)
    return value


def read_execution_request(machine_id, policy_digest, request_digest):
    request_digest = digest_value(request_digest)
    uid = int(os.environ.get("SUDO_UID", os.getuid()))
    path = STAGING_ROOT / f"execute-{uid}-{request_digest}.json"
    try: descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError: fail("native execution request is unavailable")
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != uid or
                details.st_mode & 0o077 or not 1 <= details.st_size <= 1024 * 1024):
            fail("native execution request is invalid")
        payload = b""
        while len(payload) <= 1024 * 1024:
            chunk = os.read(descriptor, 65536)
            if not chunk: break
            payload += chunk
        if len(payload) != details.st_size: fail("native execution request changed")
    finally:
        os.close(descriptor)
    try: request = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError): fail("native execution request is invalid")
    keys = {"machine_id", "policy_digest", "argv", "environment", "credential_refs", "timeout"}
    if (not isinstance(request, dict) or set(request) != keys or
            request.get("machine_id") != machine_id or
            request.get("policy_digest") != policy_digest or
            canonical_digest(request) != request_digest):
        fail("native execution request identity changed")
    return request


def validated_execution_argv(policy, request):
    argv = request.get("argv")
    environment = request.get("environment")
    credential_refs = request.get("credential_refs")
    timeout = request.get("timeout")
    if (not isinstance(argv, list) or not argv or len(argv) > 512 or
            any(not isinstance(item, str) or not item or "\x00" in item for item in argv) or
            sum(len(item) for item in argv) > 131072 or
            not isinstance(environment, dict) or
            any(key not in EXECUTION_ENV_ALLOWLIST or not isinstance(value, str) or "\x00" in value
                for key, value in environment.items()) or
            not isinstance(credential_refs, list) or
            any(ref not in policy["credentials"] for ref in credential_refs) or
            isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600):
        fail("native execution request is invalid")
    proxy_keys = {key for key in environment if key in {"HTTP_PROXY", "HTTPS_PROXY"}}
    host = str(ipaddress.ip_interface(policy["network"]["host_address"]).ip)
    proxy = f"http://{host}:{BROKER_PORT}"
    grant_record = installed_grant_record(policy["machine_id"], policy["digest"]) \
        if policy["network"].get("grant_authority") == GRANT_AUTHORITY else None
    grants = (grant_record["grants"] if grant_record is not None else
              policy["network"].get("grants", ()))
    active_grants = tuple(grant for grant in grants if not grant["revoked"])
    expected_proxy = ({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}
                      if active_grants else {})
    if {key: environment.get(key) for key in proxy_keys} != expected_proxy:
        fail("native execution proxy policy changed")
    try: separator = argv.index("--")
    except ValueError: fail("native execution boundary is missing")
    command = argv[separator + 1:]
    if not command: fail("native execution command is missing")
    # The privileged side requires the payload profile stack; it never takes the
    # caller's word for it. Without this check a caller could simply omit the
    # wrapper and run under the weaker bwrap profile.
    prefix = payload_stack_prefix(policy["machine_id"])
    if tuple(command[:len(prefix)]) != prefix or len(command) <= len(prefix):
        fail("native payload profile stack is missing")
    writable = {item["target"] for item in policy["writable_mounts"]} | EXECUTION_WRITABLE_TARGETS
    expected = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--clearenv",
                "--unshare-user", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                "--ro-bind", "/", "/"]
    for target in sorted(writable): expected.extend(("--bind", target, target))
    expected.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                     "--tmpfs", "/run/credentials", "--dir", GUEST_CREDENTIAL_TARGET_ROOT))
    for ref in sorted(credential_refs):
        name = ref.rsplit("/", 1)[-1]
        source = f"{GUEST_CREDENTIAL_SOURCE_ROOT}/{name}"
        target = f"{GUEST_CREDENTIAL_TARGET_ROOT}/{name}"
        expected.extend(("--ro-bind", source, target))
    expected.extend(("--tmpfs", GUEST_CREDENTIAL_SOURCE_ROOT,
                     "--tmpfs", "/run/systemd", "--tmpfs", "/run/dbus"))
    expected.extend(("--chdir", "/workspace", "--cap-drop", "ALL",
                     "--uid", "33", "--gid", "33"))
    for key, value in sorted(environment.items()): expected.extend(("--setenv", key, value))
    expected.extend(("--", *command))
    if argv != expected: fail("native execution boundary changed")
    return tuple(argv), min(timeout, policy["resources"]["runtime_seconds"])


def bounded_guest_execution(machine_id, argv, timeout):
    command = subprocess.Popen(
        (*guest_command(machine_id, ()), *argv),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True, start_new_session=True, env=FIXED_ENVIRONMENT,
    )
    selector = selectors.DefaultSelector()
    selector.register(command.stdout, selectors.EVENT_READ, "stdout")
    selector.register(command.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    truncated = False
    timed_out = False
    while selector.get_map():
        if time.monotonic() >= deadline and not timed_out:
            timed_out = True
            try: os.killpg(command.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: command.wait(timeout=2)
            except subprocess.TimeoutExpired: os.killpg(command.pid, signal.SIGKILL)
            truncated = True
        for key, _mask in selector.select(timeout=0.1):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                selector.unregister(key.fileobj); continue
            remaining = 1024 * 1024 - len(output[key.data])
            output[key.data].extend(chunk[:max(0, remaining)])
            if len(chunk) > remaining:
                truncated = True
                try: os.killpg(command.pid, signal.SIGTERM)
                except ProcessLookupError: pass
        if truncated and command.poll() is not None:
            for key in list(selector.get_map().values()): selector.unregister(key.fileobj)
    returncode = command.wait()
    sys.stdout.buffer.write(bytes(output["stdout"])); sys.stdout.buffer.flush()
    sys.stderr.buffer.write(bytes(output["stderr"])); sys.stderr.buffer.flush()
    return 124 if timed_out else (125 if truncated else returncode)


def execute_request(machine_id, policy_digest, request_digest):
    _path, policy = applied_policy(machine_id, policy_digest)
    request = read_execution_request(machine_id, policy_digest, request_digest)
    argv, timeout = validated_execution_argv(policy, request)
    return bounded_guest_execution(machine_id, argv, timeout)


def payload_stack_prefix(machine_id):
    """The exec wrapper that stacks the payload profile onto the final exec.

    A domain transition cannot be used: bubblewrap sets NoNewPrivileges before
    exec, under which the kernel refuses one, and with any `px` rule present
    every exec inside bubblewrap is refused before the payload runs at all.
    Stacking yields the intersection of the bwrap and payload profiles, and is
    irreversible because the payload profile grants no change_profile.

    A failed write exits instead of continuing, because continuing would run the
    payload under the weaker bwrap profile -- the one outcome this prevents.
    `sh -c SCRIPT NAME ARG...` puts NAME in $0 and the payload in $@.
    """
    profile = f"sandbox-native-{machine_id}//payload"
    script = (f"printf %s 'stack {profile}' > /proc/self/attr/apparmor/exec "
              "|| exit 126\nexec \"$@\"\n")
    return ("/bin/sh", "-c", script, "sandbox-payload")


def stacked_payload_command(machine_id, command):
    return (*payload_stack_prefix(machine_id), *tuple(command))


def fixed_probe_bwrap(policy, command, credential_refs=()):
    writable = {item["target"] for item in policy["writable_mounts"]} | EXECUTION_WRITABLE_TARGETS
    argv = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--clearenv",
            "--unshare-user", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--ro-bind", "/", "/"]
    for target in sorted(writable): argv.extend(("--bind", target, target))
    if any(ref not in policy["credentials"] for ref in credential_refs):
        fail("native probe credential is not declared")
    argv.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--tmpfs", "/run/credentials", "--dir", GUEST_CREDENTIAL_TARGET_ROOT))
    for ref in sorted(credential_refs):
        name = ref.rsplit("/", 1)[-1]
        source = f"{GUEST_CREDENTIAL_SOURCE_ROOT}/{name}"
        target = f"{GUEST_CREDENTIAL_TARGET_ROOT}/{name}"
        argv.extend(("--ro-bind", source, target))
    argv.extend(("--tmpfs", GUEST_CREDENTIAL_SOURCE_ROOT,
                 "--tmpfs", "/run/systemd", "--tmpfs", "/run/dbus"))
    argv.extend(("--chdir", "/workspace", "--cap-drop", "ALL", "--uid", "33", "--gid", "33",
                 "--", *stacked_payload_command(policy["machine_id"], command)))
    return tuple(argv)


def parse_status_fields(text):
    values = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1); values[key] = value.strip()
    return values


def probe_payload_state(machine_id, policy):
    script = ("printf '---status---\\n'; cat /proc/self/status; "
              "printf '%s\\n' '---profile---'; cat /proc/self/attr/current; "
              "printf '%s\\n' '---fds---'; "
              "for value in /proc/$$/fd/*; do printf '%s\\n' \"${value##*/}\"; done; "
              "printf '%s\\n' '---controls---'; "
              "for value in /run/systemd/private /run/dbus/system_bus_socket "
              "/var/run/docker.sock /run/credentials/sandbox/db-credential "
              "/run/sandbox-native-credentials /run/host; do "
              "test ! -e \"$value\" || printf '%s\\n' \"$value\"; done; "
              "printf '%s\\n' '---env---'; env")
    result = guest_run(machine_id, fixed_probe_bwrap(
        policy, ("/bin/sh", "-c", script)), "native payload isolation probe failed")
    text = result.stdout or ""

    def invalid(marker):
        # Name the missing section, show what the probe produced, and answer the
        # first two questions any failure here raises: which profile confines a
        # plain guest command, and whether a MINIMAL bwrap can set up at all.
        # Without those, an AppArmor transition failure and a kernel namespace
        # restriction produce the same opaque message.
        seen = (text.strip() or (result.stderr or "").strip()
                or "no output on stdout or stderr")
        seen = seen if len(seen) <= 400 else "…" + seen[-399:]
        confinement = run_optional((*guest_command(machine_id, ()),
                                    "/usr/bin/cat", "/proc/self/attr/current"))
        minimal = run_optional((*guest_command(machine_id, ()),
                                "/usr/bin/bwrap", "--ro-bind", "/", "/",
                                "--proc", "/proc", "--", "/bin/true"))

        def summary(name, outcome):
            detail = ((outcome.stdout or "").strip()
                      or (outcome.stderr or "").strip() or "no output")
            detail = detail if len(detail) <= 200 else "…" + detail[-199:]
            return f"{name}: rc={outcome.returncode} {detail}"

        # Order matters: callers truncate long messages, so the two answers go
        # before the probe's own (longer) output.
        fail(f"native payload isolation probe is invalid: no {marker} section; "
             f"{summary('minimal bwrap', minimal)}; "
             f"{summary('guest confinement', confinement)}; "
             f"probe produced: {seen}")

    parts = text.split("---profile---\n", 1)
    if len(parts) != 2: invalid("profile")
    status_text = parts[0].split("---status---\n", 1)[-1]
    profile_parts = parts[1].split("---fds---\n", 1)
    if len(profile_parts) != 2: invalid("fds")
    profile = profile_parts[0].strip().removesuffix(" (enforce)")
    fd_parts = profile_parts[1].split("---controls---\n", 1)
    if len(fd_parts) != 2: invalid("controls")
    fds = [int(value) for value in fd_parts[0].split() if value.isdigit()]
    control_parts = fd_parts[1].split("---env---\n", 1)
    if len(control_parts) != 2: invalid("env")
    controls = [line for line in control_parts[0].splitlines() if line.startswith("/")]
    environment = [line.split("=", 1)[0] for line in control_parts[1].splitlines() if "=" in line]
    status = parse_status_fields(status_text)
    nested = run_optional((*guest_command(machine_id, ()),
                           *fixed_probe_bwrap(policy, ("/usr/bin/unshare", "--user",
                                                       "/usr/bin/true"))))
    return {"no_new_privileges": status.get("NoNewPrivs") == "1",
            "capabilities": [] if int(status.get("CapEff", "1"), 16) == 0 else [status.get("CapEff")],
            "ambient_capabilities": [] if int(status.get("CapAmb", "1"), 16) == 0 else [status.get("CapAmb")],
            "seccomp": status.get("Seccomp") == "2", "apparmor_profile": profile,
            "nested_userns": nested.returncode == 0,
            "leaked_fds": [value for value in fds if value > 2],
            "leaked_environment": sorted(set(environment) - {"PWD", "SHLVL", "_"}),
            "control_sockets": controls}


def observed_namespaces(leader):
    result = {}
    for public, kernel in (("user", "user"), ("mount", "mnt"), ("pid", "pid"),
                           ("ipc", "ipc"), ("uts", "uts"), ("network", "net")):
        try:
            result[public] = os.readlink(f"/proc/{leader}/ns/{kernel}") != \
                             os.readlink(f"/proc/1/ns/{kernel}")
        except OSError: result[public] = False
    return result


def observed_mounts(leader, policy):
    try: rows = Path(f"/proc/{leader}/mountinfo").read_text().splitlines()
    except OSError: fail("native mount observation failed")
    mounts = {}
    canonical_pseudo_mounts = {
        "/proc": {("proc", "proc", "/")},
        "/sys": {("sysfs", "sysfs", "/")},
        "/dev": {("tmpfs", "tmpfs", "/"), ("devtmpfs", "devtmpfs", "/")},
        "/dev/pts": {("devpts", "devpts", "/")},
        "/dev/shm": {("tmpfs", "tmpfs", "/")},
        "/dev/mqueue": {("mqueue", "mqueue", "/")},
        "/run": {("tmpfs", "tmpfs", "/")},
        "/sys/fs/cgroup": {("cgroup2", "cgroup2", "/")},
    }
    for row in rows:
        fields = row.split()
        if len(fields) < 7 or "-" not in fields: continue
        separator = fields.index("-")
        if separator + 2 >= len(fields): continue
        target = fields[4].replace("\\040", " ").replace("\\011", "\t")
        root = fields[3].replace("\\040", " ").replace("\\011", "\t")
        mounts[target] = {
            "options": set(fields[5].split(",")), "root": root,
            "device": fields[2], "filesystem": fields[separator + 1],
            "source": fields[separator + 2],
        }
    expected = {
        item["target"]: item
        for item in (*policy["read_only_mounts"], *policy["writable_mounts"])
    }
    unexpected = []
    identity_matches = set()
    for target, item in expected.items():
        observed = mounts.get(target)
        if observed is None:
            continue
        try:
            source_stat = os.stat(item["source"], follow_symlinks=False)
            target_path = f"/proc/{leader}/root{target}"
            target_stat = os.stat(target_path, follow_symlinks=False)
            matched = (source_stat.st_dev, source_stat.st_ino) == \
                      (target_stat.st_dev, target_stat.st_ino)
        except OSError:
            matched = False
        if matched:
            identity_matches.add(target)
        else:
            unexpected.append(target)
    for target, observed in mounts.items():
        if target in expected or target == "/":
            continue
        allowed = canonical_pseudo_mounts.get(target, set())
        signature = (observed["filesystem"], observed["source"], observed["root"])
        guest_owned = False
        if signature in allowed:
            try:
                guest_stat = os.stat(f"/proc/{leader}/root{target}", follow_symlinks=False)
                host_stat = os.stat(target, follow_symlinks=False)
                guest_owned = (guest_stat.st_dev, guest_stat.st_ino) != \
                              (host_stat.st_dev, host_stat.st_ino)
            except OSError:
                guest_owned = False
        if not guest_owned:
            unexpected.append(target)
    read_only = [item["target"] for item in policy["read_only_mounts"]
                 if item["target"] in identity_matches
                 and "ro" in mounts.get(item["target"], {}).get("options", set())]
    writable = [item["target"] for item in policy["writable_mounts"]
                if item["target"] in identity_matches
                and "rw" in mounts.get(item["target"], {}).get("options", set())]
    return read_only, writable, unexpected


def resource_limits_match(machine_id, policy):
    unit, _profile = machine_names(machine_id)
    expected = policy["resources"]
    memory_high = max(1, expected["memory_bytes"] * 9 // 10)
    properties = {"MemoryMax": str(expected["memory_bytes"]),
                  "MemoryHigh": str(memory_high), "MemorySwapMax": "0",
                  "TasksMax": str(expected["pids"]),
                  "LimitNOFILE": str(expected["fds"]), "IOWeight": str(expected["io_weight"])}
    for name, wanted in properties.items():
        result = run_optional(("systemctl", "show", unit, f"--property={name}", "--value"))
        actual = (result.stdout or "").strip().split(":", 1)[0]
        if result.returncode != 0 or actual != wanted: return {}
    def duration_us(value):
        match = re.fullmatch(r"([0-9]+)(us|ms|s|min|h)", value)
        if not match: return None
        factors = {"us": 1, "ms": 1000, "s": 1000000,
                   "min": 60 * 1000000, "h": 3600 * 1000000}
        return int(match.group(1)) * factors[match.group(2)]
    for name, wanted in (("CPUQuotaPerSecUSec", expected["cpu_percent"] * 10000),):
        result = run_optional(("systemctl", "show", unit, f"--property={name}", "--value"))
        if result.returncode != 0 or duration_us((result.stdout or "").strip()) != wanted:
            return {}
    cron_runtime = run_optional((*guest_command(machine_id, ()),
                                 "/usr/bin/cat",
                                 "/usr/local/libexec/sandbox-wordpress-cron"))
    guest = str(ipaddress.ip_interface(policy["network"]["guest_address"]).ip)
    compiled_files, _units = compile_service_files(
        guest, expected["connections"], expected["runtime_seconds"], "nginx",
        policy["network"]["ingress_port"],
        tuple(item["target"] for item in policy["writable_mounts"]),
    )
    expected_cron = compiled_files["/usr/local/libexec/sandbox-wordpress-cron"]
    if (cron_runtime.returncode != 0
            or (cron_runtime.stdout or "") != expected_cron):
        return {}
    _instance, image, _mountpoint = image_paths(machine_id)
    try:
        details = image.lstat()
    except OSError:
        return {}
    if (stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or
            details.st_uid != 0 or details.st_size != expected["disk_bytes"]):
        return {}
    filesystem = run_optional(("dumpe2fs", "-h", str(image)))
    if filesystem.returncode != 0:
        return {}
    fields = {}
    for name in ("Block count", "Block size", "Inode count"):
        matches = re.findall(rf"(?m)^{re.escape(name)}:\s*([0-9]+)\s*$",
                             filesystem.stdout or "")
        if len(matches) != 1:
            return {}
        fields[name] = int(matches[0])
    if (fields["Block count"] * fields["Block size"] != expected["disk_bytes"] or
            fields["Inode count"] != expected["inodes"]):
        return {}
    service_files = {}
    for name, path in (("marker", "/etc/sandbox-native/services.json"),):
        result = run_optional((*guest_command(machine_id, ()),
                               "/usr/bin/cat", path))
        if result.returncode != 0:
            return {}
        service_files[name] = result.stdout or ""
    expected_php = max(2, min(32, expected["connections"] // 4))
    database = run_optional((
        *guest_command(machine_id, ()),
        "/usr/bin/mariadb", "--protocol=socket", "--skip-column-names", "--batch",
        "--socket=/run/mysqld/mysqld.sock", "-e", "SELECT @@GLOBAL.max_connections;",
    ))
    php = run_optional((*guest_command(machine_id, ()),
                        "/usr/sbin/php-fpm8.3", "-tt"))
    php_output = (php.stdout or "") + "\n" + (php.stderr or "")
    php_children = re.findall(r"(?m)^.*pm\.max_children\s*=\s*([0-9]+)\s*$",
                              php_output)
    php_listeners = re.findall(r"(?m)^.*listen\s*=\s*(\S+)\s*$", php_output)
    php_terminate = re.findall(
        r"(?m)^.*request_terminate_timeout\s*=\s*([0-9]+)s\s*$", php_output,
    )
    if (database.returncode != 0 or (database.stdout or "").strip() !=
            str(expected["connections"]) or php.returncode != 0 or
            php_children != [str(expected_php)] or
            php_listeners != ["/run/php/sandbox.sock"] or
            php_terminate != [str(expected["runtime_seconds"])]):
        return {}
    try:
        marker = json.loads(service_files["marker"])
    except json.JSONDecodeError:
        return {}
    if (not isinstance(marker, dict) or marker.get("machine_id") != machine_id or
            marker.get("policy_digest") != policy["digest"] or
            marker.get("web_server") not in {"nginx", "apache"}):
        return {}
    if marker["web_server"] == "nginx":
        effective = run_optional((*guest_command(machine_id, ()),
                                  "/usr/sbin/nginx", "-T"))
        output = (effective.stdout or "") + "\n" + (effective.stderr or "")
        workers = re.findall(r"(?m)^\s*worker_processes\s+([0-9]+);\s*$", output)
        ceilings = re.findall(r"(?m)^\s*worker_connections\s+([0-9]+);\s*$", output)
        if (effective.returncode != 0 or workers != ["1"] or
                ceilings != [str(expected["connections"])]):
            return {}
    else:
        runtime = run_optional((*guest_command(machine_id, ()),
                                "/usr/sbin/apache2ctl", "-t", "-D", "DUMP_RUN_CFG"))
        output = (runtime.stdout or "") + "\n" + (runtime.stderr or "")
        server = re.findall(r"(?mi)^\s*ServerLimit:\s*([0-9]+)\s*$", output)
        workers = re.findall(r"(?mi)^\s*MaxRequestWorkers:\s*([0-9]+)\s*$", output)
        keepalive = re.findall(r"(?mi)^\s*KeepAlive:\s*(\S+)\s*$", output)
        if (runtime.returncode != 0 or "Server MPM: prefork" not in output or
                server != [str(expected["connections"])] or
                workers != [str(expected["connections"])] or
                keepalive != ["Off"]):
            return {}
    return {**dict(expected), "memory_high_bytes": memory_high,
            "memory_swap_bytes": 0}


def denied_reachability(machine_id, policy):
    network = policy["network"]
    addresses = {
        "host": str(ipaddress.ip_interface(network["host_address"]).ip),
        "metadata": "169.254.169.254", "public": "1.1.1.1",
    }
    siblings = []
    for path in NETWORK_STATE_ROOT.glob("sb-*.json"):
        if path.name == f"{machine_id}.json": continue
        try:
            value = json.loads(path.read_text())
            sibling_policy = checked_policy(POLICY_ROOT / path.name, path.stem, applied=True)[1]
            siblings.append(str(ipaddress.ip_interface(
                sibling_policy["network"]["guest_address"]).ip))
        except (OSError, ValueError, KeyError, json.JSONDecodeError, SystemExit):
            continue
    addresses["sibling"] = siblings[0] if siblings else "10.203.255.254"
    result = {}
    for boundary, address in addresses.items():
        if boundary == "host": continue
        route = run_optional((*guest_command(machine_id, ()),
                              *fixed_probe_bwrap(policy, (
                                  "/usr/sbin/ip", "route", "get", address))))
        # Any payload route to a forbidden boundary is itself a proof failure;
        # a closed TCP port must never be mistaken for network isolation.
        result[boundary] = route.returncode == 0
    table, _alias = network_names(machine_id)
    before_state = observed_nft_state(table)
    before = before_state["counters"]["guest_host_drop"]["packets"] \
        if before_state else -1
    host = addresses["host"]
    probe = run_optional((*guest_command(machine_id, ()),
                          *fixed_probe_bwrap(policy, (
                              "/usr/bin/curl", "--silent", "--show-error",
                              "--connect-timeout", "1", "--max-time", "2",
                              f"http://{host}:9/"))))
    after_state = observed_nft_state(table)
    after = after_state["counters"]["guest_host_drop"]["packets"] \
        if after_state else -1
    result["host"] = not (probe.returncode != 0 and after > before >= 0)
    return result


def isolation_observe(machine_id):
    path, policy = checked_policy(POLICY_ROOT / f"{machine_id}.json", machine_id, applied=True)
    read_policy_owner(machine_id, policy)
    digest = policy["digest"]; leader = machine_leader(machine_id)
    payload = probe_payload_state(machine_id, policy)
    read_only, writable, unexpected = observed_mounts(leader, policy)
    guest = observed_guest_network(machine_id)
    network = policy["network"]; table, _alias = network_names(machine_id)
    nft_state = observed_nft_state(table); record = network_state_record(machine_id)
    grant_record = installed_grant_record(machine_id, digest) \
        if network.get("grant_authority") == GRANT_AUTHORITY else None
    grants = grant_record["grants"] if grant_record else list(network.get("grants", ()))
    active = tuple(grant for grant in grants if not grant["revoked"])
    broker = None
    if active:
        config = egress_config_record(machine_id)
        broker = query_egress_status(config) if config else None
    devices_result = run_optional((*guest_command(machine_id, ()),
                                   "/usr/bin/find", "/dev", "-mindepth", "1", "-maxdepth", "1",
                                   "-type", "c", "-printf", "%f\\n"))
    devices = sorted(set((devices_result.stdout or "").split())) if devices_result.returncode == 0 else []
    dangerous = [] if not payload["capabilities"] else list(payload["capabilities"])
    result = {"machine_id": machine_id, "policy_digest": digest,
              "private_namespaces": observed_namespaces(leader), **payload,
              "dangerous_capabilities": dangerous, "devices": devices,
              "nft_default_drop": bool(record and nft_state and
                  nft_state_matches_record(nft_state, record)),
              "default_route": guest["default_route"], "guest_address": guest["guest_address"],
              "reachability": denied_reachability(machine_id, policy),
              "cgroup_limits": resource_limits_match(machine_id, policy),
              "read_only_mounts": read_only, "writable_mounts": writable,
              "unexpected_host_mounts": unexpected,
              "egress_broker": ({"ok": True, "policy_digest": digest,
                  "grant_digest": grant_record["grant_digest"] if grant_record else
                                  ABSENT_GRANT_DIGEST,
                  "listener": {"address": str(ipaddress.ip_interface(network["host_address"]).ip),
                               "port": BROKER_PORT, "interface": network["veth"]},
                  "grants": sorted(grant["grant_id"] for grant in active),
                  "counters": broker.get("grants", {}) if broker else {}}
                  if active and broker else {})}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


def service_plan(machine_id, policy_digest, service_digest):
    _path, policy = applied_policy(machine_id, policy_digest)
    service_digest = digest_value(service_digest)
    marker = guest_json(machine_id, "/etc/sandbox-native/services.json",
                        "native service marker is unavailable")
    web_server = marker.get("web_server")
    expected_units = ["mariadb.service", "php8.3-fpm.service",
                      "nginx.service" if web_server == "nginx" else "apache2.service",
                      "cron.service"]
    if (web_server not in {"nginx", "apache"} or
            marker.get("machine_id") != machine_id or
            marker.get("policy_digest") != policy["digest"] or
            marker.get("service_digest") != service_digest or
            marker.get("units") != expected_units):
        fail("native service marker changed")
    return policy, tuple(expected_units)


def services_activate(machine_id, policy_digest, service_digest):
    _policy, units = service_plan(machine_id, policy_digest, service_digest)
    try:
        for unit in units:
            guest_run(machine_id, ("/usr/bin/systemctl", "unmask", unit),
                      "native service unmask failed")
        guest_run(machine_id, ("/usr/bin/systemctl", "daemon-reload"),
                  "native guest daemon reload failed")
        guest_run(machine_id, ("/usr/bin/systemctl", "start", *units),
                  "native service activation failed", timeout=180)
    except BaseException:
        run_optional((*guest_command(machine_id, ()),
                      "/usr/bin/systemctl", "stop", *tuple(reversed(units))))
        for unit in units:
            run_optional((*guest_command(machine_id, ()),
                          "/usr/bin/systemctl", "mask", unit))
        raise


def services_health(machine_id, policy_digest, service_digest):
    policy, units = service_plan(machine_id, policy_digest, service_digest)
    guest_run(machine_id, ("/usr/bin/systemctl", "is-active", *units),
              "native service health failed")
    guest_run(machine_id, ("/usr/bin/test", "-S", "/run/mysqld/mysqld.sock"),
              "native database socket health failed")
    guest_run(machine_id, ("/usr/bin/mariadb-admin", "--protocol=socket",
                           "--socket=/run/mysqld/mysqld.sock", "ping", "--silent"),
              "native database health failed")
    guest = str(ipaddress.ip_interface(policy["network"]["guest_address"]).ip)
    port = policy["network"]["ingress_port"]
    guest_run(machine_id, ("/usr/bin/curl", "--silent", "--show-error",
                           "--output", "/dev/null", "--connect-timeout", "2",
                           "--max-time", "5", f"http://{guest}:{port}/"),
              "native private backend health failed")


def services_ownership_status(machine_id, policy_digest, service_digest):
    """Prove the units are ours, which is not the same as proving they run.

    This asks whether the guest knows each unit, not whether it is active. The
    check used to require `is-active`, so a machine whose units were installed
    but never started answered "inactive" and cleanup refused to stop and mask
    them -- treating an already-stopped service as changed ownership. Ownership
    is established by the marker `service_plan` verifies; state is irrelevant to
    it. Cleanup observation must never make an HTTP request or execute plugin PHP.
    """
    _policy, units = service_plan(machine_id, policy_digest, service_digest)
    states = guest_unit_load_states(machine_id, units)
    if states is None:
        fail("native service ownership observation failed")
    # `masked` is what this cleanup's own stop step leaves behind, so a rerun
    # must accept it: refusing it made cleanup treat its own completed work as
    # changed ownership and stall.
    if any(states.get(unit) not in {"loaded", "masked"} for unit in units):
        fail("native service ownership changed")


def services_stop(machine_id, policy_digest, service_digest):
    _policy, units = service_plan(machine_id, policy_digest, service_digest)
    # Preserve MariaDB until the subsequent database-owned cleanup has removed
    # the exact Sandbox schemas/user.  No project PHP/web/cron execution remains.
    project_units = tuple(unit for unit in units if unit != "mariadb.service")
    guest_run(machine_id, ("/usr/bin/systemctl", "stop", *tuple(reversed(project_units))),
              "native service stop failed", timeout=180)
    for unit in project_units:
        guest_run(machine_id, ("/usr/bin/systemctl", "mask", unit),
                  "native service mask failed")


def database_objects(machine_id):
    """The Sandbox-owned schemas and user that the guest database actually has."""
    production, tests, user = database_names(machine_id)
    query = ("SELECT CONCAT('db:',SCHEMA_NAME) FROM information_schema.SCHEMATA "
             f"WHERE SCHEMA_NAME IN ('{production}','{tests}') UNION ALL "
             "SELECT CONCAT('user:',User) FROM mysql.user "
             f"WHERE User='{user}' AND Host='localhost' ORDER BY 1")
    result = guest_run(machine_id, (
        "/usr/bin/mariadb", "--protocol=socket", "--socket=/run/mysqld/mysqld.sock",
        "--batch", "--skip-column-names", "--execute", query,
    ), "native database ownership observation failed")
    return sorted(line for line in (result.stdout or "").splitlines() if line.strip())


def database_ownership_matches(machine_id):
    production, tests, user = database_names(machine_id)
    return database_objects(machine_id) == sorted((
        f"db:{production}", f"db:{tests}", f"user:{user}",
    ))


def unit_absent(unit):
    """True only when systemd answered and said it has no such unit.

    A read that could not be made is never absence.  `run_optional` returning
    non-zero means the question went unanswered, and cleanup must retry rather
    than conclude there is nothing to remove.
    """
    loaded = run_optional(("systemctl", "show", unit, "--property=LoadState", "--value"))
    return loaded.returncode == 0 and (loaded.stdout or "").strip() == "not-found"


def machine_absent(machine_id):
    """True when the registry lists no such machine and its unit does not exist."""
    listed = run_optional(("machinectl", "list", "--no-legend"))
    if listed.returncode != 0:
        return False
    registered = {line.split()[0] for line in (listed.stdout or "").splitlines() if line.split()}
    unit, _profile = machine_names(machine_id)
    return machine_id not in registered and unit_absent(unit)


GUEST_ABSENCE_PATHS = {
    "marker": ("-e", "/etc/sandbox-native/services.json"),
    "database-socket": ("-S", "/run/mysqld/mysqld.sock"),
}


def guest_path_absent(machine_id, name):
    """True when the guest answered and the named fixed path is not there.

    Only the enumerated paths can be asked about, so nothing caller-controlled
    reaches the guest shell. A non-zero result means the question went
    unanswered, which is never absence.
    """
    flag, path = GUEST_ABSENCE_PATHS[name]
    result = run_optional(guest_command(machine_id, (
        "/bin/sh", "-c", f"test {flag} {path} && echo present || echo absent")))
    return result.returncode == 0 and (result.stdout or "").strip() == "absent"


def guest_marker_absent(machine_id):
    """True when the guest answered and has no service marker at all.

    Provisioning writes the marker when it activates services, so a machine that
    never got that far has none. Reading that as a changed marker made cleanup
    stop at `services` and never reach anything after it.
    """
    return guest_path_absent(machine_id, "marker")


def guest_unit_load_states(machine_id, units):
    """Map each named unit to its LoadState, or None when the guest did not answer.

    `--value` with several units emits bare values with no way to tell which unit
    each belongs to, and an empty value silently shifts the whole list, so one
    unit's state could be read as another's. Asking for Id alongside LoadState
    and parsing per unit block keeps the mapping explicit.
    """
    result = run_optional(guest_command(machine_id, (
        "/usr/bin/systemctl", "show", *tuple(units),
        "--property=Id", "--property=LoadState")))
    if result.returncode != 0:
        return None
    states = {}
    for block in (result.stdout or "").split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id"):
            states[fields["Id"].strip()] = fields.get("LoadState", "").strip()
    return states


def guest_units_absent(machine_id, units):
    """True when the guest answered and knows none of the named units."""
    states = guest_unit_load_states(machine_id, units)
    if states is None:
        return False
    return all(states.get(unit) == "not-found" for unit in tuple(units))


def cleanup_observe(resource, machine_id, policy_digest, resource_digest):
    """Report which resource this is and whether the host still has it.

    Absence and drift are different answers.  A resource provisioning never
    created, or that an earlier cleanup already removed, has nothing left to
    remove and must not stall the run; a resource that exists but no longer
    matches is drift and must.  Every absence below is a successful read that
    found nothing, never a failed read: an unreachable observer still raises.
    """
    _path, policy = applied_policy(machine_id, policy_digest)
    resource_digest = digest_value(resource_digest)
    state = "present"
    if resource == "services":
        # The marker check comes before `service_plan`, which reads the marker
        # and fails when it is missing: a machine whose services were never
        # activated has none, and that is absence, not a changed marker.
        if machine_absent(machine_id) or guest_marker_absent(machine_id):
            state = "absent"
        else:
            _policy, units = service_plan(machine_id, policy_digest, resource_digest)
            if guest_units_absent(machine_id, units):
                state = "absent"
            else:
                services_ownership_status(machine_id, policy_digest, resource_digest)
    elif resource == "database":
        # No socket means no database process, so there are no owned schemas or
        # users to drop: the data itself lives in the machine's image and goes
        # with it. A machine that never reached database bootstrap answers this
        # way, and reading it as an unavailable runtime stalled cleanup here.
        if (machine_absent(machine_id)
                or guest_units_absent(machine_id, ("mariadb.service",))
                or guest_path_absent(machine_id, "database-socket")):
            state = "absent"
        else:
            database_status(machine_id, policy_digest)
            production, tests, user = database_names(machine_id)
            objects = database_objects(machine_id)
            if not objects:
                state = "absent"
            elif objects != sorted((f"db:{production}", f"db:{tests}", f"user:{user}")):
                fail("native database ownership changed")
    elif resource == "machine":
        unit, _profile = machine_names(machine_id)
        if machine_absent(machine_id):
            state = "absent"
        else:
            expected = f"Sandbox native {machine_id} policy {policy_digest}"
            active = run_optional(("systemctl", "is-active", unit))
            observed = run_optional(("machinectl", "show", machine_id))
            if (unit_description(unit) != expected or active.returncode != 0 or
                    (active.stdout or "").strip() != "active" or observed.returncode != 0):
                fail("native machine ownership changed")
    elif resource == "network":
        network = policy["network"]
        table, alias_prefix = network_names(machine_id)
        record = network_state_record(machine_id)
        observed_table = observed_nft_table(table)
        link = observed_link(network["veth"])
        if record is None and observed_table is None and link is None:
            state = "absent"
        elif record is None:
            # A table or interface with no ownership record of ours cannot be
            # proven ours, so it is never touched.
            fail("native network ownership changed")
        else:
            grant_record = installed_grant_record(machine_id, policy_digest) \
                if network.get("grant_authority") == GRANT_AUTHORITY else None
            grants = grant_record["grants"] if grant_record else list(network.get("grants", ()))
            grant_digest = grant_record["grant_digest"] if grant_record else ABSENT_GRANT_DIGEST
            broker = any(not grant["revoked"] for grant in grants)
            desired = desired_network_state(machine_id, policy_digest, network,
                                            broker=broker, grant_digest=grant_digest)
            if not network_record_matches(record, desired):
                fail("native network ownership changed")
            # Verify each piece that exists. An interrupted removal leaves the
            # record behind after the table is gone; that is a network still
            # half-ours to finish removing, not a changed one, and `network-remove`
            # already removes exactly what remains. The host is compared against
            # the rules the policy asks for, so an older record's spelling cannot
            # make an untouched host look drifted.
            if observed_table is not None and (
                    observed_table.get("comment") != record["marker"]
                    or not nft_state_matches_record(observed_nft_state(table), desired)):
                fail("native network ownership changed")
            if link is not None and link.get("ifalias") != alias_prefix:
                fail("native veth ownership changed")
    elif resource in {"mount", "image"}:
        _instance, image, mountpoint = image_paths(machine_id)
        if (not os.path.lexists(image) and not mountpoint.is_symlink()
                and not os.path.ismount(mountpoint)):
            state = "absent"
        elif (mountpoint.is_symlink() or os.path.ismount(mountpoint) or not image.is_file()
                or image.is_symlink() or image.stat().st_uid != 0
                or image.stat().st_mode & 0o077
                or image.stat().st_size != policy["root_image"]["bytes"]):
            fail("native image ownership changed")
    elif resource == "policy":
        # `policy` never reports absent: `applied_policy` above already proved the
        # applied record exists, and `policy-remove` removes that record and the
        # instance root as well as the profile. An absent profile alone is a
        # partially removed policy, so only a profile that is actually there --
        # or a profile state that cannot be read -- is checked for drift.
        destination = APPARMOR_ROOT / f"sandbox-native-{machine_id}"
        if os.path.lexists(destination) or _apparmor_loaded_state(machine_id) is not False:
            expected = compile_apparmor_profile(machine_id, policy_digest).encode()
            if (not destination.is_file() or destination.is_symlink()
                    or destination.stat().st_uid != 0 or destination.read_bytes() != expected
                    or not apparmor_loaded(machine_id)):
                fail("native policy ownership changed")
    else:
        fail("native cleanup resource is invalid")
    print(json.dumps({"machine_id": machine_id, "policy_digest": policy_digest,
                      "resource": resource, "resource_digest": resource_digest,
                      "state": state},
                     sort_keys=True, separators=(",", ":")))


def credential_install(machine_id, policy_digest, name):
    _path, policy = applied_policy(machine_id, policy_digest)
    if not CREDENTIAL_NAME.fullmatch(name):
        fail("native credential name is invalid")
    matches = [ref for ref in policy["credentials"] if ref.rsplit("/", 1)[-1] == name]
    if len(matches) != 1:
        fail("native credential is not declared by policy")
    uid = int(os.environ.get("SUDO_UID", os.getuid()))
    source = INJECTED_ROOT / machine_id / name
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        fail("native credential staging file is unavailable")
    temporary = None
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != uid or
                details.st_mode & 0o077 or not 1 <= details.st_size <= 65536):
            fail("native credential staging file is invalid")
        payload = b""
        while len(payload) <= 65536:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            payload += chunk
        if len(payload) != details.st_size:
            fail("native credential changed during validation")
        run_root = RUNTIME_ROOT
        ensure_root_directory(run_root, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=f"credential-{machine_id}-", dir=run_root)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(payload); output.flush(); os.fsync(output.fileno())
        destination = f"{GUEST_CREDENTIAL_SOURCE_ROOT}/{name}"
        guest_run(machine_id, ("/usr/bin/install", "-d", "-o", "root", "-g", "root",
                               "-m", "0700", GUEST_CREDENTIAL_SOURCE_ROOT),
                  "native credential directory installation failed")
        run_fixed(("machinectl", "copy-to", machine_id, temporary, destination),
                  "native credential installation failed")
        guest_run(machine_id, ("/usr/bin/chown", "root:www-data", destination),
                  "native credential ownership failed")
        guest_run(machine_id, ("/usr/bin/chmod", "0440", destination),
                  "native credential mode failed")
    finally:
        os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def database_names(machine_id):
    suffix = hashlib.sha256(machine_id.encode()).hexdigest()[:16]
    return f"sb_{suffix}", f"sb_{suffix}_tests", f"sbu_{suffix[:12]}"


def database_bootstrap(machine_id, policy_digest):
    _path, policy = applied_policy(machine_id, policy_digest)
    if f"native/{machine_id}/db-credential" not in policy["credentials"]:
        fail("native database credential is not declared")
    production, tests, user = database_names(machine_id)
    try:
        guest_run(machine_id, ("/usr/bin/systemctl", "unmask", "mariadb.service"),
                  "native database service unmask failed")
        guest_run(machine_id, ("/usr/bin/systemctl", "daemon-reload"),
                  "native guest daemon reload failed")
        guest_run(machine_id, ("/usr/bin/systemctl", "start", "mariadb.service"),
                  "native database service start failed", timeout=180)
        guest_run(machine_id, ("/usr/local/libexec/sandbox-db-bootstrap",
                               production, tests, user),
                  "native database bootstrap failed", timeout=180)
    except BaseException:
        sql = (f"DROP DATABASE IF EXISTS {production}; DROP DATABASE IF EXISTS {tests}; "
               f"DROP USER IF EXISTS '{user}'@'localhost'; FLUSH PRIVILEGES;")
        run_optional((*guest_command(machine_id, ()),
                      "/usr/bin/mariadb", "--protocol=socket",
                      "--socket=/run/mysqld/mysqld.sock", "--execute", sql))
        run_optional((*guest_command(machine_id, ()),
                      "/usr/bin/systemctl", "stop", "mariadb.service"))
        run_optional((*guest_command(machine_id, ()),
                      "/usr/bin/systemctl", "mask", "mariadb.service"))
        raise


def database_status(machine_id, policy_digest):
    applied_policy(machine_id, policy_digest)
    guest_run(machine_id, ("/usr/bin/test", "-S", "/run/mysqld/mysqld.sock"),
              "native database socket is unavailable")
    guest_run(machine_id, ("/usr/bin/mariadb-admin", "--protocol=socket",
                           "--socket=/run/mysqld/mysqld.sock", "ping", "--silent"),
              "native database health failed")


def wordpress_bootstrap(machine_id, policy_digest):
    _path, policy = applied_policy(machine_id, policy_digest)
    production, _tests, user = database_names(machine_id)
    proxy = str(ipaddress.ip_interface(policy["network"]["host_address"]).ip)
    guest_run(machine_id, fixed_probe_bwrap(policy, (
                           "/usr/local/libexec/sandbox-wordpress-bootstrap",
                           production, user, proxy, str(BROKER_PORT)),
                           policy["credentials"]),
              "native WordPress bootstrap failed", timeout=300)
    # The bootstrap credential is single-use. WordPress retains only its own
    # instance database setting; plugin/web/CLI payloads never inherit staging.
    guest_run(machine_id, ("/usr/bin/rm", "-f",
                           f"{GUEST_CREDENTIAL_SOURCE_ROOT}/db-credential"),
              "native bootstrap credential cleanup failed")


def wordpress_status(machine_id, policy_digest):
    applied_policy(machine_id, policy_digest)
    guest_run(machine_id, ("/usr/local/bin/wp", "core", "is-installed",
                           "--path=/var/www/html", "--quiet"),
              "native WordPress status failed")


def database_remove(machine_id, policy_digest):
    applied_policy(machine_id, policy_digest)
    production, tests, user = database_names(machine_id)
    sql = (f"DROP DATABASE IF EXISTS {production}; DROP DATABASE IF EXISTS {tests}; "
           f"DROP USER IF EXISTS '{user}'@'localhost'; FLUSH PRIVILEGES;")
    try:
        guest_run(machine_id, ("/usr/bin/systemctl", "unmask", "mariadb.service"),
                  "native database service unmask failed")
        guest_run(machine_id, ("/usr/bin/systemctl", "daemon-reload"),
                  "native guest daemon reload failed")
        guest_run(machine_id, ("/usr/bin/systemctl", "start", "mariadb.service"),
                  "native database service start failed", timeout=180)
        guest_run(machine_id, ("/usr/bin/mariadb", "--protocol=socket",
                               "--socket=/run/mysqld/mysqld.sock", "--execute", sql),
                  "native database cleanup failed")
    finally:
        run_optional((*guest_command(machine_id, ()),
                      "/usr/bin/systemctl", "stop", "mariadb.service"))
        run_optional((*guest_command(machine_id, ()),
                      "/usr/bin/systemctl", "mask", "mariadb.service"))


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
  # `/**` matches paths BELOW the root, never the root directory itself, so
  # systemd-nspawn was denied `open /` while pinning the outer mount namespace
  # and every machine failed to start. Read access to the directory entry is
  # not access to its contents, which `/**` already governs.
  / r,
  /** rwklm,
  /** ix,
  /usr/lib/systemd/systemd cx -> guest,
  /lib/systemd/systemd cx -> guest,
  /sbin/init cx -> guest,

  profile guest flags=(attach_disconnected,mediate_deleted) {{
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
    # The machine's PID 1 needs sys_admin for the typed API-filesystem mounts
    # enumerated below, and nothing else in this profile grants a mount
    # primitive. Every service that runs untrusted code strips the capability
    # in its own unit (CapabilityBoundingSet), and exec payloads transition into
    # the payload profile, which denies it outright (FR-044).
    capability sys_admin,
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    dbus,
    # Read-only ptrace confined to this machine's own processes (systemd's
    # generators read sibling /proc entries, and PID 1 reads its children's).
    # Three things were wrong here and each one alone breaks the guest:
    #   * the emitted peer was `@sandbox-native-<id>` -- an AppArmor variable
    #     reference to a variable that does not exist, so it matched nothing;
    #   * the peer must name the CHILD profile, which is what confined
    #     processes actually run under;
    #   * the kernel checks `read` on the reader and `readby` on the target,
    #     so granting only `read` still denies half of every pair.
    # A denial here breaks systemd's session bookkeeping, and `machinectl
    # shell` then exits 0 with no output at all. A blanket `ptrace,` would
    # reach outside the machine and is deliberately absent.
    ptrace (read, readby) peer={profile}//guest,
    ptrace (read, readby) peer={profile},
    # The guest's PID 1 mounts its own API filesystems inside the machine's
    # mount namespace; without them it dies with "Failed to mount tmpfs ...
    # Permission denied" before any service starts. These are enumerated by
    # type and target on purpose: the guest must not hold a general mount
    # primitive, which stays with the bwrap profile.
    mount fstype=tmpfs -> /run/lock/,
    mount fstype=tmpfs -> /dev/shm/,
    mount fstype=tmpfs -> /tmp/,
    mount fstype=cgroup2 -> /sys/fs/cgroup/,
    mount fstype=mqueue -> /dev/mqueue/,
    # systemd-logind binds the machine's own root aside while it sets up its
    # private mounts, and the credential generator needs a ramfs for secrets
    # that must never reach disk. Both stay inside the machine's namespace.
    mount options=(rw,rbind) -> /run/systemd/mount-rootfs/,
    mount options=(rw,rbind) -> /run/systemd/mount-rootfs/**,
    mount fstype=ramfs -> /dev/shm/,
    # systemd-logind sets up a private namespace per session, and machinectl
    # shell goes through logind: without these its sessions fail intermittently
    # and every observation through them returns empty output, which reads as a
    # broken probe rather than a denied mount.
    mount fstype=proc -> /run/systemd/namespace-*/,
    mount fstype=proc -> /run/systemd/mount-rootfs/**,
    mount options=(rw,rbind) -> /run/systemd/namespace-*/,
    umount /run/systemd/mount-rootfs/**,
    umount /run/systemd/namespace-*/,
    mount options=(rw,remount) -> /run/lock/,
    # Propagation changes only: no filesystem is attached, and the machine's
    # own init needs them during early boot (`(sd-gens)` makes / rslave).
    mount options=(rw,rslave),
    mount options=(rw,rprivate),
    mount options=(rw,rshared),
    mount options=(rw,runbindable),
    # Remounting an existing bind read-only only ever removes access. Flag
    # sets are matched exactly, so every combination the guest's generators
    # actually use is listed: /dev/pts/ arrives without `nodev`, and / arrives
    # with `nodev` but without `nosuid,noexec`.
    mount options=(ro,remount,bind),
    mount options=(ro,remount,bind,nodev),
    mount options=(ro,remount,bind,nosuid,noexec),
    mount options=(ro,remount,bind,nosuid,nodev,noexec),
    umount /run/lock/,
    umount /dev/shm/,
    umount /tmp/,
    / r,
    /** rwklm,
    # `cx` names a child of the CURRENT profile, so the kernel looked for
    # `guest//bwrap` and refused the exec with "profile transition not found".
    # The bwrap profile is a sibling child of the top-level profile, so it has
    # to be addressed by its full name.
    /usr/bin/bwrap px -> {profile}//bwrap,
    /** ix,
  }}

  # Only root can execute /usr/bin/bwrap in the managed image (0750). This
  # transition owns the narrowly-scoped namespace/mount setup, then every
  # command exec transitions irreversibly into the payload profile.
  profile bwrap flags=(attach_disconnected,mediate_deleted) {{
    capability chown,
    capability dac_override,
    capability fowner,
    capability setgid,
    capability setuid,
    capability sys_admin,
    capability sys_chroot,
    # bwrap drops the bounding set itself (setpcap) and reads its child's
    # /proc entry while wiring the sandbox up (sys_ptrace). Without them it
    # fails part-way through, and the symptom surfaces as an unrelated
    # "Can't mount proc on /newroot/proc: Operation not permitted". This is
    # the trusted root-only setup step; the payload profile below denies both.
    capability setpcap,
    capability sys_ptrace,
    userns,
    mount,
    remount,
    umount,
    pivot_root,
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    # bwrap reads its own child's /proc entry while wiring the sandbox up, and
    # the kernel checks both directions. Without this the setup fails only when
    # a fresh procfs is mounted, which made it look like a mount problem.
    ptrace (read, readby) peer={profile}//bwrap,
    # Entering the payload profile is a STACK at the final exec, not a domain
    # transition, so this profile must be allowed to stack onto itself. AppArmor
    # 4 rejects every scoped form of the rule (`change_profile -> &<target>` does
    # not load), so it is unqualified. That is an accepted trade, not an
    # oversight: bwrap is the trusted root-only setup step (mode 0750) that
    # already holds sys_admin, mount and userns, so the rule reaches nothing an
    # attacker could not already reach here, and under NoNewPrivileges a change
    # can only narrow. Recorded in contracts/managed-isolation.md (FR-047).
    change_profile,
    # `/**` matches paths BELOW the root, never the root directory entry, so
    # bwrap was denied `open /` while binding it as the sandbox root and every
    # payload died before it started. Read access to the entry is not access to
    # its contents, which `/**` already governs. (Same defect as the supervisor
    # profile's `/ r,`; it was fixed there and missed here.)
    / r,
    /** rwklm,
    # Inherit, do not transition. Three transition forms were tried on Ubuntu
    # 24.04 / AppArmor 4 and all three were refused, from audit records:
    #   `cx -> payload`              -> "profile transition not found"
    #                                   (`cx` names a child of THIS profile)
    #   `px -> <full>//payload`      -> "no new privs": bwrap sets NNP before
    #                                   exec and the kernel refuses the domain
    #                                   transition
    #   `px -> <full>//&payload`     -> "profile transition not found"
    # Worse, with ANY `px` rule present every exec inside bwrap is refused under
    # NNP -- including /bin/sh, before the payload can run at all. The payload
    # therefore inherits this profile and stacks its own at the final exec
    # (FR-047), which yields the intersection of both and cannot be unstacked
    # because the payload profile grants no change_profile.
    /** ix,
  }}

  profile payload flags=(attach_disconnected,mediate_deleted) {{
    #include <abstractions/base>
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    # The root directory entry, for the same reason as the bwrap profile. The
    # payload's access to everything below it is governed by the rules here.
    / r,
    /** rwklm,
    # The payload must not create a user namespace of its own. bwrap's own
    # mechanism for this (`--disable-userns`) writes /proc/sys, which nspawn
    # mounts read-only, so it can never succeed inside a machine. Ubuntu 24.04
    # mediates unprivileged userns creation through AppArmor, so the rule is
    # stated here; FR-046 requires it to be measured effective and a seccomp
    # filter added if it is not.
    deny userns create,
    deny userns,
    /run/credentials/sandbox/* r,
    deny /run/credentials/** wklmx,
    deny /run/sandbox-native-credentials/** rwklmx,
    deny /run/systemd/** rwklmx,
    deny /run/dbus/** rwklmx,
    /** ix,
  }}
}}
"""


def _apparmor_loaded_state(machine_id):
    profile = f"sandbox-native-{machine_id}"
    try:
        names = {line.split(" ", 1)[0]
                 for line in Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines()}
        return all(candidate in names for candidate in (
            profile, profile + "//guest", profile + "//bwrap", profile + "//payload"))
    except OSError:
        return None


def apparmor_loaded(machine_id):
    return _apparmor_loaded_state(machine_id) is True


def remove_exact_apparmor_profile(machine_id, digest):
    destination = APPARMOR_ROOT / f"sandbox-native-{machine_id}"
    state = _apparmor_loaded_state(machine_id)
    if not os.path.lexists(destination):
        if state is False:
            return
        fail("native AppArmor profile file is missing")
    payload = compile_apparmor_profile(machine_id, digest).encode()
    exact_privileged_file(destination, payload)
    if state is None:
        fail("native AppArmor profile state is unavailable")
    if state is True:
        run_fixed(("apparmor_parser", "--remove", "--skip-cache", str(destination)),
                  "native AppArmor profile removal failed")
        if _apparmor_loaded_state(machine_id) is not False:
            fail("native AppArmor profile removal was not observed")
    destination.unlink()


def apparmor_install(machine_id, digest):
    _path, _policy = applied_policy(machine_id, digest)
    destination = APPARMOR_ROOT / f"sandbox-native-{machine_id}"
    payload = compile_apparmor_profile(machine_id, digest).encode()
    created = False
    if destination.exists() or destination.is_symlink():
        details = destination.lstat()
        if (stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or
                details.st_uid != 0 or destination.read_bytes() != payload):
            fail("native AppArmor profile ownership changed")
    else:
        try:
            atomic_install_bytes(payload, destination)
            created = True
        except BaseException:
            if os.path.lexists(destination):
                try:
                    if exact_privileged_file(destination, payload): destination.unlink()
                except (OSError, SystemExit):
                    pass
            raise
    try:
        run_fixed(("apparmor_parser", "--replace", "--skip-cache", str(destination)),
                  "native AppArmor profile load failed")
        if not apparmor_loaded(machine_id): fail("native AppArmor profiles were not observed")
    except BaseException:
        if created:
            try:
                state = _apparmor_loaded_state(machine_id)
                if state is True:
                    run_optional((
                        "apparmor_parser", "--remove", "--skip-cache", str(destination),
                    ))
                    state = _apparmor_loaded_state(machine_id)
                if (state is False and exact_privileged_file(destination, payload)):
                    destination.unlink()
            except (OSError, SystemExit):
                pass
        raise


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
    remove_exact_apparmor_profile(machine_id, digest)


def unit_description(unit):
    loaded = run_optional(("systemctl", "show", unit, "--property=LoadState", "--value"))
    if loaded.returncode != 0 or (loaded.stdout or "").strip() in {"", "not-found"}:
        return ""
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
        # NoNewPrivileges is applied by the guest's own service units (FR-043):
        # on the machine unit it makes the kernel refuse the AppArmor transition
        # into the tighter //guest profile, so the guest init cannot exec.
        "LockPersonality=yes", "RestrictSUIDSGID=yes",
        f"AppArmorProfile={profile}", f"CPUQuota={resources['cpu_percent']}%",
        f"MemoryMax={resources['memory_bytes']}",
        f"MemoryHigh={max(1, resources['memory_bytes'] * 9 // 10)}", "MemorySwapMax=0",
        f"TasksMax={resources['pids']}",
        f"LimitNOFILE={resources['fds']}", f"IOWeight={resources['io_weight']}",
    )
    # CAP_SYS_ADMIN stays INSIDE the machine's private user namespace: systemd as
    # PID 1 needs it to mount its API filesystems, and namespaced it cannot act
    # on the host. Untrusted code still never holds it -- the AppArmor payload
    # profile denies mount/userns/sys_admin, and every transient exec payload
    # runs with NoNewPrivileges and ProtectSystem=strict (FR-043).
    dropped = ("CAP_AUDIT_CONTROL", "CAP_DAC_READ_SEARCH", "CAP_IPC_OWNER", "CAP_LEASE",
               "CAP_LINUX_IMMUTABLE", "CAP_MKNOD", "CAP_NET_ADMIN", "CAP_NET_BROADCAST",
               "CAP_NET_RAW", "CAP_SYS_BOOT", "CAP_SYS_NICE",
               "CAP_SYS_PTRACE", "CAP_SYS_RESOURCE", "CAP_SYS_TTY_CONFIG")
    nspawn = ["/usr/bin/systemd-nspawn", "--quiet", "--boot", "--keep-unit",
              "--register=yes", "--settings=no", f"--machine={machine_id}",
              f"--image={policy['root_image']['path']}",
              f"--private-users={policy['uid_map']['base']}:{policy['uid_map']['count']}",
              # `map` (idmapped mount) is the working path: with `chown` the
              # supervisor cannot adjust the OS tree's UID/GID shift on this
              # host ("Operation not permitted"), while `map` boots the guest
              # init far enough to mount its own API filesystems.
              "--private-users-ownership=map",
              "--private-network",
              f"--network-veth-extra={policy['network']['veth']}:host0",
              "--resolv-conf=off",
              # systemd-nspawn accepts no|host|try-host|guest|try-guest|auto.
              # "no-host" is not one of them, so every machine failed to start
              # with "Failed to parse link journal mode no-host".
              # NoNewPrivileges is applied by the guest's own service units. On
              # the machine it makes the kernel refuse the AppArmor transition
              # into the `//guest` subprofile ("exec ... info=no new privs"), so
              # the guest init could never start and the tighter profile — the
              # stronger control — would have to be abandoned to keep the flag.
              "--link-journal=no",
              "--drop-capability=" + ",".join(dropped),
              # @system-service does not include the mount syscalls, and the
              # machine's init must mount its API filesystems inside its own
              # namespace (FR-044). AppArmor still denies mount to the guest and
              # payload profiles, so this widens the syscall set without giving
              # untrusted code a mount primitive.
              "--system-call-filter=@system-service @mount ~@raw-io ~@reboot ~@swap"]
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
            break
        time.sleep(0.1)
    else:
        fail("native machine did not expose its owned unit and veth")
    # "Started" must mean "usable": every later probe runs a transient unit
    # inside the guest, which needs the guest's system bus. Without this wait
    # provisioning raced the guest's boot and failed with "There is no system
    # bus in container ...".
    for _attempt in range(300):
        probe = run_optional((*guest_command(machine_id, ()),
                              "/bin/true"))
        if probe.returncode == 0:
            return
        if unit_description(unit) != description:
            fail("native machine exited before its system bus was available")
        time.sleep(0.2)
    fail("native machine did not expose a system bus")


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


def installed_helper_ready():
    try:
        details = INSTALL_PATH.lstat()
    except OSError:
        return False
    return bool(stat.S_ISREG(details.st_mode) and details.st_uid == 0 and
                not details.st_mode & 0o022 and os.access(INSTALL_PATH, os.X_OK))


def ipv6_default_route(row):
    """True only for a usable IPv6 default route in /proc/net/ipv6_route.

    A fresh network namespace always carries kernel-installed UNREACHABLE ::/0
    entries on lo. They are the ABSENCE of IPv6 connectivity, and counting them
    as a default route failed the private-network isolation gate on every modern
    kernel, which blocked the managed runtime outright.
    """
    columns = row.split()
    if len(columns) < 9 or columns[0] != "0" * 32 or columns[1] != "00":
        return False
    try:
        flags = int(columns[8], 16)
    except ValueError:
        return False
    return bool(flags & 0x1) and not flags & 0x0200


def _probe_child_private_network(_token):
    try:
        links = sorted(path.name for path in Path("/sys/class/net").iterdir())
        routes = Path("/proc/net/route").read_text().splitlines()[1:]
        ipv6_routes = Path("/proc/net/ipv6_route").read_text().splitlines()
    except OSError:
        raise SystemExit(69)
    has_ipv4_default = any(len(row.split()) >= 4 and row.split()[1] == "00000000" and
                           int(row.split()[3], 16) & 1 for row in routes)
    has_ipv6_default = any(ipv6_default_route(row) for row in ipv6_routes)
    if links != ["lo"] or has_ipv4_default or has_ipv6_default:
        raise SystemExit(69)


def _probe_child_nftables(token):
    table = f"sb_probe_{token}"
    marker = f"sandbox-native:preflight:{token}"
    if run_optional(("nft", "-j", "list", "table", "inet", table)).returncode == 0:
        raise SystemExit(73)
    if run_optional(("ip", "link", "set", "dev", "lo", "up")).returncode != 0:
        raise SystemExit(69)
    created = False
    script = "\n".join((
        f'add table inet {table} {{ comment "{marker}"; }}',
        f"add chain inet {table} probe {{ type filter hook output priority filter; policy accept; }}",
        f'add rule inet {table} probe oifname "lo" udp dport 9 counter drop comment "{marker}:drop"',
    )) + "\n"
    try:
        applied = run_optional(("nft", "-f", "-"), input_text=script)
        if applied.returncode != 0:
            raise SystemExit(69)
        created = True
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                probe_socket.sendto(b"sandbox-native-preflight", ("127.0.0.1", 9))
            except OSError as exc:
                # A locally generated packet rejected by an nft output verdict
                # commonly reports EPERM/EACCES to sendto().  That rejection is
                # the effect being proven; all other socket errors still fail.
                if exc.errno not in {errno.EPERM, errno.EACCES}:
                    raise SystemExit(69) from None
        finally:
            probe_socket.close()
        observed = run_optional(("nft", "-j", "list", "table", "inet", table))
        if observed.returncode != 0:
            raise SystemExit(69)
        try:
            document = json.loads(observed.stdout or "{}")
        except json.JSONDecodeError:
            raise SystemExit(69) from None
        rows = document.get("nftables") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise SystemExit(69)
        table_rows = [item["table"] for item in rows if isinstance(item, dict) and
                      isinstance(item.get("table"), dict)]
        chain_rows = [item["chain"] for item in rows if isinstance(item, dict) and
                      isinstance(item.get("chain"), dict)]
        rule_rows = [item["rule"] for item in rows if isinstance(item, dict) and
                     isinstance(item.get("rule"), dict)]
        if (len(table_rows) != 1 or table_rows[0].get("family") != "inet" or
                table_rows[0].get("name") != table or
                table_rows[0].get("comment") != marker or
                len(chain_rows) != 1 or chain_rows[0].get("name") != "probe" or
                chain_rows[0].get("type") != "filter" or
                chain_rows[0].get("hook") != "output" or
                chain_rows[0].get("policy") != "accept" or len(rule_rows) != 1 or
                rule_rows[0].get("comment") != marker + ":drop"):
            raise SystemExit(69)
        expressions = rule_rows[0].get("expr")
        if (not isinstance(expressions, list) or
                not any(isinstance(item, dict) and "counter" in item
                        and isinstance(item["counter"], dict) and
                        item["counter"].get("packets", 0) >= 1 and
                        item["counter"].get("bytes", 0) >= 1 for item in expressions) or
                not any(isinstance(item, dict) and "drop" in item
                        for item in expressions)):
            raise SystemExit(69)
    finally:
        if created:
            ownership = run_optional(("nft", "-j", "list", "table", "inet", table))
            owned = False
            if ownership.returncode == 0:
                try:
                    value = json.loads(ownership.stdout or "{}")
                    owned = any(isinstance(item, dict) and
                                isinstance(item.get("table"), dict) and
                                item["table"].get("comment") == marker
                                for item in value.get("nftables", ()))
                except (AttributeError, json.JSONDecodeError):
                    owned = False
            if not owned:
                raise SystemExit(73)
            if run_optional(("nft", "delete", "table", "inet", table)).returncode != 0:
                raise SystemExit(69)


def _probe_child_cgroup_delegation(token):
    try:
        rows = Path("/proc/self/cgroup").read_text().splitlines()
        if len(rows) != 1 or not rows[0].startswith("0::/"):
            raise SystemExit(69)
        raw_relative = rows[0][3:]
        if not raw_relative.startswith("/"):
            raise SystemExit(69)
        relative = PurePosixPath(raw_relative.lstrip("/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit(69)
        base = Path("/sys/fs/cgroup").joinpath(*relative.parts)
        child = base / f"sb-probe-{token}"
        if child.exists() or child.is_symlink():
            raise SystemExit(73)
        child.mkdir(mode=0o755)
        details = child.lstat()
        if not stat.S_ISDIR(details.st_mode):
            raise SystemExit(69)
    except OSError:
        raise SystemExit(69) from None
    finally:
        try:
            if "child" in locals() and child.exists() and not child.is_symlink():
                child.rmdir()
        except OSError:
            raise SystemExit(69) from None


def _probe_child_seccomp(_token):
    try:
        values = parse_status_fields(Path("/proc/self/status").read_text())
    except OSError:
        raise SystemExit(69) from None
    if values.get("NoNewPrivs") != "1" or values.get("Seccomp") != "2":
        raise SystemExit(69)


def preflight_probe(probe):
    """Run one bounded kernel-effect probe through the installed helper."""
    if probe not in {"private-network", "nftables", "cgroup-delegation", "seccomp"}:
        return {"ok": False, "probe": str(probe)[:32], "state": "invalid"}
    if not installed_helper_ready():
        return {"ok": False, "probe": probe, "state": "helper_unavailable"}
    token = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    marker = f"Sandbox native preflight {token}"
    suffix = "scope" if probe == "cgroup-delegation" else "service"
    unit = f"sandbox-native-probe-{token}.{suffix}"
    command = None
    if probe == "nftables":
        command = ("unshare", "--net", "--", str(INSTALL_PATH),
                   "_preflight-child", probe, token)
    else:
        existing = run_optional(("systemctl", "show", unit, "--property=LoadState",
                                 "--value"))
        if (existing.returncode == 0 and
                (existing.stdout or "").strip() not in {"", "not-found"}):
            return {"ok": False, "probe": probe, "state": "collision"}
        command = ["systemd-run", "--quiet", "--collect",
                   f"--unit={unit}", f"--description={marker}"]
        if probe == "private-network":
            command.extend(("--wait", "--property=PrivateNetwork=yes",
                            "--property=IPAddressDeny=any"))
        elif probe == "seccomp":
            command.extend(("--wait", "--property=NoNewPrivileges=yes",
                            "--property=SystemCallFilter=@system-service"))
        else:
            # A scope runs synchronously in the caller's context, and systemd
            # refuses `--wait` with `--scope` outright ("--wait may not be
            # combined with --scope"). Passing both made the cgroup-delegation
            # gate impossible to satisfy on any host.
            command.extend(("--scope", "--property=Delegate=yes"))
        command.extend((str(INSTALL_PATH), "_preflight-child", probe, token))
        command = tuple(command)
    state = "failed"
    try:
        try:
            result = run_optional(command)
            state = "ready" if result.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            state = "timeout"
    finally:
        if probe != "nftables":
            description = unit_description(unit)
            if description == marker:
                run_optional(("systemctl", "stop", unit))
                run_optional(("systemctl", "reset-failed", unit))
            elif description:
                state = "collision"
    return {"ok": state == "ready", "probe": probe, "state": state}


def preflight_child(probe, token):
    if not PROBE_TOKEN.fullmatch(token):
        raise SystemExit(64)
    {"private-network": _probe_child_private_network,
     "nftables": _probe_child_nftables,
     "cgroup-delegation": _probe_child_cgroup_delegation,
     "seccomp": _probe_child_seccomp}[probe](token)


def policy_remove(machine_id, digest):
    path = POLICY_ROOT / f"{machine_id}.json"
    owner_path = policy_owner_path(machine_id)
    policy = None
    if os.path.lexists(path):
        path, policy = checked_policy(path, machine_id, applied=True)
        if policy["digest"] != digest: fail("applied policy digest changed")
        if os.path.lexists(owner_path):
            read_policy_owner(machine_id, policy)
        else:
            project_source_identity(policy, invoking_uid())
    elif os.path.lexists(owner_path):
        read_partial_policy_owner(machine_id, digest)
    else:
        return
    unit, _profile = machine_names(machine_id)
    instance, image, mountpoint = image_paths(machine_id)
    egress = egress_config_record(machine_id)
    grants = installed_grant_record(machine_id, digest)
    network = network_state_record(machine_id)
    if (unit_description(unit) or os.path.ismount(mountpoint) or image.exists()
            or network is not None or egress is not None or grants is not None):
        fail("native policy still owns runtime resources")
    if os.path.lexists(APPARMOR_ROOT / f"sandbox-native-{machine_id}") \
            or _apparmor_loaded_state(machine_id) is not False:
        remove_exact_apparmor_profile(machine_id, digest)
    if instance.exists():
        try: instance.rmdir()
        except OSError: fail("native instance root is not empty")
    # Owner-first deletion makes an interrupted removal leave a policy-only
    # state that remains attributable through the policy's scoped source.
    if os.path.lexists(owner_path):
        try: owner_path.unlink()
        except OSError: fail("native policy owner removal failed")
    if os.path.lexists(path):
        try: path.unlink()
        except OSError: fail("native policy removal failed")


def _foreign_tree_digest(root):
    """Hash one fixed config tree without returning file contents or timestamps."""
    root = Path(root)
    if not os.path.lexists(root):
        return {"present": False, "digest": "absent"}
    digest = hashlib.sha256()
    paths = (root, *sorted(root.rglob("*"))) \
        if root.is_dir() and not root.is_symlink() else (root,)
    count = 0
    for path in paths:
        details = path.lstat()
        relative = str(path.relative_to(root.parent))
        row = {
            "path": relative, "type": stat.S_IFMT(details.st_mode),
            "mode": stat.S_IMODE(details.st_mode), "uid": details.st_uid,
            "gid": details.st_gid, "size": details.st_size,
        }
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
        if stat.S_ISLNK(details.st_mode):
            digest.update(os.readlink(path).encode())
        elif stat.S_ISREG(details.st_mode):
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
        count += 1
    return {"present": True, "entries": count, "digest": digest.hexdigest()}


def _foreign_mount_identity(path):
    path = Path(path).resolve(strict=True)
    selected = None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if "-" not in fields or len(fields) < 10:
            continue
        separator = fields.index("-")
        mountpoint = Path(fields[4].replace("\\040", " "))
        try:
            path.relative_to(mountpoint)
        except ValueError:
            continue
        if selected is None or len(mountpoint.parts) > len(selected[0].parts):
            selected = (mountpoint, fields, separator)
    if selected is None:
        fail("foreign data mount identity is unavailable")
    mountpoint, fields, separator = selected
    return {
        "mountpoint": str(mountpoint), "root": fields[3],
        "options": sorted(fields[5].split(",")), "fstype": fields[separator + 1],
        "source": fields[separator + 2],
    }


def _foreign_data_identity():
    root = Path("/var/lib/mysql")
    if not os.path.lexists(root):
        return {"present": False, "sentinel": {"present": False}}
    details = root.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        fail("foreign database data root is unsafe")
    sentinel = {"present": False}
    if os.path.lexists(FOREIGN_DATA_SENTINEL):
        item = FOREIGN_DATA_SENTINEL.lstat()
        if (not stat.S_ISREG(item.st_mode) or stat.S_ISLNK(item.st_mode)
                or item.st_uid != 0 or item.st_gid != 0
                or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1):
            fail("foreign database sentinel is unsafe")
        sentinel = {
            "present": True, "device": item.st_dev, "inode": item.st_ino,
            "uid": item.st_uid, "gid": item.st_gid,
            "mode": stat.S_IMODE(item.st_mode), "size": item.st_size,
            "sha256": hashlib.sha256(FOREIGN_DATA_SENTINEL.read_bytes()).hexdigest(),
        }
    return {
        "present": True, "device": details.st_dev, "inode": details.st_ino,
        "uid": details.st_uid, "gid": details.st_gid,
        "mode": stat.S_IMODE(details.st_mode),
        "mount": _foreign_mount_identity(root), "sentinel": sentinel,
    }


def _foreign_unit(unit, package):
    result = run_optional((
        "systemctl", "show", unit, "--no-pager",
        "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath,MainPID",
    ))
    values = {line.split("=", 1)[0]: line.split("=", 1)[1]
              for line in (result.stdout or "").splitlines() if "=" in line}
    fragment = values.get("FragmentPath", "")
    if fragment:
        try: fragment = str(Path(fragment).resolve(strict=True))
        except OSError: fragment = "unavailable"
    pid = int(values.get("MainPID", "0")) if values.get("MainPID", "0").isdigit() else 0
    process = {"present": False}
    if pid > 0:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().split()
            executable = Path(f"/proc/{pid}/exe").resolve(strict=True)
            executable_stat = executable.stat()
            process = {
                "present": True, "pid": pid, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "starttime": fields[21], "exe": str(executable),
                "exe_device": executable_stat.st_dev, "exe_inode": executable_stat.st_ino,
            }
        except (OSError, IndexError):
            fail("foreign service process identity changed during observation")
    package_result = run_optional(("dpkg-query", "-W", "-f=${Version}", package))
    return {
        "LoadState": values.get("LoadState", "unknown"),
        "ActiveState": values.get("ActiveState", "unknown"),
        "SubState": values.get("SubState", "unknown"),
        "UnitFileState": values.get("UnitFileState", "unknown"),
        "FragmentPath": fragment, "process": process,
        "package": package,
        "package_version": ((package_result.stdout or "").strip()
                            if package_result.returncode == 0 else "absent"),
    }


def _foreign_listeners():
    result = run_optional(("ss", "-H", "-ltnpe"))
    if result.returncode != 0:
        fail("foreign listener observation failed")
    rows = []
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        local = fields[3]
        match = re.search(r":([0-9]+)$", local)
        if not match or int(match.group(1)) not in {80, 443, 3306, 9000}:
            continue
        port = int(match.group(1)); address = local[:match.start()]
        address = address.strip("[]")
        inode = re.search(r"\bino:([0-9]+)\b", line)
        pid = re.search(r"\bpid=([0-9]+)\b", line)
        owner_uid = None
        if pid:
            try: owner_uid = Path(f"/proc/{pid.group(1)}").stat().st_uid
            except OSError: fail("foreign listener owner changed during observation")
        health_address = "::1" if ":" in address else "127.0.0.1"
        family = socket.AF_INET6 if ":" in health_address else socket.AF_INET
        probe = socket.socket(family, socket.SOCK_STREAM)
        probe.settimeout(0.25)
        try: healthy = probe.connect_ex((health_address, port)) == 0
        finally: probe.close()
        rows.append({
            "protocol": "tcp", "address": address, "port": port,
            "inode": int(inode.group(1)) if inode else None,
            "pid": int(pid.group(1)) if pid else None, "uid": owner_uid,
            "healthy": healthy,
        })
    return sorted(rows, key=lambda row: (row["port"], row["address"], row["inode"] or -1))


def host_baseline_observe():
    """Emit a content-free, stable snapshot of fixed foreign host services."""
    units = {
        "nginx.service": _foreign_unit("nginx.service", "nginx"),
        "apache2.service": _foreign_unit("apache2.service", "apache2"),
        "mariadb.service": _foreign_unit("mariadb.service", "mariadb-server"),
        "mysql.service": _foreign_unit("mysql.service", "mysql-server"),
        "php8.3-fpm.service": _foreign_unit("php8.3-fpm.service", "php8.3-fpm"),
    }
    baseline = {
        "schema": "sandbox.native-host-baseline/v1", "units": units,
        "listeners": _foreign_listeners(),
        "config": {root: _foreign_tree_digest(root) for root in (
            "/etc/nginx", "/etc/apache2", "/etc/mysql", "/etc/php/8.3/fpm",
        )},
        "data": _foreign_data_identity(),
    }
    digest = hashlib.sha256(json.dumps(
        baseline, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    print(json.dumps({"ok": True, "digest": digest, "baseline": baseline},
                     sort_keys=True, separators=(",", ":")))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sandbox-native-helper")
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check-policy"); check.add_argument("machine"); check.add_argument("path")
    install = sub.add_parser("install")
    host_apply = sub.add_parser("host-packages-apply")
    host_apply.add_argument("path"); host_apply.add_argument("digest")
    sub.add_parser("host-baseline-observe")
    bootstrap = sub.add_parser("image-bootstrap")
    bootstrap.add_argument("machine"); bootstrap.add_argument("policy_digest")
    bootstrap.add_argument("plan_path"); bootstrap.add_argument("plan_digest")
    bootstrap.add_argument("web_server", choices=("nginx", "apache"))
    configure = sub.add_parser("image-configure")
    configure.add_argument("machine"); configure.add_argument("policy_digest")
    configure.add_argument("web_server", choices=("nginx", "apache"))
    configure.add_argument("service_digest")
    apply_policy = sub.add_parser("policy-install"); apply_policy.add_argument("machine"); apply_policy.add_argument("path")
    grants = sub.add_parser("grant-reconcile")
    grants.add_argument("machine"); grants.add_argument("base_policy_digest")
    grants.add_argument("expected_grant_digest"); grants.add_argument("desired_grant_digest")
    status = sub.add_parser("policy-status"); status.add_argument("machine")
    remove_policy = sub.add_parser("policy-remove")
    remove_policy.add_argument("machine"); remove_policy.add_argument("digest")
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("machine"); execute_parser.add_argument("digest")
    execute_parser.add_argument("request_digest")
    observe_parser = sub.add_parser("isolation-observe")
    observe_parser.add_argument("machine")
    cleanup_parser = sub.add_parser("cleanup-observe")
    cleanup_parser.add_argument("resource", choices=(
        "services", "database", "machine", "network", "mount", "image", "policy"))
    cleanup_parser.add_argument("machine"); cleanup_parser.add_argument("digest")
    cleanup_parser.add_argument("resource_digest")
    probe = sub.add_parser("preflight-probe")
    probe.add_argument("probe", choices=("private-network", "nftables",
                                         "cgroup-delegation", "seccomp"))
    probe_child = sub.add_parser("_preflight-child")
    probe_child.add_argument("probe", choices=("private-network", "nftables",
                                               "cgroup-delegation", "seccomp"))
    probe_child.add_argument("token")
    for name in ("image-create", "image-mount", "image-unmount", "image-remove",
                 "network-apply", "network-grants-apply", "network-status", "network-remove",
                 "egress-apply", "egress-status", "egress-remove",
                 "machine-start-minimal", "machine-status", "machine-stop",
                 "apparmor-install", "apparmor-status", "apparmor-remove",
                 "database-bootstrap", "database-status", "database-remove",
                 "wordpress-bootstrap", "wordpress-status"):
        action = sub.add_parser(name); action.add_argument("machine"); action.add_argument("digest")
    credential = sub.add_parser("credential-install")
    credential.add_argument("machine"); credential.add_argument("digest"); credential.add_argument("name")
    for name in ("services-activate", "services-health", "services-status", "services-stop"):
        action = sub.add_parser(name); action.add_argument("machine"); action.add_argument("digest")
        action.add_argument("service_digest")
    args = parser.parse_args(argv)
    if args.verb == "check-policy":
        identity = machine(args.machine); checked_policy(args.path, identity); print("policy-ok")
    elif args.verb == "install":
        require_root()
        owner_uid = invoking_uid()
        ensure_root_directory(Path("/var/lib/sandbox"), 0o755)
        ensure_root_directory(Path("/var/lib/sandbox/native"), 0o755)
        ensure_root_directory(STAGING_ROOT, 0o1777)
        ensure_user_directory(INJECTED_ROOT, int(os.environ.get("SUDO_UID", os.getuid())))
        ensure_root_directory(Path("/etc/sandbox"), 0o755)
        ensure_root_directory(POLICY_ROOT, 0o755)
        ensure_root_directory(POLICY_OWNER_ROOT, 0o755)
        ensure_root_directory(NETWORK_STATE_ROOT, 0o755)
        ensure_root_directory(EGRESS_ROOT, 0o755)
        ensure_root_directory(GRANT_ROOT, 0o755)
        ensure_root_directory(GRANT_LOCK_ROOT, 0o755)
        atomic_install(Path(__file__).resolve(), INSTALL_PATH)
        os.chmod(INSTALL_PATH, 0o755)
        broker_source = BROKER_SOURCE if BROKER_SOURCE.is_file() else BROKER_INSTALL_PATH
        if not broker_source.is_file() or broker_source.is_symlink():
            fail("native egress broker source is unavailable")
        atomic_install(broker_source, BROKER_INSTALL_PATH)
        os.chmod(BROKER_INSTALL_PATH, 0o755)
        try: login = pwd.getpwuid(owner_uid).pw_name
        except KeyError: fail("native helper caller account is unavailable")
        if not LOGIN_NAME.fullmatch(login): fail("native helper caller account is invalid")
        sudoers = Path(f"/etc/sudoers.d/sandbox-native-{owner_uid}")
        sudoers_payload = (f"{login} ALL=(root) NOPASSWD: {INSTALL_PATH} *\n").encode()
        if sudoers.exists() or sudoers.is_symlink():
            details = sudoers.lstat()
            if (not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)
                    or details.st_uid != 0 or sudoers.read_bytes() != sudoers_payload):
                fail("native sudo policy ownership changed")
        else:
            atomic_install_bytes(sudoers_payload, sudoers)
            os.chmod(sudoers, 0o440)
        run_fixed(("visudo", "-cf", str(sudoers)), "native sudo policy validation failed")
    elif args.verb == "host-packages-apply":
        require_root(); host_packages_apply(args.path, args.digest)
    elif args.verb == "host-baseline-observe":
        require_root(); host_baseline_observe()
    elif args.verb == "preflight-probe":
        require_root(); result = preflight_probe(args.probe)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        if not result["ok"]: raise SystemExit(69)
    elif args.verb == "_preflight-child":
        require_root(); preflight_child(args.probe, args.token)
    elif args.verb == "image-bootstrap":
        require_root(); image_bootstrap(machine(args.machine), digest_value(args.policy_digest),
                                        args.plan_path, args.plan_digest, args.web_server)
    elif args.verb == "image-configure":
        require_root(); image_configure(machine(args.machine), digest_value(args.policy_digest),
                                        args.web_server, digest_value(args.service_digest))
    elif args.verb == "policy-install":
        require_root(); identity = machine(args.machine)
        _source, value, payload = _read_checked_policy(args.path, identity)
        # Install the exact bytes read from the validated O_NOFOLLOW descriptor;
        # never reopen the user-controlled staging pathname.
        install_policy_pair(identity, value, payload)
    elif args.verb == "policy-status":
        require_root(); identity = machine(args.machine)
        _path, value = checked_policy(POLICY_ROOT / f"{identity}.json", identity, applied=True)
        read_policy_owner(identity, value)
        print(json.dumps({"machine_id": identity, "digest": value["digest"]}, sort_keys=True))
    elif args.verb == "grant-reconcile":
        require_root(); grant_reconcile(
            machine(args.machine), digest_value(args.base_policy_digest),
            digest_value(args.expected_grant_digest),
            digest_value(args.desired_grant_digest))
    elif args.verb == "policy-remove":
        require_root(); policy_remove(machine(args.machine), digest_value(args.digest))
    elif args.verb == "execute":
        require_root(); raise SystemExit(execute_request(
            machine(args.machine), digest_value(args.digest), args.request_digest))
    elif args.verb == "isolation-observe":
        require_root(); isolation_observe(machine(args.machine))
    elif args.verb == "cleanup-observe":
        require_root(); cleanup_observe(args.resource, machine(args.machine),
                                        digest_value(args.digest), args.resource_digest)
    elif args.verb == "credential-install":
        require_root(); credential_install(machine(args.machine), digest_value(args.digest), args.name)
    elif args.verb in {"services-activate", "services-health", "services-status", "services-stop"}:
        require_root(); identity = machine(args.machine); policy_digest = digest_value(args.digest)
        {"services-activate": services_activate,
         "services-health": services_health,
         "services-status": services_health,
         "services-stop": services_stop}[args.verb](
             identity, policy_digest, digest_value(args.service_digest))
    elif args.verb in {"image-create", "image-mount", "image-unmount", "image-remove",
                      "network-apply", "network-grants-apply", "network-status", "network-remove",
                      "egress-apply", "egress-status", "egress-remove",
                      "machine-start-minimal", "machine-status", "machine-stop",
                      "apparmor-install", "apparmor-status", "apparmor-remove",
                      "database-bootstrap", "database-status", "database-remove",
                      "wordpress-bootstrap", "wordpress-status"}:
        require_root(); identity = machine(args.machine)
        {"image-create": image_create, "image-mount": image_mount,
         "image-unmount": image_unmount, "image-remove": image_remove,
         "network-apply": network_apply, "network-status": network_status,
         "network-grants-apply": network_grants_apply,
         "network-remove": network_remove, "machine-start-minimal": machine_start_minimal,
         "egress-apply": egress_apply, "egress-status": egress_status,
         "egress-remove": egress_remove,
         "machine-status": machine_status, "machine-stop": machine_stop,
         "apparmor-install": apparmor_install, "apparmor-status": apparmor_status,
         "apparmor-remove": apparmor_remove,
         "database-bootstrap": database_bootstrap,
         "database-status": database_status, "database-remove": database_remove,
         "wordpress-bootstrap": wordpress_bootstrap,
         "wordpress-status": wordpress_status}[args.verb](
             identity, args.digest)


if __name__ == "__main__": main()

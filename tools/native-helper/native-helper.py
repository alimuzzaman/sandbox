#!/usr/bin/env python3
"""Fixed-verb privileged boundary for managed-native host objects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
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
    image = value["root_image"].get("path")
    expected_image = f"/var/lib/sandbox/native/instances/{machine_id}/root.img"
    if image != expected_image: fail("policy image path is outside its fixed root")
    if value["network"].get("egress") != "deny": fail("policy must default-deny egress")
    if value["syscalls"].get("no_new_privileges") is not True:
        fail("policy must enforce no-new-privileges")
    if any(not isinstance(ref, str) or not ref or "=" in ref
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sandbox-native-helper")
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check-policy"); check.add_argument("machine"); check.add_argument("path")
    install = sub.add_parser("install")
    apply_policy = sub.add_parser("policy-install"); apply_policy.add_argument("machine"); apply_policy.add_argument("path")
    status = sub.add_parser("policy-status"); status.add_argument("machine")
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


if __name__ == "__main__": main()

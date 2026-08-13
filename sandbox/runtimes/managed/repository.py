"""Locked atomic ownership state for managed-native resources."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Mapping
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading

from sandbox.isolation.models import canonical_digest


VERSION = 1
SECTIONS = ("selections", "backends", "policies", "packages", "grants", "networks",
            "recovery")


def _empty(): return {"version": VERSION, **{name: {} for name in SECTIONS}}


class NativeRepository:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.thread_lock = threading.RLock()

    @contextmanager
    def _lock(self):
        with self.thread_lock:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX); yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def _read(self):
        if not self.path.exists(): return _empty()
        try:
            payload = self.path.read_text()
        except OSError as exc:
            # Never fall back to empty state: this file is the only record of
            # which host resources are ours, and forgetting it orphans every one
            # of them. A run under sudo leaves it root-owned, which is how a
            # later ordinary run finds it unreadable.
            raise ValueError(
                f"native state at {self.path} cannot be read ({exc.strerror}); "
                "check its ownership -- running the product under sudo leaves it "
                "owned by root") from exc
        value = json.loads(payload)
        if not isinstance(value, dict) or value.get("version") not in {0, VERSION}:
            raise ValueError("unsupported native state version")
        result = _empty()
        for section in SECTIONS:
            if not isinstance(value.get(section, {}), dict):
                raise ValueError(f"native state {section} must be an object")
            result[section] = value.get(section, {})
        return result

    def _write(self, value):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    @contextmanager
    def transaction(self):
        with self._lock():
            value = copy.deepcopy(self._read()); yield value
            value["version"] = VERSION; self._write(value)

    def snapshot(self):
        with self._lock():
            value = self._read()
            if not self.path.exists() or value.get("version") != VERSION: self._write(value)
            return copy.deepcopy(value)

    def put_owned(self, section, identity, record):
        if section not in SECTIONS or section == "recovery":
            raise ValueError("native ownership section is invalid")
        value = dict(record)
        with self.transaction() as state:
            prior = state[section].get(identity)
            if prior and prior.get("owner") != value.get("owner"):
                raise ValueError("foreign native ownership collision")
            state[section][identity] = value

    def put_package_plan(self, machine_id, *, owner, plan):
        """Persist one exact package/extension approval receipt.

        The receipt is optional machine-local state; package planning itself
        remains read-only.  A later apply can compare the stored simulation
        and extension digests and refuse stale approval without consulting
        arbitrary state JSON or mutating a host package manager.
        """
        if not isinstance(machine_id, str) or not isinstance(owner, Mapping):
            raise ValueError("native package ownership identity is invalid")
        if hasattr(plan, "to_dict") and callable(plan.to_dict):
            value = plan.to_dict()
        elif isinstance(plan, Mapping):
            value = dict(plan)
        else:
            raise ValueError("native package plan is invalid")
        simulation_digest = value.get("simulation_digest")
        if not isinstance(simulation_digest, str) or len(simulation_digest) != 64:
            raise ValueError("native package simulation digest is invalid")
        extension = value.get("php_extensions")
        if extension is not None:
            if not isinstance(extension, Mapping):
                raise ValueError("native PHP extension plan is invalid")
            extension_digest = extension.get("digest")
            if not isinstance(extension_digest, str) or len(extension_digest) != 64:
                raise ValueError("native PHP extension plan digest is invalid")
        else:
            extension_digest = None
        record = {
            "owner": copy.deepcopy(dict(owner)), "machine_id": machine_id,
            "simulation_digest": simulation_digest,
            "extension_digest": extension_digest,
        }
        record["last_applied"] = canonical_digest(record)
        with self.transaction() as state:
            prior = state["packages"].get(machine_id)
            if prior is not None and prior.get("owner") != record["owner"]:
                raise ValueError("foreign native package ownership collision")
            state["packages"][machine_id] = record
        return copy.deepcopy(record)

    def package_record(self, machine_id, *, owner=None):
        """Return a detached package approval receipt, if one exists."""
        with self._lock():
            record = self._read()["packages"].get(machine_id)
            if record is None:
                return None
            if owner is not None and record.get("owner") != owner:
                raise ValueError("foreign native package ownership collision")
            return copy.deepcopy(record)

    def remove_if_unchanged(self, section, identity, observed):
        with self.transaction() as state:
            record = state[section].get(identity)
            if record is None: return "absent"
            expected = record.get("last_applied") or canonical_digest(record)
            actual = canonical_digest(observed)
            if expected != actual:
                state["recovery"][f"{section}:{identity}"] = {
                    "owner": record.get("owner"), "object_type": section,
                    "identity": identity, "expected_digest": expected,
                    "observed_digest": actual, "reason_code": "owned_state_drifted",
                    "retry_state": "pending",
                }
                return "drifted"
            del state[section][identity]
            state["recovery"].pop(f"{section}:{identity}", None)
            return "removed"

    def put_recovery(self, key, record):
        with self.transaction() as state: state["recovery"][key] = dict(record)

    def remove_recovery(self, key):
        with self.transaction() as state: state["recovery"].pop(key, None)

    def remove_recovery_if_unchanged(self, key, observed):
        """Remove one recovery entry only when its persisted record is unchanged.

        Cleanup retries use this to retire an old, resource-specific failure
        without racing a newer failure record written by another control-plane
        actor.  A caller that did not observe the exact current record has no
        authority to discard it.
        """
        with self.transaction() as state:
            record = state["recovery"].get(key)
            if record is None:
                return "absent"
            if canonical_digest(record) != canonical_digest(observed):
                return "drifted"
            del state["recovery"][key]
            return "removed"

    def reserve_network(self, machine_id, *, owner=None, allocator=None):
        """Atomically reserve a unique point-to-point subnet and veth identity."""
        from sandbox.isolation.network import SubnetAllocator

        allocator = allocator or SubnetAllocator()
        with self.transaction() as state:
            existing = state["networks"].get(machine_id)
            expected_owner = machine_id if owner is None else owner
            if existing is not None:
                if existing.get("owner") != expected_owner:
                    raise ValueError("foreign native network ownership collision")
                return copy.deepcopy(existing)
            used = [record.get("subnet") for record in state["networks"].values()
                    if isinstance(record, dict) and isinstance(record.get("subnet"), str)]
            allocation = allocator.allocate(machine_id, used=used)
            if any(record.get("veth") == allocation["veth"]
                   for record in state["networks"].values() if isinstance(record, dict)):
                raise ValueError("native veth ownership collision")
            record = {"owner": expected_owner, **allocation}
            record["last_applied"] = canonical_digest(record)
            state["networks"][machine_id] = record
            return copy.deepcopy(record)

    def release_network(self, machine_id, observed):
        value = {key: item for key, item in dict(observed).items()
                 if key != "last_applied"}
        return self.remove_if_unchanged("networks", machine_id, value)

    def grant_record(self, machine_id, *, owner=None):
        """Return one attributed grant record without exposing a mutable view."""
        with self._lock():
            record = self._read()["grants"].get(machine_id)
            if record is None:
                return None
            if owner is not None and record.get("owner") != owner:
                raise ValueError("foreign native grant ownership collision")
            return copy.deepcopy(record)

    def put_grants_if_expected(self, machine_id, *, owner, policy_digest,
                               expected_digest, grant_set):
        """Persist a helper-confirmed grant transition using compare-and-swap.

        The caller runs the privileged reconcile before this method.  We only
        commit the requested record after it succeeds, and only when the local
        expectation still names the same capability revision.  This prevents a
        stale ensure from silently overwriting a newer grant revocation.
        """
        from sandbox.isolation.models import EgressGrantSet

        if not isinstance(grant_set, EgressGrantSet):
            raise ValueError("native grant record is invalid")
        if (grant_set.machine_id != machine_id or
                grant_set.base_policy_digest != policy_digest):
            raise ValueError("native grant binding is invalid")
        if not isinstance(expected_digest, str) or len(expected_digest) != 64:
            raise ValueError("native expected grant digest is invalid")
        record = {
            "owner": owner, "machine_id": machine_id,
            "policy_digest": policy_digest, "grant_digest": grant_set.digest,
            "grant_set": grant_set.to_dict(),
        }
        record["last_applied"] = canonical_digest(record)
        with self.transaction() as state:
            prior = state["grants"].get(machine_id)
            if prior is not None:
                if prior.get("owner") != owner:
                    raise ValueError("foreign native grant ownership collision")
                if (prior.get("policy_digest") != policy_digest or
                        prior.get("grant_digest") != expected_digest):
                    return "drifted"
            elif expected_digest != "0" * 64:
                return "drifted"
            state["grants"][machine_id] = record
            return "stored"

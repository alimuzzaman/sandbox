"""Locked atomic ownership state for managed-native resources."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading

from sandbox.isolation.models import canonical_digest


VERSION = 1
SECTIONS = ("selections", "backends", "policies", "packages", "grants", "recovery")


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
        value = json.loads(self.path.read_text())
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

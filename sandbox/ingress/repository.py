"""Locked atomic ownership state for host ingress routes."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading

from .models import RouteRecord, digest


VERSION = 1


def _empty():
    return {"version": VERSION, "routes": {}, "consents": {}, "recovery": {}}


class IngressRepository:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = threading.RLock()
        self._migration_needed = False

    @contextmanager
    def _lock(self):
        with self._thread_lock:
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read(self):
        if not self.path.exists():
            return _empty()
        raw = json.loads(self.path.read_text())
        if not isinstance(raw, dict) or raw.get("version", 0) not in {0, 1}:
            raise ValueError("unsupported ingress state version")
        self._migration_needed = raw.get("version", 0) != VERSION
        result = _empty()
        for key in ("routes", "consents", "recovery"):
            if not isinstance(raw.get(key, {}), dict):
                raise ValueError(f"ingress state {key} must be an object")
            result[key] = raw.get(key, {})
        return result

    def _write(self, value):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as stream:
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
            working = copy.deepcopy(self._read())
            yield working
            working["version"] = VERSION
            self._write(working)

    def snapshot(self):
        with self._lock():
            value = self._read()
            if not self.path.exists() or self._migration_needed:
                self._write(value)
                self._migration_needed = False
            return copy.deepcopy(value)

    def route(self, route_id):
        value = self.snapshot()["routes"].get(route_id)
        return RouteRecord.from_dict(value) if value else None

    def put_route(self, route):
        with self.transaction() as value:
            prior = value["routes"].get(route.route_id)
            if prior and (prior["owner"] != route.owner or prior["adapter_id"] != route.adapter_id):
                raise ValueError("route identity collision")
            value["routes"][route.route_id] = route.to_dict()

    def remove_route_if_unchanged(self, route_id, observed):
        with self.transaction() as value:
            raw = value["routes"].get(route_id)
            if raw is None: return "absent"
            route = RouteRecord.from_dict(raw)
            expected = digest(route.last_applied) if route.last_applied else None
            actual = digest(observed)
            if expected != actual:
                value["recovery"][route_id] = {
                    "route_id": route_id, "adapter_id": route.adapter_id,
                    "expected_digest": expected, "observed_digest": actual,
                    "reason_code": "route_drifted", "status": "drifted",
                }
                return "drifted"
            del value["routes"][route_id]
            value["recovery"].pop(route_id, None)
            return "removed"

    def put_recovery(self, route_id, recovery):
        with self.transaction() as value:
            value["recovery"][route_id] = dict(recovery)

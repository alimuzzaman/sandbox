"""Shared fixtures for spec 058 server capture tests.

The helper runs for real against a temporary capture root, with the fake
``docker``, ``tar`` and ``loginctl`` executables from
``tests/fixtures/server_capture/bin`` first on ``PATH``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from sandbox.recovery.server_capture import slot_for
from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "sandbox" / "recovery" / "server_capture_helper.py"
FIXTURES = ROOT / "tests" / "fixtures" / "server_capture"
BIN = FIXTURES / "bin"
DUMPS = FIXTURES / "dumps"
SECRET = "sentinel-db-credential-7f3a"


def declarations_bytes(backup_id: str = "set-a") -> bytes:
    return (json.dumps({"schema_version": 1, "backup_operation_id": backup_id,
                        "sources": ["sandbox-state"]}, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def request_for(remote: str, backup_id: str, *, salt: str = "", declarations: bytes | None = None) -> dict:
    declarations = declarations if declarations is not None else declarations_bytes(backup_id)
    return {
        "schema_version": 1, "slot": slot_for(remote, backup_id), "remote": remote,
        "backup_id": backup_id, "backup_operation_id": backup_id,
        "request_id": "recovery-" + hashlib.sha256((remote + backup_id + salt).encode()).hexdigest(),
        "profile_id": "amarsonar-bangla-prod", "artifact_id": "amarsonar-bangla-prod-primary",
        "profiles": ["amarsonar-bangla-prod", "control-plane"],
        "source_binding": {"machine_identity": remote + ":host", "revision": "b" * 40,
                           "source_digest": "sha256:" + "c" * 64},
        "declarations_sha256": hashlib.sha256(declarations).hexdigest(),
    }


class HelperHarness:
    """Run the real helper as a child process against one temporary home."""

    def __init__(self, directory: str | Path) -> None:
        self.base = Path(directory)
        self.home = self.base / "home"
        self.root = self.home / "runtime" / "recovery-captures"
        self.log = self.base / "docker.log"
        self.home.mkdir(parents=True, exist_ok=True)

    def env(self, **overrides: str) -> dict:
        path = os.pathsep.join([str(BIN), str(Path(sys.executable).parent), "/usr/bin", "/bin"])
        values = {"PATH": path, "FAKE_LOG": str(self.log),
                  "FAKE_DUMP": str(DUMPS / "tables_only.sql"),
                  "FAKE_INVENTORY": str(DUMPS / "tables_only.tsv")}
        values.update(overrides)
        return values

    def raw(self, op: str, *args: str, stdin: bytes = b"", env: dict | None = None,
            root: Path | None = None) -> subprocess.CompletedProcess:
        return run_test_process(
            [sys.executable, str(HELPER), op, str(root or self.root), *args],
            input=stdin, capture_output=True, env=env if env is not None else self.env())

    def run(self, op: str, *args: str, stdin: bytes = b"", env: dict | None = None) -> dict:
        completed = self.raw(op, *args, stdin=stdin, env=env)
        assert completed.returncode == 0, completed
        return json.loads(completed.stdout)

    def start(self, remote: str = "fixture-remote", backup_id: str = "set-a", *,
              request: dict | None = None, password: str = SECRET, env: dict | None = None,
              declarations: bytes | None = None) -> dict:
        declarations = declarations if declarations is not None else declarations_bytes(backup_id)
        request = request or request_for(remote, backup_id, declarations=declarations)
        return self.run("start", request["slot"], json.dumps(request),
                        stdin=password.encode() + b"\n" + declarations, env=env)

    def status(self, remote: str = "fixture-remote", backup_id: str = "set-a") -> dict:
        return self.run("status", slot_for(remote, backup_id))

    def wait(self, remote: str = "fixture-remote", backup_id: str = "set-a",
             timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            status = self.status(remote, backup_id)
            state = (status.get("state") or {}).get("state")
            if state in ("complete", "failed") and status.get("lock_free"):
                return status
            if time.monotonic() > deadline:
                raise AssertionError(f"capture did not finish: {status}")
            time.sleep(0.05)

    def slot_path(self, remote: str = "fixture-remote", backup_id: str = "set-a") -> Path:
        return self.root / slot_for(remote, backup_id)

    def docker_calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def all_bytes(self) -> bytes:
        payload = b""
        for path in sorted(self.home.rglob("*")):
            if path.is_file() and not path.is_symlink():
                payload += path.read_bytes()
        return payload

    def cleanup(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


class TaggedFileCrypto:
    """File crypto fixture bound to a passphrase tag; streams in 1 MiB pieces."""

    def __init__(self, passphrase: str = "fixture-passphrase") -> None:
        self.tag = b"cipher:" + hashlib.sha256(passphrase.encode()).hexdigest()[:8].encode() + b":"

    def encrypt_file(self, source, target):
        with open(source, "rb") as reader, open(target, "wb") as writer:
            writer.write(self.tag)
            shutil.copyfileobj(reader, writer, 1024 * 1024)
        os.chmod(target, 0o600)
        return Path(target)

    def decrypt_file(self, source, target):
        from sandbox.recovery.errors import RecoveryError
        with open(source, "rb") as reader:
            if reader.read(len(self.tag)) != self.tag:
                raise RecoveryError("GnuPG encryption operation failed", "gpg_failed")
            with open(target, "wb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
        os.chmod(target, 0o600)
        return Path(target)

    def verify_file(self, source, target):
        from sandbox.recovery.integrity import sha256_file
        return sha256_file(source)


class LocalSsh:
    """Fake ``ssh_process`` that runs the shipped helper command locally.

    It executes exactly the ``python3 -c <source> <op> <root> <args...>``
    command the transport built, so the transport and the helper are tested
    together.  Every command and stdin payload is recorded.
    """

    def __init__(self, harness: HelperHarness, env: dict | None = None) -> None:
        self.harness = harness
        self.env = env
        self.calls: list[tuple[str, bytes]] = []
        self.ops: list[str] = []

    def __call__(self, _entry, command, *, input_data=None, timeout=30):
        import shlex
        argv = shlex.split(command)
        assert argv[:2] == ["python3", "-c"], argv[:2]
        self.calls.append((command, input_data or b""))
        self.ops.append(argv[3])
        return run_test_process([sys.executable, *argv[1:]], input=input_data or b"",
                                capture_output=True,
                                env=self.env if self.env is not None else self.harness.env())


def remote_inventory() -> dict:
    wordpress = "sandbox-host-amarsonar-bangla-production-wordpress-1"
    database = "sandbox-host-amarsonar-bangla-production-db-1"
    prefix = "sandbox-host-amarsonar-bangla-production_"
    return {
        "host_projects": ["amarsonar-bangla"],
        "runtime_environments": {"amarsonar-bangla": ["production"]},
        "managed_containers": [wordpress, database],
        "mounts": {
            wordpress: [
                {"type": "volume", "name": prefix + "wordpress-root",
                 "destination": "/var/www/html", "rw": True},
                {"type": "volume", "name": prefix + "wordpress-uploads",
                 "destination": "/var/www/html/wp-content/uploads", "rw": True},
            ],
            database: [
                {"type": "volume", "name": prefix + "wordpress-db",
                 "destination": "/var/lib/mysql", "rw": True},
            ],
        },
        "repositories": {"amarsonar-bangla": {"head": "a" * 40, "branch": "master",
                                                "dirty_count": 0, "untracked_count": 0}},
    }


def local_transport(harness: HelperHarness, *, revision_state: str = "match",
                    ssh=None, provisioned: bool = True, compatibility: dict | None = None):
    """A real ``RegisteredServerCaptureTransport`` wired to the local helper."""
    from sandbox.transports.remote_server_capture import RegisteredServerCaptureTransport
    ssh = ssh or LocalSsh(harness)

    def ssh_run(_entry, command, **_kwargs):
        return subprocess.CompletedProcess([], 0, "capture-host\n", "")

    transport = RegisteredServerCaptureTransport(
        remote_lookup=lambda name: {"provisioned": provisioned, "name": name},
        ssh_run=ssh_run, ssh_process=ssh,
        resolve_home=lambda _entry: str(harness.home),
        service_status=lambda _entry: {"installed_runtime_revision": "b" * 40,
                                       "runtime_revision_state": revision_state,
                                       **({"compatibility": compatibility}
                                          if compatibility is not None else {})},
        inventory=lambda _remote: remote_inventory(),
    )
    return transport, ssh


def fake_facts(backup_id: str = "set-a", *, remote: str = "fixture-remote",
               state: str | None = "complete", phase: str | None = "receipt",
               lock_free: bool = True, receipt_valid: bool = True, completed_at: float = 1_000.0,
               promoted: dict | None = None, reason: str | None = None,
               archive: bytes = b"archive-bytes", accepted_at: float = 900.0,
               residue_bytes: int = 0) -> dict:
    """Helper ``status`` facts for one slot, shaped like the real helper output."""
    request = request_for(remote, backup_id)
    request["accepted_at"] = accepted_at
    receipt = None
    if receipt_valid:
        receipt = {"archive_sha256": hashlib.sha256(archive).hexdigest(),
                   "archive_size": len(archive),
                   "members": [{"name": "database.sql", "sha256": "1" * 64, "size": 1},
                               {"name": "wordpress.tar", "sha256": "2" * 64, "size": 2}],
                   "declarations_sha256": request["declarations_sha256"],
                   "inventory_summary": {"table_count": 3, "view_count": 0,
                                         "rows_estimate_total": 5},
                   "started_at": 950.0, "completed_at": completed_at}
    raw = None
    if state is not None:
        raw = {"state": state, "phase": phase, "accepted_at": accepted_at, "started_at": 950.0,
               "ended_at": completed_at if state in ("complete", "failed") else None,
               "reason": reason, "detail": None}
    return {"ok": True, "slot": slot_for(remote, backup_id), "request": request, "state": raw,
            "lock_free": lock_free, "receipt_valid": receipt_valid, "receipt": receipt,
            "archive_size": len(archive) if receipt_valid else None,
            "residue_bytes": residue_bytes, "promoted": promoted}


def list_item(facts: dict) -> dict:
    """The helper ``list`` row for one slot's facts."""
    request, state, receipt = facts["request"] or {}, facts["state"] or {}, facts["receipt"] or {}
    return {"slot": facts["slot"], "backup_id": request.get("backup_id"),
            "request_id": request.get("request_id"), "accepted_at": request.get("accepted_at"),
            "state": state.get("state"), "phase": state.get("phase"),
            "reason": state.get("reason"), "lock_free": facts["lock_free"],
            "receipt_valid": facts["receipt_valid"], "archive_size": facts["archive_size"],
            "archive_sha256": receipt.get("archive_sha256"),
            "completed_at": receipt.get("completed_at"),
            "residue_bytes": facts["residue_bytes"], "promoted": bool(facts["promoted"])}


class FakeSource:
    machine_identity = "fixture-remote:host"
    revision = "b" * 40
    source_digest = "sha256:" + "c" * 64
    revision_state = "match"


class FakeTransport:
    """In-memory transport recording every call; no process is started."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.slots: dict[str, dict] = {}
        self.archives: dict[str, bytes] = {}
        self.declarations: dict[str, str] = {}
        self.legacy: list[dict] = []
        self.errors: dict[str, Exception] = {}
        self.started: list[tuple] = []
        self.marked: list[dict] = []
        self.retired: list[dict] = []
        self.chunk_hook = None
        self.after_retire_plan = None

    def add(self, facts: dict, archive: bytes = b"archive-bytes",
            declarations: bytes | None = None) -> dict:
        self.slots[facts["slot"]] = facts
        self.archives[facts["slot"]] = archive
        self.declarations[facts["slot"]] = (declarations or declarations_bytes(
            facts["request"]["backup_id"])).decode()
        return facts

    def _hit(self, name: str) -> None:
        self.calls.append(name)
        if name in self.errors:
            raise self.errors[name]

    def observe(self, remote):
        self._hit("observe")
        return FakeSource()

    def declaration(self, remote, artifact, source, backup_id):
        return {"schema_version": 1, "backup_operation_id": backup_id,
                "sources": ["sandbox-state"]}

    def list(self, remote):
        self._hit("list")
        return {"ok": True, "slots": [list_item(item) for item in self.slots.values()],
                "legacy": list(self.legacy), "truncated": False}

    def start(self, remote, slot, request, password, declarations):
        self._hit("start")
        self.started.append((remote, slot, request, password, declarations))
        if slot in self.slots:
            return {"ok": True, "existing": True, "status": self.slots[slot]}
        return {"ok": True, "existing": False, "state": "queued", "phase": None,
                "accepted_at": 1.0}

    def status(self, remote, slot):
        self._hit("status")
        if slot not in self.slots:
            from sandbox.recovery.errors import RecoveryError
            raise RecoveryError("no server capture", "capture_not_found")
        return self.slots[slot]

    def read_receipt(self, remote, slot):
        self._hit("read_receipt")
        facts = self.slots[slot]
        return dict(facts["receipt"], schema_version=1,
                    request_id=facts["request"]["request_id"],
                    backup_operation_id=facts["request"]["backup_id"],
                    source_binding=facts["request"]["source_binding"],
                    inventory={"summary": facts["receipt"]["inventory_summary"], "tables": []})

    def read_declaration(self, remote, slot):
        self._hit("read_declaration")
        return self.declarations[slot]

    def read_chunk(self, remote, slot, offset, length):
        self._hit("read_chunk")
        if self.chunk_hook:
            self.chunk_hook(offset, length)
        return self.archives[slot][offset:offset + length]

    def mark_promoted(self, remote, slot, marker):
        self._hit("mark_promoted")
        self.marked.append(dict(marker))
        existing = bool(self.slots[slot].get("promoted"))
        self.slots[slot]["promoted"] = self.slots[slot].get("promoted") or dict(marker)
        return {"ok": True, "existing": existing}

    def check_integrity(self, remote, slot):
        self._hit("check_integrity")
        facts = self.slots[slot]
        mismatch = (hashlib.sha256(self.archives[slot]).hexdigest()
                    != facts["receipt"]["archive_sha256"])
        if mismatch:
            facts["state"] = dict(facts["state"], state="failed", reason="integrity_mismatch")
        return {"ok": True, "mismatch": mismatch}

    def retire_plan(self, remote, slot):
        self._hit("retire_plan")
        if slot not in self.slots:
            from sandbox.recovery.errors import RecoveryError
            raise RecoveryError("no server capture", "capture_not_found")
        from sandbox.recovery.server_capture import review_state
        facts = self.slots[slot]
        state = review_state(facts)
        if state not in ("promoted", "failed", "incomplete"):
            from sandbox.recovery.errors import RecoveryError
            error = RecoveryError("server capture is not retirable", "not_retirable")
            error.data = {"state": state}
            raise error
        archive = self.archives.get(slot)
        receipt = facts.get("receipt") or {}
        candidate = {"state": state, "receipt_sha256": receipt.get("archive_sha256"),
                     "archive_sha256": hashlib.sha256(archive).hexdigest() if archive is not None else None,
                     "archive_size": len(archive) if archive is not None else None}
        if self.after_retire_plan:
            self.after_retire_plan(slot)
        return candidate

    def retire(self, remote, slot, plan):
        self._hit("retire")
        from sandbox.recovery.errors import RecoveryError
        from sandbox.recovery.server_capture import review_state
        facts = self.slots.get(slot)
        if facts is None:
            raise RecoveryError("no server capture", "capture_not_found")
        archive = self.archives.get(slot)
        receipt = facts.get("receipt") or {}
        observed = {"state": review_state(facts),
                    "receipt_sha256": receipt.get("archive_sha256"),
                    "archive_sha256": hashlib.sha256(archive).hexdigest() if archive is not None else None,
                    "archive_size": len(archive) if archive is not None else None}
        if dict(plan) != observed:
            raise RecoveryError("server capture changed since it was reviewed",
                                "retire_candidate_changed")
        self.retired.append(dict(plan))
        facts["state"] = {"state": "retired", "previous_state": observed["state"],
                           "retired_at": 2_000.0}
        facts["receipt"] = None
        facts["receipt_valid"] = False
        facts["archive_size"] = None
        self.archives.pop(slot, None)
        return {"ok": True, "retired_at": 2_000.0, "removed_bytes": 13}

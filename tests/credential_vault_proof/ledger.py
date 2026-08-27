"""Replay-safe durable ledger for one Credential Vault proof run.

One request identity owns one run. A retry reads this ledger before it launches
anything, so a lost response can never turn into a second live attempt against
the proof host. Empty or malformed job acceptance is `acceptance_unknown`, never
success, and cleanup trouble outranks every passing check.

Records hold identities, states, and digests only. They never hold a command's
output, a request body, a header, a credential, or an internal broker
identifier.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from . import scanner
from .manifest import canonical_json


LEDGER_VERSION = 1
MAX_RECORD_BYTES = 512 * 1024
MAX_CHECKS = 128
MAX_ARTIFACTS = 64

CLASSIFICATIONS = (
    "passed_live", "failed_live", "blocked", "acceptance_unknown",
    "cleanup_incomplete",
)
CHECK_STATES = frozenset({"pending", "passed", "failed", "blocked", "skipped"})
CLEANUP_STATES = frozenset({"pending", "complete", "incomplete"})
ACCEPTANCE_STATES = frozenset({"pending", "accepted", "unknown", "refused"})

_REQUEST_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,63}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{3,127}$")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ARTIFACT = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_RECORD_FIELDS = frozenset({
    "version", "request_id", "manifest_digest", "target", "expected", "job",
    "started_at", "terminal_at", "checks", "artifacts", "cleanup_state",
    "classification", "provenance",
})
_TARGET_FIELDS = frozenset({"machine_id", "broker_epoch", "host_label"})
_EXPECTED_FIELDS = frozenset({"git_sha", "sandbox_revision"})
_JOB_FIELDS = frozenset({"state", "job_id"})
PROVENANCE = frozenset({"live_authorized_host", "local_injected_fake"})


class LedgerError(ValueError):
    def __init__(self, code: str, location: str = "ledger") -> None:
        super().__init__(code)
        self.code = code
        self.location = location[:256]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "location": self.location}


def _refuse(code: str, location: str = "ledger") -> LedgerError:
    return LedgerError(code, location)


def _identity(value: Any, pattern: re.Pattern[str], location: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _refuse("field_invalid", location)
    return value


def validate_record(document: Any) -> dict[str, Any]:
    """Accept only an exact, bounded, secret-free ledger record."""
    if not isinstance(document, dict) or frozenset(document) != _RECORD_FIELDS:
        raise _refuse("schema_unknown_key")
    if document["version"] != LEDGER_VERSION:
        raise _refuse("version_unsupported")
    findings = scanner.scan_document(document, location="record")
    if findings:
        raise _refuse("secret_like_material", findings[0]["location"])
    _identity(document["request_id"], _REQUEST_ID, "request_id")
    _identity(document["manifest_digest"], _DIGEST, "manifest_digest")
    target = document["target"]
    if not isinstance(target, dict) or frozenset(target) != _TARGET_FIELDS:
        raise _refuse("schema_unknown_key", "target")
    expected = document["expected"]
    if not isinstance(expected, dict) or frozenset(expected) != _EXPECTED_FIELDS:
        raise _refuse("schema_unknown_key", "expected")
    job = document["job"]
    if not isinstance(job, dict) or frozenset(job) != _JOB_FIELDS \
            or job["state"] not in ACCEPTANCE_STATES:
        raise _refuse("schema_unknown_key", "job")
    if job["job_id"] is not None:
        _identity(job["job_id"], _JOB_ID, "job.job_id")
    if job["state"] == "accepted" and job["job_id"] is None:
        raise _refuse("acceptance_contradiction", "job")
    _identity(document["started_at"], _TIMESTAMP, "started_at")
    if document["terminal_at"] is not None:
        _identity(document["terminal_at"], _TIMESTAMP, "terminal_at")
        if document["terminal_at"] < document["started_at"]:
            raise _refuse("timestamp_not_monotonic", "terminal_at")
    checks = document["checks"]
    if not isinstance(checks, dict) or len(checks) > MAX_CHECKS:
        raise _refuse("schema_unknown_key", "checks")
    for name, state in checks.items():
        _identity(name, _CHECK_ID, f"checks.{name}")
        if state not in CHECK_STATES:
            raise _refuse("field_invalid", f"checks.{name}")
    artifacts = document["artifacts"]
    if not isinstance(artifacts, dict) or len(artifacts) > MAX_ARTIFACTS:
        raise _refuse("schema_unknown_key", "artifacts")
    for name, digest in artifacts.items():
        _identity(name, _ARTIFACT, f"artifacts.{name}")
        _identity(digest, _DIGEST, f"artifacts.{name}")
    if document["cleanup_state"] not in CLEANUP_STATES:
        raise _refuse("field_invalid", "cleanup_state")
    if document["classification"] is not None \
            and document["classification"] not in CLASSIFICATIONS:
        raise _refuse("field_invalid", "classification")
    if document["provenance"] not in PROVENANCE:
        raise _refuse("field_invalid", "provenance")
    return document


def classify(record: dict[str, Any], *, required: Any) -> str:
    """Decide one final classification from the record and the required checks.

    The order is deliberate. Cleanup trouble outranks a clean test result,
    because a run that left something behind on the proof host is not a pass.
    An unknown acceptance outranks everything else, because we do not know
    whether the run happened at all.
    """
    required_ids = tuple(required or ())
    if record["job"]["state"] in {"pending", "unknown"}:
        return "acceptance_unknown"
    if record["cleanup_state"] == "incomplete":
        return "cleanup_incomplete"
    states = {name: record["checks"].get(name, "pending") for name in required_ids}
    if any(state == "failed" for state in states.values()):
        return "failed_live"
    if record["job"]["state"] == "refused":
        return "blocked"
    if any(state in {"pending", "blocked", "skipped"} for state in states.values()):
        return "blocked"
    if record["cleanup_state"] != "complete":
        return "cleanup_incomplete"
    if record["provenance"] != "live_authorized_host":
        # Local fakes can produce a complete record; they never produce proof.
        return "blocked"
    return "passed_live"


class ProofRunLedger:
    """One directory of durable, owner-only, canonical run records."""

    def __init__(self, root: Any, *, owner_uid: int | None = None) -> None:
        self.root = Path(root)
        self.owner_uid = os.getuid() if owner_uid is None else int(owner_uid)

    def _path(self, request_id: str) -> Path:
        _identity(request_id, _REQUEST_ID, "request_id")
        return self.root / f"{request_id}.json"

    def read(self, request_id: str) -> dict[str, Any] | None:
        """Return one record, refusing anything unsafe rather than repairing it."""
        path = self._path(request_id)
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink():
            raise _refuse("record_symlink", str(path))
        try:
            details = path.lstat()
        except OSError as exc:
            raise _refuse("record_unreadable", str(path)) from exc
        if not stat.S_ISREG(details.st_mode):
            raise _refuse("record_not_regular", str(path))
        if details.st_uid != self.owner_uid:
            raise _refuse("record_foreign_owner", str(path))
        if details.st_size > MAX_RECORD_BYTES:
            raise _refuse("record_oversize", str(path))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise _refuse("record_unreadable", str(path)) from exc
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _refuse("record_corrupt", str(path)) from exc
        record = validate_record(document)
        if canonical_json(record).encode("ascii") != raw.rstrip(b"\n"):
            raise _refuse("encoding_not_canonical", str(path))
        return record

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        validate_record(record)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(record["request_id"])
        payload = canonical_json(record).encode("ascii") + b"\n"
        if len(payload) > MAX_RECORD_BYTES:
            raise _refuse("record_oversize", str(path))
        temporary = path.with_suffix(".json.tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600,
        )
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        return record

    def open_run(self, *, request_id: str, manifest: dict[str, Any],
                 started_at: str, provenance: str = "live_authorized_host",
                 manifest_digest_value: str | None = None) -> dict[str, Any]:
        """Create or re-attach one run identity; conflicting inputs are refused."""
        from .manifest import manifest_digest

        if provenance not in PROVENANCE:
            raise _refuse("field_invalid", "provenance")
        digest = manifest_digest_value or manifest_digest(manifest)
        record = {
            "version": LEDGER_VERSION,
            "request_id": request_id,
            "manifest_digest": digest,
            "target": dict(manifest["target"]),
            "expected": dict(manifest["source"]),
            "job": {"state": "pending", "job_id": None},
            "started_at": started_at,
            "terminal_at": None,
            "checks": {name: "pending" for name in
                       (item["check_id"] for item in manifest["checks"])},
            "artifacts": {},
            "cleanup_state": "pending",
            "classification": None,
            "provenance": provenance,
        }
        validate_record(record)
        existing = self.read(request_id)
        if existing is None:
            return self._write(record)
        for field in ("manifest_digest", "target", "expected", "provenance"):
            if existing[field] != record[field]:
                raise _refuse("request_id_conflict", field)
        # Re-attaching is how a retry resumes without minting a second identity.
        return existing

    def record_acceptance(self, request_id: str, acceptance: Any) -> dict[str, Any]:
        """Record a durable job's acceptance; empty or malformed is unknown."""
        record = self.read(request_id)
        if record is None:
            raise _refuse("run_unknown", request_id)
        job_id = None
        state = "unknown"
        if isinstance(acceptance, dict):
            candidate = acceptance.get("job_id")
            if isinstance(candidate, str) and _JOB_ID.fullmatch(candidate):
                accepted = acceptance.get("accepted")
                state = "accepted" if accepted is not False else "refused"
                job_id = candidate if state == "accepted" else None
            elif acceptance.get("accepted") is False:
                state = "refused"
        if record["job"]["state"] == "accepted":
            if state != "accepted" or job_id != record["job"]["job_id"]:
                raise _refuse("acceptance_conflict", "job")
            return record
        record["job"] = {"state": state, "job_id": job_id}
        if state == "unknown":
            record["classification"] = "acceptance_unknown"
        return self._write(record)

    def should_launch(self, request_id: str) -> dict[str, Any]:
        """A retry consults this before it launches anything at all."""
        record = self.read(request_id)
        if record is None:
            return {"ok": True, "code": "launch_permitted", "launch": True}
        if record["job"]["state"] == "unknown":
            # An indeterminate acceptance is checked first: we do not know
            # whether the run happened, so nothing may launch under this
            # identity and no second identity may be minted automatically.
            return {"ok": False, "code": "acceptance_unknown", "launch": False}
        if record["classification"] is not None:
            return {"ok": True, "code": "run_terminal", "launch": False,
                    "classification": record["classification"]}
        if record["job"]["state"] == "accepted":
            return {"ok": True, "code": "job_already_accepted", "launch": False,
                    "job_id": record["job"]["job_id"]}
        return {"ok": True, "code": "launch_permitted", "launch": True}

    def record_check(self, request_id: str, check_id: str, state: str) -> dict[str, Any]:
        record = self.read(request_id)
        if record is None:
            raise _refuse("run_unknown", request_id)
        _identity(check_id, _CHECK_ID, "check_id")
        if state not in CHECK_STATES or state == "pending":
            raise _refuse("field_invalid", "state")
        if check_id not in record["checks"]:
            raise _refuse("check_unknown", check_id)
        previous = record["checks"][check_id]
        if previous != "pending" and previous != state:
            raise _refuse("check_contradiction", check_id)
        record["checks"][check_id] = state
        return self._write(record)

    def record_artifact(self, request_id: str, name: str, digest: str) -> dict[str, Any]:
        record = self.read(request_id)
        if record is None:
            raise _refuse("run_unknown", request_id)
        _identity(name, _ARTIFACT, "name")
        _identity(digest, _DIGEST, "digest")
        previous = record["artifacts"].get(name)
        if previous is not None and previous != digest:
            raise _refuse("artifact_conflict", name)
        record["artifacts"][name] = digest
        return self._write(record)

    def record_cleanup(self, request_id: str, state: str) -> dict[str, Any]:
        record = self.read(request_id)
        if record is None:
            raise _refuse("run_unknown", request_id)
        if state not in CLEANUP_STATES or state == "pending":
            raise _refuse("field_invalid", "cleanup_state")
        if record["cleanup_state"] == "incomplete" and state == "complete":
            raise _refuse("cleanup_contradiction", "cleanup_state")
        record["cleanup_state"] = state
        return self._write(record)

    def finalize(self, request_id: str, *, required: Any,
                 terminal_at: str) -> dict[str, Any]:
        record = self.read(request_id)
        if record is None:
            raise _refuse("run_unknown", request_id)
        _identity(terminal_at, _TIMESTAMP, "terminal_at")
        classification = classify(record, required=required)
        if record["classification"] is not None \
                and record["classification"] != classification:
            if record["classification"] == "acceptance_unknown":
                classification = "acceptance_unknown"
            else:
                raise _refuse("classification_conflict", "classification")
        record["classification"] = classification
        record["terminal_at"] = terminal_at
        return self._write(record)


def artifact_digest(payload: Any) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, (bytes, bytearray)):
        raise _refuse("field_invalid", "payload")
    return hashlib.sha256(bytes(payload)).hexdigest()


__all__ = [
    "ACCEPTANCE_STATES", "CHECK_STATES", "CLASSIFICATIONS", "CLEANUP_STATES",
    "LEDGER_VERSION", "LedgerError", "PROVENANCE", "ProofRunLedger",
    "artifact_digest", "classify", "validate_record",
]

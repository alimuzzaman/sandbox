"""Offline validator for a completed Credential Vault proof bundle.

A bundle is only evidence if it is the bundle the manifest planned, produced by
the revision the manifest named, on the host the manifest named, with every
required artifact present, intact, ordered, and free of anything that must not
be retained. Anything less is a refusal with a stable code.

The validator is deliberately suspicious of its own inputs. Copied evidence,
stale evidence, mixed revisions, contradictory records, and a local fake wearing
a live label are all things it must catch, because the alternative is a review
that believes a run happened when it did not.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone
from typing import Any

from . import cleanup as cleanup_module
from . import probes as probes_module
from . import scanner
from .ledger import CLASSIFICATIONS, validate_record
from .manifest import canonical_json, manifest_digest, validate_manifest


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 4096
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

_EVENT_FIELDS = frozenset({"sequence", "at", "check_id", "state", "code"})
EVENT_STATES = frozenset({"started", "passed", "failed", "blocked", "skipped"})
TERMINAL_EVENT_STATES = frozenset({"passed", "failed", "blocked", "skipped"})

# A live bundle must not contain any of these; they are what a local, injected
# run leaves behind.
FAKE_MARKERS = (
    "injected", "fake", "synthetic", "mock", "stub", "simulated", "local_only",
    "local_injected_fake",
)


class BundleError(ValueError):
    def __init__(self, code: str, location: str = "bundle") -> None:
        super().__init__(code)
        self.code = code
        self.location = location[:256]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "location": self.location}


def _refuse(code: str, location: str = "bundle") -> BundleError:
    return BundleError(code, location)


def _validate_ancestors(path: Path) -> None:
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        try:
            details = ancestor.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _refuse("artifact_ancestor_unreadable", path.name) from exc
        if stat.S_ISLNK(details.st_mode):
            if ancestor == absolute:
                raise _refuse("artifact_symlink", path.name)
            if details.st_uid == os.getuid():
                raise _refuse("artifact_ancestor_symlink", path.name)
            continue
        if ancestor != absolute and not stat.S_ISDIR(details.st_mode):
            raise _refuse("artifact_ancestor_invalid", path.name)


def _read(path: Path, *, limit: int) -> bytes:
    _validate_ancestors(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise _refuse("artifact_missing", path.name) from exc
    except OSError as exc:
        code = "artifact_symlink" if path.is_symlink() else "artifact_unreadable"
        raise _refuse(code, path.name) from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise _refuse("artifact_not_regular", path.name)
        if details.st_size > limit:
            raise _refuse("artifact_oversize", path.name)
        chunks = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise _refuse("artifact_oversize", path.name)
        return b"".join(chunks)
    except BundleError:
        raise
    except OSError as exc:
        raise _refuse("artifact_unreadable", path.name) from exc
    finally:
        os.close(descriptor)


def _json(raw: bytes, location: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _refuse("artifact_corrupt", location) from exc


def validate_events(document: Any, *, checks: Any) -> tuple[dict[str, Any], ...]:
    """Require monotonic sequence, one terminal event per check, no extras."""
    if not isinstance(document, list) or not 1 <= len(document) <= MAX_EVENTS:
        raise _refuse("events_invalid", "events")
    known = set(checks or ())
    previous = 0
    previous_at = ""
    started: set[str] = set()
    terminal: dict[str, str] = {}
    for index, item in enumerate(document):
        place = f"events[{index}]"
        if not isinstance(item, dict) or frozenset(item) != _EVENT_FIELDS:
            raise _refuse("events_schema_unknown_key", place)
        sequence = item["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) \
                or sequence != previous + 1:
            raise _refuse("events_not_monotonic", place)
        previous = sequence
        at = item["at"]
        if not isinstance(at, str) or not _TIMESTAMP.fullmatch(at):
            raise _refuse("timestamp_invalid", place)
        if at < previous_at:
            raise _refuse("events_not_monotonic", place)
        previous_at = at
        check_id = item["check_id"]
        if not isinstance(check_id, str) or not _CHECK_ID.fullmatch(check_id):
            raise _refuse("events_check_invalid", place)
        if check_id not in known:
            raise _refuse("events_check_unplanned", place)
        state = item["state"]
        if state not in EVENT_STATES:
            raise _refuse("events_state_invalid", place)
        if not isinstance(item["code"], str) or len(item["code"]) > 64 \
                or not re.fullmatch(r"[a-z0-9_.-]{1,64}", item["code"]):
            raise _refuse("events_code_invalid", place)
        if state == "started":
            if check_id in started:
                raise _refuse("events_duplicate_start", place)
            started.add(check_id)
            continue
        if check_id not in started:
            raise _refuse("events_terminal_without_start", place)
        if check_id in terminal:
            raise _refuse("events_duplicate_terminal", place)
        terminal[check_id] = state
    missing = tuple(sorted(known - set(terminal)))
    if missing:
        raise _refuse("events_terminal_missing", missing[0])
    return tuple(document)


def _artifact_expectations(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in manifest["artifacts"]}


def validate_artifact_payload(name: str, raw: bytes, manifest: dict[str, Any]) -> Any:
    """Validate one bounded retained artifact before it enters the ledger."""
    expectations = _artifact_expectations(manifest)
    expected = expectations.get(name)
    if expected is None:
        raise _refuse("artifact_unplanned", name[:64])
    if len(raw) > min(expected["max_bytes"], MAX_ARTIFACT_BYTES):
        raise _refuse("artifact_oversize", name)
    findings = scanner.scan_text(raw.decode("utf-8", errors="replace"), location=name)
    if findings:
        raise _refuse("secret_like_material", findings[0]["location"])
    document = _json(raw, name)
    try:
        if name == "checks.json":
            return probes_module.validate_execution_artifact(document, manifest)
        if name == "cleanup.json":
            return cleanup_module.validate_artifact(document, manifest)
    except (probes_module.ProbeError, cleanup_module.CleanupError) as exc:
        raise _refuse(exc.code, exc.location) from exc
    raise _refuse("artifact_unplanned", name)


def validate_bundle(root: Any, *, manifest: Any, expected_request_id: Any = None,
                    now: Any = None) -> dict[str, Any]:
    """Validate one bundle directory against its manifest and ledger record."""
    path = Path(root)
    if path.is_symlink() or not path.is_dir():
        raise _refuse("bundle_root_invalid", str(path))
    accepted = validate_manifest(manifest)
    digest = manifest_digest(accepted)

    record_raw = _read(path / "run.json", limit=512 * 1024)
    record = validate_record(_json(record_raw, "run.json"))
    if canonical_json(record).encode("ascii") != record_raw.rstrip(b"\n"):
        raise _refuse("encoding_not_canonical", "run.json")
    if record["manifest_digest"] != digest:
        raise _refuse("manifest_digest_mismatch", "run.json")
    if expected_request_id is not None and record["request_id"] != expected_request_id:
        raise _refuse("request_identity_mismatch", "run.json")
    if record["expected"] != accepted["source"]:
        raise _refuse("revision_mismatch", "run.json")
    if record["target"] != accepted["target"]:
        raise _refuse("target_mismatch", "run.json")
    if record["classification"] not in CLASSIFICATIONS:
        raise _refuse("classification_missing", "run.json")
    if record["terminal_at"] is None:
        raise _refuse("run_not_terminal", "run.json")
    if record["job"]["state"] != "accepted":
        raise _refuse("acceptance_unknown", "run.json")
    if not record["started_at"] <= record["job"]["accepted_at"] <= record["terminal_at"]:
        raise _refuse("acceptance_timestamp_invalid", "run.json")
    if record["provenance"] != "live_authorized_host":
        raise _refuse("provenance_not_live", "run.json")

    # The manifest digest binds the plan, but nothing binds the record's own
    # check set to it. Without this comparison a bundle could claim
    # `passed_live` while simply omitting most required checks: they would be
    # neither failed nor blocked, just absent.
    planned = frozenset(item["check_id"] for item in accepted["checks"])
    recorded = frozenset(record["checks"])
    missing = tuple(sorted(planned - recorded))
    if missing:
        raise _refuse("check_missing", missing[0])
    unplanned = tuple(sorted(recorded - planned))
    if unplanned:
        raise _refuse("check_unplanned", unplanned[0])
    pending = tuple(sorted(name for name, state in record["checks"].items()
                           if state == "pending"))
    if pending:
        raise _refuse("check_incomplete", pending[0])

    events_raw = _read(path / "events.json", limit=1024 * 1024)
    events = validate_events(_json(events_raw, "events.json"),
                             checks=tuple(record["checks"]))

    findings = scanner.scan_directory(path)
    if findings:
        # Structural problems keep their own code: a symlinked or unreadable
        # member is a bundle defect, not a suspected leak, and a reviewer
        # should not have to guess which one they are looking at.
        first = findings[0]
        structural = {
            "evidence_symlink", "evidence_unreadable", "evidence_root_invalid",
            "evidence_too_many_files", "oversize_scan_target", "undecodable_bytes",
        }
        code = first["code"] if first["code"] in structural else "secret_like_material"
        raise _refuse(code, first["location"])

    surface = (record_raw + events_raw).decode("utf-8", errors="ignore").lower()
    for marker in FAKE_MARKERS:
        if marker in surface:
            raise _refuse("fake_evidence_marker", marker)

    expectations = _artifact_expectations(accepted)
    decoded_artifacts: dict[str, Any] = {}
    for name, expected in expectations.items():
        member = path / name
        raw = _read(member, limit=min(expected["max_bytes"], MAX_ARTIFACT_BYTES))
        observed = hashlib.sha256(raw).hexdigest()
        if expected["sha256"] is not None and observed != expected["sha256"]:
            raise _refuse("artifact_digest_mismatch", name)
        recorded = record["artifacts"].get(name)
        if recorded is None:
            raise _refuse("artifact_unrecorded", name)
        if recorded != observed:
            raise _refuse("artifact_digest_mismatch", name)
        decoded_artifacts[name] = _json(raw, name)

    try:
        executions = probes_module.validate_execution_artifact(
            decoded_artifacts["checks.json"], accepted)
        cleanup_result = cleanup_module.validate_artifact(
            decoded_artifacts["cleanup.json"], accepted)
    except (probes_module.ProbeError, cleanup_module.CleanupError) as exc:
        raise _refuse(exc.code, exc.location) from exc
    if cleanup_result["state"] != record["cleanup_state"]:
        raise _refuse("cleanup_artifact_state_mismatch", "cleanup.json")

    # Walk the whole tree, not just the top level: a nested directory was an
    # unwatched place to park an artifact nobody planned.
    reserved = {"run.json", "events.json"}
    unexpected = []
    for member in sorted(path.rglob("*")):
        relative = str(member.relative_to(path))
        if member.is_dir():
            unexpected.append(relative)
            continue
        if relative in expectations or relative in reserved:
            continue
        unexpected.append(relative)
    if unexpected:
        raise _refuse("artifact_unplanned", unexpected[0])

    # The event record and the ledger must tell the same story.
    terminal = {item["check_id"]: item["state"] for item in events
                if item["state"] in TERMINAL_EVENT_STATES}
    executed = {item["check_id"]: item["state"] for item in executions}
    for check_id, state in record["checks"].items():
        if state == "pending":
            raise _refuse("check_incomplete", check_id)
        if terminal.get(check_id) != state:
            raise _refuse("result_contradiction", check_id)
        if executed.get(check_id) != state:
            raise _refuse("execution_artifact_result_mismatch", check_id)

    required = tuple(item["check_id"] for item in accepted["checks"] if item["required"])
    failed = tuple(name for name in required if record["checks"].get(name) == "failed")
    blocked = tuple(name for name in required
                    if record["checks"].get(name) in {"blocked", "skipped"})
    if record["cleanup_state"] != "complete" \
            and record["classification"] not in {"cleanup_incomplete"}:
        raise _refuse("cleanup_contradiction", "run.json")
    if record["classification"] == "passed_live" and (failed or blocked):
        raise _refuse("result_contradiction", "classification")
    if record["classification"] == "passed_live" \
            and record["cleanup_state"] != "complete":
        raise _refuse("cleanup_contradiction", "classification")
    if now is not None:
        if not isinstance(now, str) or not _TIMESTAMP.fullmatch(now):
            raise _refuse("timestamp_invalid", "now")
        if record["terminal_at"] > now:
            raise _refuse("evidence_from_the_future", "run.json")
        current = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        terminal = datetime.strptime(record["terminal_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        if (current - terminal).total_seconds() > MAX_EVIDENCE_AGE_SECONDS:
            raise _refuse("evidence_stale", "run.json")
    return {
        "ok": True,
        "code": "bundle_verified",
        "classification": record["classification"],
        "request_id": record["request_id"],
        "manifest_digest": digest,
        "required_failed": failed,
        "required_blocked": blocked,
        "cleanup_state": record["cleanup_state"],
        "artifact_count": len(expectations),
        "event_count": len(events),
    }


__all__ = [
    "BundleError", "EVENT_STATES", "FAKE_MARKERS", "MAX_ARTIFACT_BYTES",
    "MAX_EVIDENCE_AGE_SECONDS",
    "TERMINAL_EVENT_STATES", "validate_artifact_payload", "validate_bundle",
    "validate_events",
]

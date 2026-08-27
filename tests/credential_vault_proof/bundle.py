"""Offline validator for a completed Credential Vault proof bundle.

A bundle is only evidence if it is the bundle the manifest planned, produced by
the revision the manifest named, on the host the manifest named, with every
required artifact present, intact, ordered, and free of anything that must not
be retained. Anything less is a refusal with a stable code.

The validator is deliberately suspicious of its own inputs. Copied evidence,
stale evidence, mixed revisions, contradictory records, and local fake markers
are all things it must catch. The `live_authorized_host` provenance value is an
operator assertion; proving that assertion requires external host/job evidence
and independent review, which this offline module cannot manufacture.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
from typing import Any

from . import cleanup, probes, scanner
from .ledger import CLASSIFICATIONS, LedgerError, validate_record
from .manifest import ARTIFACT_SCHEMAS, canonical_json, manifest_digest, validate_manifest


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 4096

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

_EVENT_FIELDS = frozenset({"sequence", "at", "check_id", "state", "code"})
EVENT_STATES = frozenset({"started", "passed", "failed", "blocked", "skipped"})
TERMINAL_EVENT_STATES = frozenset({"passed", "failed", "blocked", "skipped"})
_CHECK_RESULT_FIELDS = frozenset({"state", "code", "observations"})
_CHECK_RESULT_CODE = re.compile(r"^[a-z0-9_.-]{1,64}$")
_CHECK_RESULT_OBSERVATION = re.compile(
    r"^[A-Za-z0-9@][A-Za-z0-9 ._:/=@+%,-]{0,255}$")
_CHECK_ARTIFACT_FIELDS = frozenset({
    "version", "request_id", "manifest_digest", "checks",
})
_CLEANUP_ARTIFACT_FIELDS = frozenset({
    "version", "request_id", "manifest_digest", "state", "observations",
    "removed", "retained", "unexpected",
})
_CLEANUP_RETAINED_FIELDS = frozenset({"kind", "identity", "reason_code"})

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


def _read(path: Path, *, limit: int, owner_uid: int) -> bytes:
    if path.is_symlink():
        raise _refuse("artifact_symlink", path.name)
    try:
        details = path.lstat()
    except OSError as exc:
        raise _refuse("artifact_missing", path.name) from exc
    if not stat.S_ISREG(details.st_mode):
        raise _refuse("artifact_not_regular", path.name)
    if details.st_uid != owner_uid:
        raise _refuse("artifact_foreign_owner", path.name)
    if details.st_size > limit:
        raise _refuse("artifact_oversize", path.name)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise _refuse("artifact_unreadable", path.name) from exc


def _json(raw: bytes, location: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _refuse("artifact_corrupt", location) from exc


def _canonical(raw: bytes, document: Any, location: str) -> None:
    expected = canonical_json(document).encode("ascii")
    actual = raw[:-1] if raw.endswith(b"\n") else raw
    if actual != expected:
        raise _refuse("encoding_not_canonical", location)


def _timestamp(value: Any, location: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise _refuse("timestamp_invalid", location)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        raise _refuse("timestamp_invalid", location) from None


def validate_events(document: Any, *, checks: Any) -> tuple[dict[str, Any], ...]:
    """Require monotonic sequence, one terminal event per check, no extras."""
    if not isinstance(document, list) or not 1 <= len(document) <= MAX_EVENTS:
        raise _refuse("events_invalid", "events")
    known = set(checks or ())
    previous = 0
    previous_at: datetime | None = None
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
        at_dt = _timestamp(at, place)
        if previous_at and at_dt < previous_at:
            raise _refuse("events_not_monotonic", place)
        previous_at = at_dt
        check_id = item["check_id"]
        if not isinstance(check_id, str) or not _CHECK_ID.fullmatch(check_id):
            raise _refuse("events_check_invalid", place)
        if check_id not in known:
            raise _refuse("events_check_unplanned", place)
        state = item["state"]
        if not isinstance(state, str) or state not in EVENT_STATES:
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


def _manifest_checks(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["check_id"]: item for item in manifest["checks"]}


def _validate_check_artifact(document: Any, *, manifest: dict[str, Any],
                             record: dict[str, Any], digest: str) -> None:
    if not isinstance(document, dict) or frozenset(document) != _CHECK_ARTIFACT_FIELDS:
        raise _refuse("artifact_schema_invalid", "checks.json")
    if isinstance(document["version"], bool) \
            or not isinstance(document["version"], int) \
            or document["version"] != 1 \
            or document["request_id"] != record["request_id"] \
            or document["manifest_digest"] != digest:
        raise _refuse("artifact_binding_mismatch", "checks.json")
    checks = document["checks"]
    planned = _manifest_checks(manifest)
    if not isinstance(checks, dict) or frozenset(checks) != frozenset(planned):
        raise _refuse("artifact_checks_invalid", "checks.json")
    for check_id, value in checks.items():
        place = f"checks.json.checks.{check_id}"
        if not isinstance(value, dict) or frozenset(value) != _CHECK_RESULT_FIELDS:
            raise _refuse("artifact_check_schema_invalid", place)
        state = value["state"]
        if not isinstance(state, str) or state not in TERMINAL_EVENT_STATES:
            raise _refuse("artifact_check_state_invalid", place)
        if record["checks"].get(check_id) != state:
            raise _refuse("artifact_check_contradiction", place)
        code = value["code"]
        if not isinstance(code, str) or not _CHECK_RESULT_CODE.fullmatch(code):
            raise _refuse("artifact_check_code_invalid", place)
        observations = value["observations"]
        if not isinstance(observations, list) or len(observations) > probes.MAX_OBSERVATIONS:
            raise _refuse("artifact_observations_invalid", place)
        for index, observation in enumerate(observations):
            if not isinstance(observation, str) \
                    or not _CHECK_RESULT_OBSERVATION.fullmatch(observation):
                raise _refuse("artifact_observation_invalid", f"{place}.observations[{index}]")
        expected = tuple(planned[check_id]["expected"])
        if any(observation not in expected for observation in observations):
            raise _refuse("artifact_observation_unplanned", place)
        expectation = probes.expectation_kind(check_id)
        if state == "passed":
            expected_code = "observed" if expectation == "exit_zero" \
                else "observed_absent"
            if code != expected_code:
                raise _refuse("artifact_check_code_invalid", place)
            if expectation == "exit_zero" and tuple(observations) != expected:
                raise _refuse("artifact_observation_missing", place)
        if expectation != "exit_zero" and observations:
            raise _refuse("artifact_observation_unusable", place)


def _validate_cleanup_artifact(document: Any, *, manifest: dict[str, Any],
                               record: dict[str, Any], digest: str) -> None:
    if not isinstance(document, dict) \
            or frozenset(document) != _CLEANUP_ARTIFACT_FIELDS:
        raise _refuse("artifact_schema_invalid", "cleanup.json")
    if isinstance(document["version"], bool) \
            or not isinstance(document["version"], int) \
            or document["version"] != 1 \
            or document["request_id"] != record["request_id"] \
            or document["manifest_digest"] != digest:
        raise _refuse("artifact_binding_mismatch", "cleanup.json")
    observations = document["observations"]
    if not isinstance(observations, list) or len(observations) > 512:
        raise _refuse("artifact_observations_invalid", "cleanup.json")
    try:
        verified = cleanup.verify(manifest, observations)
    except cleanup.CleanupError as exc:
        raise _refuse(exc.code, f"cleanup.json.{exc.location}") from None
    if document["state"] != verified["state"] \
            or record["cleanup_state"] != verified["state"]:
        raise _refuse("artifact_cleanup_contradiction", "cleanup.json.state")
    if document["removed"] != list(verified["removed"]):
        raise _refuse("artifact_cleanup_contradiction", "cleanup.json.removed")
    retained = document["retained"]
    if not isinstance(retained, list):
        raise _refuse("artifact_cleanup_invalid", "cleanup.json.retained")
    for index, item in enumerate(retained):
        if not isinstance(item, dict) or frozenset(item) != _CLEANUP_RETAINED_FIELDS:
            raise _refuse("artifact_cleanup_invalid", f"cleanup.json.retained[{index}]")
    if retained != [dict(item) for item in verified["retained"]]:
        raise _refuse("artifact_cleanup_contradiction", "cleanup.json.retained")
    if document["unexpected"] != list(verified["unexpected"]):
        raise _refuse("artifact_cleanup_contradiction", "cleanup.json.unexpected")


def validate_bundle(root: Any, *, manifest: Any, expected_request_id: Any = None,
                    now: Any = None, owner_uid: int | None = None) -> dict[str, Any]:
    """Validate one bundle directory against its manifest and ledger record."""
    path = Path(root)
    if path.is_symlink() or not path.is_dir():
        raise _refuse("bundle_root_invalid", str(path))
    try:
        root_details = path.lstat()
    except OSError as exc:
        raise _refuse("bundle_root_invalid", str(path)) from exc
    if not stat.S_ISDIR(root_details.st_mode):
        raise _refuse("bundle_root_invalid", str(path))
    expected_owner = os.getuid() if owner_uid is None else int(owner_uid)
    if root_details.st_uid != expected_owner:
        raise _refuse("bundle_foreign_owner", str(path))
    accepted = validate_manifest(manifest)
    digest = manifest_digest(accepted)

    record_raw = _read(path / "run.json", limit=512 * 1024, owner_uid=expected_owner)
    try:
        record = validate_record(_json(record_raw, "run.json"))
    except LedgerError as exc:
        raise _refuse(exc.code, exc.location) from None
    _canonical(record_raw, record, "run.json")
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
    if record["provenance"] != "live_authorized_host":
        raise _refuse("provenance_not_live", "run.json")

    started_dt = _timestamp(record["started_at"], "run.json.started_at")
    terminal_dt = _timestamp(record["terminal_at"], "run.json.terminal_at")

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

    events_raw = _read(path / "events.json", limit=1024 * 1024,
                       owner_uid=expected_owner)
    events_document = _json(events_raw, "events.json")
    _canonical(events_raw, events_document, "events.json")
    events = validate_events(events_document, checks=tuple(record["checks"]))
    event_times = tuple(
        _timestamp(item["at"], f"events[{index}]")
        for index, item in enumerate(events)
    )
    if event_times[0] < started_dt or event_times[-1] > terminal_dt:
        raise _refuse("events_outside_run", "events")

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
    for check_id, state in record["checks"].items():
        if state == "pending":
            raise _refuse("check_incomplete", check_id)
        if terminal.get(check_id) != state:
            raise _refuse("result_contradiction", check_id)

    for name, expected in expectations.items():
        member = path / name
        raw = _read(member, limit=min(expected["max_bytes"], MAX_ARTIFACT_BYTES),
                    owner_uid=expected_owner)
        observed = hashlib.sha256(raw).hexdigest()
        if expected["sha256"] is not None and observed != expected["sha256"]:
            raise _refuse("artifact_digest_mismatch", name)
        recorded = record["artifacts"].get(name)
        if recorded is None:
            raise _refuse("artifact_unrecorded", name)
        if recorded != observed:
            raise _refuse("artifact_digest_mismatch", name)
        document = _json(raw, name)
        _canonical(raw, document, name)
        surface = raw.decode("utf-8", errors="ignore").lower()
        if any(marker in surface for marker in FAKE_MARKERS):
            raise _refuse("fake_evidence_marker", name)
        if expected["schema"] == ARTIFACT_SCHEMAS["checks.json"]:
            _validate_check_artifact(document, manifest=accepted, record=record,
                                     digest=digest)
        elif expected["schema"] == ARTIFACT_SCHEMAS["cleanup.json"]:
            _validate_cleanup_artifact(document, manifest=accepted, record=record,
                                       digest=digest)
        else:
            raise _refuse("artifact_schema_invalid", name)
    extra = tuple(sorted(set(record["artifacts"]) - set(expectations)))
    if extra:
        raise _refuse("artifact_unplanned", extra[0])
    missing = tuple(sorted(set(expectations) - set(record["artifacts"])))
    if missing:
        raise _refuse("artifact_unrecorded", missing[0])

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
    if now is None:
        raise _refuse("timestamp_required", "now")
    now_dt = _timestamp(now, "now")
    if terminal_dt > now_dt:
        raise _refuse("evidence_from_the_future", "run.json")
    age = (now_dt - terminal_dt).total_seconds()
    if age > accepted["bounds"]["max_evidence_age_seconds"]:
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
    "TERMINAL_EVENT_STATES", "validate_bundle", "validate_events",
]

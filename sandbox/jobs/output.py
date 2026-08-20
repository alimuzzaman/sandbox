"""Durable, bounded job-output storage and presentation.

The supervisor is the only pipe reader.  This module persists bytes locally before
any CLI/MCP consumer sees them, so disconnecting a caller cannot back-pressure a
test process.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sandbox.services.redaction import RedactionError, StreamingRedactor

from .models import OutputProfile, OutputQuery, validate_job_id
from .storage import StoragePressureError


class OutputError(RuntimeError):
    pass


class OutputCursorError(OutputError):
    pass


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _utc() -> float:
    return time.time()


_CURSOR_MAX = (1 << 63) - 1
_OUTPUT_STREAMS = frozenset({"combined", "stdout", "stderr"})


def _rendered_bytes(data: str) -> int:
    """Return the UTF-8 size of the exact response data string.

    ``bytes_read`` intentionally keeps its legacy page-accounting meaning. This
    separate count is measured
    after decoding, control-code filtering, base64 encoding, and any profile
    rendering so callers can account for the bytes they actually received.
    """
    return len(data.encode("utf-8"))


def _cursor(job_id: str, stream: str, sequence: int, offset: int = 0) -> str:
    """Encode the v2 opaque output position.

    ``sequence`` identifies the event and ``offset`` is a byte position inside
    that event.  The offset is deliberately part of the cursor rather than a
    separate query selector so a capped page can be resumed without dropping a
    suffix of a large event.
    """
    if (not isinstance(job_id, str) or not job_id or
            stream not in _OUTPUT_STREAMS or
            isinstance(sequence, bool) or not isinstance(sequence, int) or
            not 0 <= sequence <= _CURSOR_MAX or
            isinstance(offset, bool) or not isinstance(offset, int) or
            not 0 <= offset <= _CURSOR_MAX):
        raise OutputCursorError("output cursor position is invalid")
    payload = json.dumps({"v": 2, "j": job_id, "s": stream, "q": sequence, "o": offset},
                         separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    """Decode one strict, URL-safe JSON cursor envelope.

    A legacy v1 envelope omitted ``v`` and ``o``.  It remains readable for
    reconnecting callers, but every newly emitted cursor is v2.
    """
    if not isinstance(value, str) or not value or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError
    unpadded = value.rstrip("=")
    if len(unpadded) % 4 == 1:
        raise ValueError
    encoded = unpadded + "=" * (-len(unpadded) % 4)
    payload = base64.b64decode(encoded, altchars=b"-_", validate=True)

    duplicates: list[str] = []

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                duplicates.append(str(key))
            result[key] = item
        return result

    decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    if duplicates or not isinstance(decoded, dict):
        raise ValueError
    return decoded


def _parse_cursor(value: str, job_id: str, stream: str) -> tuple[int, int]:
    """Return ``(sequence, offset)`` for a v1 or v2 cursor.

    Parsing is intentionally strict: no unknown envelope keys, booleans, float
    positions, unbounded integers, or cross-job/stream cursors are accepted.
    """
    try:
        decoded = _decode_cursor(value)
        keys = set(decoded)
        if keys == {"j", "s", "q"}:
            sequence, offset = decoded["q"], 0
        elif (keys == {"v", "j", "s", "q", "o"} and
              isinstance(decoded["v"], int) and not isinstance(decoded["v"], bool) and
              decoded["v"] == 2):
            sequence, offset = decoded["q"], decoded["o"]
        else:
            raise ValueError
        if decoded["j"] != job_id or decoded["s"] != stream or stream not in _OUTPUT_STREAMS:
            raise ValueError
        for position in (sequence, offset):
            if (isinstance(position, bool) or not isinstance(position, int) or
                    not 0 <= position <= _CURSOR_MAX):
                raise ValueError
        return sequence, offset
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OutputCursorError("output cursor is invalid for this job and stream") from exc


def _parse_since(value: str) -> float:
    """Accept an RFC 3339 timestamp or a finite Unix-seconds value."""
    try:
        timestamp = float(value)
    except ValueError:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        except ValueError as exc:
            raise OutputError("output since value is invalid") from exc
    if timestamp != timestamp or timestamp in {float("inf"), float("-inf")}:
        raise OutputError("output since value is invalid")
    return timestamp


@dataclass(frozen=True)
class OutputEvent:
    sequence: int
    stream: str
    offset: int
    size: int
    timestamp: float

    def as_dict(self) -> dict[str, Any]:
        return {"sequence": self.sequence, "stream": self.stream, "offset": self.offset,
                "size": self.size, "timestamp": self.timestamp}


class JobOutputStore:
    """Append-only segmented byte streams plus a combined order index for one job."""

    segment_bytes = 1_048_576

    def __init__(self, storage, repository, job_id: str, *, secrets: Iterable[bytes | str] = ()) -> None:
        self.storage = storage
        self.repository = repository
        self.job_id = validate_job_id(job_id)
        self.directory = storage.job_dir(job_id) / "output"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self._redactors = {"stdout": StreamingRedactor(secrets), "stderr": StreamingRedactor(secrets)}

    def _legacy_path(self, stream: str) -> Path:
        if stream not in {"stdout", "stderr"}:
            raise OutputError("output stream is invalid")
        return self.directory / f"{stream}.bin"

    def _segments_dir(self, stream: str) -> Path:
        if stream not in {"stdout", "stderr"}:
            raise OutputError("output stream is invalid")
        return self.directory / stream

    def _segment_paths(self, stream: str) -> list[Path]:
        directory = self._segments_dir(stream)
        if directory.exists():
            return sorted(directory.glob("*.bin"))
        legacy = self._legacy_path(stream)
        return [legacy] if legacy.exists() else []

    def _stream_size(self, stream: str) -> int:
        return sum(path.stat().st_size for path in self._segment_paths(stream))

    def _stream_digest(self, stream: str) -> str | None:
        paths = self._segment_paths(stream)
        if not paths:
            return None
        digest = hashlib.sha256()
        for path in paths:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65_536), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def _append_stream(self, stream: str, content: bytes) -> int:
        """Append bytes to numbered segments and return the logical stream offset."""
        offset = self._stream_size(stream)
        legacy = self._legacy_path(stream)
        # Old persisted jobs predate segmentation. Keep their one-file layout
        # readable and appendable instead of relocating logs during a read/write.
        if legacy.exists() and not self._segments_dir(stream).exists():
            with legacy.open("ab", buffering=0) as handle:
                os.chmod(legacy, 0o600); handle.write(content); os.fsync(handle.fileno())
            return offset
        directory = self._segments_dir(stream)
        directory.mkdir(mode=0o700, exist_ok=True)
        remaining = memoryview(content)
        paths = self._segment_paths(stream)
        while remaining:
            path = paths[-1] if paths else directory / "00000000.bin"
            current = path.stat().st_size if path.exists() else 0
            if current >= self.segment_bytes:
                path = directory / f"{len(paths):08d}.bin"
                current = 0
            amount = min(len(remaining), self.segment_bytes - current)
            with path.open("ab", buffering=0) as handle:
                os.chmod(path, 0o600); handle.write(remaining[:amount]); os.fsync(handle.fileno())
            if not paths or path != paths[-1]:
                paths.append(path)
            remaining = remaining[amount:]
        return offset

    def _read_stream(self, stream: str, offset: int, size: int) -> bytes:
        remaining_offset, remaining_size, chunks = offset, size, []
        for path in self._segment_paths(stream):
            segment_size = path.stat().st_size
            if remaining_offset >= segment_size:
                remaining_offset -= segment_size
                continue
            with path.open("rb") as handle:
                handle.seek(remaining_offset)
                chunk = handle.read(remaining_size)
            chunks.append(chunk)
            remaining_size -= len(chunk)
            if remaining_size <= 0:
                break
            remaining_offset = 0
        return b"".join(chunks)

    @property
    def _events_path(self) -> Path:
        return self.directory / "combined.jsonl"

    def append(self, stream: str, chunk: bytes, *, timestamp: float | None = None) -> OutputEvent | None:
        if stream not in self._redactors:
            raise OutputError("output stream is invalid")
        # Capacity is checked before the shared streaming redactor mutates its
        # carry state. A deferred partial token must not make a storage-pressure
        # failure appear successful until finalization.
        try:
            self.storage.require_capacity(len(chunk) + 512)
        except StoragePressureError as exc:
            raise OutputError("durable output storage pressure") from exc
        content = self._redactors[stream].feed(chunk)
        return self._append_ready(stream, content, timestamp=timestamp)

    def finish(self, stream: str, *, timestamp: float | None = None) -> OutputEvent | None:
        if stream not in self._redactors:
            raise OutputError("output stream is invalid")
        event = self._append_ready(stream, self._redactors[stream].finish(), timestamp=timestamp)
        self._index(stream, complete=True)
        return event

    def complete(self) -> str:
        """Seal all retained stream indexes and return combined integrity hash."""
        self._index("stdout", complete=True)
        self._index("stderr", complete=True)
        self._index("combined", complete=True)
        return hashlib.sha256(self._events_path.read_bytes()).hexdigest() if self._events_path.exists() else hashlib.sha256(b"").hexdigest()

    def _append_ready(self, stream: str, content: bytes, *, timestamp: float | None) -> OutputEvent | None:
        if not content:
            return None
        try:
            try:
                self.storage.require_capacity(len(content) + 512)
            except StoragePressureError as exc:
                raise OutputError("durable output storage pressure") from exc
            offset = self._append_stream(stream, content)
            sequence = self._next_sequence()
            event = OutputEvent(sequence, stream, offset, len(content), timestamp or _utc())
            with self._events_path.open("ab", buffering=0) as handle:
                os.chmod(self._events_path, 0o600)
                handle.write((json.dumps(event.as_dict(), separators=(",", ":")) + "\n").encode())
                os.fsync(handle.fileno())
            self.repository.append_event(self.job_id, "output", event.as_dict())
            self._index(stream)
            self._index("combined")
            return event
        except OSError as exc:
            raise OutputError("durable output storage failed") from exc

    def _next_sequence(self) -> int:
        if not self._events_path.exists():
            return 0
        with self._events_path.open("rb") as handle:
            last = handle.readlines()[-1:]
        return int(json.loads(last[0])["sequence"]) + 1 if last else 0

    def _events(self) -> list[OutputEvent]:
        if not self._events_path.exists():
            return []
        with self._events_path.open("rb") as handle:
            return [OutputEvent(**json.loads(line)) for line in handle if line.strip()]

    @staticmethod
    def _has_unread(events: list[OutputEvent], sequence: int, offset: int) -> bool:
        """Whether a cursor position has any retained bytes after it."""
        for event in events:
            if event.sequence < sequence:
                continue
            if event.sequence == sequence:
                return offset < event.size
            return True
        return False

    @staticmethod
    def _validate_cursor_position(all_events: list[OutputEvent], stream: str,
                                  sequence: int, offset: int) -> None:
        """Reject positions outside the currently retained event range."""
        if not all_events:
            if (sequence, offset) != (0, 0):
                raise OutputCursorError("output cursor is outside the retained range")
            return
        first = all_events[0].sequence
        next_sequence = all_events[-1].sequence + 1
        if sequence < first or sequence > next_sequence:
            raise OutputCursorError("output cursor is outside the retained range")
        if sequence == next_sequence:
            if offset:
                raise OutputCursorError("output cursor is outside the retained range")
            return
        event = next((item for item in all_events if item.sequence == sequence), None)
        # A stream-specific cursor may legitimately point between two events in
        # the combined ordering.  An offset is meaningful only for the exact
        # event that belongs to the selected stream.
        if event is None or (stream != "combined" and event.stream != stream):
            if offset:
                raise OutputCursorError("output cursor is outside the retained range")
            return
        if offset >= event.size:
            raise OutputCursorError("output cursor is outside the retained range")

    def _index(self, stream: str, *, complete: bool = False) -> None:
        if stream == "combined":
            events = self._events()
            size = sum(event.size for event in events)
            digest = hashlib.sha256(self._events_path.read_bytes()).hexdigest() if self._events_path.exists() else None
            self.repository.upsert_output_stream(self.job_id, stream, bytes_stored=size,
                events_stored=len(events), next_sequence=len(events), complete=complete, sha256=digest)
            return
        paths = self._segment_paths(stream)
        size = self._stream_size(stream)
        event_count = sum(1 for event in self._events() if event.stream == stream)
        digest = self._stream_digest(stream)
        self.repository.upsert_output_stream(self.job_id, stream, bytes_stored=size,
            events_stored=event_count, next_sequence=event_count, complete=complete, sha256=digest,
            segments=len(paths), last_segment_bytes=paths[-1].stat().st_size if paths else 0)

    def read(self, query: OutputQuery) -> dict[str, Any]:
        stream = query.stream
        all_events = self._events()
        if query.cursor:
            start_sequence, start_offset = _parse_cursor(query.cursor, self.job_id, stream)
            self._validate_cursor_position(all_events, stream, start_sequence, start_offset)
            stream_events = ([event for event in all_events if event.stream == stream]
                             if stream != "combined" else list(all_events))
            # A partial cursor already has a retained suffix.  Return it without
            # waiting; only a cursor at the retained end needs long-polling.
            if query.wait_seconds and not self._has_unread(stream_events, start_sequence, start_offset):
                deadline = time.monotonic() + query.wait_seconds
                while time.monotonic() < deadline:
                    time.sleep(min(.1, deadline - time.monotonic()))
                    candidate_all = self._events()
                    candidate_stream = ([event for event in candidate_all if event.stream == stream]
                                        if stream != "combined" else list(candidate_all))
                    if self._has_unread(candidate_stream, start_sequence, start_offset):
                        all_events = candidate_all
                        break
            events = ([event for event in all_events if event.stream == stream]
                      if stream != "combined" else list(all_events))
            events = [event for event in events if event.sequence >= start_sequence]
        else:
            start_sequence = all_events[0].sequence if all_events else 0
            start_offset = 0
            events = ([event for event in all_events if event.stream == stream]
                      if stream != "combined" else list(all_events))
        if query.since is not None:
            threshold = _parse_since(query.since)
            events = [event for event in events if event.timestamp >= threshold]
        if query.tail_bytes is not None:
            consumed = 0
            selected = []
            for event in reversed(events):
                if consumed >= query.tail_bytes:
                    break
                selected.append(event); consumed += event.size
            events = list(reversed(selected))
        line_filter = query.lines
        remaining_offset = query.offset or 0
        metadata, chunks, total = [], [], 0
        consumed_events = 0
        next_sequence, next_offset = start_sequence, start_offset
        for event in events:
            event_offset = start_offset if event.sequence == start_sequence else 0
            if remaining_offset:
                available = max(0, event.size - event_offset)
                if available <= remaining_offset:
                    remaining_offset -= available
                    next_sequence, next_offset = event.sequence + 1, 0
                    continue
                event_offset += remaining_offset
                remaining_offset = 0
            available = max(0, event.size - event_offset)
            if not available:
                next_sequence, next_offset = event.sequence + 1, 0
                continue
            read_limit = min(available, query.max_bytes - total)
            if read_limit <= 0:
                break
            # Read only the bounded suffix needed by this page.  In particular,
            # profile caps applied by the service cannot cause an entire large
            # retained event to be loaded before it is truncated.
            raw = self._read_event(event, stream, event_offset, read_limit)
            if not raw:
                next_sequence, next_offset = event.sequence + 1, 0
                continue
            consumed_events += 1
            chunks.append(raw); total += len(raw)
            if not (query.cursor and event.sequence == start_sequence and start_offset):
                metadata.append(event)
            if len(raw) < available:
                next_sequence, next_offset = event.sequence, event_offset + len(raw)
                break
            next_sequence, next_offset = event.sequence + 1, 0
            if consumed_events >= query.max_events or total >= query.max_bytes:
                break
        data = b"".join(chunks)
        if line_filter is not None and query.encoding == "utf8":
            data = b"".join(data.splitlines(keepends=True)[-line_filter:])
        if query.encoding == "base64":
            rendered = base64.b64encode(data).decode()
        else:
            rendered = _CONTROL.sub("", data.decode("utf-8", errors="replace"))
        return {"ok": True, "job_id": self.job_id, "stream": stream, "profile": query.profile,
                "encoding": query.encoding, "data": rendered,
                "events": [event.as_dict() for event in metadata], "bytes_read": len(data),
                "rendered_bytes": _rendered_bytes(rendered),
                "events_read": len(metadata), "cursor": _cursor(self.job_id, stream, next_sequence, next_offset),
                "has_more": self._has_unread(events, next_sequence, next_offset), "bounded": True,
                "retained": {"first_sequence": all_events[0].sequence if all_events else 0,
                             "next_sequence": all_events[-1].sequence + 1 if all_events else 0}}

    def _read_event(self, event: OutputEvent, stream: str, offset: int = 0,
                    size: int | None = None) -> bytes:
        available = max(0, event.size - offset)
        if size is None:
            size = available
        return self._read_stream(event.stream, event.offset + offset, min(size, available))


def present_output(page: dict[str, Any], profile: OutputProfile) -> dict[str, Any]:
    """Apply a display-only profile to a bounded retained-output page."""
    if profile.mode == "full" or page["encoding"] == "base64":
        result = dict(page)
        result["rendered_bytes"] = _rendered_bytes(result["data"])
        return result
    source_lines = page["data"].splitlines(keepends=True)
    lines = source_lines
    events = list(page.get("events") or ())
    events_align_to_lines = bool(events) and len(events) == len(source_lines)
    # Output profiles are deliberately declarative. Treat include/exclude values
    # as case-insensitive literals rather than executable filters or unbounded
    # regular expressions.
    def matches(line: str, patterns: tuple[str, ...]) -> bool:
        folded = line.casefold()
        return any(pattern.casefold() in folded for pattern in patterns)

    selected = list(range(len(lines)))
    if profile.mode == "quiet":
        selected = []
    else:
        include = profile.include
        if profile.mode == "errors":
            include = (*include, "error", "fail", "exception")
        if include:
            matching = {index for index, line in enumerate(lines) if matches(line, include)}
            if profile.before or profile.after:
                contextual = set()
                for index in matching:
                    contextual.update(range(max(0, index - profile.before),
                                            min(len(lines), index + profile.after + 1)))
                matching = contextual
            selected = sorted(matching)
        if profile.exclude:
            selected = [index for index in selected if not matches(lines[index], profile.exclude)]
        if profile.mode == "sampled":
            if profile.every_lines:
                selected = [index for index in selected if (index + 1) % profile.every_lines == 0]
            if profile.every_events and events_align_to_lines:
                selected = [index for index in selected if (index + 1) % profile.every_events == 0]
            if profile.every_seconds and events_align_to_lines:
                last_timestamp = None
                sampled = []
                for index in selected:
                    timestamp = events[index].get("timestamp")
                    if not isinstance(timestamp, (int, float)):
                        continue
                    if last_timestamp is None or timestamp - last_timestamp >= profile.every_seconds:
                        sampled.append(index); last_timestamp = timestamp
                selected = sampled
    lines = [lines[index] for index in selected]
    if profile.mode == "smart":
        seen: set[str] = set(); compact = []
        for line in lines:
            if not profile.deduplicate or line not in seen:
                compact.append(line); seen.add(line)
        lines = compact
    if profile.timestamps and events_align_to_lines:
        lines = [f"[{events[index].get('timestamp')}] {line}"
                 for index, line in zip(selected, lines)]
    if profile.stream_prefixes and events_align_to_lines:
        lines = [f"[{events[index].get('stream', 'combined')}] {line}"
                 for index, line in zip(selected, lines)]
    result = dict(page)
    rendered = "".join(lines)
    bounded = rendered.encode("utf-8")[:profile.max_bytes].decode("utf-8", errors="ignore")
    result["data"] = bounded
    result["rendered_bytes"] = _rendered_bytes(bounded)
    if events_align_to_lines:
        result["events"] = [events[index] for index in selected[:profile.max_events]]
        result["events_read"] = len(result["events"])
    result["presentation"] = profile.mode
    if profile.heartbeat_seconds:
        result["presentation_heartbeat"] = {
            "interval_seconds": profile.heartbeat_seconds,
            "retained_events_observed": len(events),
        }
    return result

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import OutputProfile, OutputQuery, validate_job_id


class OutputError(RuntimeError):
    pass


class OutputCursorError(OutputError):
    pass


class RedactionError(OutputError):
    pass


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _utc() -> float:
    return time.time()


def _cursor(job_id: str, stream: str, sequence: int) -> str:
    payload = json.dumps({"j": job_id, "s": stream, "q": sequence}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _parse_cursor(value: str, job_id: str, stream: str) -> int:
    try:
        payload = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(payload)
        if decoded != {"j": job_id, "s": stream, "q": decoded.get("q")}:
            raise ValueError
        sequence = decoded["q"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError
        return sequence
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OutputCursorError("output cursor is invalid for this job and stream") from exc


class StreamingRedactor:
    """Byte-safe secret redactor with cross-chunk carry-over.

    The carry is deliberately withheld until the next append/finalization, preventing
    a secret split across two OS pipe reads from reaching retained output.
    """

    def __init__(self, secrets: Iterable[bytes | str] = ()) -> None:
        normalized = []
        for value in secrets:
            raw = value.encode() if isinstance(value, str) else value
            if not isinstance(raw, bytes) or not raw:
                raise RedactionError("redaction secrets must be non-empty bytes")
            normalized.append(raw)
        self.secrets = tuple(sorted(set(normalized), key=len, reverse=True))
        self._carry = b""
        self._window = max((len(value) for value in self.secrets), default=1) - 1

    def feed(self, chunk: bytes, *, final: bool = False) -> bytes:
        if not isinstance(chunk, bytes):
            raise RedactionError("output chunk must be bytes")
        data = self._carry + chunk
        if final:
            complete, self._carry = data, b""
        else:
            # Retain only a suffix that could become a secret once the next pipe
            # read arrives.  Fixed-size splitting is insufficient: it can emit a
            # prefix of a secret whose remainder is kept in the next chunk.
            keep_from = len(data)
            for secret in self.secrets:
                for size in range(1, min(len(secret) - 1, len(data)) + 1):
                    if data.endswith(secret[:size]):
                        keep_from = min(keep_from, len(data) - size)
            complete, self._carry = data[:keep_from], data[keep_from:]
        for secret in self.secrets:
            complete = complete.replace(secret, b"[REDACTED]")
        return complete

    def finish(self) -> bytes:
        return self.feed(b"", final=True)


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
    """Append-only byte files plus a combined order index for one job."""

    def __init__(self, storage, repository, job_id: str, *, secrets: Iterable[bytes | str] = ()) -> None:
        self.storage = storage
        self.repository = repository
        self.job_id = validate_job_id(job_id)
        self.directory = storage.job_dir(job_id) / "output"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self._redactors = {"stdout": StreamingRedactor(secrets), "stderr": StreamingRedactor(secrets)}

    def _path(self, stream: str) -> Path:
        if stream not in {"stdout", "stderr"}:
            raise OutputError("output stream is invalid")
        return self.directory / f"{stream}.bin"

    @property
    def _events_path(self) -> Path:
        return self.directory / "combined.jsonl"

    def append(self, stream: str, chunk: bytes, *, timestamp: float | None = None) -> OutputEvent | None:
        if stream not in self._redactors:
            raise OutputError("output stream is invalid")
        content = self._redactors[stream].feed(chunk)
        return self._append_ready(stream, content, timestamp=timestamp)

    def finish(self, stream: str, *, timestamp: float | None = None) -> OutputEvent | None:
        if stream not in self._redactors:
            raise OutputError("output stream is invalid")
        event = self._append_ready(stream, self._redactors[stream].finish(), timestamp=timestamp)
        self._index(stream, complete=True)
        return event

    def _append_ready(self, stream: str, content: bytes, *, timestamp: float | None) -> OutputEvent | None:
        if not content:
            return None
        path = self._path(stream)
        offset = path.stat().st_size if path.exists() else 0
        try:
            with path.open("ab", buffering=0) as handle:
                os.chmod(path, 0o600)
                handle.write(content)
                os.fsync(handle.fileno())
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

    def _index(self, stream: str, *, complete: bool = False) -> None:
        if stream == "combined":
            events = self._events()
            size = sum(event.size for event in events)
            digest = hashlib.sha256(self._events_path.read_bytes()).hexdigest() if self._events_path.exists() else None
            self.repository.upsert_output_stream(self.job_id, stream, bytes_stored=size,
                events_stored=len(events), next_sequence=len(events), complete=complete, sha256=digest)
            return
        path = self._path(stream)
        size = path.stat().st_size if path.exists() else 0
        event_count = sum(1 for event in self._events() if event.stream == stream)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        self.repository.upsert_output_stream(self.job_id, stream, bytes_stored=size,
            events_stored=event_count, next_sequence=event_count, complete=complete, sha256=digest)

    def read(self, query: OutputQuery) -> dict[str, Any]:
        stream = query.stream
        events = self._events()
        initial_count = len(events)
        if query.wait_seconds and query.cursor:
            deadline = time.monotonic() + query.wait_seconds
            while time.monotonic() < deadline:
                time.sleep(min(.1, deadline - time.monotonic()))
                candidate = self._events()
                if len(candidate) > initial_count:
                    events = candidate
                    break
        if stream != "combined":
            events = [event for event in events if event.stream == stream]
        if query.cursor:
            start_sequence = _parse_cursor(query.cursor, self.job_id, stream)
            events = [event for event in events if event.sequence >= start_sequence]
        if query.tail_bytes is not None:
            consumed = 0
            selected = []
            for event in reversed(events):
                if consumed >= query.tail_bytes:
                    break
                selected.append(event); consumed += event.size
            events = list(reversed(selected))
        line_filter = query.lines
        selected, chunks, total = [], [], 0
        for event in events:
            raw = self._read_event(event, stream)
            if query.offset is not None:
                raw = raw[max(0, query.offset - event.offset):] if event.stream == stream else raw
            if not raw:
                continue
            if total + len(raw) > query.max_bytes:
                raw = raw[:max(0, query.max_bytes - total)]
            if not raw:
                break
            selected.append(event); chunks.append(raw); total += len(raw)
            if len(selected) >= query.max_events or total >= query.max_bytes:
                break
        data = b"".join(chunks)
        if line_filter is not None and query.encoding == "utf8":
            data = b"".join(data.splitlines(keepends=True)[-line_filter:])
        if query.encoding == "base64":
            rendered = base64.b64encode(data).decode()
        else:
            rendered = _CONTROL.sub("", data.decode("utf-8", errors="replace"))
        next_sequence = (selected[-1].sequence + 1) if selected else (events[0].sequence if events else 0)
        return {"ok": True, "job_id": self.job_id, "stream": stream, "profile": query.profile,
                "encoding": query.encoding, "data": rendered,
                "events": [event.as_dict() for event in selected], "bytes_read": len(data),
                "events_read": len(selected), "cursor": _cursor(self.job_id, stream, next_sequence),
                "has_more": len(selected) < len(events), "bounded": True,
                "retained": {"first_sequence": 0, "next_sequence": len(self._events())}}

    def _read_event(self, event: OutputEvent, stream: str) -> bytes:
        path = self._path(event.stream)
        with path.open("rb") as handle:
            handle.seek(event.offset)
            return handle.read(event.size)


def present_output(page: dict[str, Any], profile: OutputProfile) -> dict[str, Any]:
    """Apply a display-only profile to a bounded retained-output page."""
    if profile.mode == "full" or page["encoding"] == "base64":
        return page
    lines = page["data"].splitlines(keepends=True)
    if profile.mode == "quiet":
        lines = []
    elif profile.mode == "errors":
        lines = [line for line in lines if re.search(r"error|fail|exception", line, re.I)]
    elif profile.mode == "sampled" and profile.every_lines:
        lines = [line for index, line in enumerate(lines, 1) if index % profile.every_lines == 0]
    elif profile.mode == "smart":
        seen: set[str] = set(); compact = []
        for line in lines:
            if not profile.deduplicate or line not in seen:
                compact.append(line); seen.add(line)
        lines = compact
    result = dict(page)
    result["data"] = "".join(lines)[:profile.max_bytes]
    result["presentation"] = profile.mode
    return result

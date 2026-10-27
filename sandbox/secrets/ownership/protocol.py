"""Private framed pipe protocol between the ownership broker and its worker.

Frame: 1 byte type + 4 byte big-endian length + payload.

* ``J`` canonical ASCII JSON, at most 64 KiB.
* ``V`` raw value bytes, at most ``MAX_PROVIDER_VALUE_BYTES``.
* ``E`` ASCII error code (``[a-z_]``, at most 64 bytes).

Anything else, an oversize length, non-canonical JSON or a frame of the wrong
type for the protocol step raises ``ProtocolError``. Payloads are never placed
in an error message.
"""

from __future__ import annotations

import json
import os
import select
from collections.abc import Callable

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.providers.base import MAX_PROVIDER_VALUE_BYTES


PROTOCOL_VERSION = 2
MAX_JSON_BYTES = 65_536
MAX_VALUE_BYTES = MAX_PROVIDER_VALUE_BYTES
MAX_ERROR_BYTES = 64
FRAME_JSON = b"J"
FRAME_VALUE = b"V"
FRAME_ERROR = b"E"
_LIMITS = {FRAME_JSON: MAX_JSON_BYTES, FRAME_VALUE: MAX_VALUE_BYTES, FRAME_ERROR: MAX_ERROR_BYTES}
_ERROR_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")


class ProtocolError(SecretBrokerError):
    """Malformed, oversize, unexpected or timed-out frame; never carries payload."""

    def __init__(self, code: str = "provider_failed") -> None:
        if code not in ("provider_failed", "operation_timeout"):
            code = "provider_failed"
        super().__init__(code, "secret ownership worker protocol failed")


def wipe(buffer: bytearray | None) -> None:
    if buffer is None:
        return
    buffer[:] = b"\0" * len(buffer)


def encode_json(message: object) -> bytes:
    try:
        payload = json.dumps(
            message, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError, OverflowError):
        raise ProtocolError() from None
    if len(payload) > MAX_JSON_BYTES:
        raise ProtocolError()
    return FRAME_JSON + len(payload).to_bytes(4, "big") + payload


def encode_value(value: bytes | bytearray) -> bytearray:
    if not isinstance(value, (bytes, bytearray)) or len(value) > MAX_VALUE_BYTES:
        raise ProtocolError()
    frame = bytearray(FRAME_VALUE)
    frame += len(value).to_bytes(4, "big")
    frame += value
    return frame


def encode_error(code: str) -> bytes:
    if (not isinstance(code, str) or not code or len(code) > MAX_ERROR_BYTES
            or any(char not in _ERROR_CHARS for char in code)):
        code = "provider_failed"
    payload = code.encode("ascii")
    return FRAME_ERROR + len(payload).to_bytes(4, "big") + payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-standard constant")


def decode_json(payload: bytes | bytearray) -> dict:
    """Decode one ``J`` payload; it must be a canonical ASCII JSON object."""
    try:
        text = bytes(payload).decode("ascii")
        message = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProtocolError() from None
    if type(message) is not dict:
        raise ProtocolError()
    if encode_json(message)[5:] != bytes(payload):
        raise ProtocolError()
    return message


def decode_error(payload: bytes | bytearray) -> str:
    try:
        code = bytes(payload).decode("ascii")
    except UnicodeDecodeError:
        return "provider_failed"
    if not code or any(char not in _ERROR_CHARS for char in code):
        return "provider_failed"
    return code


class FrameChannel:
    """Frame I/O over two fds, with an optional operation deadline.

    ``remaining`` returns the seconds left for the current operation, or
    ``None`` for an unbounded blocking read (worker side only).
    """

    def __init__(self, read_fd: int, write_fd: int,
                 remaining: Callable[[], float | None] | None = None) -> None:
        self._read_fd = read_fd
        self._write_fd = write_fd
        self._remaining = remaining or (lambda: None)

    def _wait(self, fd: int, *, write: bool) -> None:
        seconds = self._remaining()
        if seconds is not None and seconds <= 0:
            raise ProtocolError("operation_timeout")
        try:
            if write:
                _r, ready, _x = select.select([], [fd], [], seconds)
            else:
                ready, _w, _x = select.select([fd], [], [], seconds)
        except (OSError, ValueError):
            raise ProtocolError() from None
        if not ready:
            raise ProtocolError("operation_timeout")

    def write(self, frame: bytes | bytearray) -> None:
        view = memoryview(frame)
        try:
            while view:
                self._wait(self._write_fd, write=True)
                try:
                    written = os.write(self._write_fd, view)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise ProtocolError()
                view = view[written:]
        except OSError:
            raise ProtocolError() from None
        finally:
            view.release()

    def _read_exact(self, size: int) -> bytearray:
        buffer = bytearray()
        try:
            while len(buffer) < size:
                self._wait(self._read_fd, write=False)
                try:
                    chunk = os.read(self._read_fd, min(65_536, size - len(buffer)))
                except BlockingIOError:
                    continue
                if not chunk:
                    raise ProtocolError()
                buffer += chunk
                chunk = b""
        except OSError:
            wipe(buffer)
            raise ProtocolError() from None
        except ProtocolError:
            wipe(buffer)
            raise
        return buffer

    def read_frame(self) -> tuple[bytes, bytearray]:
        header = self._read_exact(5)
        kind = bytes(header[:1])
        size = int.from_bytes(header[1:], "big")
        limit = _LIMITS.get(kind)
        if limit is None or size > limit:
            raise ProtocolError()
        return kind, self._read_exact(size)

    def send_json(self, message: object) -> None:
        self.write(encode_json(message))

    def send_value(self, value: bytes | bytearray) -> None:
        frame = encode_value(value)
        try:
            self.write(frame)
        finally:
            wipe(frame)

    def send_error(self, code: str) -> None:
        self.write(encode_error(code))

    def recv_json(self, op: str | None = None) -> dict:
        """Next frame must be ``J`` (optionally with ``op``); ``E`` raises its code."""
        kind, payload = self.read_frame()
        try:
            if kind == FRAME_ERROR:
                raise WorkerError(decode_error(payload))
            if kind != FRAME_JSON:
                raise ProtocolError()
            message = decode_json(payload)
            if op is not None and message.get("op") != op:
                raise ProtocolError()
            return message
        finally:
            wipe(payload)

    def recv_value(self) -> bytearray:
        kind, payload = self.read_frame()
        if kind == FRAME_ERROR:
            try:
                raise WorkerError(decode_error(payload))
            finally:
                wipe(payload)
        if kind != FRAME_VALUE:
            wipe(payload)
            raise ProtocolError()
        return payload


class WorkerError(SecretBrokerError):
    """A fixed error code reported by the other side in an ``E`` frame."""

    def __init__(self, code: str) -> None:
        super().__init__(code, "secret ownership worker reported a failure")


__all__ = [
    "FRAME_ERROR", "FRAME_JSON", "FRAME_VALUE", "FrameChannel", "MAX_ERROR_BYTES",
    "MAX_JSON_BYTES", "MAX_VALUE_BYTES", "PROTOCOL_VERSION", "ProtocolError",
    "WorkerError", "decode_error", "decode_json", "encode_error", "encode_json",
    "encode_value", "wipe",
]

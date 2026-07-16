from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import socket
from typing import Protocol


class PortReservation(Protocol):
    @property
    def port(self) -> int: ...

    def __enter__(self) -> "PortReservation": ...
    def __exit__(self, exc_type, exc_value, traceback) -> None: ...


class PortAllocator(Protocol):
    def allocate(self, preferred: int | None = None) -> int: ...
    def reserve(self, preferred: int | None = None) -> PortReservation: ...


@dataclass
class SocketPortReservation(AbstractContextManager["SocketPortReservation"]):
    _socket: socket.socket
    port: int

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._socket.close()


class SocketPortAllocator:
    """Loopback-only allocator; reserve() prevents a TOCTOU gap until released."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("port allocator host must be loopback")
        self.host = host

    def _bind(self, preferred: int | None = None) -> socket.socket:
        if (preferred is not None and
                (isinstance(preferred, bool) or not isinstance(preferred, int) or
                 not 1 <= preferred <= 65535)):
            raise ValueError("preferred port must be between 1 and 65535")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.host, 0 if preferred is None else preferred))
        except OSError as exc:
            sock.close()
            if preferred is not None:
                raise ValueError(f"port {preferred} is unavailable") from exc
            raise RuntimeError("no port available") from exc
        return sock

    def allocate(self, preferred: int | None = None) -> int:
        with self.reserve(preferred) as reservation:
            return reservation.port

    def reserve(self, preferred: int | None = None) -> SocketPortReservation:
        sock = self._bind(preferred)
        return SocketPortReservation(sock, int(sock.getsockname()[1]))

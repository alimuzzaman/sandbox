from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import ipaddress
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


@dataclass
class SocketDnsEndpointReservation(AbstractContextManager["SocketDnsEndpointReservation"]):
    _tcp_socket: socket.socket
    _udp_socket: socket.socket
    address: str
    port: int

    def release(self) -> None:
        self._udp_socket.close()
        self._tcp_socket.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


# systemd-resolved owns these two loopback addresses for its own stub and
# DNS-proxy listeners. It silently refuses to use either as an UPSTREAM server
# (it would be routing to itself), so a scoped authority placed there is
# accepted as configuration and then never queried: the routing domain appears
# in `resolvectl status`, the DNS server does not, and every lookup NXDOMAINs.
RESOLVED_STUB_ADDRESSES = frozenset({"127.0.0.53", "127.0.0.54"})


class SocketDnsEndpointAllocator:
    """Atomically reserve one loopback endpoint for both DNS transports."""

    def __init__(self, address: str = "127.0.0.55", *, attempts: int = 32) -> None:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("DNS endpoint address must be loopback") from exc
        if not parsed.is_loopback:
            raise ValueError("DNS endpoint address must be loopback")
        if str(parsed) in RESOLVED_STUB_ADDRESSES:
            raise ValueError(
                f"DNS endpoint address {parsed} belongs to systemd-resolved's own "
                "stub listeners; a scoped authority there is never queried",
            )
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("DNS endpoint attempts must be positive")
        self.address = str(parsed)
        self.attempts = attempts
        self.family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET

    @staticmethod
    def _validate_port(preferred: int | None) -> None:
        if preferred is not None and (
            isinstance(preferred, bool) or not isinstance(preferred, int)
            or not 1024 <= preferred <= 65535
        ):
            raise ValueError("preferred DNS port must be between 1024 and 65535")

    def _once(self, preferred: int | None) -> SocketDnsEndpointReservation:
        tcp = socket.socket(self.family, socket.SOCK_STREAM)
        udp = socket.socket(self.family, socket.SOCK_DGRAM)
        try:
            tcp.bind((self.address, preferred or 0))
            port = int(tcp.getsockname()[1])
            udp.bind((self.address, port))
            return SocketDnsEndpointReservation(tcp, udp, self.address, port)
        except OSError:
            udp.close()
            tcp.close()
            raise

    def reserve(self, preferred: int | None = None) -> SocketDnsEndpointReservation:
        self._validate_port(preferred)
        for _attempt in range(1 if preferred is not None else self.attempts):
            try:
                return self._once(preferred)
            except OSError as exc:
                if preferred is not None:
                    raise ValueError(
                        f"DNS endpoint {self.address}:{preferred} is unavailable",
                    ) from exc
        raise RuntimeError("no paired UDP/TCP DNS endpoint available")

    def allocate(self, preferred: int | None = None) -> tuple[str, int]:
        with self.reserve(preferred) as reservation:
            return reservation.address, reservation.port

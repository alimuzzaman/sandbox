"""Fail-closed TCP CONNECT broker core for managed-native guests.

This module contains the protocol and policy enforcement core.  Production may
only expose it through a root-installed, supervised executable; importing this
module from a writable checkout is not an activation mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import ipaddress
import select
import socket
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, Mapping

from .models import EgressGrant, parse_utc_timestamp, public_ipv4_network


@dataclass
class _GrantState:
    grant: EgressGrant
    pinned_addresses: Mapping[str, frozenset[str]]
    revoked: bool = False
    counters: dict[str, int] = field(default_factory=lambda: {
        "accepted": 0, "rejected": 0, "bytes": 0,
    })


class EgressBroker:
    """Serve an instance's immutable grants on its exact host-side veth address."""

    def __init__(self, *, machine_id: str, host_address: str, guest_address: str,
                 interface: str, port: int, listener: Any,
                 resolver: Callable[[str], Iterable[str]], forbidden=None,
                 connector=None, relay=None, clock=None, utc_now=None,
                 max_connections=16, max_bytes=16 * 1024 * 1024,
                 deadline_seconds=30.0, max_header_bytes=4096,
                 max_tls_record_bytes=65535):
        if not isinstance(machine_id, str) or not machine_id.startswith("sb-"):
            raise ValueError("egress machine identity is invalid")
        try:
            host = ipaddress.ip_address(host_address)
            guest = ipaddress.ip_address(guest_address)
        except ValueError as exc:
            raise ValueError("egress endpoint address is invalid") from exc
        if host.version != 4 or guest.version != 4 or host == guest:
            raise ValueError("egress endpoint address is invalid")
        if (not isinstance(interface, str) or not interface or len(interface) > 15 or
                isinstance(port, bool) or not isinstance(port, int) or
                not 1024 <= port <= 65535):
            raise ValueError("egress listener identity is invalid")
        if not callable(resolver):
            raise ValueError("egress resolver is required")
        if (isinstance(max_connections, bool) or not isinstance(max_connections, int)
                or not 1 <= max_connections <= 256):
            raise ValueError("egress connection limit is invalid")
        if (isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
                or not 1 <= max_bytes <= 64 * 1024 * 1024):
            raise ValueError("egress byte limit is invalid")
        if (not isinstance(deadline_seconds, (int, float)) or
                not 0 < deadline_seconds <= 120):
            raise ValueError("egress deadline is invalid")
        self.machine_id = machine_id
        self.host_address = str(host)
        self.guest_address = str(guest)
        self.interface = interface
        self.port = port
        self.listener = listener
        self._resolver = resolver
        self._forbidden = forbidden or (lambda: ())
        self._connector = connector or (
            lambda address, target_port, timeout: socket.create_connection(
                (address, target_port), timeout=timeout,
            )
        )
        self._relay = relay or self._relay_with_clock
        self._clock = clock or time.monotonic
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._max_connections = max_connections
        self._max_bytes = max_bytes
        self._deadline_seconds = float(deadline_seconds)
        self._max_header_bytes = max_header_bytes
        self._max_tls_record_bytes = max_tls_record_bytes
        self._grants: dict[str, _GrantState] = {}
        self._active: dict[int, tuple[_GrantState, Any, Any]] = {}
        self._lock = threading.RLock()
        self._validate_listener()

    @classmethod
    def bind(cls, *, socket_factory=socket.socket, **kwargs):
        """Create a listener pinned to the exact address and veth device."""
        listener = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                kwargs["interface"].encode("ascii") + b"\0")
            listener.bind((kwargs["host_address"], kwargs["port"]))
            listener.listen(kwargs.get("max_connections", 16))
            return cls(listener=listener, **kwargs)
        except BaseException:
            cls._close(listener)
            raise

    def activate(self, grants: Iterable[EgressGrant]):
        """Activate grants, deriving hostname pins from the trusted resolver."""
        prepared: dict[str, _GrantState] = {}
        now = self._now()
        for grant in grants:
            if not isinstance(grant, EgressGrant) or grant.revoked:
                raise ValueError("egress grant is inactive")
            if grant.owner != self.machine_id:
                raise ValueError("egress grant owner does not match the instance")
            if parse_utc_timestamp(grant.expires_at) <= now:
                raise ValueError("egress grant is expired")
            if grant.grant_id in prepared or grant.grant_id in self._grants:
                raise ValueError("egress grant is already registered")
            pins = {}
            if grant.kind == "hostname_https":
                for hostname in grant.destinations:
                    resolved = frozenset(self._validated_addresses(self._resolver(hostname)))
                    if not resolved:
                        raise ValueError("hostname HTTPS egress resolved no safe addresses")
                    pins[hostname] = resolved
            prepared[grant.grant_id] = _GrantState(grant, pins)
        with self._lock:
            self._validate_listener()
            self._grants.update(prepared)

    def revoke(self, grant_id: str):
        with self._lock:
            state = self._grants.get(grant_id)
            if state is None:
                return False
            state.revoked = True
            active = tuple(self._active.items())
        for token, (active_state, client, upstream) in active:
            if active_state is state:
                self._close(client)
                self._close(upstream)
                with self._lock:
                    self._active.pop(token, None)
        if not any(self._available(item) for item in self._grants.values()):
            self._close(self.listener)
        return True

    def expire(self):
        for grant_id, state in tuple(self._grants.items()):
            if not state.revoked and parse_utc_timestamp(state.grant.expires_at) <= self._now():
                self.revoke(grant_id)

    def snapshot(self, grant_id: str):
        self.expire()
        state = self._grants.get(grant_id)
        if state is None:
            raise ValueError("egress grant is unknown")
        with self._lock:
            active = sum(1 for item, _client, _upstream in self._active.values()
                         if item is state)
            return {**state.counters, "active": active, "revoked": state.revoked}

    def serve_once(self):
        """Handle one connection; every malformed or ungranted request is closed."""
        self.expire()
        self._validate_listener()
        client = upstream = None
        state = None
        token = None
        try:
            client, peer = self.listener.accept()
            if (not isinstance(peer, tuple) or not peer or
                    str(ipaddress.ip_address(peer[0])) != self.guest_address):
                raise ValueError("egress peer is not the assigned guest")
            with self._lock:
                if len(self._active) >= self._max_connections:
                    raise ValueError("egress connection limit")
            host, port, initial = self._read_connect(client)
            state, address = self._authorize(host, port)
            upstream = self._connector(address, port, self._deadline_seconds)
            token = id(client)
            with self._lock:
                self._active[token] = (state, client, upstream)
            deadline = self._clock() + self._deadline_seconds
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if state.grant.kind == "hostname_https":
                initial = self._tls_client_hello(client, initial, host)
            if initial:
                upstream.sendall(initial)
            transferred = len(initial) + self._relay(
                client, upstream, self._max_bytes - len(initial), deadline,
            )
            if (isinstance(transferred, bool) or not isinstance(transferred, int) or
                    transferred < 0 or transferred > self._max_bytes or
                    self._clock() > deadline or not self._available(state)):
                raise ValueError("egress relay limits")
            state.counters["accepted"] += 1
            state.counters["bytes"] += transferred
            return {"ok": True, "state": "complete", "grant_id": state.grant.grant_id,
                    "mutated": False}
        except Exception:
            if state is not None:
                state.counters["rejected"] += 1
            return {"ok": False, "state": "rejected", "mutated": False}
        finally:
            if token is not None:
                with self._lock:
                    self._active.pop(token, None)
            self._close(upstream)
            self._close(client)

    def _authorize(self, host: str, port: int):
        candidates = []
        try:
            numeric = ipaddress.ip_address(host)
        except ValueError:
            numeric = None
        for state in self._grants.values():
            if not self._available(state) or port not in state.grant.ports:
                continue
            grant = state.grant
            if grant.kind == "public_cidr_tcp" and numeric is not None:
                address = self._validated_addresses((str(numeric),))[0]
                if any(ipaddress.ip_address(address) in public_ipv4_network(destination)
                       for destination in grant.destinations):
                    candidates.append((state, address))
            elif grant.kind == "hostname_https" and numeric is None:
                normalized = host.lower().rstrip(".")
                if normalized not in grant.destinations or port != 443:
                    continue
                resolved = self._validated_addresses(self._resolver(normalized))
                if resolved and set(resolved).issubset(state.pinned_addresses[normalized]):
                    candidates.append((state, resolved[0]))
        if len(candidates) != 1:
            raise ValueError("egress target does not resolve to exactly one grant")
        return candidates[0]

    def _read_connect(self, client):
        raw = b""
        while b"\r\n\r\n" not in raw and len(raw) <= self._max_header_bytes:
            chunk = client.recv(min(1024, self._max_header_bytes + 1 - len(raw)))
            if not isinstance(chunk, bytes) or not chunk:
                raise ValueError("egress request is incomplete")
            raw += chunk
        if len(raw) > self._max_header_bytes or b"\r\n\r\n" not in raw:
            raise ValueError("egress request is invalid")
        header, initial = raw.split(b"\r\n\r\n", 1)
        try:
            lines = header.decode("ascii").split("\r\n")
            method, authority, version = lines[0].split(" ")
            host, raw_port = authority.rsplit(":", 1)
            port = int(raw_port)
        except (UnicodeDecodeError, ValueError):
            raise ValueError("egress CONNECT request is invalid") from None
        if (method != "CONNECT" or version != "HTTP/1.1" or not host or
                not 1 <= port <= 65535 or any(line and ":" not in line for line in lines[1:])):
            raise ValueError("egress CONNECT request is invalid")
        return host.lower().rstrip("."), port, initial

    def _tls_client_hello(self, client, initial: bytes, hostname: str):
        raw = initial
        while len(raw) < 5:
            raw += self._recv(client, 5 - len(raw))
        if raw[0] != 22:
            raise ValueError("hostname HTTPS requires a TLS ClientHello")
        length = int.from_bytes(raw[3:5], "big")
        if not 4 <= length <= self._max_tls_record_bytes:
            raise ValueError("TLS record length is invalid")
        while len(raw) < 5 + length:
            raw += self._recv(client, 5 + length - len(raw))
        record = raw[5:5 + length]
        if record[0] != 1 or int.from_bytes(record[1:4], "big") != len(record) - 4:
            raise ValueError("hostname HTTPS requires one complete ClientHello")
        sni, ech = self._client_hello_extensions(record[4:])
        if ech or sni != hostname.lower().rstrip("."):
            raise ValueError("TLS server name does not match the hostname grant")
        return raw

    @staticmethod
    def _client_hello_extensions(body: bytes):
        try:
            offset = 2 + 32
            offset += 1 + body[offset]
            cipher_length = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2 + cipher_length
            offset += 1 + body[offset]
            extensions_length = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2
            end = offset + extensions_length
            if end != len(body):
                raise ValueError
            names = []
            ech = False
            while offset < end:
                kind = int.from_bytes(body[offset:offset + 2], "big")
                size = int.from_bytes(body[offset + 2:offset + 4], "big")
                value = body[offset + 4:offset + 4 + size]
                if len(value) != size:
                    raise ValueError
                offset += 4 + size
                if kind in {0xFE0D, 0xFFCE}:
                    ech = True
                if kind == 0:
                    list_length = int.from_bytes(value[:2], "big")
                    cursor = 2
                    if list_length != len(value) - 2:
                        raise ValueError
                    while cursor < len(value):
                        name_type = value[cursor]
                        name_length = int.from_bytes(value[cursor + 1:cursor + 3], "big")
                        name = value[cursor + 3:cursor + 3 + name_length]
                        if len(name) != name_length:
                            raise ValueError
                        cursor += 3 + name_length
                        if name_type == 0:
                            names.append(name.decode("ascii").lower().rstrip("."))
            if offset != end or len(names) != 1:
                raise ValueError
            return names[0], ech
        except (IndexError, UnicodeDecodeError, ValueError):
            raise ValueError("TLS ClientHello extensions are invalid") from None

    def _validated_addresses(self, values):
        if isinstance(values, (str, bytes)):
            raise ValueError("egress resolver result is invalid")
        forbidden = self._forbidden_networks()
        result = []
        for value in values:
            network = public_ipv4_network(f"{value}/32")
            address = network.network_address
            if any(address in blocked for blocked in forbidden):
                raise ValueError("egress target reaches a host or control address")
            result.append(str(address))
        return tuple(dict.fromkeys(result))

    def _forbidden_networks(self):
        values = self._forbidden()
        if isinstance(values, (str, bytes)):
            raise ValueError("egress forbidden-address observation is invalid")
        result = []
        for value in values:
            try:
                result.append(ipaddress.ip_network(value, strict=False))
            except (TypeError, ValueError) as exc:
                raise ValueError("egress forbidden-address observation is invalid") from exc
        return tuple(result)

    def _validate_listener(self):
        try:
            address = self.listener.getsockname()
            device = self.listener.getsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, 16)
        except (AttributeError, OSError) as exc:
            raise ValueError("egress listener identity cannot be proven") from exc
        try:
            bound_device = device.rstrip(b"\0").decode("ascii", "strict")
        except (AttributeError, UnicodeDecodeError) as exc:
            raise ValueError("egress listener identity cannot be proven") from exc
        if (not isinstance(address, tuple) or address[:2] != (self.host_address, self.port) or
                bound_device != self.interface):
            raise ValueError("egress listener is not bound to the assigned veth")

    def _available(self, state):
        return (not state.revoked and
                parse_utc_timestamp(state.grant.expires_at) > self._now())

    def _now(self):
        now = self._utc_now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("egress clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _recv(value, size):
        chunk = value.recv(size)
        if not isinstance(chunk, bytes) or not chunk:
            raise ValueError("egress stream ended early")
        return chunk

    @staticmethod
    def _close(value):
        if value is None:
            return
        try:
            value.close()
        except OSError:
            pass

    def _relay_with_clock(self, client, upstream, byte_limit, deadline):
        transferred = 0
        while self._clock() <= deadline and transferred < byte_limit:
            ready, _write, _errors = select.select(
                (client, upstream), (), (), max(0.0, deadline - self._clock()),
            )
            if not ready:
                break
            for source in ready:
                data = source.recv(min(65536, byte_limit - transferred))
                if not data:
                    return transferred
                (upstream if source is client else client).sendall(data)
                transferred += len(data)
        return transferred

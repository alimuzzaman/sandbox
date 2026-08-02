#!/usr/bin/python3
"""Root-installed, systemd-supervised egress broker for one native instance."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import struct
import sys
import threading
import time


MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
FORBIDDEN = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
MAX_CONFIG = 1024 * 1024
MAX_HEADER = 4096
MAX_TLS_RECORD = 65535
MAX_CONNECTION_BYTES = 64 * 1024 * 1024
CONNECTION_DEADLINE = 120.0


def fail(message):
    print(f"native-egress-broker: {message}", file=sys.stderr)
    raise SystemExit(69)


def parse_expiry(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        fail("invalid grant expiry")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("invalid grant expiry")
    return parsed.astimezone(timezone.utc)


def public_network(value):
    try:
        network = ipaddress.ip_network(value, strict=True)
    except (TypeError, ValueError):
        fail("invalid public destination")
    if (network.version != 4 or not network.network_address.is_global or
            not network.broadcast_address.is_global or
            any(network.overlaps(blocked) for blocked in FORBIDDEN)):
        fail("invalid public destination")
    return network


def read_config():
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    path = Path(directory) / "egress.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        fail("configuration credential is unavailable")
    try:
        details = os.fstat(descriptor)
        if not 1 <= details.st_size <= MAX_CONFIG:
            fail("configuration credential is invalid")
        payload = b""
        while len(payload) <= MAX_CONFIG:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            payload += chunk
        if len(payload) != details.st_size:
            fail("configuration credential changed")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("configuration credential is invalid")
    validate_config(value)
    return value


def validate_config(value):
    keys = {"version", "machine_id", "policy_digest", "grant_digest", "config_digest", "host_address",
            "guest_address", "interface", "port", "connection_limit", "forbidden", "grants"}
    if (not isinstance(value, dict) or set(value) != keys or value.get("version") != 1 or
            not MACHINE.fullmatch(str(value.get("machine_id", ""))) or
            not DIGEST.fullmatch(str(value.get("policy_digest", ""))) or
            not DIGEST.fullmatch(str(value.get("grant_digest", ""))) or
            not DIGEST.fullmatch(str(value.get("config_digest", ""))) or
            not isinstance(value.get("interface"), str) or
            not re.fullmatch(r"ve-[a-z0-9-]{1,12}", value["interface"]) or
            isinstance(value.get("port"), bool) or not isinstance(value.get("port"), int) or
            not 1024 <= value["port"] <= 65535 or
            isinstance(value.get("connection_limit"), bool) or
            not isinstance(value.get("connection_limit"), int) or
            not 16 <= value["connection_limit"] <= 20000 or
            not isinstance(value.get("forbidden"), list) or
            not isinstance(value.get("grants"), list) or not value["grants"]):
        fail("configuration schema is invalid")
    basis = {key: item for key, item in value.items() if key != "config_digest"}
    expected = hashlib.sha256(json.dumps(
        basis, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if value["config_digest"] != expected:
        fail("configuration digest changed")
    try:
        host = ipaddress.ip_address(value["host_address"])
        guest = ipaddress.ip_address(value["guest_address"])
    except (KeyError, ValueError):
        fail("configuration endpoint is invalid")
    if host.version != 4 or guest.version != 4 or host == guest:
        fail("configuration endpoint is invalid")
    for item in value["forbidden"]:
        try:
            ipaddress.ip_network(item, strict=False)
        except (TypeError, ValueError):
            fail("configuration forbidden network is invalid")
    ids = set()
    for grant in value["grants"]:
        grant_keys = {"grant_id", "kind", "destinations", "ports", "expires_at", "pins"}
        if (not isinstance(grant, dict) or set(grant) != grant_keys or
                not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(grant.get("grant_id", ""))) or
                grant["grant_id"] in ids or
                grant.get("kind") not in {"public_cidr_tcp", "hostname_https"} or
                not isinstance(grant.get("destinations"), list) or
                not grant["destinations"] or not isinstance(grant.get("ports"), list) or
                not grant["ports"] or not isinstance(grant.get("pins"), dict)):
            fail("configuration grant is invalid")
        ids.add(grant["grant_id"])
        parse_expiry(grant["expires_at"])
        if any(isinstance(port, bool) or not isinstance(port, int) or
               not 1 <= port <= 65535 for port in grant["ports"]):
            fail("configuration grant port is invalid")
        if (len(set(grant["ports"])) != len(grant["ports"]) or
                len(set(grant["destinations"])) != len(grant["destinations"])):
            fail("configuration grant is invalid")
        if grant["kind"] == "public_cidr_tcp":
            if grant["pins"]:
                fail("fixed grant contains hostname pins")
            for destination in grant["destinations"]:
                public_network(destination)
        else:
            if grant["ports"] != [443] or set(grant["pins"]) != set(grant["destinations"]):
                fail("hostname grant is invalid")
            for hostname, addresses in grant["pins"].items():
                if (not isinstance(hostname, str) or not HOSTNAME.fullmatch(hostname) or
                        hostname != hostname.lower().rstrip(".") or
                        not isinstance(addresses, list) or not addresses):
                    fail("hostname pins are invalid")
                for address in addresses:
                    public_network(f"{address}/32")


def local_ipv4_addresses():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        result = set()
        try:
            interface_names = [path.name for path in Path("/sys/class/net").iterdir()]
        except OSError:
            fail("local address observation failed")
        for name in interface_names:
            try:
                row = fcntl.ioctl(probe.fileno(), 0x8915,
                                  struct.pack("256s", name[:15].encode("ascii")))
            except OSError:
                continue
            result.add(socket.inet_ntoa(row[20:24]))
        if not result:
            fail("local address observation failed")
        return result
    except (OSError, struct.error):
        fail("local address observation failed")
    finally:
        probe.close()


def recv_until(client, marker, maximum):
    data = b""
    while marker not in data and len(data) <= maximum:
        chunk = client.recv(min(1024, maximum + 1 - len(data)))
        if not chunk:
            raise ValueError("stream ended")
        data += chunk
    if marker not in data or len(data) > maximum:
        raise ValueError("request too large")
    return data


def connect_request(client):
    raw = recv_until(client, b"\r\n\r\n", MAX_HEADER)
    header, initial = raw.split(b"\r\n\r\n", 1)
    try:
        lines = header.decode("ascii").split("\r\n")
        method, authority, version = lines[0].split(" ")
        host, raw_port = authority.rsplit(":", 1)
        port = int(raw_port)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("invalid CONNECT") from None
    if (method != "CONNECT" or version != "HTTP/1.1" or not host or
            not 1 <= port <= 65535 or any(line and ":" not in line for line in lines[1:])):
        raise ValueError("invalid CONNECT")
    return host.lower().rstrip("."), port, initial


def client_hello_extensions(body):
    try:
        offset = 34
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
                cursor = 2
                if int.from_bytes(value[:2], "big") != len(value) - 2:
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
        raise ValueError("invalid ClientHello") from None


def tls_client_hello(client, initial, hostname):
    raw = initial
    while len(raw) < 5:
        chunk = client.recv(5 - len(raw))
        if not chunk:
            raise ValueError("incomplete TLS record")
        raw += chunk
    if len(raw) < 5 or raw[0] != 22:
        raise ValueError("ClientHello required")
    length = int.from_bytes(raw[3:5], "big")
    if not 4 <= length <= MAX_TLS_RECORD:
        raise ValueError("invalid TLS record")
    while len(raw) < 5 + length:
        chunk = client.recv(5 + length - len(raw))
        if not chunk:
            raise ValueError("incomplete TLS record")
        raw += chunk
    record = raw[5:5 + length]
    if record[0] != 1 or int.from_bytes(record[1:4], "big") != len(record) - 4:
        raise ValueError("ClientHello required")
    sni, ech = client_hello_extensions(record[4:])
    if ech or sni != hostname:
        raise ValueError("SNI outside grant")
    return raw


class Broker:
    def __init__(self, config, control_path):
        self.config = config
        self.control_path = Path(control_path)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.active = {}
        self.counters = {grant["grant_id"]: {
            "accepted": 0, "rejected": 0, "bytes": 0,
        } for grant in config["grants"]}
        self.listener = None
        self.control = None
        self.slots = threading.BoundedSemaphore(config["connection_limit"])

    def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                            self.config["interface"].encode("ascii") + b"\0")
        listener.bind((self.config["host_address"], self.config["port"]))
        listener.listen(self.config["connection_limit"])
        listener.settimeout(0.5)
        if (listener.getsockname()[:2] != (self.config["host_address"], self.config["port"]) or
                listener.getsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, 16).
                rstrip(b"\0").decode("ascii") != self.config["interface"]):
            fail("listener identity changed")
        self.listener = listener
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.control_path.unlink()
        except FileNotFoundError:
            pass
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        control.bind(str(self.control_path))
        os.chmod(self.control_path, 0o600)
        control.listen(4)
        control.settimeout(0.5)
        self.control = control
        threading.Thread(target=self._control_loop, daemon=True).start()
        while not self.stopping.is_set():
            self.expire()
            try:
                client, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stopping.is_set():
                    break
                raise
            self.dispatch(client, peer)
        self.stop()

    def dispatch(self, client, peer):
        """Reserve capacity before creating a handler thread."""
        if not self.slots.acquire(blocking=False):
            try: client.close()
            except OSError: pass
            return False
        try:
            threading.Thread(
                target=self.handle, args=(client, peer),
                kwargs={"slot_acquired": True}, daemon=True,
            ).start()
        except BaseException:
            self.slots.release()
            try: client.close()
            except OSError: pass
            raise
        return True

    def stop(self):
        self.stopping.set()
        for value in (self.listener, self.control):
            try:
                if value: value.close()
            except OSError:
                pass
        with self.lock:
            connections = tuple(self.active.values())
            self.active.clear()
        for _grant, client, upstream in connections:
            for value in (client, upstream):
                try: value.close()
                except OSError: pass
        try: self.control_path.unlink()
        except FileNotFoundError: pass

    def expire(self):
        now = datetime.now(timezone.utc)
        expired = {grant["grant_id"] for grant in self.config["grants"]
                   if parse_expiry(grant["expires_at"]) <= now}
        with self.lock:
            connections = [(token, value) for token, value in self.active.items()
                           if value[0] in expired]
        for token, (_grant, client, upstream) in connections:
            for value in (client, upstream):
                try: value.close()
                except OSError: pass
            with self.lock: self.active.pop(token, None)

    def authorize(self, host, port):
        now = datetime.now(timezone.utc)
        local = local_ipv4_addresses()
        forbidden = tuple(ipaddress.ip_network(item, strict=False)
                          for item in self.config["forbidden"])
        matches = []
        try: numeric = ipaddress.ip_address(host)
        except ValueError: numeric = None
        for grant in self.config["grants"]:
            if parse_expiry(grant["expires_at"]) <= now or port not in grant["ports"]:
                continue
            if grant["kind"] == "public_cidr_tcp" and numeric is not None:
                if (numeric.version == 4 and numeric.is_global and str(numeric) not in local and
                        not any(numeric in blocked for blocked in forbidden) and
                        any(numeric in public_network(item) for item in grant["destinations"])):
                    matches.append((grant, str(numeric)))
            elif grant["kind"] == "hostname_https" and numeric is None and port == 443:
                if host in grant["destinations"]:
                    pins = grant["pins"][host]
                    safe = [address for address in pins if address not in local and
                            not any(ipaddress.ip_address(address) in blocked
                                    for blocked in forbidden)]
                    if safe:
                        matches.append((grant, safe[0]))
        if len(matches) != 1:
            raise ValueError("target outside grant")
        return matches[0]

    def handle(self, client, peer, *, slot_acquired=False):
        upstream = None
        grant = None
        token = id(client)
        acquired = slot_acquired or self.slots.acquire(blocking=False)
        try:
            if not acquired:
                raise ValueError("instance connection ceiling reached")
            client.settimeout(CONNECTION_DEADLINE)
            if str(ipaddress.ip_address(peer[0])) != self.config["guest_address"]:
                raise ValueError("foreign peer")
            host, port, initial = connect_request(client)
            grant, address = self.authorize(host, port)
            upstream = socket.create_connection((address, port), timeout=10)
            upstream.settimeout(CONNECTION_DEADLINE)
            with self.lock:
                self.active[token] = (grant["grant_id"], client, upstream)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if grant["kind"] == "hostname_https":
                initial = tls_client_hello(client, initial, host)
            if initial:
                upstream.sendall(initial)
            transferred = len(initial)
            deadline = time.monotonic() + CONNECTION_DEADLINE
            closed = False
            while transferred < MAX_CONNECTION_BYTES and time.monotonic() < deadline:
                readable, _writable, _errors = select.select(
                    (client, upstream), (), (), max(0, deadline - time.monotonic()))
                if not readable:
                    break
                for source in readable:
                    data = source.recv(min(65536, MAX_CONNECTION_BYTES - transferred))
                    if not data:
                        closed = True
                        break
                    (upstream if source is client else client).sendall(data)
                    transferred += len(data)
                if closed:
                    break
            if transferred >= MAX_CONNECTION_BYTES or time.monotonic() >= deadline:
                raise ValueError("connection limit exceeded")
            with self.lock:
                self.counters[grant["grant_id"]]["accepted"] += 1
                self.counters[grant["grant_id"]]["bytes"] += transferred
        except (OSError, ValueError):
            if grant is not None:
                with self.lock: self.counters[grant["grant_id"]]["rejected"] += 1
        finally:
            with self.lock: self.active.pop(token, None)
            for value in (upstream, client):
                try:
                    if value: value.close()
                except OSError: pass
            if acquired:
                self.slots.release()

    def status(self):
        self.expire()
        now = datetime.now(timezone.utc)
        with self.lock:
            active_counts = {grant_id: 0 for grant_id in self.counters}
            for grant_id, _client, _upstream in self.active.values():
                active_counts[grant_id] += 1
            grants = {grant_id: {**counter, "active": active_counts[grant_id]}
                      for grant_id, counter in self.counters.items()}
        expired = sorted(grant["grant_id"] for grant in self.config["grants"]
                         if parse_expiry(grant["expires_at"]) <= now)
        return {"ok": not self.stopping.is_set(), "machine_id": self.config["machine_id"],
                "policy_digest": self.config["policy_digest"],
                "grant_digest": self.config["grant_digest"],
                "config_digest": self.config["config_digest"],
                "listener": {"address": self.config["host_address"],
                             "port": self.config["port"],
                             "interface": self.config["interface"]},
                "connection_limit": self.config["connection_limit"],
                "grants": grants, "expired": expired}

    def _control_loop(self):
        while not self.stopping.is_set():
            try: client, _peer = self.control.accept()
            except socket.timeout: continue
            except OSError: break
            try:
                client.settimeout(1)
                if client.recv(32) != b"status\n":
                    continue
                payload = json.dumps(self.status(), sort_keys=True,
                                     separators=(",", ":")).encode() + b"\n"
                if len(payload) <= 65536:
                    client.sendall(payload)
            finally:
                client.close()


def main():
    if len(sys.argv) != 2:
        fail("usage: native-egress-broker CONTROL_SOCKET")
    config = read_config()
    broker = Broker(config, sys.argv[1])
    signal.signal(signal.SIGTERM, lambda _signum, _frame: broker.stop())
    signal.signal(signal.SIGINT, lambda _signum, _frame: broker.stop())
    broker.start()


if __name__ == "__main__":
    main()

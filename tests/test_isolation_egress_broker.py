from datetime import datetime, timezone
import socket
import unittest


class Socket:
    def __init__(self, request=b""):
        self.request = request
        self.closed = False
        self.sent = []

    def recv(self, size):
        value, self.request = self.request[:size], self.request[size:]
        return value

    def sendall(self, value): self.sent.append(value)
    def close(self): self.closed = True


class Listener:
    def __init__(self, client, *, peer="10.203.0.2", address="10.203.0.1",
                 port=18443, interface="ve-sb-demo"):
        self.client = client
        self.peer = peer
        self.address = address
        self.port = port
        self.interface = interface
        self.closed = False

    def accept(self): return self.client, (self.peer, 12345)
    def getsockname(self): return self.address, self.port
    def getsockopt(self, level, option, size):
        if (level, option, size) != (socket.SOL_SOCKET, socket.SO_BINDTODEVICE, 16):
            raise OSError
        return self.interface.encode() + b"\0"
    def close(self): self.closed = True


def client_hello(hostname, *, ech=False):
    name = hostname.encode("ascii")
    server_name = b"\x00" + len(name).to_bytes(2, "big") + name
    server_names = len(server_name).to_bytes(2, "big") + server_name
    extensions = b"\x00\x00" + len(server_names).to_bytes(2, "big") + server_names
    if ech:
        extensions += b"\xfe\x0d\x00\x00"
    body = (b"\x03\x03" + b"\0" * 32 + b"\0" + b"\0\x02\x13\x01" +
            b"\x01\x00" + len(extensions).to_bytes(2, "big") + extensions)
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


class TestEgressBroker(unittest.TestCase):
    def grant(self, grant_id="wordpress-api", kind="public_cidr_tcp",
              destinations=("8.8.8.0/24",), ports=(443,), expires="2999-01-01T00:00:00Z"):
        from sandbox.isolation.models import EgressGrant
        return EgressGrant(grant_id, "sb-0123456789ab", kind, destinations, ports, expires)

    def broker(self, listener, *, resolver=None, forbidden=lambda: (), relay=None):
        from sandbox.isolation.egress_broker import EgressBroker
        self.connected = []
        return EgressBroker(
            machine_id="sb-0123456789ab", host_address="10.203.0.1",
            guest_address="10.203.0.2", interface="ve-sb-demo", port=18443,
            listener=listener, resolver=resolver or (lambda _host: ("8.8.8.8",)),
            forbidden=forbidden,
            connector=lambda address, port, timeout: (
                self.connected.append((address, port, timeout)) or Socket()),
            relay=relay or (lambda _client, _upstream, _limit, _deadline: 12),
            clock=lambda: 10.0,
            utc_now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            max_connections=1, max_bytes=512, deadline_seconds=5,
        )

    def test_cidr_connects_only_exact_target_from_exact_guest_and_counts(self):
        client = Socket(b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n")
        listener = Listener(client)
        broker = self.broker(listener)
        broker.activate((self.grant(),))
        result = broker.serve_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["grant_id"], "wordpress-api")
        self.assertEqual(self.connected, [("8.8.8.8", 443, 5.0)])
        self.assertTrue(client.sent[0].startswith(b"HTTP/1.1 200"))
        self.assertEqual(broker.snapshot("wordpress-api"), {
            "accepted": 1, "rejected": 0, "bytes": 12, "active": 0,
            "revoked": False,
        })

    def test_listener_and_peer_must_match_the_assigned_veth(self):
        with self.assertRaisesRegex(ValueError, "assigned veth"):
            self.broker(Listener(Socket(), address="0.0.0.0"))
        listener = Listener(Socket(b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n"),
                            peer="10.203.0.6")
        broker = self.broker(listener)
        broker.activate((self.grant(),))
        self.assertFalse(broker.serve_once()["ok"])
        self.assertEqual(self.connected, [])

    def test_dynamic_host_or_control_address_deny_applies_to_fixed_grants(self):
        listener = Listener(Socket(b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n"))
        broker = self.broker(listener, forbidden=lambda: ("8.8.8.8/32",))
        broker.activate((self.grant(),))
        self.assertFalse(broker.serve_once()["ok"])
        self.assertEqual(self.connected, [])

    def test_hostname_requires_pinned_resolution_and_matching_plaintext_sni(self):
        answers = [("8.8.8.8",)]
        resolver = lambda _host: answers[-1]
        hello = client_hello("api.wordpress.org")
        listener = Listener(Socket(
            b"CONNECT api.wordpress.org:443 HTTP/1.1\r\n\r\n" + hello))
        broker = self.broker(listener, resolver=resolver)
        broker.activate((self.grant(kind="hostname_https",
                                    destinations=("api.wordpress.org",)),))
        self.assertTrue(broker.serve_once()["ok"])
        self.assertEqual(self.connected[0][:2], ("8.8.8.8", 443))

        for sni, ech in (("other.wordpress.org", False),
                         ("api.wordpress.org", True)):
            with self.subTest(sni=sni, ech=ech):
                client = Socket(b"CONNECT api.wordpress.org:443 HTTP/1.1\r\n\r\n" +
                                client_hello(sni, ech=ech))
                blocked = self.broker(Listener(client), resolver=resolver)
                blocked.activate((self.grant(kind="hostname_https",
                                             destinations=("api.wordpress.org",)),))
                self.assertFalse(blocked.serve_once()["ok"])

    def test_hostname_rebinding_is_rejected_after_activation(self):
        answers = [("8.8.8.8",)]
        resolver = lambda _host: answers[-1]
        listener = Listener(Socket(
            b"CONNECT api.wordpress.org:443 HTTP/1.1\r\n\r\n" +
            client_hello("api.wordpress.org")))
        broker = self.broker(listener, resolver=resolver)
        broker.activate((self.grant(kind="hostname_https",
                                    destinations=("api.wordpress.org",)),))
        answers.append(("8.8.4.4",))
        self.assertFalse(broker.serve_once()["ok"])
        self.assertEqual(self.connected, [])

    def test_hostname_pins_are_isolated_per_name_within_one_grant(self):
        answers = {"api.wordpress.org": ("8.8.8.8",),
                   "downloads.wordpress.org": ("8.8.4.4",)}
        resolver = lambda host: answers[host]
        listener = Listener(Socket(
            b"CONNECT api.wordpress.org:443 HTTP/1.1\r\n\r\n" +
            client_hello("api.wordpress.org")))
        broker = self.broker(listener, resolver=resolver)
        broker.activate((self.grant(kind="hostname_https", destinations=(
            "api.wordpress.org", "downloads.wordpress.org")),))
        answers["api.wordpress.org"] = ("8.8.4.4",)
        self.assertFalse(broker.serve_once()["ok"])
        self.assertEqual(self.connected, [])

    def test_expiry_and_revocation_close_active_connections(self):
        expired = self.broker(Listener(Socket()))
        with self.assertRaisesRegex(ValueError, "expired"):
            expired.activate((self.grant(expires="2025-01-01T00:00:00Z"),))

        holder = {}
        def relay(client, upstream, _limit, _deadline):
            holder["broker"].revoke("wordpress-api")
            self.assertTrue(client.closed)
            self.assertTrue(upstream.closed)
            return 0
        listener = Listener(Socket(b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n"))
        broker = self.broker(listener, relay=relay)
        holder["broker"] = broker
        broker.activate((self.grant(),))
        self.assertFalse(broker.serve_once()["ok"])
        self.assertTrue(listener.closed)
        self.assertTrue(broker.snapshot("wordpress-api")["revoked"])


if __name__ == "__main__": unittest.main()

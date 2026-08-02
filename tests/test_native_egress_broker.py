from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


BROKER = Path(__file__).parent.parent / "tools/native-helper/native-egress-broker.py"


def module():
    spec = importlib.util.spec_from_file_location("native_egress_broker_test", BROKER)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


def config(*, kind="public_cidr_tcp", destinations=None, expiry="2999-01-01T00:00:00Z"):
    destinations = destinations or (["8.8.8.0/24"] if kind == "public_cidr_tcp"
                                    else ["api.wordpress.org"])
    pins = {} if kind == "public_cidr_tcp" else {destinations[0]: ["8.8.8.8"]}
    basis = {"version": 1, "machine_id": "sb-0123456789ab",
             "policy_digest": "a" * 64, "grant_digest": "b" * 64,
             "host_address": "10.203.0.1",
             "guest_address": "10.203.0.2", "interface": "ve-sb-demo", "port": 18443,
             "connection_limit": 16,
             "forbidden": ["10.0.0.0/8", "169.254.0.0/16"],
             "grants": [{"grant_id": "api", "kind": kind,
                         "destinations": destinations, "ports": [443],
                         "expires_at": expiry, "pins": pins}]}
    digest = hashlib.sha256(json.dumps(
        basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**basis, "config_digest": digest}


def client_hello(hostname, *, ech=False):
    name = hostname.encode()
    server_name = b"\0" + len(name).to_bytes(2, "big") + name
    names = len(server_name).to_bytes(2, "big") + server_name
    extensions = b"\0\0" + len(names).to_bytes(2, "big") + names
    if ech: extensions += b"\xfe\x0d\0\0"
    body = (b"\x03\x03" + b"\0" * 32 + b"\0" + b"\0\x02\x13\x01" +
            b"\x01\0" + len(extensions).to_bytes(2, "big") + extensions)
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


class Stream:
    def __init__(self, value): self.value = value
    def recv(self, size):
        result, self.value = self.value[:size], self.value[size:]
        return result


class Closable:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


class TestNativeEgressBroker(unittest.TestCase):
    def test_config_digest_and_public_scope_are_mandatory(self):
        broker = module(); value = config()
        broker.validate_config(value)
        changed = {**value, "port": 18444}
        with self.assertRaises(SystemExit): broker.validate_config(changed)
        private = config(destinations=["10.0.0.0/8"])
        with self.assertRaises(SystemExit): broker.validate_config(private)

    def test_fixed_cidr_authorization_is_exact_and_dynamically_blocks_host_addresses(self):
        broker = module(); instance = broker.Broker(config(), "/tmp/control")
        with mock.patch.object(broker, "local_ipv4_addresses", return_value={"203.0.113.1"}):
            grant, address = instance.authorize("8.8.8.8", 443)
        self.assertEqual((grant["grant_id"], address), ("api", "8.8.8.8"))
        for host, port in (("8.8.4.4", 443), ("8.8.8.8", 80)):
            with self.subTest(host=host, port=port), \
                    mock.patch.object(broker, "local_ipv4_addresses", return_value={host}), \
                    self.assertRaises(ValueError):
                instance.authorize(host, port)

    def test_hostname_uses_only_pins_and_requires_exact_sni_without_ech(self):
        broker = module(); instance = broker.Broker(
            config(kind="hostname_https"), "/tmp/control")
        with mock.patch.object(broker, "local_ipv4_addresses", return_value={"127.0.0.1"}):
            grant, address = instance.authorize("api.wordpress.org", 443)
        self.assertEqual((grant["grant_id"], address), ("api", "8.8.8.8"))
        hello = client_hello("api.wordpress.org")
        self.assertEqual(broker.tls_client_hello(Stream(hello), b"", "api.wordpress.org"),
                         hello)
        for value in (client_hello("other.wordpress.org"),
                      client_hello("api.wordpress.org", ech=True)):
            with self.assertRaises(ValueError):
                broker.tls_client_hello(Stream(value), b"", "api.wordpress.org")

    def test_expiry_closes_active_connections_and_status_reports_counters(self):
        broker = module(); value = config(expiry="2025-01-01T00:00:00Z")
        instance = broker.Broker(value, "/tmp/control")
        client = Closable(); upstream = Closable()
        instance.active[1] = ("api", client, upstream)
        instance.counters["api"].update({"accepted": 2, "rejected": 1, "bytes": 128})
        with mock.patch.object(broker, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            instance.expire()
            status = instance.status()
        self.assertTrue(client.closed and upstream.closed)
        self.assertEqual(status["expired"], ["api"])
        self.assertEqual(status["grants"]["api"], {
            "accepted": 2, "rejected": 1, "bytes": 128, "active": 0,
        })
        self.assertEqual(status["connection_limit"], 16)

    def test_instance_connection_ceiling_rejects_before_thread_or_outbound_connect(self):
        broker = module(); instance = broker.Broker(config(), "/tmp/control")
        for _index in range(16):
            self.assertTrue(instance.slots.acquire(blocking=False))
        client = Closable()
        with mock.patch.object(broker.threading, "Thread") as thread, \
                mock.patch.object(broker.socket, "create_connection") as connect:
            self.assertFalse(instance.dispatch(client, ("10.203.0.2", 40000)))
        self.assertTrue(client.closed)
        thread.assert_not_called()
        connect.assert_not_called()


if __name__ == "__main__": unittest.main()

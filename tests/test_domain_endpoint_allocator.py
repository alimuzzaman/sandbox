from __future__ import annotations

import socket
import unittest


class TestDomainEndpointAllocator(unittest.TestCase):
    def test_reservation_holds_udp_and_tcp_on_same_endpoint(self):
        from sandbox.services.ports import SocketDnsEndpointAllocator

        allocator = SocketDnsEndpointAllocator("127.0.0.1")
        with allocator.reserve() as reservation:
            self.assertGreater(reservation.port, 0)
            for socket_type in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                contender = socket.socket(socket.AF_INET, socket_type)
                try:
                    with self.assertRaises(OSError):
                        contender.bind((reservation.address, reservation.port))
                finally:
                    contender.close()

    def test_foreign_udp_or_tcp_owner_rejects_preferred_endpoint(self):
        from sandbox.services.ports import SocketDnsEndpointAllocator

        allocator = SocketDnsEndpointAllocator("127.0.0.1")
        foreign = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        foreign.bind(("127.0.0.1", 0))
        port = foreign.getsockname()[1]
        try:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                allocator.reserve(port)
        finally:
            foreign.close()

    def test_non_loopback_address_is_rejected(self):
        from sandbox.services.ports import SocketDnsEndpointAllocator

        with self.assertRaisesRegex(ValueError, "loopback"):
            SocketDnsEndpointAllocator("0.0.0.0")


if __name__ == "__main__":
    unittest.main()


class TestResolvedStubAddressesAreRefused(unittest.TestCase):
    """systemd-resolved will not use its own stub listeners as an upstream, so
    an authority placed there is configured and then never queried."""

    def test_default_address_is_not_a_resolved_stub(self):
        from sandbox.services.ports import (
            RESOLVED_STUB_ADDRESSES, SocketDnsEndpointAllocator,
        )

        self.assertNotIn(SocketDnsEndpointAllocator().address, RESOLVED_STUB_ADDRESSES)

    def test_explicit_stub_address_is_refused_with_the_reason(self):
        from sandbox.services.ports import SocketDnsEndpointAllocator

        for address in ("127.0.0.53", "127.0.0.54"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError) as caught:
                    SocketDnsEndpointAllocator(address)
                self.assertIn("systemd-resolved", str(caught.exception))

    def test_other_loopback_addresses_are_still_accepted(self):
        from sandbox.services.ports import SocketDnsEndpointAllocator

        self.assertEqual(SocketDnsEndpointAllocator("127.0.0.55").address, "127.0.0.55")

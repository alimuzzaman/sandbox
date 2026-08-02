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

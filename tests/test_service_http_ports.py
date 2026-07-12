import socket
import unittest

from sandbox.services.ports import SocketPortAllocator


class TestPortAllocator(unittest.TestCase):
    def test_allocates_and_rejects_collision(self):
        allocator = SocketPortAllocator()
        port = allocator.allocate()
        sock = socket.socket()
        sock.bind(("127.0.0.1", port))
        try:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                allocator.allocate(port)
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()

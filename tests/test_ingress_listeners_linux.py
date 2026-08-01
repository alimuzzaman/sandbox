from __future__ import annotations

import unittest


HEADER = "  sl  local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode"


class TestLinuxIngressListeners(unittest.TestCase):
    def test_proc_tcp_parses_listen_only_and_kernel_inode(self):
        from sandbox.ingress.listeners import parse_linux_proc
        text = HEADER + "\n" + \
            "0: 4D00007F:0050 00000000:0000 0A 0:0 00:0 0 1000 0 4321\n" + \
            "1: 0100007F:0050 00000000:0000 01 0:0 00:0 0 1000 0 9999\n"
        endpoints = parse_linux_proc(text, family="ipv4")
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0].address, "127.0.0.77")
        self.assertEqual(endpoints[0].port, 80)
        self.assertEqual(endpoints[0].socket_id, "4321")

    def test_ipv4_wildcard_conflicts_but_other_exact_loopback_does_not(self):
        from sandbox.ingress.models import ListenerEndpoint
        requested = ListenerEndpoint("127.0.0.77", 80)
        self.assertTrue(ListenerEndpoint("0.0.0.0", 80).overlaps(requested))
        self.assertFalse(ListenerEndpoint("127.0.0.1", 80).overlaps(requested))

    def test_bind_probe_reports_real_free_and_conflicting_endpoints(self):
        import socket
        from sandbox.ingress.listeners import SocketBindProbe
        from sandbox.ingress.models import ListenerEndpoint
        owner = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        owner.bind(("127.0.0.1", 0)); owner.listen(1)
        port = owner.getsockname()[1]
        try:
            self.assertEqual(SocketBindProbe().check(
                ListenerEndpoint("127.0.0.1", port)), "conflict")
        finally:
            owner.close()
        self.assertEqual(SocketBindProbe().check(
            ListenerEndpoint("127.0.0.1", port)), "free")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sandbox.services.process import ProcessResult


class RecordingProcess:
    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return ProcessResult(tuple(argv), 0, "", "")


class TestDomainAuthority(unittest.TestCase):
    def test_foreign_udp_endpoint_collision_is_preserved(self):
        import socket
        from sandbox.services.ports import SocketDnsEndpointAllocator

        foreign = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        foreign.bind(("127.0.0.54", 0))
        try:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                SocketDnsEndpointAllocator("127.0.0.54").reserve(foreign.getsockname()[1])
        finally:
            foreign.close()

    def test_generated_config_is_non_forwarding_and_scoped(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        exact = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        zone = ResolutionBinding.create(
            kind="zone", name="*.site.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        text = DnsmasqAuthority.render_config(
            address="127.0.0.54", port=5300, bindings=(exact, zone),
            pid_file="/tmp/pid", log_file="/tmp/log",
        )
        self.assertIn("no-resolv", text)
        self.assertIn("no-hosts", text)
        self.assertNotIn("server=", text)
        self.assertIn("address=/demo.test/127.0.0.77", text)
        self.assertIn("address=/site.test/127.0.0.77", text)

    def test_ensure_is_idempotent_and_final_cleanup_stops_owned_pid(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            process = RecordingProcess()
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda pid, identity: pid == 123 and identity == "boot:1",
            )
            first = authority.ensure((binding,), address="127.0.0.54", port=5300)
            second = authority.ensure((binding,), address="127.0.0.54", port=5300)
            removed = authority.remove(binding.binding_id)
        self.assertEqual(first.config_digest, second.config_digest)
        starts = [call for call, _kwargs in process.calls if "--conf-file" in " ".join(call)]
        self.assertEqual(len(starts), 1)
        self.assertTrue(removed)
        self.assertTrue(any(call[:2] == ("kill", "-TERM") for call, _kwargs in process.calls))


if __name__ == "__main__":
    unittest.main()

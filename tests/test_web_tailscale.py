import contextlib
import io
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import sandbox.core._bridge as bridge
from sandbox.commands import ui_dash

TAILNET = "100.64.0.9"


class FakeServer:
    bound = []
    taken = set()

    def __init__(self, address, handler):
        if address in FakeServer.taken:
            raise OSError("in use")
        FakeServer.bound.append((address, getattr(type(self), "address_family", socket.AF_INET)))

    def serve_forever(self):
        raise KeyboardInterrupt

    def shutdown(self):
        pass

    def server_close(self):
        pass


def start(host=None, tailscale_output=None, exact=True, taken=()):
    args = SimpleNamespace(port=8765, exact_port=exact, open=False)
    if host is not None:
        args.host = host
    ip = SimpleNamespace(returncode=0 if tailscale_output else 1, stdout=tailscale_output or "", stderr="")
    FakeServer.bound, FakeServer.taken = [], set(taken)
    with mock.patch("http.server.ThreadingHTTPServer", FakeServer), \
            mock.patch.object(ui_dash.threading, "Thread"), \
            mock.patch.object(ui_dash.subprocess, "run", return_value=ip) as run, \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ui_dash.cmd_web({}, args)
    return (FakeServer.bound[-1] if FakeServer.bound else None), run


class BindAddressTest(unittest.TestCase):
    def test_by_default_it_listens_only_on_this_machine(self):
        self.assertEqual(start()[0], (("127.0.0.1", 8765), socket.AF_INET))

    def test_an_explicit_address_is_used(self):
        self.assertEqual(start("0.0.0.0")[0][0], ("0.0.0.0", 8765))

    def test_tailscale_listens_on_this_machines_tailnet_address(self):
        bound, run = start("tailscale", tailscale_output=f"{TAILNET}\n")
        self.assertEqual(bound[0], (TAILNET, 8765))
        self.assertEqual(run.call_args.args[0], ["tailscale", "ip", "-4"])

    def test_tailscale_without_a_tailnet_address_refuses_to_start(self):
        for output in (None, "203.0.113.5\n", "warning\n100.64.0.9\n"):
            with self.subTest(output=output), self.assertRaises(SystemExit):
                start("tailscale", tailscale_output=output)

    def test_a_name_is_refused_so_it_cannot_bind_whatever_it_resolves_to(self):
        with self.assertRaises(SystemExit):
            start("example.test")

    def test_an_ipv6_address_gets_an_ipv6_socket(self):
        self.assertEqual(start("::1")[0], (("::1", 8765), socket.AF_INET6))

    def test_several_addresses_share_one_port(self):
        start(["127.0.0.1", "172.17.0.1"])
        self.assertEqual([address for address, _ in FakeServer.bound],
                         [("127.0.0.1", 8765), ("172.17.0.1", 8765)])

    def test_a_port_busy_on_any_address_moves_every_address_on(self):
        start(["127.0.0.1", "172.17.0.1"], exact=False, taken={("172.17.0.1", 8765)})
        self.assertEqual([address for address, _ in FakeServer.bound][-2:],
                         [("127.0.0.1", 8766), ("172.17.0.1", 8766)])


class RequestGateTest(unittest.TestCase):
    def allowed(self, peer, bound="127.0.0.1", host="127.0.0.1:8765", origin=None):
        return ui_dash._web_request_allowed(peer, bound, host, origin)

    def test_this_machine_is_allowed(self):
        self.assertTrue(self.allowed("127.0.0.1"))
        self.assertTrue(self.allowed("::1", host="localhost:8765"))

    def test_tailnet_devices_are_allowed_when_it_listens_on_the_tailnet(self):
        self.assertTrue(self.allowed("100.100.1.2", bound=TAILNET, host=f"{TAILNET}:8765"))
        self.assertTrue(self.allowed("100.100.1.2", bound=TAILNET, host="box.example.ts.net:8765"))

    def test_tailnet_addresses_are_not_trusted_on_other_bindings(self):
        self.assertFalse(self.allowed("100.100.1.2", bound="0.0.0.0", host="203.0.113.10:8765"))

    def test_other_addresses_are_refused(self):
        self.assertFalse(self.allowed("203.0.113.7", bound=TAILNET, host=f"{TAILNET}:8765"))
        self.assertFalse(self.allowed("172.17.0.2", bound="0.0.0.0"))

    def test_a_rebound_name_is_refused(self):
        self.assertFalse(self.allowed("127.0.0.1", host="evil.example:8765"))
        self.assertFalse(self.allowed("127.0.0.1", host=None))
        self.assertFalse(self.allowed("127.0.0.1", host=""))

    def test_a_page_from_another_site_is_refused(self):
        for origin in ("http://evil.example", "null", f"http://{TAILNET}:8025",
                       "http://203.0.113.5"):
            with self.subTest(origin=origin):
                self.assertFalse(self.allowed("100.100.1.2", bound=TAILNET,
                                              host=f"{TAILNET}:8765", origin=origin))

    def test_the_panel_itself_and_loopback_proxies_are_allowed(self):
        self.assertTrue(self.allowed("100.100.1.2", bound=TAILNET, host=f"{TAILNET}:8765",
                                     origin=f"http://{TAILNET}:8765"))
        # The desktop app's proxy and the web dev server forward a loopback origin.
        self.assertTrue(self.allowed("127.0.0.1", origin="http://127.0.0.1:53012"))
        self.assertTrue(self.allowed("127.0.0.1", origin="http://localhost:5173"))

    def test_a_localhost_page_on_a_tailnet_device_is_refused(self):
        for origin in ("http://localhost:3000", "http://127.0.0.1:8000"):
            with self.subTest(origin=origin):
                self.assertFalse(self.allowed("100.100.1.2", bound=TAILNET,
                                              host=f"{TAILNET}:8765", origin=origin))


class BridgeStartTest(unittest.TestCase):
    def hosts(self, platform, gateway="172.17.0.1", returncode=0, error=None):
        inspect = SimpleNamespace(returncode=returncode, stdout=f"{gateway}\n", stderr="")
        with tempfile.TemporaryDirectory() as runtime, \
                mock.patch.object(bridge, "RUNTIME_DIR", Path(runtime)), \
                mock.patch.object(bridge.sys, "platform", platform), \
                mock.patch.object(bridge, "_bridge_port_up", side_effect=[False] + [True] * 20), \
                mock.patch.object(bridge.subprocess, "run", side_effect=error, return_value=inspect), \
                mock.patch.object(bridge.subprocess, "Popen") as popen, \
                mock.patch("time.sleep"):
            bridge._ensure_bridge_server()
        argv = popen.call_args.args[0]
        return [argv[index + 1] for index, item in enumerate(argv) if item == "--host"]

    def test_on_linux_it_listens_for_containers_on_the_docker_gateway_only(self):
        self.assertEqual(self.hosts("linux"), ["127.0.0.1", "172.17.0.1"])

    def test_on_macos_docker_desktop_reaches_it_on_loopback(self):
        self.assertEqual(self.hosts("darwin"), ["127.0.0.1"])

    def test_it_never_listens_on_every_address(self):
        for case in ({"gateway": "0.0.0.0"}, {"gateway": "203.0.113.5"}, {"gateway": "nonsense"},
                     {"returncode": 1}, {"error": OSError("no docker")},
                     {"error": subprocess.TimeoutExpired("docker", 5)}):
            with self.subTest(**{key: str(value) for key, value in case.items()}):
                self.assertEqual(self.hosts("linux", **case), ["127.0.0.1"])


if __name__ == "__main__":
    unittest.main()

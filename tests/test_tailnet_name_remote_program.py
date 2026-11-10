"""The remote program for tailnet names (spec 078), run locally against a
temporary root. ``openssl`` is real; ``nginx``, ``systemctl`` and ``ip`` are
faked through the injected runner. Placeholders only.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sandbox.hosting.tailnet_names import _remote_main as rp  # noqa: E402
from sandbox.hosting.tailnet_names import remote_program  # noqa: E402

FQDN = "app.example.test"
IP = "100.64.0.10"


class FakeHost:
    """Runner: real openssl, scripted nginx/systemctl/ip."""

    def __init__(self):
        self.nginx_ok = True
        self.reload_ok = True
        self.addresses = f"2: tailscale0    inet {IP}/32 scope global tailscale0\n"
        self.calls = []

    def __call__(self, argv, *, timeout=30, input=None, binary=False):
        self.calls.append(list(argv))
        tool = os.path.basename(argv[0])
        if tool == "nginx":
            return (0, "ok") if self.nginx_ok else (1, "emerg: bad cert")
        if tool == "systemctl":
            return (0, "") if self.reload_ok else (1, "failed")
        if tool == "ip":
            return 0, self.addresses
        return rp.run(argv, timeout=timeout, input=input, binary=binary)


class RemoteProgramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.paths = {"conf_dir": str(root / "conf.d"), "cert_root": str(root / "certs"),
                      "locks": [str(root / f"lock{i}") for i in range(3)],
                      "edge_lock": str(root / "lock1")}
        os.makedirs(self.paths["conf_dir"])
        self.host = FakeHost()

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, action, **payload):
        return rp.main({"action": action, **payload}, paths=self.paths, runner=self.host)

    def sign(self, csr_pem: str, *, name=FQDN, days=90, other_key=False) -> str:
        """A self-signed chain for the CSR (or for a different key)."""
        d = Path(self.tmp.name)
        (d / "req.csr").write_text(csr_pem)
        (d / "san.ext").write_text(f"subjectAltName=DNS:{name}\n")
        key = Path(self.paths["cert_root"]) / FQDN / "privkey.pem"
        if other_key:
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                            "-out", str(d / "other.pem")], check=True, capture_output=True)
            key = d / "other.pem"
            subprocess.run(["openssl", "req", "-new", "-key", str(key), "-subj", "/CN=" + name,
                            "-out", str(d / "req.csr")], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", str(d / "req.csr"), "-signkey", str(key),
                        "-days", str(days), "-extfile", str(d / "san.ext"), "-out", str(d / "c.pem")],
                       check=True, capture_output=True)
        return (d / "c.pem").read_text()

    def test_constants_match_the_owning_modules(self):
        from sandbox.hosting.front_door import nginx
        from sandbox.hosting.tailnet_names import models
        from sandbox.resources import host_guard
        self.assertEqual(tuple(rp.DEFAULT_PATHS["locks"]), host_guard.APPLY_TRANSACTION_LOCKS)
        self.assertEqual(rp.DEFAULT_PATHS["edge_lock"], nginx.LOCK_PATH)
        self.assertEqual(rp.DEFAULT_PATHS["conf_dir"], nginx.CONF_DIR)
        self.assertEqual(rp.DEFAULT_PATHS["cert_root"], models.CERT_ROOT)
        self.assertEqual(rp.FILE_PREFIX, nginx.FILE_PREFIX + models.ROUTE_PREFIX)

    def test_program_source_is_self_contained(self):
        source = remote_program.program_source()
        self.assertNotIn("import sandbox", source)
        self.assertNotIn("from sandbox", source)
        compile(source, "remote", "exec")

    def test_csr_creates_a_private_key_once(self):
        first = self.call("csr", fqdn=FQDN)
        self.assertTrue(first["ok"], first)
        self.assertIn("BEGIN CERTIFICATE REQUEST", first["csr"])
        key = Path(self.paths["cert_root"]) / FQDN / "privkey.pem"
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        before = key.read_bytes()
        self.assertTrue(self.call("csr", fqdn=FQDN)["ok"])
        self.assertEqual(key.read_bytes(), before)
        self.assertNotIn("PRIVATE KEY", json.dumps(first))

    def test_install_checks_the_chain_and_sets_modes(self):
        csr = self.call("csr", fqdn=FQDN)["csr"]
        result = self.call("install", fqdn=FQDN, fullchain=self.sign(csr), reload=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["sha256"]), 64)
        chain = Path(self.paths["cert_root"]) / FQDN / "fullchain.pem"
        self.assertEqual(stat.S_IMODE(chain.stat().st_mode), 0o644)
        self.assertFalse([c for c in self.host.calls if os.path.basename(c[0]) in ("nginx", "systemctl")])
        again = self.call("install", fqdn=FQDN, fullchain=chain.read_text(), reload=True)
        self.assertEqual((again["ok"], again["changed"]), (True, False))

    def test_install_refuses_wrong_key_or_name(self):
        csr = self.call("csr", fqdn=FQDN)["csr"]
        for chain in (self.sign(csr, other_key=True), self.sign(csr, name="other.example.test"),
                      "not a certificate"):
            result = self.call("install", fqdn=FQDN, fullchain=chain, reload=False)
            self.assertEqual((result["ok"], result["code"]), (False, "certificate_mismatch"))
        self.assertFalse((Path(self.paths["cert_root"]) / FQDN / "fullchain.pem").exists())

    def test_renewal_restores_the_previous_chain_when_nginx_rejects(self):
        csr = self.call("csr", fqdn=FQDN)["csr"]
        first = self.sign(csr)
        self.assertTrue(self.call("install", fqdn=FQDN, fullchain=first, reload=False)["ok"])
        self.host.nginx_ok = False
        result = self.call("install", fqdn=FQDN, fullchain=self.sign(csr, days=60), reload=True)
        self.assertEqual((result["ok"], result["code"]), (False, "nginx_rejected"))
        chain = Path(self.paths["cert_root"]) / FQDN / "fullchain.pem"
        self.assertEqual(chain.read_text(), first)
        self.assertFalse([c for c in self.host.calls if c[:2] == ["systemctl", "reload"]])
        self.host.nginx_ok, self.host.reload_ok = True, False
        result = self.call("install", fqdn=FQDN, fullchain=self.sign(csr, days=30), reload=True)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertEqual(chain.read_text(), first)

    def test_install_refuses_while_the_edge_lock_is_held(self):
        csr = self.call("csr", fqdn=FQDN)["csr"]
        fd = os.open(self.paths["edge_lock"], os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = rp.main({"action": "install", "fqdn": FQDN, "fullchain": self.sign(csr),
                              "reload": False}, paths=self.paths, runner=self.host, lock_wait=0.2)
            self.assertEqual((result["ok"], result["code"]), (False, "edge_lock_busy"))
        finally:
            os.close(fd)

    def test_inspect_reports_locks_binding_and_inventory(self):
        csr = self.call("csr", fqdn=FQDN)["csr"]
        self.call("install", fqdn=FQDN, fullchain=self.sign(csr), reload=False)
        conf = Path(self.paths["conf_dir"]) / f"sandbox-tailnet-{FQDN}.conf"
        conf.write_text('# sandbox-tailnet-name {"fqdn": "%s", "tailnet_ip": "%s", '
                        '"upstream": "127.0.0.1:8025", "version": 1}\nserver {}\n' % (FQDN, IP))
        (Path(self.paths["conf_dir"]) / "sandbox-other.conf").write_text("server {}\n")
        fd = os.open(self.paths["locks"][2], os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = self.call("inspect", tailnet_ip=IP)
        finally:
            os.close(fd)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["tailnet_ip_bound"])
        self.assertEqual(result["locks_held"], [self.paths["locks"][2]])
        [row] = result["inventory"]
        self.assertEqual(row["fqdn"], FQDN)
        self.assertTrue(row["block_present"] and row["cert_present"] and row["key_present"])
        self.assertTrue(row["key_matches"])
        self.assertEqual(row["cert_names"], [FQDN])
        self.assertEqual(row["header"]["upstream"], "127.0.0.1:8025")
        self.assertRegex(row["cert_not_after"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.host.addresses = "2: eth0    inet 203.0.113.10/24 scope global eth0\n"
        self.assertFalse(self.call("inspect", tailnet_ip=IP, fqdn=FQDN)["tailnet_ip_bound"])

    def test_remove_cert_refuses_while_the_block_exists(self):
        self.call("csr", fqdn=FQDN)
        conf = Path(self.paths["conf_dir"]) / f"sandbox-tailnet-{FQDN}.conf"
        conf.write_text("server {}\n")
        result = self.call("remove_cert", fqdn=FQDN)
        self.assertEqual((result["ok"], result["code"]), (False, "block_present"))
        conf.unlink()
        self.assertTrue(self.call("remove_cert", fqdn=FQDN)["ok"])
        self.assertFalse((Path(self.paths["cert_root"]) / FQDN).exists())
        self.assertEqual(self.call("remove_cert", fqdn=FQDN)["removed"], False)

    def test_payload_validation(self):
        for payload in ({"action": "csr", "fqdn": "../etc"}, {"action": "csr", "fqdn": "A.example.test"},
                        {"action": "nope"}, {"action": "inspect", "tailnet_ip": "203.0.113.1"}):
            with self.subTest(payload=payload):
                result = rp.main(payload, paths=self.paths, runner=self.host)
                self.assertEqual((result["ok"], result["code"]), (False, "request_invalid"))


if __name__ == "__main__":
    unittest.main()

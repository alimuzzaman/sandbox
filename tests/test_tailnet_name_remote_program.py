"""The remote program for tailnet names (spec 078), run locally against a
temporary root. ``openssl`` is real; ``nginx``, ``systemctl``, ``ip`` and the
remote ``sb job-list`` are faked through the injected runner. Placeholders only.
"""
from __future__ import annotations

import fcntl
import json
import os
import pwd
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sandbox.hosting.tailnet_names import _remote_main as rp  # noqa: E402
from sandbox.hosting.tailnet_names import remote_program  # noqa: E402
from tests.subprocess_support import run_test_process  # noqa: E402

FQDN = "app.example.test"
IP = "100.64.0.10"
TOKEN = "a" * 32
OTHER = "b" * 32
def block(fqdn=FQDN, port=8025, body=""):
    """A block as the controller renders it (header, marker); ``body`` is
    appended to make a corrupt variant."""
    from sandbox.hosting.tailnet_names import models, render
    return render.render_block(models.TailnetName(fqdn, "example.test"),
                               models.Upstream("127.0.0.1", port), IP,
                               http2_directive=True) + body


def openssl(argv, *, input=None, check=False):
    """Real openssl with a synthetic environment (never the parent's)."""
    res = run_test_process(list(argv), input=input, capture_output=True, timeout=60)
    if check and res.returncode != 0:
        raise AssertionError(res.stderr)
    return res.returncode, res.stdout or b"", res.stderr or b""


class FakeHost:
    """Runner: real openssl, scripted nginx/systemctl/ip/job-list."""

    def __init__(self):
        self.nginx_ok = True
        self.reload_ok = True
        self.reload_accepted = True  # False: reload acknowledged, old config kept
        self.loaded = {}  # fqdn -> (block bytes | None, chain bytes | None) nginx serves
        self.paths = None
        self.jobs = []
        self.jobs_seq = None  # per-query answers, to admit a job mid-commit
        self.jobs_rc = 0
        self.addresses = f"2: tailscale0    inet {IP}/32 scope global tailscale0\n"
        self.calls = []

    def __call__(self, argv, *, timeout=30, input=None):
        self.calls.append(list(argv))
        if "job-list" in argv:
            if self.jobs_rc:
                return 1, b"", b"failed"
            jobs = self.jobs_seq.pop(0) if self.jobs_seq else self.jobs
            return 0, (json.dumps({"ok": True, "jobs": jobs}) + "\n").encode(), b""
        tool = os.path.basename(argv[0])
        if tool == "nginx":
            return (0, b"ok", b"") if self.nginx_ok else (1, b"", b"emerg: bad cert")
        if tool == "systemctl":
            if argv[1] == "reload":
                if not self.reload_ok:
                    return 1, b"", b"failed"
                if self.reload_accepted:
                    self.snapshot()
            return 0, b"", b""
        if tool == "ip":
            return 0, self.addresses.encode(), b""
        return openssl(argv, input=input)

    def snapshot(self):
        """An accepted reload: nginx now serves exactly what is on disk."""
        conf, certs = Path(self.paths["conf_dir"]), Path(self.paths["cert_root"])
        self.loaded = {}
        names = {p.name[len("sandbox-tailnet-"):-len(".conf")] for p in conf.glob("sandbox-tailnet-*.conf")}
        if certs.is_dir():
            names |= {p.name for p in certs.iterdir() if not p.name.startswith(".")}
        for name in names:
            block_path = conf / f"sandbox-tailnet-{name}.conf"
            chain_path = certs / name / "fullchain.pem"
            self.loaded[name] = (block_path.read_bytes() if block_path.exists() else None,
                                 chain_path.read_bytes() if chain_path.exists() else None)

    def served(self, ip, fqdn):
        """What the fake nginx presents for SNI ``fqdn`` on the tailnet address."""
        entry = self.loaded.get(fqdn)
        if entry and entry[0] is not None:
            return ("handshake", rp._leaf_sha(entry[1]) if entry[1] else None,
                    (rp._marker_of(entry[0]) or "").encode())
        if any(other[0] is not None for name, other in self.loaded.items() if name != fqdn):
            return "handshake", "e" * 64, b""
        return "refused", None, None

    def ran(self, *prefix):
        return [call for call in self.calls if call[:len(prefix)] == list(prefix)]


class Base(unittest.TestCase):
    def setUp(self):
        # Canonical path: the program refuses every symlinked component, and
        # /tmp itself is a symlink on some systems.
        tmp_parent = os.path.realpath("/tmp")
        st = os.lstat(tmp_parent)
        if st.st_uid != 0 or not st.st_mode & stat.S_ISVTX:
            self.skipTest("needs a root-owned sticky /tmp")
        self.base = Path(os.path.realpath(tempfile.mkdtemp(dir=tmp_parent)))
        os.chmod(self.base, 0o755)
        uid = os.getuid()
        self.paths = {"conf_dir": str(self.base / "conf.d"), "cert_root": str(self.base / "certs"),
                      "locks": [str(self.base / f"lock{i}") for i in range(3)],
                      "edge_lock": str(self.base / "lock1"), "trusted_uids": [0, uid],
                      "reload_wait": 0.5}
        os.mkdir(self.paths["conf_dir"], 0o755)
        os.chmod(self.paths["conf_dir"], 0o755)
        self.home = self.base / "home"
        (self.home / "runtime" / "jobs").mkdir(parents=True)
        (self.home / "runtime" / "jobs" / "registry.sqlite3").write_bytes(b"")
        self.probe = {"home": str(self.home)}
        self.host = FakeHost()
        self.host.paths = self.paths
        self.paths["served"] = self.host.served
        env = mock.patch.dict(os.environ, {"SUDO_USER": pwd.getpwuid(uid).pw_name})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        for root, dirs, _files in os.walk(self.base):
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o755)
        shutil.rmtree(self.base, ignore_errors=True)

    @property
    def name_dir(self) -> Path:
        return Path(self.paths["cert_root"]) / FQDN

    @property
    def conf(self) -> Path:
        return Path(self.paths["conf_dir"]) / f"sandbox-tailnet-{FQDN}.conf"

    def call(self, action, lock_wait=2, **payload):
        if action in ("commit", "remove"):
            payload.setdefault("tailnet_ip", IP)
        return rp.main({"action": action, **payload}, paths=self.paths, runner=self.host,
                       lock_wait=lock_wait)

    def reserve(self, token=TOKEN, fqdn=FQDN, seconds=600):
        return self.call("reserve", fqdn=fqdn, token=token, seconds=seconds)

    def csr(self, token=TOKEN):
        result = self.call("csr", fqdn=FQDN, token=token)
        self.assertTrue(result["ok"], result)
        return result["csr"]

    def sign(self, csr_pem: str, *, name=FQDN, days=90, other_key=False) -> str:
        """A self-signed chain for the CSR's key (or for a different key)."""
        d = self.base / "signing"
        d.mkdir(exist_ok=True)
        (d / "req.csr").write_text(csr_pem)
        (d / "san.ext").write_text(f"subjectAltName=DNS:{name}\n")
        key = self.name_dir / "privkey.pem"
        if other_key:
            openssl(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                     "-out", str(d / "other.pem")], check=True)
            key = d / "other.pem"
            openssl(["openssl", "req", "-new", "-key", str(key), "-subj", "/CN=" + name,
                     "-out", str(d / "req.csr")], check=True)
        openssl(["openssl", "x509", "-req", "-in", str(d / "req.csr"), "-signkey", str(key),
                 "-days", str(days), "-extfile", str(d / "san.ext"),
                 "-out", str(d / "c.pem")], check=True)
        return (d / "c.pem").read_text()

    def commit(self, token=TOKEN, **payload):
        payload.setdefault("jobs_probe", self.probe)
        return self.call("commit", fqdn=FQDN, token=token, **payload)

    def publish(self, port=8025):
        self.assertTrue(self.reserve()["ok"])
        chain = self.sign(self.csr())
        result = self.commit(block=block(port=port), chain=chain)
        self.assertTrue(result["ok"], result)
        return chain


class ProgramShapeTests(Base):
    def test_constants_match_the_owning_modules(self):
        from sandbox.hosting.front_door import nginx
        from sandbox.hosting.tailnet_names import models
        from sandbox.resources import host_guard
        self.assertEqual(tuple(rp.DEFAULT_PATHS["locks"]), host_guard.APPLY_TRANSACTION_LOCKS)
        self.assertEqual(rp.DEFAULT_PATHS["edge_lock"], nginx.LOCK_PATH)
        self.assertEqual(rp.DEFAULT_PATHS["conf_dir"], nginx.CONF_DIR)
        self.assertEqual(rp.DEFAULT_PATHS["cert_root"], models.CERT_ROOT)
        self.assertEqual(rp.DEFAULT_PATHS["trusted_uids"], [0])
        self.assertEqual(rp.FILE_PREFIX, nginx.FILE_PREFIX + models.ROUTE_PREFIX)

    def test_program_source_is_self_contained(self):
        source = remote_program.program_source()
        self.assertNotIn("import sandbox", source)
        self.assertNotIn("from sandbox", source)
        compile(source, "remote", "exec")

    def test_payload_validation(self):
        for payload in ({"action": "csr", "fqdn": "../etc", "token": TOKEN},
                        {"action": "csr", "fqdn": "A.example.test", "token": TOKEN},
                        {"action": "csr", "fqdn": FQDN, "token": "short"},
                        {"action": "reserve", "fqdn": FQDN, "token": TOKEN, "seconds": 999999},
                        {"action": "nope"}, {"action": "inspect", "tailnet_ip": "203.0.113.1"}):
            with self.subTest(payload=payload):
                result = rp.main(payload, paths=self.paths, runner=self.host)
                self.assertEqual((result["ok"], result["code"]), (False, "request_invalid"))


class KeyAndPathTests(Base):
    """F3: no symlinks, exclusive staging, a trusted 0600 key."""

    def test_csr_creates_a_private_key_once_and_marks_the_directory(self):
        self.reserve()
        first = self.csr()
        self.assertIn("BEGIN CERTIFICATE REQUEST", first)
        key = self.name_dir / "privkey.pem"
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        marker = json.loads((self.name_dir / "managed.json").read_text())
        self.assertEqual((marker["owner"], marker["fqdn"]), ("sandbox-tailnet-name", FQDN))
        before = key.read_bytes()
        self.csr()
        self.assertEqual(key.read_bytes(), before)
        # openssl never got a path to the key; it read stdin.
        for call in self.host.calls:
            self.assertFalse(any(str(self.name_dir) in part for part in call), call)
        self.assertEqual([p for p in os.listdir(self.name_dir) if ".tmp-" in p], [])

    def test_an_existing_key_with_a_loose_mode_is_refused(self):
        self.reserve()
        self.csr()
        os.chmod(self.name_dir / "privkey.pem", 0o644)
        result = self.call("csr", fqdn=FQDN, token=TOKEN)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))

    def test_a_symlinked_key_is_refused_and_its_target_untouched(self):
        self.reserve()
        self.csr()
        target = self.base / "elsewhere.pem"
        target.write_text("not yours")
        os.chmod(target, 0o600)
        (self.name_dir / "privkey.pem").unlink()
        os.symlink(target, self.name_dir / "privkey.pem")
        result = self.call("csr", fqdn=FQDN, token=TOKEN)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))
        self.assertEqual(target.read_text(), "not yours")

    def test_symlinked_or_writable_directories_are_refused(self):
        self.reserve()
        self.csr()
        real = self.base / "real-dir"
        os.rename(self.name_dir, real)
        os.symlink(real, self.name_dir)
        result = self.call("csr", fqdn=FQDN, token=TOKEN)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))
        os.unlink(self.name_dir)
        os.rename(real, self.name_dir)
        os.chmod(self.name_dir, 0o777)
        result = self.call("csr", fqdn=FQDN, token=TOKEN)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))
        os.chmod(self.name_dir, 0o755)
        root = Path(self.paths["cert_root"])
        os.chmod(root, 0o775)
        result = self.call("csr", fqdn=FQDN, token=TOKEN)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))

    def test_a_symlinked_cert_root_is_refused(self):
        real = self.base / "real-root"
        os.mkdir(real, 0o755)
        os.chmod(real, 0o755)
        os.symlink(real, self.paths["cert_root"])
        result = self.reserve()
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))
        self.assertEqual(os.listdir(real), [])

    def test_write_never_follows_or_replaces_a_non_file(self):
        fd = os.open(str(self.base), os.O_RDONLY | os.O_DIRECTORY)
        try:
            target = self.base / "target"
            target.write_text("keep")
            os.symlink(target, self.base / "link")
            with self.assertRaises(rp.Refusal) as caught:
                rp._write(fd, "link", b"x", 0o600)
            self.assertEqual(caught.exception.code, "path_unsafe")
            self.assertEqual(target.read_text(), "keep")
            rp._write(fd, "plain", b"data", 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(self.base / "plain").st_mode), 0o600)
            self.assertEqual([p for p in os.listdir(self.base) if ".tmp-" in p], [])
        finally:
            os.close(fd)


class OwnershipTests(Base):
    """F4: only marked directories and headed 0600 blocks are Sandbox's."""

    def test_an_unmarked_certificate_directory_is_left_alone(self):
        self.reserve()
        os.chmod(self.paths["cert_root"], 0o755)
        os.mkdir(self.name_dir, 0o755)
        os.chmod(self.name_dir, 0o755)
        (self.name_dir / "privkey.pem").write_text("someone else's")
        os.chmod(self.name_dir / "privkey.pem", 0o600)
        for result in (self.call("csr", fqdn=FQDN, token=TOKEN),
                       self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)):
            self.assertEqual((result["ok"], result["code"]), (False, "unmanaged"))
        self.assertEqual((self.name_dir / "privkey.pem").read_text(), "someone else's")

    def test_a_foreign_block_is_refused_on_commit_and_remove(self):
        chain = self.publish()
        for content, mode in (("server { listen 443; }\n", 0o600),
                              (block(fqdn="other.example.test"), 0o600),
                              (block(), 0o644)):
            with self.subTest(content=content[:30], mode=oct(mode)):
                self.conf.write_text(content)
                os.chmod(self.conf, mode)
                result = self.commit(block=block(port=9000), chain=chain)
                self.assertEqual((result["ok"], result["code"]), (False, "unmanaged"))
                result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
                self.assertEqual((result["ok"], result["code"]), (False, "unmanaged"))
                self.assertEqual(self.conf.read_text(), content)

    def test_remove_refuses_unknown_files_in_the_directory(self):
        self.publish()
        (self.name_dir / "notes.txt").write_text("operator's")
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertEqual((result["ok"], result["code"]), (False, "unmanaged"))
        self.assertTrue((self.name_dir / "notes.txt").exists())

    def test_remove_takes_the_block_down_then_the_directory(self):
        self.publish()
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["block_removed"], result["directory_removed"]), (True, True))
        self.assertFalse(self.conf.exists())
        self.assertFalse(self.name_dir.exists())
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), 2)

    def test_remove_keep_directory_only_takes_the_block_down(self):
        self.publish()
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe,
                           keep_directory=True)
        self.assertTrue(result["ok"], result)
        self.assertFalse(self.conf.exists())
        self.assertTrue((self.name_dir / "privkey.pem").exists())
        # The lease is kept for the controller's next run.
        self.assertTrue(self.csr())


class LeaseAndGateTests(Base):
    """F7: a per-name lease, and the gate re-checked inside the edge lock."""

    def test_a_second_controller_is_busy_while_the_lease_is_live(self):
        self.assertTrue(self.reserve()["ok"])
        result = self.reserve(token=OTHER)
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))
        result = self.call("csr", fqdn=FQDN, token=OTHER)
        self.assertEqual((result["ok"], result["code"]), (False, "lease_lost"))
        self.assertTrue(self.reserve()["ok"])  # the holder may renew its own lease

    def test_an_expired_lease_can_be_taken_over_and_the_old_holder_is_stopped(self):
        self.assertTrue(self.reserve()["ok"])
        lease = self.name_dir.parent / ".leases" / f"{FQDN}.json"
        data = json.loads(lease.read_text())
        data["expires"] = time.time() - 1
        os.chmod(lease, 0o600)
        lease.write_text(json.dumps(data))
        self.assertTrue(self.reserve(token=OTHER)["ok"])
        result = self.call("csr", fqdn=FQDN, token=TOKEN)
        self.assertEqual((result["ok"], result["code"]), (False, "lease_lost"))

    def test_commit_rechecks_host_locks_inside_the_edge_lock(self):
        self.reserve()
        chain = self.sign(self.csr())
        fd = os.open(self.paths["locks"][2], os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = self.commit(block=block(), chain=chain)
        finally:
            os.close(fd)
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))
        self.assertFalse(self.conf.exists())
        self.assertFalse(self.host.ran("nginx"))

    def test_commit_waits_on_the_edge_lock(self):
        self.reserve()
        chain = self.sign(self.csr())
        fd = os.open(self.paths["edge_lock"], os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = self.call("commit", lock_wait=0.2, fqdn=FQDN, token=TOKEN, block=block(),
                               chain=chain, jobs_probe=self.probe)
        finally:
            os.close(fd)
        self.assertEqual((result["ok"], result["code"]), (False, "lock_busy"))
        self.assertFalse(self.conf.exists())

    def test_commit_rechecks_durable_jobs_and_fails_closed(self):
        self.reserve()
        chain = self.sign(self.csr())
        self.host.jobs = [{"id": "j1"}]
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))
        self.host.jobs, self.host.jobs_rc = [], 1
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "gate_unavailable"))
        self.host.jobs_rc = 0
        result = self.commit(block=block(), chain=chain, jobs_probe=None)
        self.assertEqual((result["ok"], result["code"]), (False, "request_invalid"))
        (self.home / "runtime" / "jobs" / "registry.sqlite3").unlink()
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "gate_unavailable"))
        self.assertFalse(self.conf.exists())
        self.assertFalse(self.host.ran("nginx"))

    def test_job_probe_runs_as_the_invoking_user_with_an_explicit_home(self):
        calls = []

        def runner(argv, *, timeout=30, input=None):
            calls.append(argv)
            return 0, b'{"ok": true, "jobs": []}\n', b""

        self.assertFalse(rp._jobs_active(self.probe, runner))
        argv = calls[-1]
        self.assertEqual(argv[:4], ["runuser", "-u", pwd.getpwuid(os.getuid()).pw_name, "--"])
        self.assertIn("SANDBOX_HOME=" + str(self.home), argv)
        self.assertIn(str(self.home) + "/sb-src/sb", argv)
        other = mock.Mock(pw_uid=os.getuid() + 1)
        with self.assertRaises(rp.Refusal) as caught:
            rp._jobs_active(self.probe, runner, getpwnam=lambda user: other)
        self.assertEqual(caught.exception.code, "gate_unavailable")
        for home in ("relative/home", "/x/../etc", "/x//y", None):
            with self.assertRaises(rp.Refusal) as caught:
                rp._jobs_active({"home": home}, runner)
            self.assertEqual(caught.exception.code, "request_invalid")


class ActivationTests(Base):
    """F8: written -> tested -> loaded; a rerun reconciles, never skips."""

    def journal(self):
        return json.loads((self.name_dir / "activation.json").read_text())

    def test_commit_installs_tests_reloads_and_records_loaded(self):
        self.publish()
        self.assertEqual(stat.S_IMODE(self.conf.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.name_dir / "fullchain.pem").stat().st_mode), 0o644)
        self.assertEqual(self.journal()["state"], "loaded")
        self.assertEqual(len(self.host.ran("nginx", "-t")), 1)
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), 1)

    def test_an_identical_loaded_commit_is_unchanged(self):
        chain = self.publish()
        before = len(self.host.calls)
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["changed"]), (True, False))
        self.assertFalse([c for c in self.host.calls[before:] if c[0] in ("nginx", "systemctl")])

    def test_files_on_disk_without_a_loaded_journal_are_tested_and_reloaded(self):
        chain = self.publish()
        (self.name_dir / "activation.json").unlink()
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["changed"]), (True, True))
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), 2)

    def test_a_crash_after_writing_is_settled_by_the_next_run(self):
        chain = self.publish()
        os.chmod(self.name_dir / "activation.json", 0o600)
        old = block().encode()
        (self.name_dir / "block.prev").write_bytes(old)
        os.chmod(self.name_dir / "block.prev", 0o600)
        self.conf.write_text(block(port=9000))
        os.chmod(self.conf, 0o600)
        (self.name_dir / "activation.json").write_text(json.dumps(
            {"state": "written", "generation": "0" * 64, "had_block": True, "had_chain": True,
             "chain_changed": False}))
        result = self.commit(block=block(port=9000), chain=chain)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["settled"], "reloaded")
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), 2)
        self.assertEqual(self.journal()["state"], "loaded")

    def test_an_unsettleable_crash_restores_the_previous_block(self):
        chain = self.publish()
        old = block().encode()
        (self.name_dir / "block.prev").write_bytes(old)
        os.chmod(self.name_dir / "block.prev", 0o600)
        (self.name_dir / "fullchain.prev.pem").write_text(chain)
        os.chmod(self.name_dir / "fullchain.prev.pem", 0o644)
        self.conf.write_text(block(port=9000, body="broken {\n"))
        os.chmod(self.conf, 0o600)
        (self.name_dir / "activation.json").write_text(json.dumps(
            {"state": "written", "generation": "0" * 64, "had_block": True, "had_chain": True,
             "chain_changed": False}))
        calls = {"n": 0}
        real = self.host.__call__

        def nginx_fails_once(argv, **kwargs):
            if argv[:2] == ["nginx", "-t"]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return 1, b"", b"emerg"
            return real(argv, **kwargs)

        result = rp.main({"action": "commit", "fqdn": FQDN, "token": TOKEN, "block": block(),
                          "chain": chain, "jobs_probe": self.probe, "tailnet_ip": IP},
                         paths=self.paths, runner=nginx_fails_once)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["settled"], "restored")
        self.assertEqual(self.conf.read_bytes(), old)

    def test_nginx_rejection_restores_the_previous_chain_and_block(self):
        chain = self.publish()
        self.host.nginx_ok = False
        result = self.commit(block=block(port=9000), chain=self.sign(self.csr(), days=60))
        self.assertEqual((result["ok"], result["code"]), (False, "nginx_rejected"))
        self.assertEqual((self.name_dir / "fullchain.pem").read_text(), chain)
        self.assertEqual(self.conf.read_text(), block())
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), 1)

    def test_reload_failure_restores_and_is_not_recorded_as_loaded(self):
        chain = self.publish()
        self.host.reload_ok = False
        result = self.commit(block=block(port=9000), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertEqual(self.conf.read_text(), block())
        self.assertNotEqual(self.journal()["state"], "loaded")
        self.host.reload_ok = True
        result = self.commit(block=block(), chain=chain)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.journal()["state"], "loaded")

    def test_a_chain_for_another_key_or_name_is_refused(self):
        self.reserve()
        csr = self.csr()
        for chain in (self.sign(csr, other_key=True), self.sign(csr, name="other.example.test"),
                      "not a certificate"):
            result = self.commit(block=block(), chain=chain)
            self.assertEqual((result["ok"], result["code"]), (False, "certificate_mismatch"))
        self.assertFalse((self.name_dir / "fullchain.pem").exists())
        self.assertFalse(self.conf.exists())

    def test_a_block_without_this_names_header_is_refused(self):
        self.reserve()
        chain = self.sign(self.csr())
        result = self.commit(block=block(fqdn="other.example.test"), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "request_invalid"))


class InspectTests(Base):
    def test_inspect_reports_locks_binding_ownership_and_activation(self):
        self.publish()
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
        self.assertTrue(row["block_present"] and row["block_managed"] and row["dir_managed"])
        self.assertTrue(row["cert_present"] and row["key_present"] and row["key_matches"])
        self.assertEqual(row["activation"], "loaded")
        self.assertEqual(row["cert_names"], [FQDN])
        self.assertEqual(row["header"]["upstream"], "127.0.0.1:8025")
        self.assertRegex(row["cert_not_after"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("PRIVATE KEY", json.dumps(result))
        self.host.addresses = "2: eth0    inet 203.0.113.10/24 scope global eth0\n"
        self.assertFalse(self.call("inspect", tailnet_ip=IP, fqdn=FQDN)["tailnet_ip_bound"])

    def test_inspect_flags_a_foreign_block_and_an_unsafe_directory(self):
        self.publish()
        self.conf.write_text("server {}\n")
        os.chmod(self.name_dir, 0o777)
        [row] = self.call("inspect", tailnet_ip=IP)["inventory"]
        self.assertTrue(row["block_present"])
        self.assertFalse(row["block_managed"])
        self.assertTrue(row["dir_unsafe"])


class RemovalAndCrashTests(Base):
    """Re-review N2-N5 and the fail-closed lock probe."""

    def test_remove_keeps_the_lease_for_the_controller_to_release(self):
        self.publish()
        self.assertTrue(self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)["ok"])
        result = self.reserve(token=OTHER)
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))
        self.assertTrue(self.call("release", fqdn=FQDN, token=TOKEN)["released"])
        self.assertTrue(self.reserve(token=OTHER)["ok"])

    def test_remove_reads_ownership_under_the_edge_lock(self):
        self.publish()
        fd = os.open(self.paths["edge_lock"], os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        out = {}
        worker = threading.Thread(target=lambda: out.update(self.call(
            "remove", lock_wait=10, fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)))
        worker.start()
        try:
            time.sleep(0.5)  # remove is now waiting for the edge lock
            self.conf.write_text("server { listen 443; }\n")
        finally:
            os.close(fd)
        worker.join(15)
        self.assertEqual((out.get("ok"), out.get("code")), (False, "unmanaged"))
        self.assertEqual(self.conf.read_text(), "server { listen 443; }\n")
        self.assertTrue(self.name_dir.exists())

    def test_only_exact_sandbox_temp_names_are_cleaned_up(self):
        self.publish()
        for foreign in ("notes.tmp-operator", ".fullchain.pem.tmp-backup",
                        ".other.pem.tmp-0123456789abcdef"):
            with self.subTest(foreign=foreign):
                (self.name_dir / foreign).write_text("operator's")
                result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
                self.assertEqual((result["ok"], result["code"]), (False, "unmanaged"))
                self.assertEqual((self.name_dir / foreign).read_text(), "operator's")
                self.assertTrue(self.conf.exists(), "nothing was taken down")
                (self.name_dir / foreign).unlink()
        (self.name_dir / ".fullchain.pem.tmp-0123456789abcdef").write_text("partial")
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertTrue(result["ok"], result)
        self.assertFalse(self.name_dir.exists())

    def leftover(self, kind, files, marker=True):
        root = Path(self.paths["cert_root"])
        path = root / f".{FQDN}.{kind}-{'0' * 16}"
        os.mkdir(path, 0o755)
        os.chmod(path, 0o755)
        if marker:
            (path / "managed.json").write_text(json.dumps(
                {"owner": "sandbox-tailnet-name", "fqdn": FQDN, "version": 1}))
            os.chmod(path / "managed.json", 0o600)
        for name, text in files.items():
            (path / name).write_text(text)
        return path

    def test_a_create_or_remove_interrupted_by_a_crash_is_finished(self):
        self.reserve()
        staged = self.leftover("new", {})
        tomb = self.leftover("removed", {"privkey.pem": "old", "fullchain.pem": "old"})
        self.csr()
        self.assertFalse(staged.exists())
        self.assertFalse(tomb.exists())
        self.assertTrue(json.loads((self.name_dir / "managed.json").read_text())["fqdn"] == FQDN)
        self.assertEqual([p for p in os.listdir(self.paths["cert_root"]) if ".new-" in p], [])

    def test_a_leftover_with_foreign_content_is_left_alone(self):
        self.reserve()
        foreign = self.leftover("removed", {"notes.txt": "operator's"})
        other = self.leftover("new", {}, marker=False)
        (other / "managed.json").write_text(json.dumps({"owner": "x", "fqdn": "other.example.test"}))
        os.chmod(other / "managed.json", 0o600)
        self.csr()
        self.assertEqual((foreign / "notes.txt").read_text(), "operator's")
        self.assertTrue((other / "managed.json").exists())

    def test_removal_never_leaves_an_unmarked_directory_at_the_name(self):
        self.publish()
        calls = []

        def crash(path, *args, **kwargs):
            calls.append(path)
            raise OSError("simulated crash")
        with mock.patch.object(rp.os, "rmdir", side_effect=crash):
            result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertFalse(result["ok"])
        self.assertTrue(calls and ".removed-" in calls[0], calls)
        self.assertFalse(self.name_dir.exists(), "the name itself is free")
        self.reserve()
        self.csr()  # a fresh add proceeds and cleans the tombstone
        self.assertEqual([p for p in os.listdir(self.paths["cert_root"]) if ".removed-" in p], [])

    def test_an_unprobeable_lock_counts_as_held(self):
        os.symlink(self.base / "nowhere", self.paths["locks"][0])
        self.assertTrue(rp._held(self.paths["locks"][0], self.paths))
        self.assertFalse(rp._held(self.paths["locks"][2], self.paths))
        self.reserve()
        chain = self.sign(self.csr())
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))

    def test_inventory_proves_loaded_only_for_the_bytes_on_disk(self):
        self.publish()
        [row] = self.call("inspect", tailnet_ip=IP)["inventory"]
        self.assertEqual(row["activation"], "loaded")
        self.assertEqual(row["generation"], json.loads(
            (self.name_dir / "activation.json").read_text())["generation"])
        self.conf.write_text(block(port=9000))
        [row] = self.call("inspect", tailnet_ip=IP)["inventory"]
        self.assertEqual((row["activation"], row["generation"]), ("stale", None))

    def test_a_rejected_change_over_an_unproven_state_stays_unproven(self):
        chain = self.publish()
        (self.name_dir / "activation.json").unlink()
        self.host.nginx_ok = False
        result = self.commit(block=block(port=9000), chain=chain)
        self.assertEqual(result["code"], "nginx_rejected")
        journal = json.loads((self.name_dir / "activation.json").read_text())
        self.assertNotEqual(journal["state"], "loaded")
        self.assertEqual(self.conf.read_text(), block())


class RoundThreeTests(Base):
    """Re-review 2: R1 removal proof, R2 lease extension, R3 gate at the boundary."""

    def crash_after_unlink(self, state="removing"):
        self.conf.unlink()
        os.chmod(self.name_dir / "activation.json", 0o600)
        (self.name_dir / "activation.json").write_text(json.dumps({"state": state}))

    def remove(self, **extra):
        return self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe, **extra)

    def test_a_block_gone_from_disk_is_not_proof_of_unload(self):
        self.publish()
        self.crash_after_unlink()
        self.host.nginx_ok = False
        result = self.remove()
        self.assertEqual((result["ok"], result["code"]), (False, "nginx_rejected"))
        self.assertEqual(json.loads((self.name_dir / "activation.json").read_text())["state"],
                         "removing", "the unload stays unproven")
        self.assertTrue(self.name_dir.exists())
        reloads = len(self.host.ran("systemctl", "reload", "nginx"))
        self.host.nginx_ok = True
        result = self.remove()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["reloaded"])
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), reloads + 1)
        self.assertFalse(self.name_dir.exists())

    def test_every_unproven_state_without_a_block_is_reloaded_before_cleanup(self):
        for state in ("restored", "written", "tested", None):
            with self.subTest(state=state):
                self.publish()
                if state is None:
                    self.conf.unlink()
                    (self.name_dir / "activation.json").unlink()
                else:
                    self.crash_after_unlink(state)
                reloads = len(self.host.ran("systemctl", "reload", "nginx"))
                for keep in (True, False):
                    result = self.remove(keep_directory=keep)
                    self.assertTrue(result["ok"], result)
                self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), reloads + 1,
                                 "reloaded once to prove the unload, then trusted")
                self.call("release", fqdn=FQDN, token=TOKEN)

    def test_remove_reports_a_failed_reload_as_such(self):
        self.publish()
        self.host.reload_ok = False
        result = self.remove()
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertEqual(self.conf.read_text(), block())

    def test_a_job_admitted_after_the_first_query_stops_the_commit(self):
        old = self.publish()
        before = len(self.host.ran("systemctl", "reload", "nginx"))
        for answers, tested in (([[], [{"id": "j"}]], False), ([[], [], [{"id": "j"}]], True)):
            with self.subTest(tested=tested):
                tests = len(self.host.ran("nginx", "-t"))
                self.host.jobs_seq = [list(a) for a in answers]
                result = self.commit(block=block(port=9000), chain=old)
                self.assertEqual((result["ok"], result["code"]), (False, "busy"))
                self.assertEqual(self.conf.read_text(), block(), "the previous block is back")
                self.assertEqual(len(self.host.ran("nginx", "-t")) > tests, tested)
                self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), before)
                self.assertEqual(json.loads((self.name_dir / "activation.json").read_text())["state"],
                                 "loaded")

    def test_a_job_admitted_mid_remove_stops_it_with_the_block_back(self):
        self.publish()
        self.host.jobs_seq = [[], [{"id": "j"}]]
        result = self.remove()
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))
        self.assertEqual(self.conf.read_text(), block())
        self.assertTrue(self.name_dir.exists())

    def test_extend_proves_and_renews_the_lease(self):
        self.assertEqual(self.call("extend", fqdn=FQDN, token=TOKEN, seconds=600)["code"],
                         "lease_lost", "no lease, no cert root yet")
        self.reserve()
        self.assertTrue(self.call("extend", fqdn=FQDN, token=TOKEN, seconds=600)["ok"])
        self.assertEqual(self.call("extend", fqdn=FQDN, token=OTHER, seconds=600)["code"],
                         "lease_lost")
        self.call("release", fqdn=FQDN, token=TOKEN)
        self.assertEqual(self.call("extend", fqdn=FQDN, token=TOKEN, seconds=600)["code"],
                         "lease_lost", "a released lease is not revived")


class RoundFourTests(Base):
    """Re-review 3: proven reload acceptance, gates on recovery reloads."""

    def journal(self):
        return json.loads((self.name_dir / "activation.json").read_text())

    def test_an_acknowledged_but_rejected_reload_is_a_failure(self):
        chain = self.publish()
        self.host.reload_accepted = False
        result = self.commit(block=block(port=9000), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertEqual(self.conf.read_text(), block(), "the previous block is back")
        # nginx kept serving the previous block, and the files are that block
        # again, so the rollback is provably loaded.
        [row] = self.call("inspect", tailnet_ip=IP)["inventory"]
        self.assertEqual(row["activation"], "loaded")
        self.host.reload_accepted = True
        result = self.commit(block=block(port=9000), chain=chain)
        self.assertEqual((result["ok"], result["changed"]), (True, True))
        self.assertEqual(self.journal()["state"], "loaded")

    def test_remove_never_cleans_up_after_an_unaccepted_reload(self):
        self.publish()
        self.host.reload_accepted = False
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertTrue(self.name_dir.exists())
        self.assertEqual(self.conf.read_text(), block())

    def test_an_acknowledged_reload_without_the_new_marker_is_not_proof(self):
        """Re-review 4 NEW-A: only what nginx serves proves a reload, never
        the reload command's exit status or process churn."""
        self.publish()
        conf_fd = os.open(self.paths["conf_dir"], os.O_RDONLY | os.O_DIRECTORY)
        name_fd = os.open(str(self.name_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.conf.write_text(block(port=9000))
            proof = rp._disk_proof(conf_fd, name_fd, FQDN, self.paths, IP)
            self.assertFalse(proof(), "nginx still serves the previous block")
            self.host.reload_accepted = False
            ok, _out = rp._reload(self.host, self.paths, proof)
            self.assertFalse(ok)
            self.host.reload_accepted = True
            ok, _out = rp._reload(self.host, self.paths, proof)
            self.assertTrue(ok)
            self.conf.unlink()
            self.assertFalse(proof(), "our certificate is still presented")
            self.host.snapshot()
            self.assertTrue(proof())
        finally:
            os.close(conf_fd)
            os.close(name_fd)

    def test_an_unaccepted_reload_never_settles_a_crash(self):
        """A crash left a new block on disk; nginx rejects every reload. The
        new block is never recorded as loaded: the previous one is restored,
        and only it is proven served."""
        self.publish()
        os.chmod(self.name_dir / "activation.json", 0o600)
        (self.name_dir / "block.prev").write_text(block())
        os.chmod(self.name_dir / "block.prev", 0o600)
        self.conf.write_text(block(port=9000))
        (self.name_dir / "activation.json").write_text(json.dumps(
            {"state": "written", "generation": "0" * 64, "had_block": True, "had_chain": False,
             "chain_changed": False}))
        self.host.reload_accepted = False
        result = self.commit(block=block(port=9000), chain=None)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertEqual(self.conf.read_text(), block())
        [row] = self.call("inspect", tailnet_ip=IP)["inventory"]
        self.assertEqual(row["activation"], "loaded")
        self.assertEqual(row["header"]["upstream"], "127.0.0.1:8025")

    def test_blocks_must_carry_a_valid_marker_and_the_tailnet_address(self):
        self.reserve()
        chain = self.sign(self.csr())
        good = block()
        tampered = good.replace("8025", "8026", 1)  # marker no longer matches
        unmarked = good.replace("location = /.well-known/sandbox-tailnet-activation", "# x", 1)
        for bad in (tampered, unmarked):
            result = self.commit(block=bad, chain=chain)
            self.assertEqual((result["ok"], result["code"]), (False, "request_invalid"))
        result = self.commit(block=good, chain=chain, tailnet_ip="100.64.0.99")
        self.assertEqual((result["ok"], result["code"]), (False, "request_invalid"))
        self.assertFalse(self.conf.exists())

    def test_a_foreign_edge_lock_inode_is_refused(self):
        """Re-review 4 NEW-B."""
        self.reserve()
        chain = self.sign(self.csr())
        edge = self.paths["edge_lock"]
        fd = os.open(edge, os.O_RDWR | os.O_CREAT, 0o666)
        os.close(fd)
        os.chmod(edge, 0o666)
        self.assertTrue(rp._held(edge, self.paths), "a replaceable lock proves nothing")
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))
        os.unlink(edge)
        os.symlink(self.base / "elsewhere", edge)
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "path_unsafe"))
        self.assertFalse((self.base / "elsewhere").exists())
        os.unlink(edge)
        self.assertTrue(self.commit(block=block(), chain=chain)["ok"])
        self.assertEqual(stat.S_IMODE(os.stat(edge).st_mode) & 0o022, 0)

    def test_a_rollback_reload_rechecks_the_gate(self):
        chain = self.publish()
        before = len(self.host.ran("systemctl", "reload", "nginx"))
        self.host.reload_ok = False
        # gate, before test, before reload, rollback before test, rollback before reload
        self.host.jobs_seq = [[], [], [], [], [{"id": "j"}]]
        result = self.commit(block=block(port=9000), chain=chain)
        self.assertEqual(result["code"], "reload_failed")
        self.assertEqual(len(self.host.ran("systemctl", "reload", "nginx")), before + 1,
                         "no rollback reload while a job is active")
        self.assertEqual(self.journal()["state"], "restored")

    def test_settling_a_crash_rechecks_the_gate_before_its_test(self):
        chain = self.publish()
        os.chmod(self.name_dir / "activation.json", 0o600)
        (self.name_dir / "activation.json").write_text(json.dumps(
            {"state": "written", "generation": "0" * 64, "had_block": True, "had_chain": True,
             "chain_changed": False}))
        tests = len(self.host.ran("nginx", "-t"))
        self.host.jobs_seq = [[], [{"id": "j"}]]
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "busy"))
        self.assertEqual(len(self.host.ran("nginx", "-t")), tests)
        self.assertEqual(self.journal()["state"], "written")


class RoundFiveTests(Base):
    """Re-review 5: an unload is proven only when no managed block answers."""

    def test_remove_after_an_interrupted_renewal_waits_for_the_old_block_to_go(self):
        old_chain = self.publish()
        # A renewal wrote certificate B and crashed before reloading: nginx
        # still serves A and the old block, the disk holds B.
        new_chain = self.sign(self.csr(), days=60)
        self.assertNotEqual(old_chain, new_chain)
        os.chmod(self.name_dir / "fullchain.pem", 0o644)
        (self.name_dir / "fullchain.pem").write_text(new_chain)
        os.chmod(self.name_dir / "activation.json", 0o600)
        (self.name_dir / "activation.json").write_text(json.dumps(
            {"state": "written", "generation": "0" * 64, "had_block": True, "had_chain": True,
             "chain_changed": True}))
        self.host.reload_accepted = False
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertTrue(self.name_dir.exists(), "the key and certificates stay")
        self.host.reload_accepted = True
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertTrue(result["ok"], result)
        self.assertFalse(self.name_dir.exists())

    def test_a_first_add_that_fails_can_be_rerun_beside_another_https_site(self):
        """No previous chain to compare with, and another server answers
        the tailnet socket with its own certificate."""
        self.host.loaded = {"other.example.test": (b"server {}", b"")}
        self.reserve()
        chain = self.sign(self.csr())
        self.host.reload_accepted = False
        result = self.commit(block=block(), chain=chain)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertFalse(self.conf.exists())
        [row] = self.call("inspect", tailnet_ip=IP)["inventory"]
        self.assertEqual(row["activation"], "loaded", "the rollback is proven")
        self.host.reload_accepted = True
        result = self.commit(block=block(), chain=chain)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.journal_state(), "loaded")

    def journal_state(self):
        return json.loads((self.name_dir / "activation.json").read_text())["state"]

    def test_an_unload_is_proven_only_by_a_foreign_certificate_or_no_listener(self):
        """Re-review 6: the response body never proves an unload."""
        chain = self.publish()
        ours = rp._leaf_sha(chain.encode())
        self.conf.unlink()
        conf_fd = os.open(self.paths["conf_dir"], os.O_RDONLY | os.O_DIRECTORY)
        name_fd = os.open(str(self.name_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            for served, proven in ((("refused", None, None), True),
                                   (("handshake", "e" * 64, b""), True),
                                   (("handshake", "e" * 64, None), True),
                                   (("handshake", ours, b""), False),
                                   (("handshake", ours, b"01234567"), False),
                                   (("handshake", ours, b"<html>not found</html>"), False),
                                   (("handshake", None, b""), False),
                                   (("error", None, None), False)):
                with self.subTest(served=served):
                    paths = dict(self.paths, served=lambda ip, fqdn, s=served: s)
                    self.assertEqual(rp._disk_proof(conf_fd, name_fd, FQDN, paths, IP)(),
                                     proven)
                    # Without any known certificate, only "nothing listens" proves it.
                    self.assertEqual(rp._disk_proof(conf_fd, None, FQDN, paths, IP)(),
                                     served[0] == "refused")
        finally:
            os.close(conf_fd)
            os.close(name_fd)

    def test_a_truncated_answer_from_the_old_block_never_allows_cleanup(self):
        chain = self.publish()
        ours = rp._leaf_sha(chain.encode())
        self.paths["served"] = lambda ip, fqdn: ("handshake", ours, b"0123")
        result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertEqual((result["ok"], result["code"]), (False, "reload_failed"))
        self.assertTrue(self.name_dir.exists())

    def test_every_installed_leaf_is_remembered(self):
        first = self.publish()
        second = self.sign(self.csr(), days=60)
        self.assertTrue(self.commit(block=block(), chain=second)["ok"])
        leaves = set(json.loads((self.name_dir / "leaves.json").read_text()))
        self.assertEqual(leaves, {rp._leaf_sha(first.encode()), rp._leaf_sha(second.encode())})

    def test_the_block_unlink_is_durable_before_the_certificates_go(self):
        """Re-review 6 NEW-2: conf.d is fsynced after the unlink, before the
        certificate directory is renamed away."""
        self.publish()
        conf_ino = os.stat(self.paths["conf_dir"]).st_ino
        events = []
        real_unlink, real_fsync, real_rename = rp._unlink, os.fsync, os.rename

        def unlink(dir_fd, name):
            events.append(("unlink", name))
            return real_unlink(dir_fd, name)

        def fsync(fd):
            events.append(("fsync", os.fstat(fd).st_ino))
            return real_fsync(fd)

        def rename(src, dst, **kwargs):
            events.append(("rename", dst))
            return real_rename(src, dst, **kwargs)
        with mock.patch.object(rp, "_unlink", unlink), \
                mock.patch.object(rp.os, "fsync", fsync), mock.patch.object(rp.os, "rename", rename):
            result = self.call("remove", fqdn=FQDN, token=TOKEN, jobs_probe=self.probe)
        self.assertTrue(result["ok"], result)
        block_unlink = events.index(("unlink", f"sandbox-tailnet-{FQDN}.conf"))
        conf_sync = events.index(("fsync", conf_ino), block_unlink)
        tomb = next(i for i, e in enumerate(events) if e[0] == "rename" and ".removed-" in e[1])
        self.assertLess(block_unlink, conf_sync)
        self.assertLess(conf_sync, tomb)

if __name__ == "__main__":
    unittest.main()

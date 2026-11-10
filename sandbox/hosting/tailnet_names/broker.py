"""Token consumers for tailnet names, run only through broker use profiles.

The Cloudflare token reaches exactly two programs, each through a use profile
that Sandbox defines in-process (never persisted, never MCP-reachable) and
runs with ``SecretService.use_profile``:

- ``sandbox-tailnet-name-dns``: this interpreter with ``-I`` running the shipped
  ``cloudflare_child.py`` on a fixed request file;
- ``sandbox-tailnet-name-lego``: the pinned ``lego`` binary with a fixed argv
  (contact, directory, fixed CSR path, fixed data directory).

Use profiles take no per-call arguments, so per-call data travels in fixed
controller-local files under a 0700 directory, guarded by a controller lock.
``lego`` is pinned before it can receive the token: resolved real path, owner
root or this user, no group/world write on the file or any ancestor, inside an
allow-listed install location or matching ``tailnet_names.lego_sha256``, and a
``--version`` probe (run without the token) must report lego 5 or later. The
remaining trust boundary is the installed lego binary itself.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from sandbox.hosting.tailnet_names import cloudflare_child
from sandbox.hosting.tailnet_names.models import (
    ACME_CLIENT_MISSING,
    ACME_CLIENT_UNTRUSTED,
    BUSY,
    CERTIFICATE_FAILED,
    DNS_FAILED,
    DNS_TOKEN_DESTINATION,
    SECRET_UNAVAILABLE,
    DnsResult,
    TailnetNameConfig,
    TailnetNameError,
)

DNS_PROFILE = "sandbox-tailnet-name-dns"
LEGO_PROFILE = "sandbox-tailnet-name-lego"
DNS_TIMEOUT_SECONDS = 120
ISSUE_TIMEOUT_SECONDS = 900
TAIL_LINES = 6
LEGO_MIN_MAJOR = 5
# Install locations whose binaries are trusted without a recorded digest.
LEGO_TRUSTED_PREFIXES = (
    "/opt/homebrew/Cellar/lego/",
    "/usr/local/Cellar/lego/",
    "/home/linuxbrew/.linuxbrew/Cellar/lego/",
)
LEGO_TRUSTED_PATHS = ("/usr/bin/lego", "/usr/local/bin/lego")
_VERSION = re.compile(r"lego version (\d+)\.(\d+)\.(\d+)")


def _tail(text: str) -> str:
    return " | ".join(line[-200:] for line in (text or "").splitlines()[-TAIL_LINES:])


def _untrusted(why: str) -> TailnetNameError:
    return TailnetNameError(ACME_CLIENT_UNTRUSTED, f"lego was not trusted with the token: {why}")


def _check_ownership(path: Path, uid: int) -> None:
    """The file and every ancestor: owner root or ``uid``, no group/world write."""
    current = path
    while True:
        st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode):
            raise _untrusted(f"{current} is a symlink after resolution")
        if st.st_uid not in (0, uid):
            raise _untrusted(f"{current} is owned by another user")
        if st.st_mode & 0o022:
            raise _untrusted(f"{current} is group- or world-writable")
        if current.parent == current:
            return
        current = current.parent


def pin_lego(config: TailnetNameConfig, *, uid: int | None = None,
             runner: Callable = subprocess.run) -> tuple[str, str]:
    """Return (real path, sha256) of a trusted lego, or raise before any token use."""
    candidate = config.lego_path or shutil.which("lego")
    if not candidate:
        raise TailnetNameError(ACME_CLIENT_MISSING,
                               "lego is not installed; install it (for example "
                               "`brew install lego`) or set tailnet_names.lego")
    real = Path(os.path.realpath(candidate))
    try:
        st = os.stat(real)
    except OSError:
        raise TailnetNameError(ACME_CLIENT_MISSING, f"lego was not found at {candidate}") from None
    if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o111:
        raise _untrusted(f"{real} is not an executable file")
    _check_ownership(real, os.getuid() if uid is None else uid)
    digest = hashlib.sha256(real.read_bytes()).hexdigest()
    pinned = config.lego_sha256
    if pinned is not None:
        if digest != pinned:
            raise _untrusted(f"{real} does not match tailnet_names.lego_sha256")
    elif not (str(real).startswith(LEGO_TRUSTED_PREFIXES) or str(real) in LEGO_TRUSTED_PATHS):
        raise _untrusted(f"{real} is not in a trusted install location; record its digest in "
                         "tailnet_names.lego_sha256 to trust it")
    try:
        probe = runner([str(real), "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=15, env={"PATH": "/usr/bin:/bin"}, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise _untrusted("the version probe did not run") from None
    match = _VERSION.search(probe.stdout or "")
    if probe.returncode != 0 or not match or int(match.group(1)) < LEGO_MIN_MAJOR:
        raise _untrusted(f"the version probe did not report lego {LEGO_MIN_MAJOR} or later")
    return str(real), digest


@contextmanager
def _controller_lock(path: Path, *, wait: float) -> Iterator[None]:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TailnetNameError(
                        BUSY, "another tailnet-name certificate or DNS request is running "
                              "on this controller") from None
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def _write_private(path: Path, data: str) -> None:
    staged = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.monotonic_ns()}")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


class Broker:
    """``service_factory(extra_profiles)`` builds a SecretService with those profiles."""

    def __init__(self, service_factory: Callable[[dict], object], *, acme_root: Path,
                 executable: str = sys.executable, pin: Callable = pin_lego) -> None:
        self._service_factory = service_factory
        self.acme_root = Path(acme_root)
        self.work = self.acme_root.parent / "broker"
        self.executable = executable
        self._pin = pin

    # -- profiles -----------------------------------------------------------

    def _dns_request_path(self) -> Path:
        return self.work / "dns-request.json"

    def _csr_path(self) -> Path:
        return self.acme_root / "csr" / "request.csr"

    def profiles(self, config: TailnetNameConfig, lego: str | None) -> dict:
        common = {"source": config.token_source, "key": config.token_key,
                  "destination": DNS_TOKEN_DESTINATION, "maxOutputBytes": 1_048_576,
                  "mcp": False}
        out = {DNS_PROFILE: dict(common, timeoutSeconds=DNS_TIMEOUT_SECONDS, argv=[
            self.executable, "-I", cloudflare_child.__file__, str(self._dns_request_path())])}
        if lego is not None:
            out[LEGO_PROFILE] = dict(common, timeoutSeconds=ISSUE_TIMEOUT_SECONDS, argv=[
                lego, "run", "--accept-tos", "--email", config.acme_email,
                "--server", config.acme_server, "--dns", "cloudflare",
                "--csr", str(self._csr_path()), "--path", str(self.acme_root)])
        return out

    def _use(self, config: TailnetNameConfig, name: str, lego: str | None) -> dict:
        from sandbox.secrets import SecretBrokerError
        try:
            payload = self._service_factory(self.profiles(config, lego)).use_profile(
                name, surface="cli")
        except SecretBrokerError as exc:
            raise TailnetNameError(
                SECRET_UNAVAILABLE,
                f"the secrets broker did not provide the DNS token ({exc.code})") from None
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise TailnetNameError(SECRET_UNAVAILABLE, "the secrets broker returned no result")
        return result

    def _prepare(self) -> None:
        for directory in (self.acme_root.parent, self.acme_root, self.work,
                          self.acme_root / "csr"):
            directory.mkdir(parents=True, exist_ok=True)
            st = os.lstat(directory)
            if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
                raise TailnetNameError(CERTIFICATE_FAILED, f"{directory} is not a private directory")
            os.chmod(directory, 0o700)

    # -- operations ---------------------------------------------------------

    def find_lego(self, config: TailnetNameConfig) -> str:
        return self._pin(config)[0]

    def dns(self, config: TailnetNameConfig, request: dict) -> DnsResult:
        self._prepare()
        with _controller_lock(self.work / "dns.lock", wait=DNS_TIMEOUT_SECONDS + 30):
            _write_private(self._dns_request_path(), json.dumps(request, sort_keys=True))
            try:
                result = self._use(config, DNS_PROFILE, None)
            finally:
                self._dns_request_path().unlink(missing_ok=True)
        lines = [line for line in str(result.get("output") or "").splitlines() if line.strip()]
        try:
            parsed = json.loads(lines[-1]) if lines else None
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            raise TailnetNameError(DNS_FAILED, "the DNS helper returned no result")
        return DnsResult.parse(parsed)

    def cached_chain(self, fqdn: str) -> str | None:
        """A chain lego already issued for this name, kept for reuse after a crash."""
        try:
            text = (self.acme_root / "certificates" / f"{fqdn}.crt").read_text()
        except OSError:
            return None
        return text if "-----BEGIN CERTIFICATE-----" in text else None

    def issue(self, config: TailnetNameConfig, fqdn: str, csr_pem: str, lego: str) -> str:
        """Obtain a chain for the remote-made CSR; the key never comes here."""
        self._prepare()
        with _controller_lock(self.work / "lego.lock", wait=0):
            pinned = self.find_lego(config)
            if pinned != lego:
                raise _untrusted("the pinned binary changed during this run")
            _write_private(self._csr_path(), csr_pem)
            certificates = self.acme_root / "certificates"
            for suffix in (".crt", ".issuer.crt", ".json"):
                (certificates / f"{fqdn}{suffix}").unlink(missing_ok=True)
            result = self._use(config, LEGO_PROFILE, lego)
        if result.get("exit_code") != 0:
            raise TailnetNameError(
                CERTIFICATE_FAILED,
                f"lego did not issue a certificate ({result.get('termination')}): "
                + _tail(str(result.get("output") or "")))
        chain = self.cached_chain(fqdn)
        if chain is None:
            raise TailnetNameError(CERTIFICATE_FAILED, "lego finished without a certificate bundle")
        return chain

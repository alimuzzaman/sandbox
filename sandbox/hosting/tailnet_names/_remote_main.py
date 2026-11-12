from __future__ import annotations

# Remote program for `sb host tailnet-name` (spec 078).
#
# Shipped as source and run on the remote as root through the nginx front
# door's `sudo -n python3 -c` runner, with one JSON payload on stdin and one
# JSON result on stdout. Stdlib only, and it must never import Sandbox.
# The paths below are fixed: the payload cannot choose them. Tests pass
# `paths` and `runner` from Python, never through the payload.
# tests/test_tailnet_name_remote_program.py keeps DEFAULT_PATHS in lockstep
# with sandbox.resources.host_guard and the nginx front door.
#
# File safety: every directory is opened component by component from `/`
# with O_NOFOLLOW and checked (trusted owner, no group/world write); every
# file is read with O_NOFOLLOW and fstat; every write goes to a unique
# O_CREAT|O_EXCL|O_NOFOLLOW temp file in the same trusted directory, is
# fsynced and renamed into place. openssl only ever reads stdin and writes
# stdout, so no path is handed to it.
#
# Ownership: a per-name directory is Sandbox's only when it holds a valid
# managed.json; a server block only when it is a root 0600 regular file whose
# first line is this feature's header for the same name. Anything else at
# those paths is refused, never replaced or removed.
#
# Concurrency: a per-name lease (token + expiry) is taken by `reserve` and
# checked by every mutating action under a per-name flock, so two
# controllers cannot interleave work on one name. `commit` and `remove` also
# take the nginx edge flock and re-check the host gate inside it, right before
# `nginx -t` and the reload.
#
# Crash safety: `commit` and `remove` keep an activation journal in the
# per-name directory (written -> tested -> loaded, or removing). A rerun that
# finds an unfinished journal tests and reloads (or restores) before doing
# anything else; "the file is on disk" never counts as loaded.

import datetime
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import time

DEFAULT_PATHS = {
    "conf_dir": "/etc/nginx/conf.d",
    "cert_root": "/etc/nginx/sandbox-tailnet",
    "locks": [
        "/run/lock/sandbox-hosting-caddy.lock",
        "/run/lock/sandbox-edge-nginx.lock",
        "/run/lock/sandbox-docker-pool.lock",
    ],
    "edge_lock": "/run/lock/sandbox-edge-nginx.lock",
    "trusted_uids": [0],
}
FILE_PREFIX = "sandbox-tailnet-"
HEADER_PREFIX = "# sandbox-tailnet-name "
MARKER = "managed.json"
LEAVES = "leaves.json"
JOURNAL = "activation.json"
LEASES = ".leases"
OWNER = "sandbox-tailnet-name"
KNOWN_FILES = ("privkey.pem", "fullchain.pem", "request.csr", JOURNAL, "block.prev",
               "fullchain.prev.pem", MARKER, LEAVES)
LOCK_WAIT_SECONDS = 30
LEASE_SECONDS_MAX = 3600
COMMAND_TIMEOUT_SECONDS = 30
MAX_TEXT_BYTES = 65536
# Recorded leaves per name. Bounded so leaves.json stays far below
# MAX_TEXT_BYTES; see _remember_leaf for why the bound is safe.
LEAF_HISTORY = 64
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# Exactly the temp names _write creates for files Sandbox owns.
_TEMP = re.compile(r"\.(%s)\.tmp-[0-9a-f]{16}" % "|".join(re.escape(n) for n in KNOWN_FILES))


class Refusal(Exception):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def run(argv, *, timeout=COMMAND_TIMEOUT_SECONDS, input=None):
    """Run argv with bytes stdin; return (rc, stdout bytes, stderr bytes)."""
    try:
        res = subprocess.run(argv, input=input, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 127, b"", b""
    return res.returncode, res.stdout or b"", res.stderr or b""


def _text(data):
    return (data or b"").decode("utf-8", "replace")


def _tail(data, lines=6):
    return " | ".join(line[-200:] for line in _text(data).splitlines()[-lines:])


# ---------------------------------------------------------------------------
# Validation

def _fqdn(value):
    if not isinstance(value, str) or not value or len(value) > 253 or value != value.lower():
        raise Refusal("request_invalid", "invalid name")
    labels = value.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        raise Refusal("request_invalid", "invalid name")
    return value


def _tailnet_ip(value):
    try:
        if ipaddress.IPv4Address(value) in _TAILNET:
            return str(ipaddress.IPv4Address(value))
    except (ValueError, TypeError):
        pass
    raise Refusal("request_invalid", "invalid tailnet address")


def _token(value):
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise Refusal("request_invalid", "invalid lease token")
    return value


def _bounded_text(value, what):
    if not isinstance(value, str) or not value or len(value.encode()) > MAX_TEXT_BYTES:
        raise Refusal("request_invalid", f"invalid {what}")
    return value


# ---------------------------------------------------------------------------
# Trusted directories and files

def _trusted(st, paths, *, allow_sticky=False):
    if st.st_uid not in paths["trusted_uids"]:
        return False
    if st.st_mode & 0o022:
        return allow_sticky and bool(st.st_mode & stat.S_ISVTX) and st.st_uid == 0
    return True


def _make_dir(parent_fd, name, mode):
    """Create ``name`` under ``parent_fd`` and return its fd (not yet durable:
    the caller runs _make_durable once the directory is trusted)."""
    os.mkdir(name, mode, dir_fd=parent_fd)
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    try:
        os.fchmod(fd, mode)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _make_durable(fd, parent_fd):
    """fsync a directory and its parent, so its entry survives power loss.

    Run on every mutating open, not only right after mkdir: a run that died
    between mkdir and the parent fsync left an entry that is not yet durable,
    and the retry finds it already there.
    """
    os.fsync(fd)
    os.fsync(parent_fd)


def _open_dir(path, paths, *, create_mode=None, sticky_last=False):
    """Open an absolute directory through trusted, non-symlink components.

    With ``create_mode`` the last component is created when missing. Returns a
    directory fd; raises Refusal(path_unsafe) on any symlink or untrusted
    component, Refusal(missing) when absent and not created.
    """
    if not path.startswith("/"):
        raise Refusal("path_unsafe", "relative path")
    parts = [part for part in path.split("/") if part]
    fd = os.open("/", _DIR_FLAGS)
    try:
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not (last and create_mode is not None):
                    raise Refusal("missing", path) from None
                child = _make_dir(fd, part, create_mode)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise Refusal("path_unsafe", f"{path} is not a plain directory") from None
                raise
            parent, fd = fd, child
            try:
                # A root-owned sticky ancestor (like /tmp) is safe to pass through:
                # nobody else can replace the trusted child below it.
                if not _trusted(os.fstat(fd), paths, allow_sticky=sticky_last or not last):
                    raise Refusal("path_unsafe", f"{path} has an untrusted owner or mode")
                if last and create_mode is not None:
                    _make_durable(fd, parent)
            finally:
                os.close(parent)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _child_dir(parent_fd, name, paths, *, create_mode=None):
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if create_mode is None:
            return None
        fd = _make_dir(parent_fd, name, create_mode)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Refusal("path_unsafe", f"{name} is not a plain directory") from None
        raise
    try:
        if not _trusted(os.fstat(fd), paths):
            raise Refusal("path_unsafe", f"{name} has an untrusted owner or mode")
        if create_mode is not None:
            _make_durable(fd, parent_fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _lstat(dir_fd, name):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read(dir_fd, name, paths, *, mode=None):
    """Bytes of a regular trusted file, or None when absent; refuses anything else."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Refusal("path_unsafe", f"{name} is a symlink") from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in paths["trusted_uids"]:
            raise Refusal("path_unsafe", f"{name} is not a trusted regular file")
        if mode is not None and stat.S_IMODE(st.st_mode) != mode:
            raise Refusal("path_unsafe", f"{name} has mode {oct(stat.S_IMODE(st.st_mode))}")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def _write(dir_fd, name, data, mode):
    """Exclusive unique temp file, fsync, rename over ``name`` (never through a link)."""
    existing = _lstat(dir_fd, name)
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise Refusal("path_unsafe", f"{name} is not a regular file")
    staged = f".{name}.tmp-{secrets.token_hex(8)}"
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode,
                 dir_fd=dir_fd)
    try:
        try:
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(staged, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(staged, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    os.fsync(dir_fd)


def _unlink(dir_fd, name):
    try:
        os.unlink(name, dir_fd=dir_fd)
        return True
    except FileNotFoundError:
        return False


def _unlink_durably(dir_fd, name):
    """Unlink and fsync the directory, so the removal survives a power loss
    before anything that depends on it (deleting the certificates it uses)."""
    removed = _unlink(dir_fd, name)
    os.fsync(dir_fd)
    return removed


# ---------------------------------------------------------------------------
# Locks and leases

def _lock_inode_ok(st, paths):
    """A shared host lock file must be a regular file only root (or the root
    group) can write, so nobody else can swap the inode under its holder."""
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in paths["trusted_uids"]:
        return False
    if st.st_mode & 0o002:
        return False
    return not (st.st_mode & 0o020 and st.st_gid != 0)


def _open_host_lock(absolute, paths):
    """Open a shared host lock through its trusted (sticky) parent, creating
    it exclusively when absent; a foreign inode is refused, never repaired."""
    parent = _open_dir(os.path.dirname(absolute), paths, sticky_last=True)
    name = os.path.basename(absolute)
    try:
        try:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            try:
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                             dir_fd=parent)
            except FileExistsError:
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Refusal("path_unsafe", f"{absolute} is a symlink") from None
            raise
    finally:
        os.close(parent)
    if not _lock_inode_ok(os.fstat(fd), paths):
        os.close(fd)
        raise Refusal("path_unsafe", f"{absolute} has an untrusted owner, type or mode")
    return fd


def _flock(dir_fd, name, wait, *, absolute=None, paths=None):
    """Exclusive flock on a lock file; ``absolute`` for the shared host locks."""
    if absolute is not None:
        fd = _open_host_lock(absolute, paths)
    else:
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise Refusal("lock_busy", f"{absolute or name} stayed locked")
            time.sleep(0.1)


def _held(path, paths):
    """True when the lock is held, or when it cannot be probed (fail closed)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if not _lock_inode_ok(os.fstat(fd), paths):
            return True  # a lock anyone could replace proves nothing: fail closed
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


class _NameScope:
    """cert_root fd, leases fd and the per-name flock for one action."""

    def __init__(self, paths, fqdn, wait, *, create):
        self.paths, self.fqdn = paths, fqdn
        self.root = _open_dir(paths["cert_root"], paths, create_mode=0o755 if create else None)
        self.leases = self.lock = None
        try:
            self.leases = _child_dir(self.root, LEASES, paths, create_mode=0o700)
            self.lock = _flock(self.leases, fqdn + ".lock", wait)
        except BaseException:
            self.close()
            raise

    def lease(self):
        raw = _read(self.leases, self.fqdn + ".json", self.paths, mode=0o600)
        try:
            value = json.loads(raw) if raw else None
        except ValueError:
            value = None
        return value if isinstance(value, dict) else None

    def require(self, token, *, extend=None):
        current = self.lease()
        if not current or current.get("token") != token or current.get("expires", 0) < time.time():
            raise Refusal("lease_lost", "this run no longer holds the name's lease; rerun")
        if extend:
            self.put(token, extend)

    def put(self, token, seconds):
        body = json.dumps({"token": token, "expires": time.time() + seconds}).encode()
        _write(self.leases, self.fqdn + ".json", body, 0o600)

    def close(self):
        for fd in (self.lock, self.leases, self.root):
            if fd is not None:
                os.close(fd)


# ---------------------------------------------------------------------------
# Host gate (re-checked inside the edge flock)

def _check_locks(paths, *, holding_edge):
    held = [p for p in paths["locks"] if not (holding_edge and p == paths["edge_lock"]) and _held(p, paths)]
    if held:
        raise Refusal("busy", "a Sandbox host transaction holds " + ", ".join(
            os.path.basename(p) for p in held))


def _gate(paths, payload, runner, *, holding_edge):
    _check_locks(paths, holding_edge=holding_edge)
    if _jobs_active(payload.get("jobs_probe"), runner):
        raise Refusal("busy", "a durable job is active on the remote")
    # The job query takes a moment; look at the locks again after it.
    _check_locks(paths, holding_edge=holding_edge)


def _jobs_active(probe, runner, *, environ=None, getpwnam=None, lstat=os.lstat):
    """Ask the remote's own Sandbox CLI whether a durable job is active.

    The controller passes the Sandbox home it validated for this remote; the
    program runs as root under sudo, so the home is never derived here (root's
    would be a different, empty one). The CLI runs as the invoking SSH user
    (``SUDO_USER``) with ``SANDBOX_HOME`` set explicitly. Fails closed: a home
    not owned by that user, a missing job registry, or anything but a clean
    answer is ``gate_unavailable``; any listed job is busy.
    """
    import pwd
    environ = os.environ if environ is None else environ
    getpwnam = pwd.getpwnam if getpwnam is None else getpwnam
    home = probe.get("home") if isinstance(probe, dict) else None
    if not (isinstance(home, str) and home.startswith("/") and len(home) < 4096
            and "\x00" not in home and not set(home.split("/")[1:]) & {"", ".", ".."}):
        raise Refusal("request_invalid", "invalid job probe")
    excluded = probe.get("exclude_job_id")
    if excluded is not None and (not isinstance(excluded, str) or
                                 re.fullmatch(r"[0-9a-f]{32}", excluded) is None):
        raise Refusal("request_invalid", "invalid owner job identity")
    user = environ.get("SUDO_USER") or "root"
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise Refusal("gate_unavailable", "the invoking user could not be determined")
    try:
        uid = getpwnam(user).pw_uid
        home_st = lstat(home)
        registry_st = lstat(home + "/runtime/jobs/registry.sqlite3")
    except (KeyError, OSError):
        raise Refusal("gate_unavailable", "the remote's job registry was not found") from None
    if not stat.S_ISDIR(home_st.st_mode) or home_st.st_uid != uid \
            or not stat.S_ISREG(registry_st.st_mode):
        raise Refusal("gate_unavailable", "the Sandbox home is not the invoking user's")
    command = ["env", "SANDBOX_HOME=" + home, "SANDBOX_AUTO_UPDATE=0",
               home + "/sb-src/sb", "job-list", "--active-only", "--limit", "2" if excluded else "1", "--json"]
    if user != "root":
        command = ["runuser", "-u", user, "--"] + command
    rc, out, _err = runner(command, timeout=60)
    try:
        page = json.loads(_text(out).strip().splitlines()[-1]) if rc == 0 else None
    except (ValueError, IndexError):
        page = None
    if not (isinstance(page, dict) and page.get("ok") is True and isinstance(page.get("jobs"), list)):
        raise Refusal("gate_unavailable", "the remote's durable jobs could not be listed")
    jobs = page["jobs"]
    if any(not isinstance(row, dict) or not isinstance(row.get("job_id"), str) for row in jobs):
        raise Refusal("gate_unavailable", "the remote's job identities are unavailable")
    if excluded is not None:
        if not any(row["job_id"] == excluded for row in jobs):
            return True
        return any(row["job_id"] != excluded for row in jobs)
    return bool(jobs)


# ---------------------------------------------------------------------------
# Certificates (openssl over stdin/stdout only)

def _not_after(text):
    value = text.strip().split("=", 1)[-1]
    try:
        parsed = datetime.datetime.strptime(value, "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _chain_facts(chain, key, runner):
    facts = {"cert_not_after": None, "cert_sha256": None, "cert_names": [],
             "key_matches": None, "expired": None}
    rc, out, _ = runner(["openssl", "x509", "-noout", "-enddate"], input=chain)
    if rc != 0:
        return facts
    facts["cert_not_after"] = _not_after(_text(out))
    rc, der, _ = runner(["openssl", "x509", "-outform", "DER"], input=chain)
    if rc == 0 and der:
        facts["cert_sha256"] = hashlib.sha256(der).hexdigest()
    rc, out, _ = runner(["openssl", "x509", "-noout", "-ext", "subjectAltName"], input=chain)
    if rc == 0:
        facts["cert_names"] = sorted(set(re.findall(r"DNS:([^,\s]+)", _text(out))))
    rc, _, _ = runner(["openssl", "x509", "-noout", "-checkend", "0"], input=chain)
    facts["expired"] = rc != 0
    if key is not None:
        rc1, cert_pub, _ = runner(["openssl", "x509", "-noout", "-pubkey"], input=chain)
        rc2, key_pub, _ = runner(["openssl", "pkey", "-pubout"], input=key)
        facts["key_matches"] = rc1 == 0 and rc2 == 0 and bool(cert_pub.strip()) \
            and cert_pub.strip() == key_pub.strip()
    return facts


def _check_chain(chain, key, fqdn, runner):
    facts = _chain_facts(chain, key, runner) if b"BEGIN CERTIFICATE" in chain else {}
    if not (facts.get("key_matches") is True and fqdn in facts.get("cert_names", [])
            and facts.get("expired") is False and facts.get("cert_sha256")):
        raise Refusal("certificate_mismatch",
                      "the chain does not match this name and key, or has expired")
    return facts


# ---------------------------------------------------------------------------
# Ownership

def _marker_ok(dir_fd, fqdn, paths):
    raw = _read(dir_fd, MARKER, paths, mode=0o600)
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return False
    return isinstance(value, dict) and value.get("owner") == OWNER and value.get("fqdn") == fqdn


def _owned_entries(dir_fd, paths):
    """Entries of a per-name directory, refusing anything Sandbox did not write."""
    entries = os.listdir(dir_fd)
    for entry in entries:
        if entry not in KNOWN_FILES and not _TEMP.fullmatch(entry):
            raise Refusal("unmanaged", "the certificate directory holds a file Sandbox did not "
                          "write: " + entry[:80])
        st = _lstat(dir_fd, entry)
        if st is not None and not (stat.S_ISREG(st.st_mode)
                                   and st.st_uid in paths["trusted_uids"]):
            raise Refusal("unmanaged", f"{entry} in the certificate directory is not a plain "
                          "Sandbox file")
    return entries


def _empty_owned(dir_fd, paths):
    """Delete a per-name directory's own files, the marker last."""
    entries = _owned_entries(dir_fd, paths)
    for entry in entries:
        if entry != MARKER:
            _unlink(dir_fd, entry)
    _unlink(dir_fd, MARKER)
    os.fsync(dir_fd)


def _discard_leftovers(root_fd, fqdn, paths):
    """Finish a staged create or a removal that a crash interrupted.

    Only ``.<fqdn>.new-<hex>`` and ``.<fqdn>.removed-<hex>`` directories made
    by this program qualify, and only when every entry is Sandbox's and any
    marker names this fqdn; anything else is left alone.
    """
    pattern = re.compile(r"\." + re.escape(fqdn) + r"\.(new|removed)-[0-9a-f]{16}")
    for entry in os.listdir(root_fd):
        if not pattern.fullmatch(entry):
            continue
        try:
            fd = _child_dir(root_fd, entry, paths)
        except Refusal:
            continue
        if fd is None:
            continue
        try:
            if MARKER in os.listdir(fd) and not _marker_ok(fd, fqdn, paths):
                continue
            _empty_owned(fd, paths)
        except (Refusal, OSError):
            continue
        finally:
            os.close(fd)
        try:
            os.rmdir(entry, dir_fd=root_fd)
        except OSError:
            pass


def _name_dir(root_fd, fqdn, paths, *, create=False):
    """The per-name directory fd when it is Sandbox's; None when absent.

    A new directory is built under a staging name with its marker and renamed
    into place, so a crash never leaves an unmarked directory at the name.
    """
    fd = _child_dir(root_fd, fqdn, paths)
    if fd is None:
        if not create:
            return None
        _discard_leftovers(root_fd, fqdn, paths)
        staged = f".{fqdn}.new-{secrets.token_hex(8)}"
        fd = _child_dir(root_fd, staged, paths, create_mode=0o755)
        try:
            _write(fd, MARKER, json.dumps({"owner": OWNER, "fqdn": fqdn, "version": 1}).encode(),
                   0o600)
            if _lstat(root_fd, fqdn) is not None:
                raise Refusal("unmanaged", f"{fqdn} appeared while its directory was prepared")
            os.rename(staged, fqdn, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        except BaseException:
            os.close(fd)
            _discard_leftovers(root_fd, fqdn, paths)
            raise
        return fd
    try:
        if not _marker_ok(fd, fqdn, paths):
            raise Refusal("unmanaged", f"{fqdn} certificate directory is not managed by Sandbox")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _block(conf_fd, fqdn, paths):
    """(content bytes | None, managed) for the reserved block path."""
    name = FILE_PREFIX + fqdn + ".conf"
    st = _lstat(conf_fd, name)
    if st is None:
        return None, False
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in paths["trusted_uids"] \
            or stat.S_IMODE(st.st_mode) != 0o600:
        return b"", False
    content = _read(conf_fd, name, paths, mode=0o600)
    first = _text(content).split("\n", 1)[0]
    if not first.startswith(HEADER_PREFIX):
        return content, False
    try:
        header = json.loads(first[len(HEADER_PREFIX):])
    except ValueError:
        return content, False
    return content, isinstance(header, dict) and header.get("fqdn") == fqdn


def _header(content):
    first = _text(content).split("\n", 1)[0]
    try:
        value = json.loads(first[len(HEADER_PREFIX):]) if first.startswith(HEADER_PREFIX) else None
    except ValueError:
        value = None
    return value if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Activation (journal, test, reload, restore)

def _nginx_test(runner):
    rc, out, err = runner(["nginx", "-t"])
    return rc == 0, out + err


MARKER_PATH = "/.well-known/sandbox-tailnet-activation"
_MARKER = re.compile(r'location = /\.well-known/sandbox-tailnet-activation \{ default_type '
                     r'text/plain; return 200 "([0-9a-f]{32})"; \}')


_HEX64 = re.compile(r"[0-9a-f]{64}")


def _marker_of(block):
    """The activation marker a block serves, or None when it has no valid one.

    Valid means the marker is the digest of the block with the marker zeroed,
    so two different blocks never share one.
    """
    found = _MARKER.findall(_text(block)) if block else []
    if len(found) != 1:
        return None
    zeroed = block.replace(found[0].encode(), b"0" * 32, 1)
    return found[0] if hashlib.sha256(zeroed).hexdigest()[:32] == found[0] else None


def _leaf_sha(chain):
    import ssl
    text = _text(chain)
    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    start, stop = text.find(begin), text.find(end)
    if start < 0 or stop < 0:
        return None
    try:
        der = ssl.PEM_cert_to_DER_cert(text[start:stop + len(end)] + "\n")
    except ValueError:
        return None
    return hashlib.sha256(der).hexdigest()


def _served(ip, fqdn, *, timeout=3):
    """What nginx serves for the name on the tailnet address right now:
    ("handshake", leaf sha256, body) | ("refused", None, None) |
    ("error", None, None). ``body`` is the response body of a complete
    response, b"" when the server closed without answering (nginx's 444), or
    None when the answer was cut off."""
    import socket
    import ssl
    try:
        raw = socket.create_connection((ip, 443), timeout=timeout)
    except ConnectionRefusedError:
        return "refused", None, None
    except OSError:
        return "error", None, None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with context.wrap_socket(raw, server_hostname=fqdn) as tls:
            der = tls.getpeercert(binary_form=True) or b""
            tls.sendall((f"GET {MARKER_PATH} HTTP/1.1\r\nHost: {fqdn}\r\n"
                         "Connection: close\r\n\r\n").encode())
            data = b""
            try:
                while len(data) < 8192:
                    chunk = tls.recv(2048)
                    if not chunk:
                        break
                    data += chunk
            except (ssl.SSLZeroReturnError, ssl.SSLEOFError, ConnectionResetError):
                pass  # 444 closes without a TLS close_notify
        leaf = hashlib.sha256(der).hexdigest() if der else None
        if not data:
            return "handshake", leaf, b""
        if b"\r\n\r\n" not in data:
            return "handshake", leaf, None
        return "handshake", leaf, data.split(b"\r\n\r\n", 1)[1].strip()
    except (ssl.SSLError, OSError):
        return "error", None, None
    finally:
        raw.close()


def _known_leaves(name_fd, paths):
    """The leaves recorded for this name (each one before any journal or chain
    write that could install it; the last LEAF_HISTORY are kept), plus whatever
    chains are on disk now."""
    leaves = set()
    if name_fd is None:
        return leaves
    raw = _read(name_fd, LEAVES, paths, mode=0o600)
    try:
        recorded = json.loads(raw) if raw else []
    except ValueError:
        recorded = []
    if isinstance(recorded, list):
        leaves.update(item for item in recorded if isinstance(item, str) and _HEX64.fullmatch(item))
    for name in ("fullchain.pem", "fullchain.prev.pem"):
        chain = _read(name_fd, name, paths, mode=0o644)
        leaf = _leaf_sha(chain) if chain else None
        if leaf:
            leaves.add(leaf)
    return leaves


def _remember_leaf(name_fd, chain, paths):
    """Record ``chain``'s leaf, keeping the current and previous chains' leaves.

    Only the last LEAF_HISTORY recorded leaves are kept, plus the current and
    previous chains' leaves. A leaf counts as a candidate: it is recorded
    before its install is tested, so failed attempts take history slots too.
    Dropping an old candidate is still safe because commit records a leaf only
    when there is no journal (nothing was ever installed), the journal is
    ``loaded`` (set only after a proof) or _settle has just proved what nginx
    serves; that proven state is the current chain, which is always kept. A dropped leaf is therefore neither on disk nor what nginx was
    last proven to serve.
    """
    leaf = _leaf_sha(chain)
    if leaf is None:
        return
    raw = _read(name_fd, LEAVES, paths, mode=0o600)
    try:
        recorded = json.loads(raw) if raw else []
    except ValueError:
        recorded = []
    recorded = [item for item in recorded if isinstance(item, str) and item != leaf][-(LEAF_HISTORY - 1):] \
        if isinstance(recorded, list) else []
    for name in ("fullchain.pem", "fullchain.prev.pem"):
        existing = _read(name_fd, name, paths, mode=0o644)
        existing_leaf = _leaf_sha(existing) if existing else None
        if existing_leaf and existing_leaf not in recorded:
            recorded.append(existing_leaf)
    _write(name_fd, LEAVES, json.dumps(recorded + [leaf]).encode(), 0o600)


def _disk_proof(conf_fd, name_fd, fqdn, paths, ip):
    """A check that nginx now serves exactly what is on disk for the name.

    With a block on disk: the block's own marker and the chain's leaf. With
    no block: nothing listens, or the TLS handshake for the name presents a
    certificate this name never had. Every managed block for the name uses
    one of the name's own certificates, so an older block still loaded
    (after an interrupted renewal, say) fails the check. The response body
    plays no part in proving an unload, so a truncated, empty or reset
    answer can never prove one.
    """
    served = paths.get("served", _served)

    def check():
        block, managed = _block(conf_fd, fqdn, paths)
        chain = _read(name_fd, "fullchain.pem", paths, mode=0o644) if name_fd is not None else None
        ours = _leaf_sha(chain) if chain else None
        kind, leaf, body = served(ip, fqdn)
        if block is not None:
            marker = _marker_of(block) if managed else None
            return (kind == "handshake" and marker is not None and ours is not None
                    and leaf == ours and body == marker.encode())
        if kind == "refused":
            return True
        known = _known_leaves(name_fd, paths)
        return kind == "handshake" and bool(known) and leaf is not None and leaf not in known
    return check


def _reload(runner, paths, proof, *, sleep=time.sleep):
    """Reload nginx, then wait for proof that it serves what is on disk.

    `systemctl reload` succeeding is not proof: nginx keeps its previous
    configuration when it rejects a reload (a socket it cannot bind, for
    example), and a worker it respawns runs that previous configuration.
    """
    rc, out, err = runner(["systemctl", "reload", "nginx"])
    if rc != 0:
        return False, out + err
    deadline = time.monotonic() + paths.get("reload_wait", 10.0)
    while True:
        if proof():
            return True, b""
        if time.monotonic() >= deadline:
            return False, (b"nginx did not start serving the new configuration for this name "
                           b"after the reload; it kept its previous one")
        sleep(0.25)


def _apply(runner, paths, fence, proof):
    """The host gate, nginx -t, the gate again, then a proven reload.

    Returns (ok, failed_stage, output); a gate refusal propagates.
    """
    fence()
    ok, out = _nginx_test(runner)
    if not ok:
        return False, "test", out
    fence()
    ok, out = _reload(runner, paths, proof)
    return ok, None if ok else "reload", out


def _journal(name_fd, paths):
    raw = _read(name_fd, JOURNAL, paths, mode=0o600)
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        value = None
    return value if isinstance(value, dict) else None


def _set_journal(name_fd, value):
    _write(name_fd, JOURNAL, json.dumps(value, sort_keys=True).encode(), 0o600)


def _generation(block, chain):
    return hashlib.sha256((block or b"") + b"\0" + (chain or b"")).hexdigest()


def _restore(conf_fd, name_fd, fqdn, journal, paths):
    name = FILE_PREFIX + fqdn + ".conf"
    previous_block = _read(name_fd, "block.prev", paths, mode=0o600)
    previous_chain = _read(name_fd, "fullchain.prev.pem", paths, mode=0o644)
    if journal.get("had_block"):
        if previous_block is None:
            raise Refusal("restore_failed", "the previous block backup is missing")
        _write(conf_fd, name, previous_block, 0o600)
    else:
        _unlink_durably(conf_fd, name)
    if journal.get("had_chain"):
        if previous_chain is None:
            raise Refusal("restore_failed", "the previous chain backup is missing")
        _write(name_fd, "fullchain.pem", previous_chain, 0o644)
    elif journal.get("chain_changed"):
        _unlink_durably(name_fd, "fullchain.pem")


def _settle(conf_fd, name_fd, fqdn, paths, runner, fence, proof):
    """Finish an interrupted commit or remove before anything else (FR-019).

    ``fence`` re-checks the host gate before every reload.
    """
    journal = _journal(name_fd, paths)
    if not journal or journal.get("state") == "loaded":
        return None
    state = journal.get("state")
    ok, _stage, out = _apply(runner, paths, fence, proof)
    if ok:
        block, _managed = _block(conf_fd, fqdn, paths)
        chain = _read(name_fd, "fullchain.pem", paths, mode=0o644)
        _set_journal(name_fd, {"state": "loaded", "generation": _generation(block, chain)})
        return "reloaded"
    if state in ("written", "tested"):
        _restore(conf_fd, name_fd, fqdn, journal, paths)
        _set_journal(name_fd, dict(journal, state="restored"))
        ok, _stage, out = _apply(runner, paths, fence, proof)
        if ok:
            block, _managed = _block(conf_fd, fqdn, paths)
            chain = _read(name_fd, "fullchain.pem", paths, mode=0o644)
            _set_journal(name_fd, {"state": "loaded", "generation": _generation(block, chain)})
            return "restored"
    raise Refusal("recovery_failed", "an interrupted change could not be settled: " + _tail(out))


# ---------------------------------------------------------------------------
# Actions

def inspect(payload, paths, runner, lock_wait):
    ip = _tailnet_ip(payload.get("tailnet_ip"))
    only = _fqdn(payload["fqdn"]) if payload.get("fqdn") is not None else None
    rc, out, _ = runner(["ip", "-4", "-o", "addr", "show"])
    bound = bool(re.search(r"\binet " + re.escape(ip) + r"/", _text(out))) if rc == 0 else None
    conf_fd = _open_dir(paths["conf_dir"], paths)
    try:
        root = _open_dir(paths["cert_root"], paths)
    except Refusal as exc:
        if exc.code != "missing":
            os.close(conf_fd)
            raise
        root = None
    try:
        names = set()
        for entry in os.listdir(conf_fd):
            if entry.startswith(FILE_PREFIX) and entry.endswith(".conf"):
                names.add(entry[len(FILE_PREFIX):-len(".conf")])
        if root is not None:
            names.update(os.listdir(root))
        valid = []
        for name in sorted(names):
            try:
                valid.append(_fqdn(name))
            except Refusal:
                continue
        if only is not None:
            valid = [only]
        rows = [_row(conf_fd, root, name, paths, runner) for name in valid]
    finally:
        os.close(conf_fd)
        if root is not None:
            os.close(root)
    return {"ok": True, "tailnet_ip_bound": bound,
            "locks_held": [path for path in paths["locks"] if _held(path, paths)],
            "inventory": rows}


def mail_access(payload, paths, runner, lock_wait):
    """Read-only panel projection; private key contents are never opened."""
    ip = _tailnet_ip(payload.get("tailnet_ip"))
    result = {"available": False, "partial": False, "names": []}
    conf = root = None
    deadline = time.monotonic() + 8

    def certificate_runner(argv, *, input=None, **_ignored):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Refusal("observation_timeout")
        return run(argv, input=input, timeout=min(2, remaining))

    try:
        conf = _open_dir(paths["conf_dir"], paths)
        root = _open_dir(paths["cert_root"], paths)
        with os.scandir(conf) as entries:
            for index, entry in enumerate(entries):
                if index >= 1000 or time.monotonic() > deadline:
                    result["partial"] = True
                    break
                if not (entry.name.startswith(FILE_PREFIX) and entry.name.endswith(".conf")):
                    continue
                fd = None
                try:
                    fqdn = _fqdn(entry.name[len(FILE_PREFIX):-len(".conf")])
                    block, managed = _block(conf, fqdn, paths)
                    header = _header(block) if managed else None
                    if not header or header.get("version") != 1:
                        raise Refusal("unmanaged_block")
                    if header.get("tailnet_ip") != ip:
                        continue
                    upstream = header.get("upstream")
                    match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", upstream or "")
                    if not match or not 1 <= int(match[1]) <= 65535:
                        continue
                    row = {"upstream": upstream, "url": None}
                    result["names"].append(row)
                    fd = _name_dir(root, fqdn, paths)
                    if fd is None:
                        continue
                    _owned_entries(fd, paths)
                    key = _lstat(fd, "privkey.pem")
                    journal = _journal(fd, paths)
                    chain = _read(fd, "fullchain.pem", paths, mode=0o644)
                    if (key is None or not stat.S_ISREG(key.st_mode) or key.st_nlink != 1
                            or key.st_uid not in paths["trusted_uids"]
                            or stat.S_IMODE(key.st_mode) != 0o600 or not chain or not journal
                            or journal.get("state") != "loaded"
                            or journal.get("generation") != _generation(block, chain)):
                        continue
                    facts = _chain_facts(chain, None, certificate_runner)
                    if facts.get("expired") is False and fqdn in facts.get("cert_names", []):
                        row["url"] = "https://" + fqdn + "/mail/"
                except (Refusal, OSError, ValueError, TypeError):
                    result["partial"] = True
                finally:
                    if fd is not None:
                        os.close(fd)
        result["available"] = True
    except (Refusal, OSError):
        pass
    finally:
        if root is not None:
            os.close(root)
        if conf is not None:
            os.close(conf)
    return {"ok": True, **result}


def _row(conf_fd, root, fqdn, paths, runner):
    content, managed = _block(conf_fd, fqdn, paths)
    row = {"fqdn": fqdn, "block_present": content is not None, "block_managed": managed,
           "header": _header(content) if managed else None, "dir_managed": False,
           "cert_present": False, "key_present": False, "cert_not_after": None,
           "cert_sha256": None, "cert_names": [], "key_matches": None, "activation": None,
           "generation": None}
    if root is None:
        return row
    try:
        name_fd = _child_dir(root, fqdn, paths)
    except Refusal:
        row["dir_unsafe"] = True
        return row
    if name_fd is None:
        return row
    row["dir_present"] = True
    try:
        row["dir_managed"] = _marker_ok(name_fd, fqdn, paths)
        try:
            key = _read(name_fd, "privkey.pem", paths, mode=0o600)
            chain = _read(name_fd, "fullchain.pem", paths, mode=0o644)
            journal = _journal(name_fd, paths)
        except Refusal:
            row["dir_unsafe"] = True
            return row
        row["key_present"] = key is not None
        row["cert_present"] = chain is not None
        state = (journal or {}).get("state")
        if state == "loaded":
            # Loaded is proven only for the exact bytes on disk now.
            if journal.get("generation") == _generation(content if managed else None, chain):
                row["generation"] = journal["generation"]
            else:
                state = "stale"
        row["activation"] = state
        if chain is not None:
            facts = _chain_facts(chain, key, runner)
            facts.pop("expired", None)
            row.update(facts)
    finally:
        os.close(name_fd)
    return row


def reserve(payload, paths, runner, lock_wait):
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    seconds = payload.get("seconds")
    if not isinstance(seconds, int) or not 60 <= seconds <= LEASE_SECONDS_MAX:
        raise Refusal("request_invalid", "invalid lease length")
    scope = _NameScope(paths, fqdn, lock_wait, create=True)
    try:
        current = scope.lease()
        if current and current.get("token") != token and current.get("expires", 0) >= time.time():
            raise Refusal("busy", "another run holds this name until "
                          + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(current["expires"])))
        scope.put(token, seconds)
        return {"ok": True}
    finally:
        scope.close()


def extend(payload, paths, runner, lock_wait):
    """Prove this run still holds the lease and push its expiry out.

    The controller calls it right before each of its own side effects (DNS,
    issuance), so a holder whose lease expired and was taken over stops.
    """
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    seconds = payload.get("seconds")
    if not isinstance(seconds, int) or not 60 <= seconds <= LEASE_SECONDS_MAX:
        raise Refusal("request_invalid", "invalid lease length")
    try:
        scope = _NameScope(paths, fqdn, lock_wait, create=False)
    except Refusal as exc:
        if exc.code == "missing":
            raise Refusal("lease_lost", "no lease exists for this name; rerun") from None
        raise
    try:
        scope.require(token, extend=seconds)
        return {"ok": True}
    finally:
        scope.close()


def release(payload, paths, runner, lock_wait):
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    try:
        scope = _NameScope(paths, fqdn, lock_wait, create=False)
    except Refusal as exc:
        if exc.code == "missing":
            return {"ok": True, "released": False}
        raise
    try:
        current = scope.lease()
        if current and current.get("token") == token:
            _unlink(scope.leases, fqdn + ".json")
            return {"ok": True, "released": True}
        return {"ok": True, "released": False}
    finally:
        scope.close()


def csr(payload, paths, runner, lock_wait):
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    scope = _NameScope(paths, fqdn, lock_wait, create=True)
    try:
        scope.require(token)
        name_fd = _name_dir(scope.root, fqdn, paths, create=True)
        try:
            key = _read(name_fd, "privkey.pem", paths, mode=0o600)
            if key is None:
                rc, key, err = runner(["openssl", "ecparam", "-name", "prime256v1", "-genkey",
                                       "-noout"])
                if rc != 0 or b"PRIVATE KEY" not in key:
                    raise Refusal("key_failed", _tail(err))
                _write(name_fd, "privkey.pem", key, 0o600)
            rc, pem, err = runner(["openssl", "req", "-new", "-key", "/dev/stdin",
                                   "-subj", "/CN=" + fqdn, "-addext",
                                   "subjectAltName=DNS:" + fqdn], input=key)
            if rc != 0 or b"BEGIN CERTIFICATE REQUEST" not in pem:
                raise Refusal("csr_failed", _tail(err))
            _write(name_fd, "request.csr", pem, 0o644)
            return {"ok": True, "csr": _text(pem)}
        finally:
            os.close(name_fd)
    finally:
        scope.close()


def commit(payload, paths, runner, lock_wait):
    """Install the block and/or chain as one activation under the edge lock."""
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    block = _bounded_text(payload["block"], "block").encode() if payload.get("block") else None
    chain = _bounded_text(payload["chain"], "chain").encode() if payload.get("chain") else None
    ip = _tailnet_ip(payload.get("tailnet_ip"))
    if block is not None:
        header = _header(block)
        if not header or header.get("fqdn") != fqdn or header.get("tailnet_ip") != ip:
            raise Refusal("request_invalid", "the block lacks this name's header")
        if _marker_of(block) is None:
            raise Refusal("request_invalid", "the block lacks a valid activation marker")
    scope = _NameScope(paths, fqdn, lock_wait, create=False)
    try:
        scope.require(token)
        name_fd = _name_dir(scope.root, fqdn, paths)
        if name_fd is None:
            raise Refusal("unmanaged", "no managed certificate directory; run add")
        conf_fd = _open_dir(paths["conf_dir"], paths)
        edge = None
        try:
            key = _read(name_fd, "privkey.pem", paths, mode=0o600)
            if key is None:
                raise Refusal("key_missing", "no private key for this name; run add again")
            facts = _check_chain(chain, key, fqdn, runner) if chain is not None else None
            edge = _flock(None, None, lock_wait, absolute=paths["edge_lock"], paths=paths)
            _gate(paths, payload, runner, holding_edge=True)
            current_block, managed = _block(conf_fd, fqdn, paths)
            if current_block is not None and not managed:
                raise Refusal("unmanaged", "the reserved block path holds a file Sandbox did not write")
            def fence():
                _gate(paths, payload, runner, holding_edge=True)
            proof = _disk_proof(conf_fd, name_fd, fqdn, paths, ip)
            settled = _settle(conf_fd, name_fd, fqdn, paths, runner, fence, proof)
            current_block, _managed = _block(conf_fd, fqdn, paths)
            current_chain = _read(name_fd, "fullchain.pem", paths, mode=0o644)
            desired_block = block if block is not None else current_block
            desired_chain = chain if chain is not None else current_chain
            if desired_block is None or desired_chain is None:
                raise Refusal("request_invalid", "nothing to activate: block or chain missing")
            if facts is None:
                facts = _check_chain(desired_chain, key, fqdn, runner)
            generation = _generation(desired_block, desired_chain)
            journal = _journal(name_fd, paths) or {}
            result = {"ok": True, "not_after": facts["cert_not_after"],
                      "sha256": facts["cert_sha256"], "settled": settled,
                      "generation": generation}
            if (journal.get("state") == "loaded" and journal.get("generation") == generation
                    and current_block == desired_block and current_chain == desired_chain
                    and not payload.get("force_reload")):
                return dict(result, changed=False)
            # Back up, journal, install, test, reload.
            if current_block is not None:
                _write(name_fd, "block.prev", current_block, 0o600)
            if current_chain is not None:
                _write(name_fd, "fullchain.prev.pem", current_chain, 0o644)
            record = {"state": "written", "generation": generation,
                      "had_block": current_block is not None,
                      "had_chain": current_chain is not None,
                      "chain_changed": desired_chain != current_chain}
            # The new leaf joins the history before any journal can exist, so a
            # crash at any later point leaves a state whose unload is provable.
            if desired_chain != current_chain:
                _remember_leaf(name_fd, desired_chain, paths)
            _set_journal(name_fd, record)
            if desired_chain != current_chain:
                _write(name_fd, "fullchain.pem", desired_chain, 0o644)
            if desired_block != current_block:
                _write(conf_fd, FILE_PREFIX + fqdn + ".conf", desired_block, 0o600)
            def undo():
                # Nothing was reloaded, so the previous journal still holds;
                # an unproven one stays unproven for the next run to settle.
                _restore(conf_fd, name_fd, fqdn, record, paths)
                _set_journal(name_fd, journal if journal.get("state") == "loaded"
                             else dict(record, state="restored"))
            try:
                fence()  # the gate again, right before the test
            except Refusal:
                undo()
                raise
            ok, out = _nginx_test(runner)
            if not ok:
                undo()
                raise Refusal("nginx_rejected", _tail(out))
            _set_journal(name_fd, dict(record, state="tested"))
            try:
                fence()  # and right before the reload
            except Refusal:
                undo()
                raise
            ok, out = _reload(runner, paths, proof)
            if not ok:
                _restore(conf_fd, name_fd, fqdn, record, paths)
                _set_journal(name_fd, dict(record, state="restored"))
                try:
                    back = _apply(runner, paths, fence, proof)[0]
                except Refusal:
                    back = False
                if back:
                    _set_journal(name_fd, {"state": "loaded", "generation": _generation(
                        current_block, current_chain)})
                raise Refusal("reload_failed", _tail(out))
            _set_journal(name_fd, {"state": "loaded", "generation": generation})
            _unlink(name_fd, "block.prev")
            _unlink(name_fd, "fullchain.prev.pem")
            return dict(result, changed=True)
        finally:
            if edge is not None:
                os.close(edge)
            os.close(conf_fd)
            os.close(name_fd)
    finally:
        scope.close()


def settle(payload, paths, runner, lock_wait):
    """Finish an interrupted activation of this one name, and nothing else.

    ``add`` runs this before its whole-config preflight when that preflight
    fails, so a crash that left this name's own candidate block failing
    ``nginx -t`` cannot block its own recovery. The same lease, edge lock,
    gate, ownership and proof rules as commit apply; any other invalid nginx
    configuration still fails the ``nginx -t`` inside _settle.
    """
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    ip = _tailnet_ip(payload.get("tailnet_ip"))
    scope = _NameScope(paths, fqdn, lock_wait, create=False)
    try:
        scope.require(token)
        name_fd = _name_dir(scope.root, fqdn, paths)
        if name_fd is None:
            return {"ok": True, "settled": None}
        conf_fd = edge = None
        try:
            journal = _journal(name_fd, paths)
            if not journal or journal.get("state") == "loaded":
                return {"ok": True, "settled": None}
            conf_fd = _open_dir(paths["conf_dir"], paths)
            edge = _flock(None, None, lock_wait, absolute=paths["edge_lock"], paths=paths)
            _gate(paths, payload, runner, holding_edge=True)
            current_block, managed = _block(conf_fd, fqdn, paths)
            if current_block is not None and not managed:
                raise Refusal("unmanaged", "the reserved block path holds a file Sandbox did not write")

            def fence():
                _gate(paths, payload, runner, holding_edge=True)
            proof = _disk_proof(conf_fd, name_fd, fqdn, paths, ip)
            return {"ok": True, "settled": _settle(conf_fd, name_fd, fqdn, paths, runner,
                                                   fence, proof)}
        finally:
            if edge is not None:
                os.close(edge)
            if conf_fd is not None:
                os.close(conf_fd)
            os.close(name_fd)
    finally:
        scope.close()


def remove(payload, paths, runner, lock_wait):
    """Remove the managed block (test, reload), then the managed directory.

    Ownership is read under the edge lock, right before anything is changed.
    The lease is left for the controller to release after its DNS step.
    """
    fqdn, token = _fqdn(payload.get("fqdn")), _token(payload.get("token"))
    ip = _tailnet_ip(payload.get("tailnet_ip"))
    keep_directory = payload.get("keep_directory") is True
    scope = _NameScope(paths, fqdn, lock_wait, create=True)
    try:
        scope.require(token)
        _discard_leftovers(scope.root, fqdn, paths)
        name_fd = _name_dir(scope.root, fqdn, paths)
        conf_fd = _open_dir(paths["conf_dir"], paths)
        edge = None
        try:
            edge = _flock(None, None, lock_wait, absolute=paths["edge_lock"], paths=paths)

            def fence():
                _gate(paths, payload, runner, holding_edge=True)
            fence()
            proof = _disk_proof(conf_fd, name_fd, fqdn, paths, ip)
            content, managed = _block(conf_fd, fqdn, paths)
            if content is not None and not managed:
                raise Refusal("unmanaged", "the reserved block path holds a file Sandbox did not write")
            if content is not None and name_fd is None:
                raise Refusal("unmanaged", "a block exists without its managed directory")
            if name_fd is not None:
                if not _marker_ok(name_fd, fqdn, paths):
                    raise Refusal("unmanaged", "the certificate directory lost its marker")
                if not keep_directory:
                    _owned_entries(name_fd, paths)  # refuse before taking anything down
            result = {"ok": True, "block_removed": False, "directory_removed": False}
            if name_fd is not None:
                journal = _journal(name_fd, paths) or {}
                chain = _read(name_fd, "fullchain.pem", paths, mode=0o644)
                proven = (journal.get("state") == "loaded"
                          and journal.get("generation") == _generation(content, chain))
                # A directory that never reached an activation write (no
                # chain, no certificate history, no journal, no backup, no
                # block) cannot be loaded by nginx: a block needs the chain,
                # and the history is written before the first chain.
                never_activated = (content is None and not journal and chain is None and all(
                    _lstat(name_fd, entry) is None
                    for entry in (LEAVES, "fullchain.prev.pem", "block.prev")))
                if never_activated:
                    proven = True
                # A block on disk is taken down; a block already gone from
                # disk is not proof that nginx unloaded it, so anything short
                # of a proven loaded state is tested and reloaded first.
                if content is not None or not proven:
                    if content is not None:
                        _write(name_fd, "block.prev", content, 0o600)
                    _set_journal(name_fd, {"state": "removing"})
                    _unlink_durably(conf_fd, FILE_PREFIX + fqdn + ".conf")
                    failure = None
                    try:
                        ok, stage, out = _apply(runner, paths, fence, proof)
                        if not ok:
                            failure = Refusal("nginx_rejected" if stage == "test"
                                              else "reload_failed", _tail(out))
                    except Refusal as exc:
                        failure = exc
                    if failure is not None:
                        if content is not None:
                            _write(conf_fd, FILE_PREFIX + fqdn + ".conf", content, 0o600)
                            try:
                                back = _apply(runner, paths, fence, proof)[0]
                            except Refusal:
                                back = False
                            _set_journal(name_fd, {"state": "loaded",
                                                   "generation": _generation(content, chain)}
                                         if back else {"state": "restored"})
                        # Without the block on disk the journal stays
                        # "removing": the unload is still unproven.
                        raise failure
                    result["block_removed"] = content is not None
                    result["reloaded"] = True
                    _set_journal(name_fd, {"state": "loaded",
                                           "generation": _generation(None, chain)})
                if keep_directory:
                    # Exposure cleanup: the block is gone, the key stays.
                    return result
                # Rename to a tombstone first, so a crash never leaves an
                # unmarked directory at the name.
                tomb = f".{fqdn}.removed-{secrets.token_hex(8)}"
                os.rename(fqdn, tomb, src_dir_fd=scope.root, dst_dir_fd=scope.root)
                os.fsync(scope.root)
                _empty_owned(name_fd, paths)
                os.close(name_fd)
                name_fd = None
                os.rmdir(tomb, dir_fd=scope.root)
                result["directory_removed"] = True
            return result
        finally:
            if edge is not None:
                os.close(edge)
            os.close(conf_fd)
            if name_fd is not None:
                os.close(name_fd)
    finally:
        scope.close()


ACTIONS = {"inspect": inspect, "mail-access": mail_access,
           "reserve": reserve, "extend": extend, "release": release,
           "settle": settle, "csr": csr,
           "commit": commit, "remove": remove}


def main(payload, paths=None, runner=None, lock_wait=LOCK_WAIT_SECONDS):
    paths = paths or DEFAULT_PATHS
    runner = runner or run
    action = payload.get("action") if isinstance(payload, dict) else None
    if action not in ACTIONS:
        return {"ok": False, "code": "request_invalid", "detail": "unknown action"}
    try:
        return ACTIONS[action](payload, paths, runner, lock_wait)
    except Refusal as exc:
        return {"ok": False, "code": exc.code, "detail": exc.detail}
    except Exception as exc:  # bounded: the type only, never values
        return {"ok": False, "code": "remote_error", "detail": type(exc).__name__}

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

import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
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
}
FILE_PREFIX = "sandbox-tailnet-"
HEADER_PREFIX = "# sandbox-tailnet-name "
LOCK_WAIT_SECONDS = 30
COMMAND_TIMEOUT_SECONDS = 30
MAX_CHAIN_BYTES = 65536
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


class Refusal(Exception):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def run(argv, *, timeout=COMMAND_TIMEOUT_SECONDS, input=None, binary=False):
    """Run argv; stdout on success, stdout+stderr on failure (binary: stdout)."""
    try:
        res = subprocess.run(argv, input=input, capture_output=True, timeout=timeout,
                             text=not binary)
    except (OSError, subprocess.TimeoutExpired):
        return 127, (b"" if binary else "")
    if binary or res.returncode == 0:
        return res.returncode, res.stdout
    return res.returncode, (res.stdout or "") + (res.stderr or "")


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


def _tail(text, lines=6):
    return " | ".join(line[-200:] for line in (text or "").splitlines()[-lines:])


def _conf_path(paths, fqdn):
    return os.path.join(paths["conf_dir"], FILE_PREFIX + fqdn + ".conf")


def _cert_dir(paths, fqdn):
    return os.path.join(paths["cert_root"], fqdn)


def _held(path):
    if not os.path.exists(path):
        return False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _not_after(text):
    value = (text or "").strip().split("=", 1)[-1]
    try:
        parsed = datetime.datetime.strptime(value, "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cert_facts(chain, key, runner):
    facts = {"cert_not_after": None, "cert_sha256": None, "cert_names": [],
             "key_matches": None, "expired": None}
    rc, end = runner(["openssl", "x509", "-noout", "-enddate", "-in", chain])
    if rc != 0:
        return facts
    facts["cert_not_after"] = _not_after(end)
    rc, der = runner(["openssl", "x509", "-outform", "DER", "-in", chain], binary=True)
    if rc == 0 and der:
        facts["cert_sha256"] = hashlib.sha256(der).hexdigest()
    rc, san = runner(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", chain])
    if rc == 0:
        facts["cert_names"] = sorted(set(re.findall(r"DNS:([^,\s]+)", san)))
    rc, _ = runner(["openssl", "x509", "-noout", "-checkend", "0", "-in", chain])
    facts["expired"] = rc != 0
    if os.path.isfile(key):
        rc1, cert_pub = runner(["openssl", "x509", "-noout", "-pubkey", "-in", chain])
        rc2, key_pub = runner(["openssl", "pkey", "-pubout", "-in", key])
        facts["key_matches"] = rc1 == 0 and rc2 == 0 and cert_pub.strip() == key_pub.strip() \
            and bool(cert_pub.strip())
    return facts


def _row(paths, fqdn, runner):
    conf = _conf_path(paths, fqdn)
    directory = _cert_dir(paths, fqdn)
    chain = os.path.join(directory, "fullchain.pem")
    key = os.path.join(directory, "privkey.pem")
    header = None
    block_present = os.path.isfile(conf)
    if block_present:
        try:
            with open(conf) as handle:
                first = handle.readline()
            if first.startswith(HEADER_PREFIX):
                header = json.loads(first[len(HEADER_PREFIX):])
        except (OSError, ValueError):
            header = None
    row = {"fqdn": fqdn, "block_present": block_present,
           "header": header if isinstance(header, dict) else None,
           "cert_present": os.path.isfile(chain), "key_present": os.path.isfile(key)}
    if row["cert_present"]:
        row.update(_cert_facts(chain, key, runner))
    else:
        row.update({"cert_not_after": None, "cert_sha256": None, "cert_names": [],
                    "key_matches": None})
    return row


def _names(paths):
    found = set()
    if os.path.isdir(paths["conf_dir"]):
        for name in os.listdir(paths["conf_dir"]):
            if name.startswith(FILE_PREFIX) and name.endswith(".conf"):
                found.add(name[len(FILE_PREFIX):-len(".conf")])
    if os.path.isdir(paths["cert_root"]):
        found.update(os.listdir(paths["cert_root"]))
    valid = []
    for name in sorted(found):
        try:
            valid.append(_fqdn(name))
        except Refusal:
            continue
    return valid


def inspect(payload, paths, runner, lock_wait):
    ip = _tailnet_ip(payload.get("tailnet_ip"))
    names = [_fqdn(payload["fqdn"])] if payload.get("fqdn") is not None else _names(paths)
    rc, text = runner(["ip", "-4", "-o", "addr", "show"])
    bound = bool(re.search(r"\binet " + re.escape(ip) + r"/", text)) if rc == 0 else None
    return {"ok": True, "tailnet_ip_bound": bound,
            "locks_held": [path for path in paths["locks"] if _held(path)],
            "inventory": [_row(paths, name, runner) for name in names]}


def _ensure_dir(path, mode):
    if os.path.islink(path):
        raise Refusal("path_unsafe", "certificate directory is a symlink")
    os.makedirs(path, mode=mode, exist_ok=True)
    os.chmod(path, mode)


def csr(payload, paths, runner, lock_wait):
    fqdn = _fqdn(payload.get("fqdn"))
    _ensure_dir(paths["cert_root"], 0o755)
    directory = _cert_dir(paths, fqdn)
    _ensure_dir(directory, 0o755)
    key = os.path.join(directory, "privkey.pem")
    if not os.path.isfile(key):
        staged = os.path.join(directory, ".privkey.pem.tmp")
        old = os.umask(0o077)
        try:
            rc, out = runner(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                              "-out", staged])
        finally:
            os.umask(old)
        if rc != 0:
            raise Refusal("key_failed", _tail(out))
        os.chmod(staged, 0o600)
        os.replace(staged, key)
    rc, pem = runner(["openssl", "req", "-new", "-key", key, "-subj", "/CN=" + fqdn,
                      "-addext", "subjectAltName=DNS:" + fqdn])
    if rc != 0 or "BEGIN CERTIFICATE REQUEST" not in pem:
        raise Refusal("csr_failed", _tail(pem))
    request = os.path.join(directory, "request.csr")
    with open(request, "w") as handle:
        handle.write(pem)
    os.chmod(request, 0o644)
    return {"ok": True, "csr": pem}


def _write(path, data, mode):
    staged = path + ".tmp"
    with open(staged, "w") as handle:
        handle.write(data)
    os.chmod(staged, mode)
    os.replace(staged, path)


def _lock(path, wait):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise Refusal("edge_lock_busy", "the nginx edge lock stayed held")
            time.sleep(0.1)


def install(payload, paths, runner, lock_wait):
    fqdn = _fqdn(payload.get("fqdn"))
    chain_text = payload.get("fullchain")
    reload = payload.get("reload") is True
    if not isinstance(chain_text, str) or len(chain_text) > MAX_CHAIN_BYTES:
        raise Refusal("certificate_mismatch", "no certificate chain")
    directory = _cert_dir(paths, fqdn)
    key = os.path.join(directory, "privkey.pem")
    if os.path.islink(directory) or not os.path.isfile(key):
        raise Refusal("key_missing", "no private key for this name; run add again")
    chain = os.path.join(directory, "fullchain.pem")
    staged = os.path.join(directory, ".fullchain.pem.new")
    _write(staged, chain_text, 0o644)
    facts = _cert_facts(staged, key, runner) if "BEGIN CERTIFICATE" in chain_text else {}
    if not (facts.get("key_matches") is True and fqdn in facts.get("cert_names", [])
            and facts.get("expired") is False and facts.get("cert_sha256")):
        os.remove(staged)
        raise Refusal("certificate_mismatch",
                      "the chain does not match this name and key, or has expired")
    result = {"ok": True, "not_after": facts["cert_not_after"], "sha256": facts["cert_sha256"]}
    if os.path.isfile(chain):
        with open(chain) as handle:
            if handle.read() == chain_text:
                os.remove(staged)
                return dict(result, changed=False)
    fd = _lock(paths["edge_lock"], lock_wait)
    try:
        previous = None
        if os.path.isfile(chain):
            with open(chain) as handle:
                previous = handle.read()

        def restore():
            if previous is None:
                os.remove(chain)
            else:
                _write(chain, previous, 0o644)

        os.replace(staged, chain)
        if reload:
            rc, out = runner(["nginx", "-t"])
            if rc != 0:
                restore()
                raise Refusal("nginx_rejected", _tail(out))
            rc, out = runner(["systemctl", "reload", "nginx"])
            if rc != 0:
                restore()
                if runner(["nginx", "-t"])[0] == 0:
                    runner(["systemctl", "reload", "nginx"])
                raise Refusal("reload_failed", _tail(out))
    finally:
        if os.path.exists(staged):
            os.remove(staged)
        os.close(fd)
    return dict(result, changed=True)


def remove_cert(payload, paths, runner, lock_wait):
    fqdn = _fqdn(payload.get("fqdn"))
    if os.path.lexists(_conf_path(paths, fqdn)):
        raise Refusal("block_present", "remove the server block first")
    directory = _cert_dir(paths, fqdn)
    if os.path.islink(directory):
        raise Refusal("path_unsafe", "certificate directory is a symlink")
    if not os.path.isdir(directory):
        return {"ok": True, "removed": False}
    shutil.rmtree(directory)
    return {"ok": True, "removed": True}


ACTIONS = {"inspect": inspect, "csr": csr, "install": install, "remove_cert": remove_cert}


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

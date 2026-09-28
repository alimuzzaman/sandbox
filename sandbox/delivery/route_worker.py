"""Isolated anonymous public probes. Only bounded, allowlisted evidence leaves here.

Run as an isolated Python script; intentionally depends on the standard library
only so no project initialization or credential discovery happens in the child.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import re
import secrets
import signal
import socket
import ssl
import sys
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit


MAX_INPUT = 32768
MAX_OUTPUT = 131072
MAX_BODY = 65536


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value):
    # Same canonical encoding as recovery.models.canonical_digest; this small
    # worker stays stdlib-only and cannot import application initialization.
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class ProbeFailure(Exception):
    def __init__(self, code, retry=False, incomplete=False):
        self.code, self.retry, self.incomplete = code, retry, incomplete


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProbeFailure("deadline_exceeded", incomplete=True)
    return min(5.0, remaining)


@contextmanager
def _network_window(deadline):
    """Bound libc DNS as well as TLS/socket/body work on supported POSIX hosts."""
    def expired(_signum, _frame):
        raise ProbeFailure("network_timeout", retry=True, incomplete=True)
    remaining = _remaining(deadline)
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _path_key(path):
    # Normalize percent spelling and unreserved characters, without treating
    # an encoded slash as a real separator or collapsing repeated slashes.
    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise ProbeFailure("redirect_path_invalid")
    def replace(match):
        char = chr(int(match.group()[1:], 16))
        return char if char.isalnum() and ord(char) < 128 or char in "-._~" else match.group().upper()
    return re.sub(r"%[0-9A-Fa-f]{2}", replace, path or "/")


def _redirect(current, location, requested, primary, policy, original_path, query):
    if not location or len(location.encode("utf-8")) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in location) or "\\" in location:
        raise ProbeFailure("redirect_invalid")
    target = urljoin(current, location)
    try:
        parsed = urlsplit(target)
        allowed = {requested} | ({primary} if policy == "serve_or_redirect_to_primary" else set())
        if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.port not in {None, 443} or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ProbeFailure("redirect_origin_mismatch")
        if _path_key(parsed.path) != _path_key(original_path):
            raise ProbeFailure("redirect_path_mismatch")
        if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.query) or Counter(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)) != Counter(query):
            raise ProbeFailure("redirect_query_mismatch")
    except (ValueError, UnicodeError):
        raise ProbeFailure("redirect_invalid") from None
    return target


def _request_unbounded(url, deadline):
    parsed = urlsplit(url)
    conn = None
    try:
        timeout = _remaining(deadline)
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(parsed.hostname, 443, timeout=timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parsed.hostname, 80, timeout=timeout)
        path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~") + ("?" + parsed.query if parsed.query else "")
        conn.request("GET", path, headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.1", "Accept-Encoding": "identity", "User-Agent": "Sandbox-Public-Route/1"})
        if conn.sock:
            conn.sock.settimeout(_remaining(deadline))
        response = conn.getresponse()
        # http.client bounds individual header lines and the total header count.
        # No headers or bodies are returned to the parent.
        body = bytearray()
        while True:
            if conn.sock:
                conn.sock.settimeout(_remaining(deadline))
            _remaining(deadline)
            chunk = response.read1(min(8192, MAX_BODY + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise ProbeFailure("response_body_too_large")
        return response.status, response.headers, bytes(body)
    except ssl.SSLCertVerificationError:
        raise ProbeFailure("tls_certificate_invalid") from None
    except ssl.SSLError:
        raise ProbeFailure("tls_failed") from None
    except (TimeoutError, socket.timeout):
        raise ProbeFailure("network_timeout", retry=True, incomplete=True) from None
    except (OSError, http.client.HTTPException):
        raise ProbeFailure("network_unavailable", retry=True, incomplete=True) from None
    finally:
        if conn is not None:
            conn.close()


def _request(url, deadline):
    with _network_window(deadline):
        return _request_unbounded(url, deadline)


def _fetch(host, path, contract, deadline):
    query = [("sandbox_probe", secrets.token_hex(8)), ("sandbox_probe", "encoded /+&=")]
    encoded = urlencode(query)
    initial = "http://" + host + path + "?" + encoded
    status, headers, _body = _request(initial, deadline)
    if status not in {301, 302, 303, 307, 308}:
        raise ProbeFailure("http_https_upgrade_missing")
    locations = headers.get_all("Location", [])
    if len(locations) != 1:
        raise ProbeFailure("redirect_invalid")
    _redirect(initial, locations[0], host, contract["primary_hostname"], contract["delivery"]["routes"]["aliasPolicy"], path, query)
    # Probe the requested hostname's own HTTPS endpoint even if HTTP sends an
    # alias straight to primary. Otherwise its missing/invalid cert is hidden.
    current = "https://" + host + path + "?" + encoded
    seen = {initial}
    redirects = 1
    while True:
        if current in seen:
            raise ProbeFailure("redirect_loop")
        seen.add(current)
        status, headers, body = _request(current, deadline)
        if status not in {301, 302, 303, 307, 308}:
            parsed = urlsplit(current)
            return status, headers, body, {"origin": "https://" + parsed.hostname, "path": path, "query_preserved": True, "http_upgrade": True, "tls": "passed", "redirect_count": redirects}
        redirects += 1
        if redirects > 5:
            raise ProbeFailure("redirect_limit")
        locations = headers.get_all("Location", [])
        if len(locations) != 1:
            raise ProbeFailure("redirect_invalid")
        current = _redirect(current, locations[0], host, contract["primary_hostname"], contract["delivery"]["routes"]["aliasPolicy"], path, query)


def _pointer(document, pointer):
    value = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if type(value) is dict and token in value:
            value = value[token]
        elif type(value) is list and re.fullmatch(r"0|[1-9][0-9]*", token) and len(token) <= 6 and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ProbeFailure("application_marker_missing")
    return value


def _json(body):
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def invalid(_value):
            raise ValueError()
        return json.loads(body, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeFailure("application_json_invalid") from None


def _marker(marker, headers, body, host, primary):
    expected = marker["expected"]
    if expected == "$primary_origin":
        expected = "https://" + primary
    elif expected == "$requested_origin":
        expected = "https://" + host
    if marker["kind"] == "header_equals":
        values = headers.get_all(marker["field"], [])
        if len(values) != 1:
            raise ProbeFailure("application_marker_missing")
        observed = values[0]
    else:
        observed = _pointer(_json(body), marker["field"])
    if marker["kind"] == "json_array_contains":
        matches = type(observed) is list and any(type(item) is type(expected) and item == expected for item in observed)
    else:
        matches = type(observed) is type(expected) and observed == expected
    if not matches:
        raise ProbeFailure("application_marker_mismatch")
    # Hash the matching public scalar only, never arbitrary response material.
    return _digest(expected)


def _check(host, check, contract, deadline):
    result = {"path": check["path"], "result": "incomplete", "started_at": _now(), "attempts": 0}
    for attempt in range(2):
        result["attempts"] = attempt + 1
        try:
            status, headers, body, proof = _fetch(host, check["path"], contract, deadline)
            result.update(proof)
            result["status"] = status
            if status not in check["statuses"]:
                raise ProbeFailure("application_status_mismatch")
            result["marker_digests"] = [_marker(marker, headers, body, host, contract["primary_hostname"]) for marker in check["markers"]]
            result["result"] = "passed"
            break
        except ProbeFailure as exc:
            result.update(result="incomplete" if exc.incomplete else "failed", reason=exc.code)
            if not exc.retry or attempt == 1 or deadline - time.monotonic() <= 0:
                break
    result["finished_at"] = _now()
    return result


def observe(contract, deadline):
    results = []
    routes = contract["delivery"]["routes"]
    for host in contract["hostnames"]:
        host_result = {"hostname": host, "dns": {"result": "incomplete", "observed_at": _now()}, "checks": [], "release_identity": {"state": "unsupported"}}
        results.append(host_result)
        try:
            for attempt in range(2):
                try:
                    _remaining(deadline)
                    with _network_window(deadline):
                        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
                    _remaining(deadline)
                    if not addresses or len(addresses) > 128:
                        raise ProbeFailure("dns_unavailable", incomplete=True)
                    host_result["dns"] = {"result": "passed", "address_count": len(addresses), "address_digest": _digest(addresses), "observed_at": _now()}
                    break
                except socket.gaierror:
                    if attempt == 1:
                        raise ProbeFailure("dns_unavailable", incomplete=True) from None
                except ProbeFailure as exc:
                    if not exc.retry or attempt == 1:
                        raise
            for check in routes["checks"]:
                host_result["checks"].append(_check(host, check, contract, deadline))
            release = routes["releaseIdentity"]
            if contract["release_expected"] is not None:
                check = {"path": release["path"], "statuses": [200], "markers": [{"kind": "header_equals" if release["kind"] == "header" else "json_pointer_equals", "field": release["field"], "expected": contract["release_expected"]}]}
                host_result["release_identity"] = _check(host, check, contract, deadline)
        except ProbeFailure as exc:
            host_result["dns"].update(result="incomplete" if exc.incomplete else "failed", reason=exc.code)
    return results


def main():
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError()
        payload = json.loads(data)
        result = {"hosts": observe(payload["contract"], payload["deadline"])}
        output = json.dumps(result, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(output) > MAX_OUTPUT:
            raise ValueError()
    except Exception:
        output = b'{"hosts":[],"error":"route_observation_unavailable"}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()

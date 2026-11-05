"""Fallback route rendering, installation, and origin probe verification (spec 070)."""
from __future__ import annotations

import json
import secrets
import shlex
from typing import Any, Callable

from sandbox.hosting.front_door import nginx
from sandbox.hosting.zone_coverage import (
    COVERAGE_FALLBACK_UNVERIFIED,
    CoverageError,
)

FALLBACK_BODY = "<!doctype html><title>Not found</title><h1>Not found</h1>"

_PROBE_SCRIPT = r'''
import http.client, json, socket, ssl, sys

host = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 443

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    raw_sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.sock = tls_sock
    conn.request("GET", "/", headers={"Host": host})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    headers = {k.lower(): v for k, v in resp.getheaders()}
    out = {
        "status": resp.status,
        "fallback_header": headers.get("x-sandbox-fallback"),
        "body": body,
    }
    print(json.dumps(out))
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__}))
    sys.exit(1)
'''


def render_fallback_conf(
    zone: str,
    *,
    cert_path: str,
    key_path: str,
    ipv6: bool = False,
    http2_directive: bool = True,
    http_port: int = 80,
    https_port: int = 443,
) -> str:
    """Render nginx configuration for the error-only wildcard fallback."""
    clean_zone = zone.strip().rstrip(".").lower()
    lines = [
        "server {",
        f"    listen {http_port};",
    ]
    if ipv6:
        lines.append(f"    listen [::]:{http_port};")
    if http2_directive:
        lines.append(f"    listen {https_port} ssl;")
        if ipv6:
            lines.append(f"    listen [::]:{https_port} ssl;")
        lines.append("    http2 on;")
    else:
        lines.append(f"    listen {https_port} ssl http2;")
        if ipv6:
            lines.append(f"    listen [::]:{https_port} ssl http2;")

    lines.extend([
        f"    server_name *.{clean_zone};",
        f"    ssl_certificate {cert_path};",
        f"    ssl_certificate_key {key_path};",
        "    default_type text/html;",
        '    add_header X-Robots-Tag "noindex, nofollow" always;',
        '    add_header X-Sandbox-Fallback "1" always;',
        f'    return 404 "{FALLBACK_BODY}";',
        "}",
        "",
    ])
    return "\n".join(lines)


def verify_origin_probe_result(data: dict[str, Any]) -> bool:
    """Validate that the loopback probe reached the fallback and got defined 404."""
    if not isinstance(data, dict):
        raise CoverageError(COVERAGE_FALLBACK_UNVERIFIED, "origin probe returned non-dict response")
    if "error" in data:
        err_type = data.get("error") or "probe_failed"
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            f"fallback origin probe failed: {err_type}",
        )
    status = data.get("status")
    if status != 404:
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            f"fallback origin probe returned HTTP {status}, expected 404",
        )
    header = data.get("fallback_header")
    if header != "1":
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            f"fallback origin probe returned X-Sandbox-Fallback header {header!r}, expected '1'",
        )
    body = data.get("body")
    if body != FALLBACK_BODY:
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            "fallback origin probe returned unexpected response body",
        )
    return True


def run_origin_probe(
    entry: dict[str, Any],
    zone: str,
    *,
    port: int = 443,
    probe_host: str | None = None,
    probe_func: Callable[[str], dict[str, Any]] | None = None,
    ssh_run: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute loopback origin probe against the installed fallback route."""
    clean_zone = zone.strip().rstrip(".").lower()
    if probe_host is None:
        token = secrets.token_hex(8)
        probe_host = f"sb-verify-{token}.{clean_zone}"

    if probe_func is not None:
        data = probe_func(probe_host)
    else:
        if ssh_run is None:
            from sandbox.core._remote import ssh_run as default_ssh_run
            ssh_run = default_ssh_run
        cmd = f"python3 -c {shlex.quote(_PROBE_SCRIPT)} {shlex.quote(probe_host)} {port}"
        res = ssh_run(entry, cmd, timeout=15)
        stdout = getattr(res, "stdout", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        try:
            lines = [l.strip() for l in stdout.strip().splitlines() if l.strip()]
            data = json.loads(lines[-1]) if lines else {"error": "empty_output"}
        except Exception:
            data = {"error": "invalid_probe_output"}

    verify_origin_probe_result(data)
    data["probe_host"] = probe_host
    return data


def install_fallback(
    entry: dict[str, Any],
    zone: str,
    *,
    client: Any,
    ipv6: bool = False,
    http2_directive: bool = True,
    ssh_run: Callable[..., Any] | None = None,
    before_effect: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Install origin certificate, render fallback route, apply, and probe."""
    clean_zone = zone.strip().rstrip(".").lower()
    route_name = f"coverage-{clean_zone}"
    if before_effect is not None:
        before_effect("certificate")
    cert_path, key_path, cert_meta = nginx.ensure_origin_certificate(
        entry,
        route_name,
        [f"*.{clean_zone}", clean_zone],
        client,
    )
    conf = render_fallback_conf(
        clean_zone,
        cert_path=cert_path,
        key_path=key_path,
        ipv6=ipv6,
        http2_directive=http2_directive,
    )
    if before_effect is not None:
        before_effect("route")
    apply_res = nginx.apply_route(entry, route_name, conf)
    probe_res = run_origin_probe(entry, clean_zone, ssh_run=ssh_run)
    return {
        "file": apply_res.get("file") or nginx.route_file(route_name),
        "digest": apply_res.get("digest"),
        "marker": probe_res.get("probe_host"),
        "certificate": {
            "path": cert_path,
            "not_after": cert_meta.get("not_after"),
            "id": cert_meta.get("id"),
        },
    }

"""nginx front-door adapter for remotes whose public 80/443 belong to a host nginx.

Ownership model
---------------
Sandbox writes only ``/etc/nginx/conf.d/sandbox-<name>.conf`` (one file per
route owner, root-owned, mode 0600) and its own state under
``/etc/sandbox-edge`` (root, 0700). The incumbent's ``nginx.conf`` already
includes ``conf.d/*.conf`` inside ``http {}``; Sandbox never edits it and never
declares ``default_server``. Every Sandbox route renders a per-file ``$host``
guard, so a Sandbox server block that nginx picks as the implicit default for
a socket answers an undeclared hostname with 444 instead of proxying it.

Secrets
-------
nginx workers on the first target run as the same user as the panel's sites.
``auth_basic_user_file`` is read by workers at request time, so it would have to
be worker-readable. Instead the Basic Auth check is a ``map`` on
``$http_authorization`` inside the root-only 0600 route file, which only the
root master process reads. TLS keys live under ``/etc/sandbox-edge/certs`` (or
certbot's root-only ``/etc/letsencrypt``) with mode 0600 for the same reason.
Route files reach the server over SSH stdin, never in argv.

Access control
--------------
The Cloudflare proxy-source check uses ``$realip_remote_addr`` (the TCP peer even
when the incumbent configures ``real_ip``), so the incumbent's real-IP settings
cannot widen ``bypass_ips``. When the realip module is absent nginx cannot
rewrite the peer at all and ``$remote_addr`` is used instead.
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import ipaddress
import json
import re
import shlex
from typing import Callable, Iterable

from sandbox.hosting.front_door import FrontDoorError

CONF_DIR = "/etc/nginx/conf.d"
FILE_PREFIX = "sandbox-"
EDGE_ROOT = "/etc/sandbox-edge"
CERT_ROOT = f"{EDGE_ROOT}/certs"
STAGING_DIR = f"{EDGE_ROOT}/staging"
ACME_ROOT = "/var/lib/sandbox-edge/acme"
LOCK_PATH = "/run/lock/sandbox-edge-nginx.lock"
MARKER_PATH = "/.well-known/sandbox-edge-route"
DEPLOY_HOOK = "/etc/letsencrypt/renewal-hooks/deploy/sandbox-edge-nginx.sh"
PHASE_TIMEOUT_SECONDS = 30
LOCK_WAIT_SECONDS = 30
TRANSACTION_TIMEOUT_SECONDS = 180
# certbot renews 30 days before expiry. A certificate still inside this window
# means renewal has been failing for days, which is reported while at least
# 14 days remain.
RENEWAL_OVERDUE_DAYS = 25
ROBOTS_DENY_BODY = "User-agent: *\\nDisallow: /\\n"
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,150}")

# Mirrors sandbox.core._hosting._CLOUDFLARE_PROXY_CIDRS; imported lazily to keep
# this module importable without the hosting manifest layer.


def _cloudflare_cidrs() -> tuple[str, ...]:
    from sandbox.core import _hosting
    return _hosting._CLOUDFLARE_PROXY_CIDRS


def route_file(name: str) -> str:
    if not _NAME_RE.fullmatch(name or ""):
        raise ValueError("invalid nginx route name")
    return f"{CONF_DIR}/{FILE_PREFIX}{name}.conf"


def is_sandbox_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return path.startswith(CONF_DIR + "/") and base.startswith(FILE_PREFIX) and base.endswith(".conf")


def _var(name: str) -> str:
    return "sbx_" + hashlib.sha256(name.encode()).hexdigest()[:10]


def _q(value: str) -> str:
    """Quote one nginx token; rendered inputs are validated, this is defence."""
    if any(ch in value for ch in "\"\r\n;{}'") or value.endswith("\\"):
        raise ValueError("unsafe nginx token")
    return f'"{value}"'


def _rx(literal: str) -> str:
    """An exact-match map key as a regex.

    Regex keys never enter nginx's map hash, so a long hostname or credential
    cannot trip the incumbent's ``map_hash_bucket_size``.
    """
    return _q("~^" + re.escape(literal) + "$")


def _host_rx(hostname: str) -> str:
    if hostname.startswith("*."):
        return _q("~^.+" + re.escape(hostname[1:]) + "$")
    return _rx(hostname)


def _route_regex(path_template: str) -> str:
    from sandbox.core import _hosting
    return _hosting._basic_auth_route_pattern(path_template)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_host_conf(validated: dict, port: int, *, name: str,
                     cert_path: str | None, key_path: str | None,
                     basic_auth_password: str | None = None,
                     redact_basic_auth: bool = False,
                     ipv6: bool = False, http2_directive: bool = True,
                     realip: bool = True, streaming: bool = True) -> str:
    """Render one Sandbox-owned nginx file for a host's routes.

    Without ``cert_path`` only the port-80 server renders (ACME bootstrap for a
    DNS-only host before its public certificate exists).
    """
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("invalid upstream port")
    marker_seed = _render(validated, port, name=name, cert_path=cert_path,
                          key_path=key_path, verifier="REDACTED" if validated.get("basic_auth") else None,
                          marker="MARKER", ipv6=ipv6, http2_directive=http2_directive,
                          realip=realip, streaming=streaming)
    marker = hashlib.sha256(marker_seed.encode()).hexdigest()[:24]
    verifier = None
    auth = validated.get("basic_auth")
    if auth:
        if basic_auth_password is None:
            if not redact_basic_auth:
                raise ValueError("basic_auth requires the password to render the nginx verifier")
            verifier = "REDACTED"
        else:
            verifier = "Basic " + base64.b64encode(
                f"{auth['username']}:{basic_auth_password}".encode()).decode()
    return _render(validated, port, name=name, cert_path=cert_path, key_path=key_path,
                   verifier=verifier, marker=marker, ipv6=ipv6,
                   http2_directive=http2_directive, realip=realip, streaming=streaming)


def route_marker(content: str) -> str | None:
    match = re.search(r"location = " + re.escape(MARKER_PATH) +
                      r" \{[^}]*return 200 \"([0-9a-f]{24})\\n\";", content)
    return match.group(1) if match else None


def route_hostnames(validated: dict) -> list[str]:
    return [route["hostname"] for route in validated["routes"]]


def _render(validated: dict, port: int, *, name: str, cert_path, key_path,
            verifier, marker: str, ipv6: bool, http2_directive: bool,
            realip: bool, streaming: bool) -> str:
    p = _var(name)
    peer = "$realip_remote_addr" if realip else "$remote_addr"
    routes = validated["routes"]
    served = [r["hostname"] for r in routes if r["mode"] == "serve"]
    redirects = [r for r in routes if r["mode"] == "redirect"]
    all_hosts = [r["hostname"] for r in routes]
    auth = validated.get("basic_auth") if verifier is not None else None
    robots = validated.get("robots") == "deny"
    out: list[str] = [
        f"# Managed by Sandbox (front door: nginx). Owner: {name}.",
        "# Do not edit: re-run the Sandbox command that owns this route.",
        "# Root-only (0600): it may carry a Basic Auth verifier.",
        "",
        f"map $host ${p}_host {{",
        "    default 0;",
        *(f"    {_host_rx(host)} 1;" for host in all_hosts),
        "}",
    ]
    if served and cert_path:
        out += [
            f"geo {peer} ${p}_cf_peer {{",
            "    default 0;",
            *(f"    {cidr} 1;" for cidr in _cloudflare_cidrs()),
            "}",
            f"map ${p}_cf_peer ${p}_cf_connecting_ip {{",
            '    default "";',
            "    1 $http_cf_connecting_ip;",
            "}",
            f"map $http_upgrade ${p}_connection {{",
            "    default upgrade;",
            '    "" "";',
            "}",
        ]
        if auth:
            out += [f"map $http_authorization ${p}_auth_ok {{", "    default 0;",
                    f"    {_rx(verifier)} 1;", "}"]
            route_keys: list[str] = []
            for path in auth.get("bypass_paths", []):
                route_keys.append(_rx(f"GET:{path}"))
            for route in auth.get("bypass_routes", []):
                methods = route["methods"]
                if "path" in route:
                    route_keys.extend(_rx(f"{method}:{route['path']}") for method in methods)
                else:
                    pattern = _route_regex(route["path_template"])[1:]  # drop ^
                    route_keys.append(_q(f"~^({'|'.join(methods)}):{pattern}"))
            out += [f'map "$request_method:$uri" ${p}_route_public {{', "    default 0;",
                    *(f"    {key} 1;" for key in route_keys), "}"]
            ip_keys = [_rx(f"1:{ipaddress.ip_address(ip)}") for ip in auth.get("bypass_ips", [])]
            out += [f'map "${p}_cf_peer:$http_cf_connecting_ip" ${p}_ip_public {{',
                    "    default 0;", *(f"    {key} 1;" for key in ip_keys), "}",
                    f'map "${p}_route_public${p}_ip_public" ${p}_public {{',
                    "    default 1;", '    "00" 0;', "}",
                    f'map "${p}_public${p}_auth_ok" ${p}_deny {{',
                    "    default 0;", '    "00" 1;', "}",
                    f"map ${p}_public ${p}_authorization {{",
                    '    default "";', "    1 $http_authorization;", "}"]
    marker_location = [
        f"    location = {MARKER_PATH} {{",
        "        default_type text/plain;",
        f'        return 200 "{marker}\\n";',
        "    }",
    ]
    guard = [f"    if (${p}_host = 0) {{", "        return 444;", "    }"]

    def tls_head(names: list[str]) -> list[str]:
        head = ["server {", "    listen 443 ssl;"]
        if ipv6:
            head.append("    listen [::]:443 ssl;")
        if http2_directive:
            head.append("    http2 on;")
        else:
            head[1] = "    listen 443 ssl http2;"
            if ipv6:
                head[2] = "    listen [::]:443 ssl http2;"
        head += [f"    server_name {' '.join(names)};",
                 f"    ssl_certificate {cert_path};",
                 f"    ssl_certificate_key {key_path};"]
        return head

    if cert_path and served:
        block = tls_head(served) + [
            "    client_max_body_size 0;",
            "    client_body_timeout 3600s;",
            "    send_timeout 3600s;",
            *guard,
            *marker_location,
        ]
        if robots:
            block += ["    location = /robots.txt {",
                      '        default_type "text/plain; charset=utf-8";',
                      f'        return 200 "{ROBOTS_DENY_BODY}";',
                      "    }"]
        block.append("    location / {")
        if auth:
            block += [f"        if (${p}_deny) {{",
                      "            add_header WWW-Authenticate 'Basic realm=\"restricted\"' always;",
                      "            return 401;",
                      "        }"]
        block += [
            f"        proxy_pass http://127.0.0.1:{int(port)};",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Host $http_host;",
            f"        proxy_set_header X-Forwarded-For {peer};",
            "        proxy_set_header X-Forwarded-Proto https;",
            "        proxy_set_header X-Forwarded-Host $http_host;",
            "        proxy_set_header Upgrade $http_upgrade;",
            f"        proxy_set_header Connection ${p}_connection;",
            f"        proxy_set_header CF-Connecting-IP ${p}_cf_connecting_ip;",
        ]
        if auth:
            block.append(f"        proxy_set_header Authorization ${p}_authorization;")
        if streaming:
            block += ["        proxy_buffering off;", "        proxy_request_buffering off;"]
        block += ["        proxy_read_timeout 3600s;", "        proxy_send_timeout 3600s;",
                  "        proxy_redirect off;", "    }", "}"]
        out += [""] + block
    if cert_path:
        for route in redirects:
            out += [""] + tls_head([route["hostname"]]) + [
                *guard, *marker_location,
                "    location / {",
                f"        return 308 {route['target']}$request_uri;",
                "    }", "}"]
    plain = ["", "server {", "    listen 80;"]
    if ipv6:
        plain.append("    listen [::]:80;")
    plain += [f"    server_name {' '.join(all_hosts)};", *guard, *marker_location,
              "    location ^~ /.well-known/acme-challenge/ {",
              f"        root {ACME_ROOT};",
              "        default_type text/plain;",
              "        try_files $uri =404;",
              "    }",
              "    location / {",
              "        return 308 https://$host$request_uri;",
              "    }", "}"]
    out += plain
    return "\n".join(out) + "\n"


def render_proxy_route(hostname: str, port: int, *, name: str, cert_path: str | None,
                       key_path: str | None, robots: str = "deny", ipv6: bool = False,
                       http2_directive: bool = True, realip: bool = True) -> str:
    """One hostname proxied to a loopback port (control endpoint and similar)."""
    validated = {"routes": [{"hostname": hostname, "mode": "serve", "primary": True}],
                 "robots": robots, "basic_auth": None}
    return render_host_conf(validated, port, name=name, cert_path=cert_path,
                            key_path=key_path, ipv6=ipv6,
                            http2_directive=http2_directive, realip=realip)


# ---------------------------------------------------------------------------
# Conflict detection (runs on the remote too: shipped by source)
# ---------------------------------------------------------------------------

def parse_server_names(dump: str) -> list[tuple[str, str]]:
    """Return ``(file, server_name)`` pairs from ``nginx -T`` output."""
    pairs: list[tuple[str, str]] = []
    current = "<unknown>"
    chunks: dict[str, list[str]] = {}
    order: list[str] = []
    for line in dump.splitlines():
        header = re.match(r"^# configuration file (.+):\s*$", line)
        if header:
            current = header.group(1).strip()
            if current not in chunks:
                chunks[current] = []
                order.append(current)
            continue
        chunks.setdefault(current, []).append(re.sub(r"(^|\s)#.*$", "", line))
        if current not in order:
            order.append(current)
    for path in order:
        text = "\n".join(chunks.get(path, []))
        for match in re.finditer(r"(?:^|[\s;{}])server_name\s+([^;]*);", text):
            for token in match.group(1).split():
                token = token.strip().strip("\"'").lower()
                if token:
                    pairs.append((path, token))
    return pairs


def _name_covers(pattern: str, host: str) -> bool:
    if pattern in {"_", '""', ""}:
        return False
    if pattern.startswith("~"):
        flags, regex = (re.IGNORECASE, pattern[2:]) if pattern.startswith("~*") else (0, pattern[1:])
        try:
            return re.search(regex, host, flags) is not None
        except re.error:
            return False
    if pattern.startswith("."):
        base = pattern[1:]
        return host == base or host.endswith("." + base)
    if pattern.startswith("*."):
        return host.endswith(pattern[1:])
    if pattern.endswith(".*"):
        return host.startswith(pattern[:-1])
    return host == pattern


def names_overlap(declared: str, existing: str) -> bool:
    declared = declared.lower()
    existing = existing.lower()
    if declared.startswith("*."):
        base = declared[2:]
        if existing.startswith(("*.", ".")):
            other = existing.lstrip("*.")
            return other == base or other.endswith("." + base) or base.endswith("." + other)
        if existing.startswith("~"):
            return _name_covers(existing, "sandbox-probe." + base)
        return existing.endswith("." + base)
    return _name_covers(existing, declared)


def find_conflicts(declared: Iterable[str], pairs: Iterable[tuple[str, str]],
                   own_file: str | None = None) -> list[dict]:
    rows = []
    for host in declared:
        for path, name in pairs:
            if own_file is not None and path == own_file:
                continue
            if names_overlap(host, name):
                rows.append({"hostname": host, "server_name": name, "file": path,
                             "owner": "sandbox" if is_sandbox_file(path) else "panel"})
    return rows


# ---------------------------------------------------------------------------
# Remote operations
# ---------------------------------------------------------------------------

_SUDO = 'if [ "$(id -u)" = 0 ]; then SUDO=; else SUDO="sudo -n"; fi; '


def _ssh(entry: dict, command: str, *, timeout: int = 60, input_data=None):
    from sandbox.core import _remote
    return _remote.ssh_run(entry, command, timeout=timeout, input_data=input_data)


def _diagnostic(entry: dict, result, limit: int = 800) -> str:
    from sandbox.core import _remote
    return _remote._safe_remote_diagnostic(result, entry, limit=limit)


def _python_program(entry: dict, program: str, payload: dict, *, timeout: int = 90) -> dict:
    encoded = base64.b64encode(program.encode()).decode()
    command = "sudo -n python3 -c " + shlex.quote(
        "import base64;exec(base64.b64decode(" + repr(encoded) + "))")
    result = _ssh(entry, command, timeout=timeout, input_data=json.dumps(payload))
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    try:
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise FrontDoorError("front_door_probe_failed",
                             "nginx front-door probe returned no result: "
                             + _diagnostic(entry, result))
    return data


_SHARED_SOURCE = "\n\n".join(inspect.getsource(fn) for fn in (
    is_sandbox_file, parse_server_names, _name_covers, names_overlap, find_conflicts))

_PROBE_PROGRAM = '''from __future__ import annotations
from typing import Iterable
import json, os, re, shutil, subprocess, sys
CONF_DIR = %(conf_dir)r
FILE_PREFIX = %(prefix)r
%(shared)s

def run(argv, timeout=30):
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout or "") + (res.stderr or "")
    except Exception as exc:
        return 127, type(exc).__name__

payload = json.loads(sys.stdin.read() or "{}")
out = {"ok": True}
nginx = shutil.which("nginx") or ("/usr/sbin/nginx" if os.path.exists("/usr/sbin/nginx") else None)
out["nginx_installed"] = bool(nginx)
caddy_bin = shutil.which("caddy")
rc, text = run(["systemctl", "is-active", "caddy"])
out["sandbox_caddy"] = {"installed": bool(caddy_bin), "active": text.strip() == "active"}
rc, text = run(["ss", "-H", "-ltnp", "( sport = :80 or sport = :443 )"])
listeners = []
for line in text.splitlines():
    parts = line.split()
    if len(parts) < 4:
        continue
    port = parts[3].rsplit(":", 1)[-1]
    procs = sorted(set(re.findall(r'\\("([^"]+)"', line)))
    listeners.append({"address": parts[3][:80], "port": port, "processes": procs[:8]})
out["listeners"] = listeners[:32]
import glob
out["caddy_routes"] = len(glob.glob("/etc/caddy/conf.d/sandbox-*.caddy"))
out["nginx_routes"] = len(glob.glob(os.path.join(CONF_DIR, FILE_PREFIX + "*.conf")))
if not nginx:
    print(json.dumps(out)); sys.exit(0)
rc, text = run([nginx, "-v"])
m = re.search(r"nginx/(\\d+)\\.(\\d+)\\.(\\d+)", text)
out["version"] = [int(x) for x in m.groups()] if m else None
rc, text = run([nginx, "-V"])
out["realip"] = "--with-http_realip_module" in text
rc, text = run(["systemctl", "is-active", "nginx"])
out["active"] = text.strip() == "active"
rc, text = run([nginx, "-t"], timeout=30)
out["config_valid"] = rc == 0
out["config_test_tail"] = [l[-200:] for l in text.splitlines()[-4:]]
rc, dump = run([nginx, "-T"], timeout=30)
out["includes_conf_d"] = bool(re.search(r"^\\s*include\\s+" + re.escape(CONF_DIR) + r"/\\*\\.conf\\s*;", dump, re.M))
pairs = parse_server_names(dump) if rc == 0 else []
declared = payload.get("declared") or []
own = payload.get("own_file")
out["conflicts"] = find_conflicts(declared, pairs, own)[:50]
routes = []
for name in sorted(os.listdir(CONF_DIR)) if os.path.isdir(CONF_DIR) else []:
    if not (name.startswith(FILE_PREFIX) and name.endswith(".conf")):
        continue
    path = os.path.join(CONF_DIR, name)
    try:
        text = open(path).read()
    except OSError:
        routes.append({"file": path, "readable": False}); continue
    st = os.stat(path)
    hosts = sorted({n for p, n in parse_server_names("# configuration file %%s:\\n%%s" %% (path, text))})
    marker = re.search(r"return 200 \\"([0-9a-f]{24})\\\\n\\";", text)
    certs = sorted(set(re.findall(r"^\\s*ssl_certificate\\s+(\\S+);", text, re.M)))
    probe_host = next((h for h in hosts if not h.startswith(("*", ".", "~"))), None)
    loaded = None
    if marker and probe_host and shutil.which("curl"):
        rc2, got = run(["curl", "-s", "--max-time", "3", "--resolve", probe_host + ":80:127.0.0.1",
                        "http://" + probe_host + "%(marker_path)s"])
        loaded = got.strip() == marker.group(1)
    cert_rows = []
    for cert in certs:
        row = {"path": cert, "present": os.path.exists(cert), "not_after": None}
        if row["present"]:
            rc3, end = run(["openssl", "x509", "-enddate", "-noout", "-in", cert])
            if rc3 == 0 and "=" in end:
                row["not_after"] = end.strip().split("=", 1)[1]
                rc4, _ = run(["openssl", "x509", "-checkend", str(%(overdue)d * 86400), "-noout", "-in", cert])
                rc5, _ = run(["openssl", "x509", "-checkend", "0", "-noout", "-in", cert])
                row["expired"] = rc5 != 0
                row["renewal_overdue"] = rc4 != 0
                row["issuer"] = "public" if cert.startswith("/etc/letsencrypt/") else "origin-ca"
        cert_rows.append(row)
    other = [(p, n) for p, n in pairs if p != path]
    routes.append({"file": path, "readable": True, "mode": oct(st.st_mode & 0o777),
                   "owner_uid": st.st_uid, "hostnames": hosts[:64], "loaded": loaded,
                   "certificates": cert_rows,
                   "conflicts": find_conflicts([h for h in hosts if not h.startswith("~")], other)[:20]})
out["routes"] = routes[:200]
print(json.dumps(out, sort_keys=True))
'''


def probe(entry: dict, *, declared: list[str] | None = None,
          own_file: str | None = None) -> dict:
    program = _PROBE_PROGRAM % {
        "conf_dir": CONF_DIR, "prefix": FILE_PREFIX, "shared": _SHARED_SOURCE,
        "marker_path": MARKER_PATH, "overdue": RENEWAL_OVERDUE_DAYS,
    }
    return _python_program(entry, program, {"declared": declared or [], "own_file": own_file})


def foreign_listeners(facts: dict) -> list[dict]:
    """Listeners on :80/:443 that are neither Sandbox's Caddy nor the declared nginx."""
    return [row for row in facts.get("listeners") or []
            if row.get("processes") and set(row["processes"]) - {"caddy"}]


def features(facts: dict) -> dict:
    version = facts.get("version") or [0, 0, 0]
    return {"http2_directive": tuple(version) >= (1, 25, 1),
            "realip": bool(facts.get("realip"))}


def preflight(entry: dict, *, declared: list[str], own_file: str | None) -> dict:
    """Refuse before any change unless the incumbent can take Sandbox routes."""
    facts = probe(entry, declared=declared, own_file=own_file)
    if not facts.get("nginx_installed"):
        raise FrontDoorError("front_door_nginx_missing",
                             "no nginx is installed on this remote")
    if not facts.get("active"):
        raise FrontDoorError("front_door_nginx_inactive", "the host nginx is not active")
    caddy = facts.get("sandbox_caddy") or {}
    caddy_listening = any("caddy" in (row.get("processes") or []) for row in facts.get("listeners") or [])
    if caddy.get("active") or caddy_listening:
        raise FrontDoorError("front_door_caddy_present",
                             "Sandbox's Caddy is running on this remote; remove it first "
                             "(Sandbox never removes it implicitly)")
    if not facts.get("includes_conf_d"):
        raise FrontDoorError("front_door_include_missing",
                             f"nginx does not include {CONF_DIR}/*.conf; Sandbox will not edit nginx.conf")
    if not facts.get("config_valid"):
        raise FrontDoorError("front_door_nginx_invalid",
                             "`nginx -t` already fails on this remote; fix the existing "
                             "configuration first: " + " | ".join(facts.get("config_test_tail") or []))
    conflicts = facts.get("conflicts") or []
    if conflicts:
        detail = ", ".join(f"{row['hostname']} vs {row['server_name']} in {row['file']} ({row['owner']})"
                           for row in conflicts[:5])
        raise FrontDoorError("front_door_hostname_conflict",
                             "declared hostnames are already served by another nginx site: " + detail)
    return facts


def _ensure_edge_dirs_command() -> str:
    return (_SUDO +
            f"$SUDO install -d -o root -g root -m 0700 {EDGE_ROOT} {STAGING_DIR} {CERT_ROOT}; "
            f"$SUDO install -d -o root -g root -m 0755 /var/lib/sandbox-edge {ACME_ROOT}")


def transaction_script() -> str:
    t = PHASE_TIMEOUT_SECONDS
    return f"""set -eu
path=$1
staged=$2
desired=$3
operation=$4
remove_route=$5
probe_host=$6
marker=$7
phase() {{
  printf '[Sandbox] nginx phase=%s state=%s digest=%s\\n' "$1" "$2" "$desired"
}}
backup=$(mktemp {STAGING_DIR}/rollback.XXXXXX)
had_previous=0
current=absent
if [ -f "$path" ]; then
  cp -p "$path" "$backup"
  had_previous=1
  current=$(sha256sum "$path" | awk '{{print $1}}')
fi
restore_local() {{
  if [ "$had_previous" = 1 ]; then install -o root -g root -m 0600 "$backup" "$path"; else rm -f "$path"; fi
}}
if [ "$remove_route" = 1 ]; then want=absent; else want=$desired; fi
if [ "$current" = "$want" ]; then
  rm -f "$backup"
  if [ "$staged" != - ]; then rm -f "$staged"; fi
  phase digest unchanged
  if ! timeout {t} systemctl is-active --quiet nginx; then phase observe failed; exit 72; fi
  phase observe active
  if [ "$operation" = apply ]; then phase noop unchanged; else phase rollback rollback_complete; fi
  exit 0
fi
phase digest changed
if [ "$remove_route" = 1 ]; then
  rm -f "$path"
else
  install -o root -g root -m 0600 "$staged" "$path"
  rm -f "$staged"
fi
phase install passed
if ! out=$(timeout {t} nginx -t 2>&1); then
  printf '%s\\n' "$out" | tail -n 6 >&2
  restore_local
  rm -f "$backup"
  phase validate failed
  exit 70
fi
phase validate passed
if ! timeout {t} systemctl reload nginx; then
  restore_local
  if timeout {t} nginx -t >/dev/null 2>&1; then timeout {t} systemctl reload nginx || true; fi
  rm -f "$backup"
  phase reload failed
  exit 71
fi
phase reload passed
if ! timeout {t} systemctl is-active --quiet nginx; then phase observe failed; exit 72; fi
if [ "$remove_route" = 0 ] && [ "$probe_host" != - ]; then
  i=0
  got=
  while [ $i -lt 40 ]; do
    got=$(curl -s --max-time 2 --resolve "$probe_host:80:127.0.0.1" "http://$probe_host{MARKER_PATH}" 2>/dev/null || true)
    if [ "$got" = "$marker" ]; then break; fi
    i=$((i + 1))
    sleep 0.25
  done
  if [ "$got" != "$marker" ]; then
    restore_local
    if timeout {t} nginx -t >/dev/null 2>&1; then timeout {t} systemctl reload nginx || true; fi
    rm -f "$backup"
    phase observe route_not_loaded
    exit 73
  fi
fi
rm -f "$backup"
phase observe active
if [ "$operation" = apply ]; then phase complete changed; else phase rollback rollback_complete; fi
"""


def transaction_command(path: str, staged: str | None, digest: str, *, operation: str,
                        remove: bool, probe_host: str | None = None,
                        marker: str | None = None) -> str:
    return (
        "set -eu; " + _SUDO +
        "$SUDO install -d -m 0755 /run/lock; "
        f"$SUDO flock -w {LOCK_WAIT_SECONDS} {shlex.quote(LOCK_PATH)} "
        f"sh -c {shlex.quote(transaction_script())} sandbox-nginx "
        f"{shlex.quote(path)} {shlex.quote(staged or '-')} {shlex.quote(digest)} "
        f"{shlex.quote(operation)} {'1' if remove else '0'} "
        f"{shlex.quote(probe_host or '-')} {shlex.quote(marker or '-')}"
    )


_EXIT_REASONS = {70: "validate", 71: "reload", 72: "observe", 73: "route_not_loaded"}


def _stage(entry: dict, name: str, content: str, digest: str) -> str:
    staged = f"{STAGING_DIR}/{name}.{digest[:16]}.conf"
    command = (_ensure_edge_dirs_command() + "; " +
               f"$SUDO sh -c {shlex.quote('umask 077; cat > ' + shlex.quote(staged))}")
    result = _ssh(entry, command, timeout=60, input_data=content)
    if result.returncode != 0:
        raise FrontDoorError("front_door_stage_failed",
                             "could not stage the nginx route: " + _diagnostic(entry, result))
    return staged


def read_route(entry: dict, name: str) -> str | None:
    path = route_file(name)
    result = _ssh(entry, _SUDO + f"$SUDO test -f {shlex.quote(path)} && $SUDO cat {shlex.quote(path)}",
                  timeout=30)
    return result.stdout if result.returncode == 0 else None


def _probe_host(content: str) -> str | None:
    for _, name in parse_server_names("# configuration file x:\n" + content):
        if not name.startswith(("*", ".", "~")):
            return name
    return None


def apply_route(entry: dict, name: str, content: str, *, operation: str = "apply",
                log: Callable[[str], None] | None = None) -> dict:
    path = route_file(name)
    digest = hashlib.sha256(content.encode()).hexdigest()
    staged = _stage(entry, name, content, digest)
    command = transaction_command(path, staged, digest, operation=operation, remove=False,
                                  probe_host=_probe_host(content), marker=route_marker(content))
    result = _ssh(entry, command, timeout=TRANSACTION_TIMEOUT_SECONDS)
    output = (result.stdout or "")
    if log:
        log(output)
    if result.returncode != 0:
        phase = _EXIT_REASONS.get(result.returncode, "transaction")
        raise FrontDoorError(f"front_door_{phase}_failed",
                             f"nginx route {name} {phase} failed (exit {result.returncode}); "
                             "previous routing restored: " + _diagnostic(entry, result))
    state = "unchanged" if "phase=noop state=unchanged" in output else "changed"
    return {"state": state, "digest": digest, "file": path}


def remove_route(entry: dict, name: str, *, operation: str = "apply") -> dict:
    path = route_file(name)
    command = transaction_command(path, None, "absent", operation=operation, remove=True)
    result = _ssh(entry, command, timeout=TRANSACTION_TIMEOUT_SECONDS)
    if result.returncode != 0:
        phase = _EXIT_REASONS.get(result.returncode, "transaction")
        raise FrontDoorError(f"front_door_{phase}_failed",
                             f"nginx route {name} removal failed: " + _diagnostic(entry, result))
    return {"state": "removed", "file": path}


def restore_route(entry: dict, name: str, previous: str | None) -> dict:
    try:
        if previous is None:
            remove_route(entry, name, operation="rollback")
            return {"state": "rollback_complete", "digest": "absent"}
        result = apply_route(entry, name, previous, operation="rollback")
        return {"state": "rollback_complete", "digest": result["digest"]}
    except FrontDoorError as exc:
        raise FrontDoorError("rollback_incomplete", f"nginx restore failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

def origin_paths(name: str) -> tuple[str, str]:
    base = f"{CERT_ROOT}/{name}"
    return f"{base}/origin.pem", f"{base}/origin.key"


def ensure_origin_certificate(entry: dict, name: str, hostnames: list[str], client) -> tuple[str, str, dict]:
    """Cloudflare Origin CA certificate for a proxied route (key root 0600)."""
    cert_path, key_path = origin_paths(name)
    base = cert_path.rsplit("/", 1)[0]
    wanted = ",".join(sorted(hostnames))
    names_path = f"{base}/hostnames"
    check = _ssh(entry, _SUDO + (
        f"$SUDO test -s {shlex.quote(cert_path)} -a -s {shlex.quote(key_path)} && "
        f"$SUDO cat {shlex.quote(names_path)}"), timeout=30)
    if check.returncode == 0 and (check.stdout or "").strip() == wanted:
        return cert_path, key_path, {"id": None, "hostnames": sorted(hostnames), "issuer": "origin-ca"}
    primary = next((h for h in hostnames if not h.startswith("*.")), hostnames[0])
    csr_path = f"{base}/origin.csr"
    keygen = ("umask 077; openssl ecparam -name prime256v1 -genkey -noout -out "
              + shlex.quote(key_path))
    command = (_ensure_edge_dirs_command() + "; " +
               f"$SUDO install -d -o root -g root -m 0700 {shlex.quote(base)}; "
               f"if ! $SUDO test -s {shlex.quote(key_path)}; then "
               f"$SUDO sh -c {shlex.quote(keygen)}; fi; "
               f"$SUDO chmod 0600 {shlex.quote(key_path)}; "
               f"$SUDO openssl req -new -key {shlex.quote(key_path)} -subj {shlex.quote('/CN=' + primary)} "
               f"-out {shlex.quote(csr_path)} && $SUDO cat {shlex.quote(csr_path)}")
    result = _ssh(entry, command, timeout=60)
    if result.returncode != 0 or "BEGIN CERTIFICATE REQUEST" not in (result.stdout or ""):
        raise FrontDoorError("front_door_certificate_failed",
                             "could not create the origin key/CSR: " + _diagnostic(entry, result))
    issued = client.create_origin_certificate(result.stdout, sorted(hostnames))
    text = issued.get("certificate") if isinstance(issued, dict) else None
    if not isinstance(text, str) or "BEGIN CERTIFICATE" not in text:
        raise FrontDoorError("front_door_certificate_failed",
                             "Cloudflare did not return an Origin CA certificate")
    inner = (f"umask 022; cat > {shlex.quote(cert_path + '.new')} && "
             f"mv {shlex.quote(cert_path + '.new')} {shlex.quote(cert_path)} && "
             f"printf %s {shlex.quote(wanted)} > {shlex.quote(names_path)}")
    install = _ssh(entry, _SUDO + f"$SUDO sh -c {shlex.quote(inner)}",
                   timeout=30, input_data=text)
    if install.returncode != 0:
        raise FrontDoorError("front_door_certificate_failed",
                             "could not install the origin certificate: " + _diagnostic(entry, install))
    return cert_path, key_path, {"id": issued.get("id"), "hostnames": sorted(hostnames),
                                 "issuer": "origin-ca"}


def public_paths(name: str) -> tuple[str, str]:
    return (f"/etc/letsencrypt/live/sandbox-{name}/fullchain.pem",
            f"/etc/letsencrypt/live/sandbox-{name}/privkey.pem")


def public_certificate_present(entry: dict, name: str, hostnames: list[str]) -> bool:
    cert_path, _ = public_paths(name)
    command = _SUDO + (
        f"$SUDO test -s {shlex.quote(cert_path)} && "
        f"$SUDO openssl x509 -noout -ext subjectAltName -in {shlex.quote(cert_path)}")
    result = _ssh(entry, command, timeout=30)
    if result.returncode != 0:
        return False
    sans = set(re.findall(r"DNS:([A-Za-z0-9.*-]+)", result.stdout or ""))
    return set(hostnames) <= sans


def certbot_command(name: str, hostnames: list[str]) -> str:
    domains = " ".join(f"-d {shlex.quote(h)}" for h in hostnames)
    hook = ("#!/bin/sh\n# Managed by Sandbox: reload the host nginx after renewal.\n"
            "nginx -t && systemctl reload nginx\n")
    return (
        _ensure_edge_dirs_command() + "; "
        "if ! command -v certbot >/dev/null 2>&1; then "
        "$SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
        "$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot; fi; "
        "$SUDO install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy; "
        f"printf %s {shlex.quote(hook)} | $SUDO tee {DEPLOY_HOOK} >/dev/null; "
        f"$SUDO chmod 0755 {DEPLOY_HOOK}; "
        "$SUDO systemctl enable --now certbot.timer >/dev/null 2>&1 || true; "
        f"$SUDO certbot certonly --webroot -w {ACME_ROOT} --cert-name {shlex.quote('sandbox-' + name)} "
        f"{domains} --non-interactive --agree-tos --register-unsafely-without-email "
        "--keep-until-expiring --expand"
    )


def ensure_public_certificate(entry: dict, name: str, hostnames: list[str]) -> tuple[str, str, dict]:
    if any(h.startswith("*.") for h in hostnames):
        raise FrontDoorError("front_door_certificate_failed",
                             "public certificates do not cover wildcard routes without a DNS challenge")
    cert_path, key_path = public_paths(name)
    if not public_certificate_present(entry, name, hostnames):
        result = _ssh(entry, certbot_command(name, hostnames), timeout=300)
        if result.returncode != 0:
            raise FrontDoorError("front_door_certificate_failed",
                                 "certbot could not issue the public certificate: "
                                 + _diagnostic(entry, result))
    return cert_path, key_path, {"id": None, "hostnames": sorted(hostnames), "issuer": "public"}


# ---------------------------------------------------------------------------
# Control route (HTTPS MCP control endpoint)
# ---------------------------------------------------------------------------

def _record_proxied(hostname: str) -> bool | None:
    """True/False when Cloudflare has an A/AAAA/CNAME for the host; None if unknown."""
    try:
        from sandbox.core import _cloudflare as cloudflare
        client = cloudflare.Client()
        zone = None
        labels = hostname.split(".")
        for index in range(len(labels) - 1):
            try:
                zone = client.zone(".".join(labels[index:]))
                break
            except cloudflare.CloudflareError:
                continue
        if zone is None:
            return None
        records = [r for r in client.records(zone["id"], hostname)
                   if r.get("type") in {"A", "AAAA", "CNAME"}]
        if not records:
            return None
        return bool(records[0].get("proxied"))
    except Exception:
        return None


def configure_control_route(entry: dict, hostname: str, port: int) -> dict:
    name = f"mcp-{hostname}"
    facts = preflight(entry, declared=[hostname], own_file=route_file(name))
    feature = features(facts)
    ipv6 = bool(entry.get("origin_ipv6"))
    proxied = _record_proxied(hostname)
    if proxied:
        from sandbox.core import _cloudflare as cloudflare
        cert_path, key_path, _ = ensure_origin_certificate(
            entry, name, [hostname], cloudflare.Client())
    else:
        if not public_certificate_present(entry, name, [hostname]):
            bootstrap = render_proxy_route(hostname, port, name=name, cert_path=None,
                                           key_path=None, ipv6=ipv6, **feature)
            apply_route(entry, name, bootstrap)
        cert_path, key_path, _ = ensure_public_certificate(entry, name, [hostname])
    content = render_proxy_route(hostname, port, name=name, cert_path=cert_path,
                                 key_path=key_path, ipv6=ipv6, **feature)
    return apply_route(entry, name, content)


# ---------------------------------------------------------------------------
# Status (sb remote edge, sb doctor)
# ---------------------------------------------------------------------------

def summarize(facts: dict) -> dict:
    routes = facts.get("routes") or []
    unloaded = [row["file"] for row in routes if row.get("loaded") is False]
    conflicts = [dict(item, route_file=row["file"]) for row in routes
                 for item in row.get("conflicts") or []]
    problems = []
    for row in routes:
        if row.get("readable") is False:
            problems.append(f"{row['file']}: unreadable")
            continue
        if row.get("mode") not in (None, "0o600"):
            problems.append(f"{row['file']}: mode {row.get('mode')} (expected 0600)")
        for cert in row.get("certificates") or []:
            if not cert.get("present"):
                problems.append(f"{row['file']}: certificate missing {cert['path']}")
            elif cert.get("expired"):
                problems.append(f"{row['file']}: certificate expired {cert.get('not_after')}")
            elif cert.get("renewal_overdue"):
                action = ("renewal has not succeeded" if cert.get("issuer") == "public"
                          else "re-apply to reissue the Origin CA certificate")
                problems.append(f"{row['file']}: certificate expires {cert.get('not_after')}; {action}")
    return {
        "mode": "nginx",
        "nginx_installed": bool(facts.get("nginx_installed")),
        "nginx_active": bool(facts.get("active")),
        "nginx_version": ".".join(str(x) for x in facts.get("version") or []) or None,
        "config_valid": bool(facts.get("config_valid")),
        "config_test_tail": facts.get("config_test_tail") or [],
        "includes_conf_d": bool(facts.get("includes_conf_d")),
        "sandbox_caddy": facts.get("sandbox_caddy") or {},
        "routes": routes,
        "unloaded_routes": unloaded,
        "conflicts": conflicts,
        "certificate_problems": problems,
    }


def status(entry: dict) -> dict:
    return summarize(probe(entry))


def remove_route_and_certificates(entry: dict, name: str) -> dict:
    """Remove one Sandbox route file, then its Sandbox-owned certificates."""
    result = remove_route(entry, name)
    cert_dir = f"{CERT_ROOT}/{name}"
    command = (_SUDO + f"$SUDO rm -rf {shlex.quote(cert_dir)}; "
               "if command -v certbot >/dev/null 2>&1 && "
               f"$SUDO test -d {shlex.quote('/etc/letsencrypt/live/sandbox-' + name)}; then "
               f"$SUDO certbot delete --non-interactive --cert-name {shlex.quote('sandbox-' + name)}; fi")
    cleanup = _ssh(entry, command, timeout=120)
    result["certificates_removed"] = cleanup.returncode == 0
    return result

"""nginx server blocks for tailnet-only names (spec 078). Pure functions."""
from __future__ import annotations

import hashlib
import json
import re

from sandbox.hosting.tailnet_names.models import CERT_ROOT, TailnetName, Upstream

HEADER_PREFIX = "# sandbox-tailnet-name "
# Served by the block itself so the remote program can prove which version
# of the block nginx has loaded: the marker is a digest of the block's own
# text (with the marker zeroed), so any change to the block changes it.
MARKER_PATH = "/.well-known/sandbox-tailnet-activation"
_ZERO_MARKER = "0" * 32
_TOKEN = re.compile(r"[a-z0-9.:-]+")


def _token(value: str) -> str:
    """Every rendered value is validated upstream; this is defence in depth."""
    if not _TOKEN.fullmatch(value):
        raise ValueError("unsafe nginx token")
    return value


def render_block(name: TailnetName, upstream: Upstream, tailnet_ip: str, *,
                 http2_directive: bool) -> str:
    text = _render(name, upstream, tailnet_ip, http2_directive=http2_directive)
    marker = hashlib.sha256(text.encode()).hexdigest()[:32]
    return text.replace(f'return 200 "{_ZERO_MARKER}"', f'return 200 "{marker}"', 1)


def _render(name: TailnetName, upstream: Upstream, tailnet_ip: str, *,
            http2_directive: bool) -> str:
    fqdn = _token(name.fqdn)
    ip = _token(tailnet_ip)
    target = _token(str(upstream))
    cert_dir = f"{CERT_ROOT}/{fqdn}"
    header = json.dumps({"fqdn": fqdn, "tailnet_ip": ip, "upstream": target, "version": 1},
                        sort_keys=True)
    ssl_listen = f"listen {ip}:443 ssl;\n    http2 on;" if http2_directive \
        else f"listen {ip}:443 ssl http2;"
    # The $host guard: this block may be nginx's implicit default for the
    # tailnet address, so any other Host on that socket is dropped (444).
    guard = f'if ($host != "{fqdn}") {{ return 444; }}'
    return f"""{HEADER_PREFIX}{header}
# Managed by `sb host tailnet-name`. Reachable only on the tailnet address;
# never the default server for any address.
server {{
    listen {ip}:80;
    server_name {fqdn};
    {guard}
    return 308 https://{fqdn}$request_uri;
}}
server {{
    {ssl_listen}
    server_name {fqdn};
    {guard}
    ssl_certificate     {cert_dir}/fullchain.pem;
    ssl_certificate_key {cert_dir}/privkey.pem;
    client_max_body_size 50m;
    location = {MARKER_PATH} {{ default_type text/plain; return 200 "{_ZERO_MARKER}"; }}
    location / {{
        proxy_pass http://{target};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 600s;
    }}
}}
"""


def parse_header(text: str) -> dict | None:
    first = (text or "").split("\n", 1)[0]
    if not first.startswith(HEADER_PREFIX):
        return None
    try:
        value = json.loads(first[len(HEADER_PREFIX):])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None

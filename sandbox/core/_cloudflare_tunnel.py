"""Read-only Cloudflare Tunnel validation and safe connector unit rendering."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


API_BASE = "https://api.cloudflare.com/client/v4"


class TunnelError(RuntimeError):
    pass


class Client:
    def __init__(self, token: str):
        if not (token or "").strip():
            raise TunnelError("Cloudflare Tunnel token is not configured")
        self.token = token.strip()

    def _request(self, path: str) -> dict:
        request = urllib.request.Request(API_BASE + path, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise TunnelError(f"Cloudflare Tunnel request failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise TunnelError(f"Cloudflare Tunnel request failed: {exc}") from exc
        if not data.get("success"):
            raise TunnelError("Cloudflare Tunnel rejected the request")
        result = data.get("result")
        if not isinstance(result, dict):
            raise TunnelError("Cloudflare Tunnel returned an invalid response")
        return result

    def tunnel(self, account_id: str, tunnel_id: str) -> dict:
        return self._request(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")

    def configuration(self, account_id: str, tunnel_id: str) -> dict:
        return self._request(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations")


def validate_configuration(value: dict, fqdn: str, target: str) -> dict:
    ingress = value.get("config", value).get("ingress") if isinstance(value.get("config", value), dict) else None
    if not isinstance(ingress, list) or len(ingress) < 2:
        raise TunnelError("Cloudflare Tunnel must have an exact ingress route and a terminal catch-all")
    route = next((item for item in ingress if item.get("hostname") == fqdn), None)
    if not isinstance(route, dict) or route.get("service") != target:
        raise TunnelError("Cloudflare Tunnel route must target the exact loopback proxy")
    terminal = ingress[-1]
    if terminal.get("hostname") or not (terminal.get("service") == "http_status:404"):
        raise TunnelError("Cloudflare Tunnel requires a terminal http_status:404 catch-all")
    return {"hostname": fqdn, "service": target}


def service_unit(unit: str, token_file: str) -> str:
    return (
        "[Unit]\nDescription=Hermes Cloudflare Tunnel\nAfter=network-online.target\n"
        "[Service]\n"
        f"ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate run --token-file {token_file}\n"
        "Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nPrivateTmp=true\n"
        "[Install]\nWantedBy=default.target\n"
    )


def token_write_command(path: str) -> str:
    encoded = base64.b64encode(path.encode()).decode()
    return f"mkdir -p $(dirname $(echo {encoded} | base64 -d)); umask 077; cat > $(echo {encoded} | base64 -d)"

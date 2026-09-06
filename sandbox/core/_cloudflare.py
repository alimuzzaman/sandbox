"""Small, token-based Cloudflare client used by managed hosting.

The client deliberately uses the standard library: Sandbox already avoids a
general HTTP dependency and this keeps credentials confined to sandbox.local.yml.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from sandbox.core._config import _local_yaml, _write_local_yaml
from sandbox.core._secrets import resolve_secret, write_secret

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(RuntimeError):
    def __init__(self, message: str, code: str = "provider_failed"):
        self.code = code
        super().__init__(message)


def cloudflare_token() -> str:
    return str(resolve_secret("CLOUDFLARE_API_TOKEN") or
               ((_local_yaml().get("cloudflare") or {}).get("api_token") or "")).strip()


def save_cloudflare_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise CloudflareError("Cloudflare API token cannot be empty")
    write_secret("CLOUDFLARE_API_TOKEN", token)


def migrate_legacy_token() -> bool:
    """Move the legacy local token into the personal secret file once."""
    local = _local_yaml()
    token = str((local.get("cloudflare") or {}).get("api_token") or "").strip()
    if not token or resolve_secret("CLOUDFLARE_API_TOKEN"):
        return False
    write_secret("CLOUDFLARE_API_TOKEN", token)
    cloudflare = dict(local.get("cloudflare") or {})
    cloudflare.pop("api_token", None)
    if cloudflare:
        local["cloudflare"] = cloudflare
    else:
        local.pop("cloudflare", None)
    _write_local_yaml(local)
    return True


class Client:
    def __init__(self, token: str | None = None):
        self.token = (token or cloudflare_token()).strip()
        if not self.token:
            raise CloudflareError("Cloudflare is not connected; run `./sb connect cloudflare`")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{API_BASE}{path}", data=payload, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read().decode())
                detail = "; ".join(str(item.get("message", item)) for item in payload.get("errors", []))
            except (ValueError, OSError):
                pass
            code = ("provider_auth" if exc.code in {401, 403} else
                    "provider_rate_limited" if exc.code == 429 else
                    "provider_failed")
            raise CloudflareError(
                f"Cloudflare request failed: HTTP {exc.code}{': ' + detail if detail else ''}",
                code,
            ) from exc
        except (urllib.error.URLError, ValueError) as exc:
            code = "provider_timeout" if isinstance(exc, urllib.error.URLError) and \
                isinstance(getattr(exc, "reason", None), TimeoutError) else "provider_failed"
            raise CloudflareError(f"Cloudflare request failed: {code}", code) from exc
        if not data.get("success"):
            messages = "; ".join(str(e.get("message", e)) for e in data.get("errors", []))
            raise CloudflareError(f"Cloudflare rejected the request: {messages or 'unknown error'}")
        return data

    def purge_cache(self, zone_id: str) -> dict:
        """Request a zone-wide purge for one already validated zone."""
        result = self._request(
            "POST", f"/zones/{zone_id}/purge_cache",
            {"purge_everything": True},
        ).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            raise CloudflareError("Cloudflare purge response is invalid", "provider_response_invalid")
        return {"id": result["id"]}

    def zone(self, hostname: str) -> dict:
        data = self._request("GET", "/zones?name=" + urllib.parse.quote(hostname))
        rows = data.get("result") or []
        if not rows:
            raise CloudflareError(f"no Cloudflare zone found for {hostname}", "zone_not_found")
        return rows[0]

    def records(self, zone_id: str, hostname: str) -> list[dict]:
        path = f"/zones/{zone_id}/dns_records?name={urllib.parse.quote(hostname)}"
        return list(self._request("GET", path).get("result") or [])

    def tunnels(self, account_id: str) -> list[dict]:
        """Return account tunnels for strict attach-only validation."""
        result = self._request("GET", f"/accounts/{account_id}/cfd_tunnel").get("result")
        if not isinstance(result, list):
            raise CloudflareError("Cloudflare tunnel list returned an invalid response")
        return result

    def access_applications(self, account_id: str) -> list[dict]:
        """Return Access applications for strict attach-only validation."""
        result = self._request("GET", f"/accounts/{account_id}/access/apps").get("result")
        if not isinstance(result, list):
            raise CloudflareError("Cloudflare Access application list returned an invalid response")
        return result

    def upsert_address(self, zone_id: str, hostname: str, address: str, proxied: bool = True) -> dict:
        record_type = "AAAA" if ":" in address else "A"
        existing = [r for r in self.records(zone_id, hostname) if r.get("type") == record_type]
        body = {"type": record_type, "name": hostname, "content": address,
                "proxied": bool(proxied), "ttl": 1,
                "comment": "managed by Sandbox hosting"}
        if existing:
            return self._request("PUT", f"/zones/{zone_id}/dns_records/{existing[0]['id']}", body)["result"]
        return self._request("POST", f"/zones/{zone_id}/dns_records", body)["result"]

    def delete_record(self, zone_id: str, record_id: str) -> None:
        """Delete one explicitly identified record; never broad-delete a zone."""
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")

    def restore_record(self, zone_id: str, previous: dict | None, created_id: str | None = None) -> None:
        """Restore one captured record, or remove only an identified created record."""
        if previous:
            record_id = str(previous.get("id") or "")
            if not record_id:
                raise CloudflareError("cannot restore DNS record without an id")
            body = {key: previous[key] for key in ("type", "name", "content", "proxied", "ttl", "comment")
                    if key in previous}
            self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", body)
        elif created_id:
            self.delete_record(zone_id, created_id)

    def update_record(self, zone_id: str, record: dict, *, proxied: bool) -> dict:
        """Update only an existing declared record while preserving its type/content."""
        record_id = str(record.get("id") or "")
        if not record_id:
            raise CloudflareError("cannot update DNS record without an id")
        body = {key: record[key] for key in ("type", "name", "content", "ttl", "comment") if key in record}
        body["proxied"] = proxied
        return self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", body)["result"]

    def ssl_mode(self, zone_id: str, value: str = "strict") -> dict:
        return self._request("PATCH", f"/zones/{zone_id}/settings/ssl", {"value": value})["result"]

    def current_ssl_mode(self, zone_id: str) -> str | None:
        result = self._request("GET", f"/zones/{zone_id}/settings/ssl").get("result") or {}
        return result.get("value")

    def create_origin_certificate(self, csr: str, hostnames: list[str], validity_days: int = 365) -> dict:
        body = {"csr": csr, "hostnames": hostnames, "requested_validity": validity_days,
                "request_type": "origin-ecc"}
        return self._request("POST", "/certificates", body)["result"]

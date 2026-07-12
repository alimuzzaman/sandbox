"""Read-only Cloudflare Access validation for Hermes public exposure."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


API_BASE = "https://api.cloudflare.com/client/v4"


class AccessError(RuntimeError):
    pass


class Client:
    """Small account-scoped client; callers supply a narrowly scoped token."""

    def __init__(self, token: str):
        if not (token or "").strip():
            raise AccessError("Cloudflare Access token is not configured")
        self.token = token.strip()

    def _request(self, path: str) -> dict:
        request = urllib.request.Request(
            API_BASE + path,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise AccessError(f"Cloudflare Access request failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise AccessError(f"Cloudflare Access request failed: {exc}") from exc
        if not data.get("success"):
            raise AccessError("Cloudflare Access rejected the request")
        result = data.get("result")
        if not isinstance(result, dict):
            raise AccessError("Cloudflare Access returned an invalid response")
        return result

    def application(self, account_id: str, app_id: str) -> dict:
        return self._request(f"/accounts/{account_id}/access/apps/{app_id}")

    def policy(self, account_id: str, policy_id: str) -> dict:
        return self._request(f"/accounts/{account_id}/access/policies/{policy_id}")


def validate_application(value: dict, fqdn: str) -> dict:
    if value.get("type") != "self_hosted" or str(value.get("domain") or "").lower().rstrip("/") != fqdn:
        raise AccessError("Cloudflare Access application must be self-hosted for the exact dashboard hostname")
    return {"id": str(value.get("id") or ""), "domain": fqdn, "type": "self_hosted"}


def validate_policy(value: dict) -> dict:
    decision = str(value.get("decision") or "").lower()
    include = value.get("include")
    if decision != "allow" or not isinstance(include, list) or not include:
        raise AccessError("Cloudflare Access policy must contain an explicit allow rule")
    serialized = json.dumps(include, sort_keys=True).lower()
    if "everyone" in serialized or "all_valid_email" in serialized:
        raise AccessError("Cloudflare Access policy is too broad")
    mfa = value.get("mfa_config")
    if not isinstance(mfa, dict) or mfa.get("mfa_disabled") is not False:
        raise AccessError("Cloudflare Access policy must require MFA")
    return {"id": str(value.get("id") or ""), "decision": "allow", "mfa_required": True}

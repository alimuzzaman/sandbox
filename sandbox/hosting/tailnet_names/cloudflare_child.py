"""Cloudflare DNS child for `sb host tailnet-name` (spec 078).

Run only by the secrets broker as ``python -I cloudflare_child.py <json>``,
with the token in ``CLOUDFLARE_DNS_API_TOKEN``. Stdlib only: ``-I`` keeps
the Sandbox package, ``PYTHONPATH`` and the working directory off the import
path. The API base is a constant, never read from the environment, so a
request cannot send the token anywhere else. Output is one JSON line with
codes only; response bodies are never echoed.

Operations (one exact-name A record):
  ensure  read the record; create it DNS-only when absent; "exists" when it
          already points at the address unproxied; "conflict" otherwise
  inspect read only
  delete  delete the recorded id only when name, address and proxied=false
          still match; "absent" when it is already gone
Exit status: 0 success, 3 conflict, 1 error.
"""
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
TOKEN_ENV = "CLOUDFLARE_DNS_API_TOKEN"
TIMEOUT_SECONDS = 30
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_ID = re.compile(r"[0-9a-f]{32}")
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _hostname(value):
    if not isinstance(value, str) or len(value) > 253 or value != value.lower():
        return False
    labels = value.split(".")
    return len(labels) >= 2 and all(_LABEL.fullmatch(label) for label in labels)


def _call(token, method, path, body=None, *, opener):
    request = urllib.request.Request(
        API_BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "User-Agent": "sandbox-tailnet-name"})
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ApiError("not_found" if exc.code == 404 else "http_%d" % exc.code) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ApiError("unreachable") from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ApiError("api_error")
    return payload.get("result")


def _zone_id(token, zone, opener):
    rows = _call(token, "GET", "/zones?" + urllib.parse.urlencode({"name": zone}), opener=opener)
    rows = [row for row in rows or [] if isinstance(row, dict) and row.get("name") == zone]
    if len(rows) != 1 or not _ID.fullmatch(str(rows[0].get("id"))):
        raise ApiError("zone_not_visible")
    return rows[0]["id"]


def _a_records(token, zone_id, name, opener):
    rows = _call(token, "GET", f"/zones/{zone_id}/dns_records?"
                 + urllib.parse.urlencode({"type": "A", "name": name}), opener=opener)
    return [row for row in rows or []
            if isinstance(row, dict) and row.get("name") == name and row.get("type") == "A"]


def _summary(result, row=None, code=None):
    out = {"result": result}
    if row is not None:
        out.update({"record_id": row.get("id"), "content": row.get("content"),
                    "proxied": row.get("proxied")})
    if code:
        out["code"] = code
    return out


def _valid(request):
    if not isinstance(request, dict) or request.get("op") not in ("ensure", "inspect", "delete"):
        return False
    zone, name, ip = request.get("zone"), request.get("name"), request.get("ip")
    if not (_hostname(zone) and _hostname(name) and name.endswith("." + zone)):
        return False
    try:
        if ipaddress.IPv4Address(ip) not in _TAILNET:
            return False
    except (ValueError, TypeError):
        return False
    if request["op"] == "delete" and not _ID.fullmatch(str(request.get("record_id"))):
        return False
    return True


def run(request, token, *, opener=urllib.request.urlopen):
    """Return ``(result, exit_status)``; never raises for API failures."""
    if not _valid(request):
        return _summary("error", code="request_invalid"), 1
    try:
        zone_id = _zone_id(token, request["zone"], opener)
        name, ip = request["name"], request["ip"]
        if request["op"] == "delete":
            try:
                row = _call(token, "GET", f"/zones/{zone_id}/dns_records/{request['record_id']}",
                            opener=opener)
            except ApiError as exc:
                if exc.code == "not_found":
                    return _summary("absent"), 0
                raise
            if not (isinstance(row, dict) and row.get("name") == name and row.get("type") == "A"
                    and row.get("content") == ip and row.get("proxied") is False):
                return _summary("conflict", row if isinstance(row, dict) else None), 3
            _call(token, "DELETE", f"/zones/{zone_id}/dns_records/{request['record_id']}",
                  opener=opener)
            return _summary("deleted", row), 0
        rows = _a_records(token, zone_id, name, opener)
        if len(rows) > 1:
            return _summary("conflict", rows[0], code="multiple_records"), 3
        if rows:
            row = rows[0]
            if row.get("content") == ip and row.get("proxied") is False:
                return _summary("exists", row), 0
            return _summary("conflict", row), 3
        if request["op"] == "inspect":
            return _summary("absent"), 0
        row = _call(token, "POST", f"/zones/{zone_id}/dns_records",
                    {"type": "A", "name": name, "content": ip, "ttl": 1, "proxied": False,
                     "comment": "sandbox tailnet-name"}, opener=opener)
        return _summary("created", row if isinstance(row, dict) else {}), 0
    except ApiError as exc:
        return _summary("error", code=exc.code), 1


def main(argv=None, environ=None, stdout=None):
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    token = environ.get(TOKEN_ENV)
    if not token:
        result, status = _summary("error", code="token_missing"), 1
    else:
        try:
            request = json.loads(argv[1]) if len(argv) == 2 else None
        except ValueError:
            request = None
        result, status = run(request, token)
    stdout.write(json.dumps(result, sort_keys=True) + "\n")
    stdout.flush()
    return status


if __name__ == "__main__":
    sys.exit(main())

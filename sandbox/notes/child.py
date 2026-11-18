"""Agent Notes MCP client run as a secrets-broker child.

The broker starts this file with ``python -I child.py REQUEST RESPONSE`` and
puts the bearer token in ``SANDBOX_NOTES_TOKEN``. The token exists only in
this process: it is never written, printed or returned. Stdlib only, because
``-I`` leaves the Sandbox package off ``sys.path``.

REQUEST is a JSON file ``{"endpoint": URL, "calls": [{"tool", "arguments"}]}``.
Each call's outcome is appended to RESPONSE as one JSON line and flushed, so a
caller keeps the results of the calls that finished even if this process is
killed part way. The process prints one short status line and exits 0 unless
the request itself could not be read.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN_ENV = "SANDBOX_NOTES_TOKEN"
RATE_RETRIES = 3
MAX_RETRY_WAIT = 65
TIMEOUT = 60


def _post(endpoint, token, body):
    request = urllib.request.Request(
        endpoint, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Bearer " + token, "User-Agent": "sandbox-notes"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def _call(endpoint, token, index, tool, arguments):
    body = {"jsonrpc": "2.0", "id": index, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    for attempt in range(RATE_RETRIES + 1):
        try:
            status, raw, headers = _post(endpoint, token, body)
        except (OSError, ValueError) as exc:
            return {"ok": False, "code": "notes_unreachable",
                    "error": type(exc).__name__ + ": " + str(exc)[:200]}
        if status == 429 and attempt < RATE_RETRIES:
            try:
                wait = int(headers.get("Retry-After") or 60)
            except ValueError:
                wait = 60
            time.sleep(max(1, min(wait, MAX_RETRY_WAIT)))
            continue
        break
    if status == 401:
        return {"ok": False, "code": "notes_unauthorized",
                "error": "the stored token was refused; run `sb notes login`"}
    if status == 429:
        return {"ok": False, "code": "notes_rate_limited", "error": "rate limit still exceeded"}
    try:
        payload = json.loads(raw.decode() or "null")
    except ValueError:
        return {"ok": False, "code": "notes_bad_response", "error": "HTTP %d, not JSON" % status}
    if not isinstance(payload, dict):
        return {"ok": False, "code": "notes_bad_response", "error": "HTTP %d" % status}
    if "error" in payload:
        error = payload.get("error") or {}
        return {"ok": False, "code": "notes_protocol_error",
                "error": str(error.get("message") if isinstance(error, dict) else error)[:500]}
    result = payload.get("result") or {}
    text = ""
    for item in result.get("content") or ():
        if isinstance(item, dict) and item.get("type") == "text":
            text += item.get("text") or ""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {"message": text}
    if result.get("isError"):
        message = data.get("error") if isinstance(data, dict) else None
        return {"ok": False, "code": "notes_tool_error", "error": str(message or text)[:500]}
    return {"ok": True, "result": data}


def main(argv):
    if len(argv) != 3:
        print('{"ok":false,"code":"usage"}')
        return 2
    token = os.environ.get(TOKEN_ENV, "")
    try:
        with open(argv[1], encoding="utf-8") as handle:
            request = json.load(handle)
        endpoint = request["endpoint"]
        calls = request["calls"]
    except (OSError, ValueError, KeyError, TypeError):
        print('{"ok":false,"code":"request_invalid"}')
        return 2
    done = 0
    stopped = None
    fd = os.open(argv[2], os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        for index, call in enumerate(calls):
            outcome = _call(endpoint, token, index, call.get("tool"), call.get("arguments") or {})
            outcome["i"] = index
            out.write(json.dumps(outcome) + "\n")
            out.flush()
            done += 1
            # A refused token or a dead service fails every later call the same way.
            if outcome.get("code") in ("notes_unauthorized", "notes_unreachable"):
                stopped = outcome["code"]
                break
    print(json.dumps({"ok": stopped is None, "calls": done, "stopped": stopped}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

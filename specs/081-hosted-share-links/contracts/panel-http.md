# Contract: panel share route (2026-11-11 follow-up)

`POST /api/hosting/share` on `sb web`. No other method or path is added.

## Request gate (all required, checked before the body is read)

- The panel access gate (`_web_request_allowed`) passes.
- `Origin` is present, its scheme is `http` or `https`, and its `host[:port]` equals
  `Host` (case-insensitive). For an `--allow-host` name the scheme must be `https`.
- `Content-Type` is `application/json` (parameters such as `charset` allowed).
- `X-Sandbox-Panel: share`.
- `Sec-Fetch-Site`, when present, is `same-origin`.
- `Content-Length` is 1 to 4096.

Failure: `403 {"ok": false, "code": "panel_request_refused", "message": "..."}`.

## Body

```json
{"action": "create" | "list" | "revoke", "remote": "<remote name>", "stack": "<slug>",
 "id": "l-<12hex>" | "l-<32hex>" (old form) | "s-<32hex>"}
```

`id` only with `revoke`, and required there. Any other key, a non-string value, a
remote name outside the remote naming rule, or a slug outside `[a-z0-9][a-z0-9-]{0,62}`:
`400 {"ok": false, "code": "panel_request_invalid", "message": "..."}`.

## Results (every response: `Cache-Control: no-store`)

- `create`: `200` with `{"ok": true, "link", "link_id", "target", "entry", "kind",
  "scope", "expires_at", "expires_clamped", "uses", "hostname", "notes"?}`. The only
  response that ever carries `link`.
- `list`: `200` with `{"ok": true, "target", "entry", "links": [{"id", "kind", "scope",
  "issued_at", "expires_at", "uses", "used"}], "sessions": [{"id", "link",
  "created_at", "expires_at"}]}`. Only these fields are passed through.
- `revoke`: `200` with `{"ok": true, "target", "entry", "revoked_links",
  "revoked_sessions", "revoked_keys"}`.
- A share refusal: `409 {"ok": false, "code": "<ShareRefusal code>", "message":
  "<redacted message>"}`.
- Anything unexpected: `500 {"ok": false, "code": "share_failed", "message": "the
  share request failed; see the CLI for details"}`, with no detail.

Every panel response also carries `X-Frame-Options: DENY` and
`Content-Security-Policy: frame-ancestors 'none'`, so no page can frame the panel and
clickjack the share and confirm buttons (Sol review, 2026-11-11). A body that fails to
parse, including one nested deep enough to exhaust the parser, is `400
panel_request_invalid`.

The handler writes nothing to stdout, stderr or any file beyond what the share
functions already do (audit records, which never hold a token).

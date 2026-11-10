# Data model: login service site gate

## Site gate entry (manifest, normalized)

| Field | Rule |
|---|---|
| `mode` | `site_gate` |
| `requires_email` | omitted or `false` |
| `hostname` | a served, non-wildcard alias of the target; never the primary; fronts no other entry |
| `upstream` | `http://<service>:<port>` with no path (or `/`), query, fragment or credentials; `<service>` is a background service of this project other than `sandbox-login` |
| `path` | link landing path; default `/__sandbox/share/<entry>`; absolute, not `/` |
| `landing_path` | where a new session is redirected; default `/` |
| `ttl_seconds` | link lifetime, 60 to 3600, default 900 |

Declaration-level changes:

- `login_service.capture` is optional when every entry is `site_gate`, and required otherwise.
- `basic_auth` on the same environment is refused.
- `session_ttl_seconds` (300 to 172800) and `enabled_until` apply unchanged.

## Share link (service tmpfs: `/run/sandbox-login/sites/<entry>/links/<token_sha256>.json`)

```json
{"schema":"sandbox.site-link.v1","entry":"mockup","id":"l-1a2b3c4d5e6f",
 "token_sha256":"<hex>","issued_at":1700000000,"expires_at":1700000900,"uses":1}
```

- `id`: `l-` + 12 hex characters, public, shown in list output.
- `uses`: 1 to 20. `expires_at - issued_at`: 60 to 3600.
- A malformed record counts as absent.
- Spent slots: `used/<token_sha256>.<k>` for `0 <= k < uses`.

States: **live** (unexpired, a slot free) → **spent** (all slots used) or **expired**
(time) or **revoked** (record deleted). Only live accepts a use. Expired and spent records
are pruned at the next mint or list.

## Gate session (`/run/sandbox-login/sites/<entry>/sessions/<secret_sha256>.json`)

```json
{"schema":"sandbox.site-session.v1","entry":"mockup","id":"s-9f8e7d6c5b4a",
 "link":"l-1a2b3c4d5e6f","created_at":1700000100,"expires_at":1700172900}
```

- `expires_at` = `min(created_at + session_ttl_seconds, enabled_until)`.
- The browser holds the secret (43 base64url characters) in cookie
  `__Host-sandbox_site_<entry>`.

States: **active** → **expired** (time) or **revoked** (record deleted directly, or with
its link).

## Caps

| Cap | Value |
|---|---|
| uses per link | 20 |
| live links per entry | 32 |
| link attempts per entry | 30 per 60 seconds |
| websocket revoke check | every 30 seconds at most |

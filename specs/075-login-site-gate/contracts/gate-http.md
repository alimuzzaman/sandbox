# HTTP contract: the gate hostname of a `site_gate` entry

Every response below carries `Cache-Control: no-store` on gate-generated responses.
"404" always means the same status, headers and body (`Not found\n`).

| Request | Condition | Response |
|---|---|---|
| any | `enabled_until` passed | 404 |
| any | request target is not origin-form, starts with `//`, or `Host` is missing, duplicated, or not the gate hostname | 404 |
| `GET`/`HEAD` `<path>` | at least one live link | 200 link page (CSP with script hash, `Referrer-Policy: no-referrer`) |
| `GET`/`HEAD` `<path>` | no live link | 404 |
| `POST` `<path>` | over 30 attempts in 60 s for the entry | 429 `rate_limited`, `Referrer-Policy: no-referrer` |
| `POST` `<path>` | token valid, link live, slot claimed | 303 to `landing_path`, `Set-Cookie: __Host-sandbox_site_<entry>=<secret>; Path=/; Max-Age=<remaining>; Secure; HttpOnly; SameSite=Lax`, `Referrer-Policy: no-referrer` |
| `POST` `<path>` | anything else (bad Origin, malformed, unknown, expired, spent, revoked) | 404 |
| any other | valid session | forwarded to the upstream; response relayed |
| `Upgrade: websocket` | valid session | tunnel to the upstream; closes at session expiry, `enabled_until`, or within 30 s of revoke |
| any other | no valid session | 404; nothing reaches the upstream |

Forwarding rules:

- The connection goes only to the declared upstream host and port.
- `Host` is set to the gate hostname. Hop-by-hop headers and the gate's own cookie are
  dropped. All other request headers and the body pass through.
- Bodies over 16 MiB get 413. Chunked request bodies get 411, as in the existing modes.

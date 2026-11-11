# Contract: gate HTTP behaviour on a `site_gate` hostname

Every response carries `X-Robots-Tag: noindex, nofollow, noarchive`,
`Referrer-Policy: no-referrer` and `Cache-Control: no-store` (forwarded responses keep
the upstream's caching headers, but the robots and referrer headers are replaced). Every
HTML page the gate itself serves has `<meta name="robots" content="noindex, nofollow">`.

| Request | Answer |
|---|---|
| `GET /robots.txt` | 200 `User-agent: *` / `Disallow: /` |
| `GET\|HEAD /s/` | 200 opener page, whatever links exist |
| `POST /s/` past 30 attempts per entry in 60 s | 429 |
| `POST /s/` with a 22-character token whose short-form link is live, with a free use | 303 to the scope (site) or `/__sandbox/stack` (stack); sets the cookie |
| `POST /s/` otherwise (wrong, unknown, expired, revoked, spent, malformed, old-form token) | 404, the same bytes and headers every time |
| Old form (until removal, R15): `GET\|HEAD /s/<id>` with `<id>` live | 200 opener page: reads the fragment, clears it, POSTs the token |
| `GET\|HEAD /s/<id>` otherwise | 404 |
| `POST /s/<id>` past 30 attempts per entry in 60 s | 429 |
| `POST /s/<id>` with a token whose link is that id, live, with a free use | 303 to the scope (site) or `/__sandbox/stack` (stack); sets the cookie |
| `POST /s/<id>` otherwise | 404 |
| `GET /__sandbox/stack` with a live stack session | 200 stack page, sign-in links freshly minted |
| Any path with a covering live site session | Forwarded to the upstream, websocket included |
| Anything else, or an unsafe path | 404 |

Cookie: `__Host-sandbox_share=<secret>[.<secret>...]` (at most 8), `Path=/`, `Secure`,
`HttpOnly`, `SameSite=Lax`, with `Max-Age` set to the latest remaining session life.

Unsafe path, refused before scope matching: a raw path containing `%2f`, `%5c`, `%2e`,
`%00`, `\`, `;`, `//`, or a `.` or `..` segment (case-insensitive).

Operator verbs inside the service (`site mint|list|revoke|stack <entry>`) read the
record on stdin and print one JSON line.

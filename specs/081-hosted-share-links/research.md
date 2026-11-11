# Research: Hosted share links

## R1. Where the gate sits

- **Decision**: `sandbox-login` publishes `127.0.0.1:<port + 3000>:8080` whenever a login
  service is declared. Both front doors route every gate hostname (the `share.hostname`
  and every login-service entry hostname) to that port. The application keeps its own
  managed port, so health checks and the owner's loopback view are unchanged.
- **Rationale**: Every served route today goes to one upstream port (`caddyfile`,
  `front_door/nginx._render`). A per-hostname upstream is a small, local change in both
  renderers. 19000-20999 is used by plugin-check lanes; 21000-21999 is free.
- **Alternatives**: Put the gate in front of all traffic (rejected: a Python proxy in
  front of public sites). Keep the project-edge model of 075 (rejected: the owner asked
  for no hand-written edge config).

## R2. Token placement

- **Decision**: `https://<gate>/s/<id>#<token>`. GET on `/s/<id>` serves an
  auto-submitting page only while that id is live; the POST carries the token.
- **Rationale**: Agent Notes puts the token in the path, but Sandbox front doors and
  Cloudflare can log request lines, and FR-003 forbids that. The fragment never leaves
  the browser. Answering only for a live id also fixes the 075 leak, where the link path
  answered whenever any link was live.
- **Alternatives**: Token in the path (logged). A fixed `/s/` page (leaks that a share
  exists).

## R3. Link and session lifetimes

- **Decision**: Link lifetime is 60 s to 30 days (default 7 days), clamped to
  `enabled_until`. A session ends at the earliest of the link's expiry,
  `session_ttl_seconds` and `enabled_until`. Each request checks that the session's link
  file still exists, so a revoke ends access even if session removal races.
- **Rationale**: Mirrors Agent Notes (`share_cookie_grants` re-checks the link on every
  request) while keeping the 075 bound on cookie life.

## R4. Path scope

- **Decision**: Scope is `/`, a folder ending in `/`, or one exact path. Before matching,
  the gate refuses (404) a raw path containing `%2f`, `%5c`, `%2e`, `%00`, a backslash,
  `//`, `;`, or a `.`/`..` segment, case-insensitive. Matching uses the decoded path. The
  request is forwarded byte-for-byte, so the upstream sees the path that was checked.
- **Rationale**: Every way an upstream could resolve a checked path to a different file
  is refused rather than normalized.

## R5. Several shares in one browser

- **Decision**: One cookie, `__Host-sandbox_share`, holds up to 8 session secrets joined
  by `.`. A request is allowed when any live session covers it. A new session drops dead
  secrets first, then the oldest.
- **Rationale**: `__Host-` cookies must use `Path=/`, so per-scope cookie paths
  (Agent Notes' approach) are unavailable.

## R6. Stack page sign-in

- **Decision**: For a WordPress stack target, `share create --stack` installs a per-link
  mu-plugin, `sandbox-share-<expiry>-<kid>.php`, next to the declared autologin path. It
  holds a random 256-bit key, the key id, the user and the expiry. The gate holds the same
  key in the stack record. Each view mints `<exp>.<nonce>.<hmac>` for at most 10 minutes.
  The WordPress link is `https://<primary>/?sandbox_share_login=<kid>#<token>`. The
  mu-plugin serves an auto-submit page on GET, verifies on POST, and claims the nonce
  with one `INSERT IGNORE` into the site's options table (a UNIQUE key on single sites and
  every multisite blog), so each token works once. Revoke deletes the file; expiry makes
  it inert, and the next mint deletes expired files.
  Login-service entries listed in `share.stack_sign_in` are minted locally by the gate:
  it writes the 075 token record for that entry, with a 600 s lifetime.
- **Rationale**: The gate never gets Docker or SSH access. The key can do no more than
  the stack link already grants, and for no longer.
- **Alternatives**: A controller round trip per view (needs the controller online).
  Standing autologin tokens (permanent bypass; rejected).

## R7. No indexing

- **Decision**: Caddy: a `header { defer; X-Robots-Tag "noindex, nofollow, noarchive" }`
  in every hosted site block, served or redirect. nginx: `proxy_hide_header X-Robots-Tag`
  plus `add_header X-Robots-Tag "..." always;` in every `location` and every `if` that has
  its own `add_header`, because nginx does not inherit `add_header` into a level that
  declares one. `robots` defaults to `deny`. `indexing: public` turns both off, and
  `robots: allow` needs `indexing: public`.
- **Rationale**: Deferred or `always` headers also cover 404, 401 and redirects.

## R8. Audit identity

- **Decision**: Controller is `socket.gethostname()`. The owner label comes from
  `lifetime.naming.owner_label` on the project checkout, falling back to the OS user.
  The remote record is appended through one SSH command (a stdlib program, O_APPEND,
  0600).
- **Rationale**: Reuses the 077 owner-label rule (never an email).

## R9. Job and MCP output

- **Decision**: `create` refuses when `SANDBOX_DURABLE_JOB_ID` is set. The MCP tool
  shells out to `sb host share create --json` and returns its JSON. The MCP layer must
  not log subprocess stdout.
- **Rationale**: Job output is stored; MCP results go only to the calling agent.

## R10. Panel request gate (2026-11-11)

- Decision: the share route passes the panel's normal access gate and then requires an
  `Origin` whose scheme is http or https and whose host and port equal `Host`, a
  `Content-Type` of `application/json`, the header `X-Sandbox-Panel: share`, and, when
  the browser sends `Sec-Fetch-Site`, the value `same-origin`. A body over 4 KiB, an
  unknown key or a bad name is refused before any share call.
- Rationale: the panel has no login. A cross-site form cannot send JSON or a custom
  header, and a cross-site `fetch` that does triggers a CORS preflight the panel never
  answers. Requiring `Origin` (the rest of the panel allows its absence for GETs and
  scripts) stops a script or a page from another loopback origin on the same machine.
- Framing: every panel response forbids framing (`X-Frame-Options: DENY`, `frame-ancestors 'none'`),
  because a same-origin gate does not stop a framed panel from being clicked through.
- Alternatives: a CSRF token in the page shell (the shell is inlined at start and
  cached by nothing, but a token adds state for no gain over Origin plus a custom
  header); reusing `_local()` alone (allows a missing Origin and a loopback Origin
  from another local port).

## R11. Where the link lives in the browser

- Decision: the create response is held only by the dialog built for it, appended to
  `document.body` outside the app root, so polling and re-rendering never touch it.
  Closing the dialog removes the element. Nothing goes to `store`, `localStorage`,
  `sessionStorage`, the URL or the console.
- Copy: `navigator.clipboard` where the page is a secure context (loopback, or
  `https://*.ts.net`); otherwise select the read-only field and use the legacy copy
  command, and tell the owner to press the copy shortcut if that fails too. A plain
  `http://<tailnet IP>` page is not a secure context.

## R12. Which stacks get the buttons

- Decision: every stack card whose `remote` is a remote name (not `this-server`). The
  share functions need the controller's remote registration and stack record; on a
  server's own panel they refuse with `share_failed` or `stack_unknown`, and the panel
  shows that message plus the CLI command to run on the controller.
- Options: none. The panel mints with the defaults (7 days, no use limit, the
  target's default email). Revoke takes one `l-` or `s-` id; `all` stays on the CLI.

## R13. Short link form and lookup (2026-11-11)

- **Decision**: New links are `https://<gate>/s/#<token>`, the token
  `secrets.token_urlsafe(16)` (128 bits, 22 characters). `GET|HEAD /s/` always serves
  the opener page. `POST /s/` reads the token, hashes it, and scans every stored link
  record of the entry (at most 32), comparing each record's hash to the digest with
  `hmac.compare_digest` and never stopping early. Only a match whose id is a short id
  (R14) counts. Then the existing liveness, Origin, enabled and use-claim checks run,
  and every refusal is the one `_answer_not_found` 404.
- **Rationale**: The token stays in the fragment, so request lines, access logs and
  Referer never hold it (FR-003). A constant opener page tells a prober nothing about
  which links exist; the old per-id 404 was safe only because the id was secret-sized.
  Scanning all records makes wrong, unknown, revoked and expired tokens do the same
  work: revoked links have no file, and expired ones are still scanned like any other
  record until pruned. A matched live record then checks its use markers (at most 20
  `stat` calls), which only a holder of a once-valid token can observe.
- **Alternatives considered**: Opening `links/<digest>.json` directly (the old path):
  fast, but an expired-unpruned record costs a read and a parse that an unknown token
  does not. Answering `/s/` with 404 while no link is live: hides an idle gate but
  leaks "some link is live" the moment one is.

## R14. The public id of a short link

- **Decision**: `l-` + the first 12 hex characters of the token's SHA-256. The gate
  refuses a record whose short id does not equal that prefix of its own hash, and
  refuses a mint whose id a stored link already has. The WordPress key id (`kid`) of
  a stack page is the id without `l-`, so 12 or 32 hex.
- **Rationale**: The id must be shown (list, revoke, panel, audit) but must not lead
  back to the token: it is 48 bits of a hash of a 128-bit secret. Deriving it from the
  hash keeps one secret and one random draw. With at most 32 live links per entry a
  prefix collision is about 32 / 2^48; the second mint is refused and minted again.
- **Alternatives considered**: A separate random id (the old model): a second value to
  generate and store for no gain once the id leaves the URL. The full hash: it would
  let anyone with the list check a guessed token offline.

## R15. Old-form links

- **Decision**: `/s/l-<32 hex>` keeps its 2026-11-10 behaviour unchanged: 200 opener
  only for a live id, a 43-character token whose record has that id. Nothing mints the
  old form any more. The id rules accept both lengths. The old path is removed by a
  listed task once 30 days have passed since the short form reached every gate.
- **Rationale**: FR-039. A link lives at most 30 days, so after that window no old
  link can still be live.
- **Alternatives considered**: Rewriting old records to short ids: the old URL holds
  the old id, so the links would break.

## R16. Gate hostname

- **Decision**: Nothing changes in validation: the gate hostname must be a served alias
  of the target on `allowed_host_suffixes`, with no length or randomness rule. The docs
  show `s.<zone>` and say the hostname is not a secret. A test validates a declaration
  with a one-letter label.
- **Rationale**: FR-041. The token is the only secret; a short name is what makes the
  link short.

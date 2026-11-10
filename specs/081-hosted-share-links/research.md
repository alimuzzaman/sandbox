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

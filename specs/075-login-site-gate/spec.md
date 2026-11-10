# Feature Specification: Login service site gate (share a private site by link)

**Feature Branch**: `feat/login-site-gate`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "075 login service site_gate entry mode. Owner (Alim, 2026-11-10): 'beside tailnet we should build something like autologin to share private notes/mockup with someone'. A hosted site (e.g. a static mockup `web` service) is private by default (tailnet-only or not publicly reachable). Its owner mints a share link with one Sandbox command (`sb host login-url --service <entry>`, optional `--uses N`, default 1, capped) and sends it to someone with no tailnet and no account. Opening the link sets a host-only Secure HttpOnly SameSite=Lax session cookie and redirects to a clean URL with no token; after that the gate proxies every request, websocket included, to the declared upstream through a public gate hostname until the session expires. Without a valid session the gate returns 404 for everything. Expired, revoked, replayed and unknown links all return the same 404. Link attempts are rate limited; the link hop sends Referrer-Policy: no-referrer. enabled_until still applies. The gate proxies only to its declared upstream and never becomes an open proxy. The link value is shown once to the operator's terminal only, never in --json output, progress, logs, audit, job output or feedback; canary tests prove it. The operator can list and revoke live links and sessions."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Share a private mockup with one person (Priority: P1)

The owner of a hosted development site (a mockup, notes, a design preview) keeps it
private: it is reachable on the tailnet or on the server only. They run one Sandbox
command and get a link. They send it to a reviewer who has no tailnet access and no
account. The reviewer opens the link and browses the site, live-reload connections
included, for the declared session length.

**Why this priority**: This is the feature. Without it the owner has to make the
site public or onboard the reviewer to the tailnet.

**Independent Test**: Declare a gate entry on a target with a static upstream, mint
a link, open it in a fresh browser: the site renders under the gate hostname and the
address bar holds no token.

**Acceptance Scenarios**:

1. **Given** a deployed target with a `site_gate` entry, **When** the owner mints a
   link and the reviewer opens it, **Then** the reviewer lands on the site at a clean
   URL with no token and every later page, asset and websocket loads.
2. **Given** a reviewer with a valid session, **When** the session length passes,
   **Then** every request to the gate hostname answers 404.
3. **Given** a link already used once (default single use), **When** anyone opens it
   again, **Then** the gate answers 404, the same as for an unknown link.
4. **Given** the owner passes `--uses 3`, **When** three browsers open the link,
   **Then** each gets its own session, and a fourth gets 404.

---

### User Story 2 - Nothing is visible without a session (Priority: P1)

Anyone who finds the gate hostname without a valid link sees nothing: no page, no
hint that a site exists, no way to reach any other service through it.

**Why this priority**: The site must stay private by default. A gate that leaks the
site or proxies elsewhere is worse than no gate.

**Independent Test**: With no session, request assorted paths, methods, a websocket
upgrade, a forged Host header and an absolute-form request line; every answer is 404
and the upstream receives nothing.

**Acceptance Scenarios**:

1. **Given** no session cookie, **When** any request reaches the gate hostname,
   **Then** it answers 404 and nothing reaches the upstream.
2. **Given** no session, **When** a websocket upgrade is attempted, **Then** it answers
   404 and no tunnel opens.
3. **Given** a valid session, **When** the request names another host, an absolute URL,
   or a path starting with `//`, **Then** the gate never connects anywhere except the
   declared upstream, and refuses the malformed forms with 404.
4. **Given** the declared `enabled_until` date has passed, **When** any request arrives,
   sessions included, **Then** it answers 404 and open tunnels close.

---

### User Story 3 - The owner sees and revokes access (Priority: P2)

The owner lists the live links and sessions for an entry (ids, expiry, uses left,
never the link value) and revokes one link, one session, or all of them. A revoked
link stops working; revoking a link also ends the sessions it started.

**Why this priority**: Links get forwarded. The owner needs a way to cut access
before expiry.

**Independent Test**: Mint two links, use one, list (two links, one session), revoke
the used link, confirm its session answers 404 and the other link still works.

**Acceptance Scenarios**:

1. **Given** live links and sessions, **When** the owner lists them, **Then** the output
   shows ids, issue and expiry times, uses left and session counts, and no link value.
2. **Given** a session, **When** the owner revokes it or the link that started it,
   **Then** the next request with that session answers 404 and an open websocket closes
   within 30 seconds.
3. **Given** an unused link, **When** the owner revokes it, **Then** opening it answers
   the same 404 as an unknown link.

---

### User Story 4 - The link value never leaks from the operator's side (Priority: P1)

The link is shown once, on the operator's terminal. It never appears in machine
output, progress lines, logs, audit or state records, job output or feedback.

**Why this priority**: The link is the credential. Earlier login-link work leaked
values through machine output; this mode must not repeat that.

**Independent Test**: Canary tests mint with a known token and assert it is absent
from `--json` output, stderr, saved host state and the remote command lines, while
present on the terminal stream exactly once.

**Acceptance Scenarios**:

1. **Given** `--json`, **When** a link is minted, **Then** the JSON holds the link id,
   expiry and uses, never the link value or its hash, and the link is written to the
   controlling terminal only.
2. **Given** no controlling terminal (a background job, an agent, a pipe), **When** a
   link is requested, **Then** the command refuses before minting with a named code.
3. **Given** a minted link, **When** host state, remote argv and the gate's own logs
   are inspected, **Then** none contains the link value.

### Edge Cases

- The gate hostname is the target's primary hostname: refused at manifest parsing.
- The upstream is the routed primary service, the login service itself, a service of
  another project, or carries a path, query or credentials: refused at parsing.
- The upstream publishes a port beyond loopback: refused before apply.
- A hostname carries a `site_gate` and another entry: refused; one gate per hostname.
- Two browsers race on a single-use link: exactly one gets a session.
- A link is revoked while being consumed: no session survives the revoke.
- A flood of link attempts: answered with 429 after the limit; tokens are 256-bit, so
  the limit guards resources, not guessing.
- `--uses` above the cap, zero or negative: refused before minting.
- Too many live links for one entry: minting refuses until some expire or are revoked.
- Duplicate `Host` headers, a `Host` that is not the gate hostname, or a request line
  in absolute form: 404.
- The service restarts: links and sessions stored on its private in-memory state
  directory survive a process restart and end when the container is recreated.
- A website sends its own cookies: they pass through; the gate's cookie never reaches
  the upstream.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A login-service entry MAY declare `mode: site_gate` with `hostname` (a
  served alias of the target, never its primary), `upstream` (an `http://service:port`
  URL naming a background service of the same project), and `ttl_seconds` (link
  lifetime, 60 to 3600). `requires_email` is false or omitted. `path` (where the link
  lands) defaults to a reserved `/__sandbox/` path.
- **FR-002**: A declaration whose entries are all `site_gate` MUST NOT need a capture
  service; `inbox_gate` and `magic_link` entries keep needing one.
- **FR-003**: A hostname with a `site_gate` entry MUST front no other entry.
- **FR-004**: `sb host login-url --service <entry>` MUST mint a link for a `site_gate`
  entry under the same protected command, `--confirm`, writer lock and refusal codes as
  existing entries. `--uses N` sets the number of sessions the link may start; default
  1, range 1 to 20.
- **FR-005**: The link value MUST be written once, to the operator's controlling
  terminal only. Machine output (`--json`), stderr progress, host state, remote command
  lines, service logs, job output and feedback MUST NOT contain it. With no controlling
  terminal the command MUST refuse before minting.
- **FR-006**: The link MUST carry its token outside the request line, so no access log
  on the path records it.
- **FR-007**: Opening a live link MUST start a session: a host-only, `Secure`,
  `HttpOnly`, `SameSite=Lax` cookie, then a redirect to a URL with no token. Every
  response on the link hop MUST send `Referrer-Policy: no-referrer`.
- **FR-008**: Link previews and unfurlers that fetch the link without running the page
  MUST NOT use it up.
- **FR-009**: Expired, revoked, already-used, malformed and unknown links MUST all get
  the same 404 response.
- **FR-010**: Link attempts MUST be rate limited per entry; over the limit the gate
  answers 429.
- **FR-011**: With a valid session the gate MUST forward every request, websocket
  upgrades included, to the declared upstream and nowhere else. The upstream never
  receives the gate's own cookie.
- **FR-012**: Without a valid session every request to the gate hostname MUST answer
  404 and reach nothing, websocket upgrades included.
- **FR-013**: Only origin-form request targets for the exact gate hostname, with one
  `Host` header, MAY be forwarded. Absolute-form targets, other hosts and duplicate
  `Host` headers MUST answer 404. A path starting with `//` MUST never name a
  destination; at most it is an ordinary path on the declared upstream.
- **FR-014**: A session MUST end at `session_ttl_seconds` after it starts, at revoke,
  or at `enabled_until`, whichever comes first. After `enabled_until` the gate answers
  404 to everything and open tunnels close.
- **FR-015**: The operator MUST be able to list live links and sessions of an entry
  (ids, times, uses left, never values or hashes) and revoke one link, one session or
  all of them. Revoking a link MUST end the sessions it started.
- **FR-016**: An open websocket MUST close within 30 seconds of its session being
  revoked or expiring.
- **FR-017**: The upstream MUST publish no port beyond loopback, checked before apply,
  as the capture service is checked today.
- **FR-018**: `sb host status` MUST list `site_gate` entries with the other entries.
- **FR-019**: Existing `inbox_gate` and `magic_link` behavior and output MUST NOT change.
- **FR-020**: `docs/login-service.md` and both `sandbox-cli` skill copies MUST document
  the mode, with a mockup-site example using placeholder hosts.

### Key Entities

- **Site gate entry**: a declared entry with its gate hostname, upstream, link
  lifetime and link landing path.
- **Share link**: a public id, the hash of its secret, issue and expiry times, and the
  number of sessions it may start. The secret exists only on the operator's terminal
  and in the browser that opens it.
- **Gate session**: a public id, the link it came from, start and expiry times; the
  browser holds its secret in the cookie, the gate holds only a hash.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An owner goes from a deployed private target to a working share link in
  one command, and the reviewer reaches the site in one click with no account.
- **SC-002**: In the test suite, 100% of requests without a valid session answer 404
  and none reach the upstream, across every method, websocket, Host and request-line
  variant tested.
- **SC-003**: Canary tests find the link value in 0 of the machine-readable or stored
  outputs, and on the terminal exactly once.
- **SC-004**: Of any number of concurrent openings of a single-use link, exactly one
  starts a session.
- **SC-005**: A revoked session or link stops granting access on the very next
  request, and open websockets close within 30 seconds.
- **SC-006**: The existing login-service test suites pass unchanged.

## Assumptions

- The gate hostname is publicly resolvable and the target's own edge forwards it to
  the login service, as the existing modes require. Sandbox cannot inspect that edge;
  the docs say so.
- The session cookie carries a `Max-Age` equal to the session's remaining time, so a
  reviewer keeps access across a browser restart until the server-side expiry.
- Rate limit default: 30 link attempts per minute per entry; uses cap 20; at most 32
  live links per entry.
- Links and sessions live on the login service's private in-memory directory; the
  operator's list and revoke run inside that service.
- The existing login-url command has no audit journal beyond the target writer lock;
  this mode adds none and keeps the link out of host state.
- One reviewer per use: Sandbox cannot tell who opened a link, only that a use was spent.

# Feature Specification: Hosted share links (agent-made links, private by default, no indexing, stack page)

**Feature Branch**: `feat/hosted-share-links`

**Created**: 2026-11-10

**Status**: Draft

**Input**: Owner brief, approved 2026-11-10, relayed by the xspeed-dev orchestrator. Owner's words, verbatim: "use random/cryptographic id instead of text for the slug." "make sure all index is off" "also make mockup private by default" "add the sharing system from agent note". On the site gate: "agent should be able to generate and give the user the link, the whole point is to make it easy for agent and user". "share the stack page from sandbox web that has autologin links". The brief builds on spec 075's `site_gate`: today only the owner can mint a link, from a terminal (`terminal_required`); links cover the whole site; every hosted hostname gets public DNS, and the project's own edge must hide the primary.

## Clarifications

### Session 2026-11-10

No questions were put to the owner: the brief fixes every high-impact choice. These
answers are read from it and recorded so planning can cite them.

- Q: Default use limit for an agent-made link? → A: None (optional limit, 1 to 20), as
  in Agent Notes. `host login-url` keeps its 075 default of one use.
- Q: Does the no-index default apply to existing targets on their next apply? → A: Yes.
  Only `indexing: public` turns it off ("make sure all index is off").
- Q: Is the token in the link's path or its fragment? → A: The fragment. The path holds
  only `/s/<random id>`, so front-door access logs never see a token (FR-003 forbids
  logging it).
- Q: Which gate serves a stack page? → A: The stack target's own gate. A stack without
  a `share` declaration is refused with guidance.
- Q: How does the owner view a private site? → A: Through the target's managed loopback
  port, forwarded by `tailscale serve` or SSH. `sb host tailnet-name` is documented as
  a later option once its hardening merges.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An agent makes a share link and hands it over (Priority: P1)

A person asks their coding agent to share a private development site (a mockup, a
preview) with a reviewer. The agent runs one Sandbox command, or calls one MCP tool,
and gets back a link it can paste into the chat. The reviewer opens it with no
tailnet and no account and browses the site. The owner allowed this once, in the
target's hosting manifest.

**Why this priority**: The owner's main ask: "the whole point is to make it easy
for agent and user". Without it, every link needs the owner at a terminal.

**Independent Test**: On a target whose manifest opts in, run the create command with
JSON output: it returns a link. Open the link in a fresh browser: the site renders and
the address bar holds no token.

**Acceptance Scenarios**:

1. **Given** a deployed target that opts in to agent-made links, **When** an agent runs
   the create command with JSON output, **Then** the result carries the link exactly
   once, with its id, expiry and use limit.
2. **Given** a target that does not opt in, **When** an agent runs the create command
   without a terminal, or calls the MCP tool, **Then** it is refused and nothing is
   minted. The owner-at-a-terminal flow from 075 still works.
3. **Given** a minted link, **When** anyone reads logs, the audit trail, Sandbox state,
   job output or the server's records, **Then** the token appears in none of them.
4. **Given** a create, list or revoke, **When** it finishes, **Then** an audit record
   names the controller, the owner label, the target, the path scope, the expiry and
   the use limit, and holds no token.
5. **Given** the create command runs inside a durable job, **When** it would print a
   link, **Then** it refuses, because job output is stored.

---

### User Story 2 - Links follow the Agent Notes sharing model (Priority: P1)

A link lasts 7 days by default and at most 30, with an optional use limit. It can be
scoped to one page or one folder, such as a single mockup, rather than the whole site.
The token is swapped for a cookie on first open and the address bar is cleaned. The
owner can revoke one link or all of them, and revoking ends the sessions the link
started.

**Why this priority**: The owner asked for this model by name. A share without path
scope exposes everything on the site.

**Independent Test**: Mint a link scoped to one folder. Open it: pages inside the
folder render, and a page outside it answers 404. Revoke it: the next request answers
404.

**Acceptance Scenarios**:

1. **Given** no lifetime is requested, **When** a link is minted, **Then** it expires in
   7 days. A request for more than 30 days is refused. A lifetime past the gate's
   `enabled_until` ends at `enabled_until`, and the result says so.
2. **Given** a link scoped to a folder, **When** its session requests a path outside
   that folder, including any encoded or dot-segment path that would resolve outside
   it, **Then** the answer is 404.
3. **Given** a link scoped to one page, **When** its session requests any other path,
   **Then** the answer is 404.
4. **Given** a browser opens a link, **When** opening completes, **Then** the browser
   holds an HttpOnly, Secure, SameSite cookie, lands on the shared path, and no token
   remains in the address bar or history.
5. **Given** a link with a use limit of N, **When** N browsers have opened it, **Then**
   a further browser gets 404. With no limit, any number of browsers may open it until
   it expires, subject to a per-gate session cap.
6. **Given** a revoke of one link, or of all links, **When** a session the link started
   makes its next request, **Then** it gets 404. An open websocket ends within 30
   seconds.
7. **Given** many link-opening attempts in a short time, **When** the limit is
   exceeded, **Then** further attempts get 429.

---

### User Story 3 - Random ids and private by default (Priority: P1)

Every id and token comes from a cryptographic random source and reads as noise. The
URL a link carries names no page, folder or project. A manifest gets the gate with
one short declaration, with no hand-written edge config. With that declaration,
the primary hostname and every unknown path answer 404, and only valid sessions reach
the site.

**Why this priority**: The owner asked for both directly ("random/cryptographic id
instead of text for the slug", "make mockup private by default").

**Independent Test**: Declare the short form on a static site and apply it. The primary
hostname answers 404, the share hostname answers 404 without a session, and a minted
link opens the site. The link URL holds only the share hostname, a fixed prefix and
random characters.

**Acceptance Scenarios**:

1. **Given** a minted link, **When** its URL is inspected, **Then** it holds the gate
   hostname, a fixed short prefix, a random id of at least 128 bits, and the token. It
   holds no path, folder, file or project name.
2. **Given** a target declared private, **When** anyone requests its primary hostname
   or any other served name except the gate hostname, **Then** the front door answers
   404 and the request never reaches the application.
3. **Given** a target declared private, **When** the owner wants to see the site
   themselves, **Then** the documented tailnet or loopback view works and needs no
   share link.
4. **Given** a target declared private whose application service publishes a port
   beyond loopback, **When** it is applied, **Then** apply refuses, because that port
   would bypass the gate.

---

### User Story 4 - Nothing hosted is indexed (Priority: P1)

Every response of every hosted target tells search engines not to index, follow or
archive it, and `/robots.txt` denies crawling. Only an explicit public flag on the
target turns this off.

**Why this priority**: "make sure all index is off".

**Independent Test**: Render every kind of hosted route, on both front doors: each
response path carries the no-index header. A test fails if any route can answer
without it.

**Acceptance Scenarios**:

1. **Given** any hosted target without the public flag, **When** any hostname of it
   answers (served, redirected, gated, refused, an auth challenge, a `robots.txt`),
   **Then** the response carries `X-Robots-Tag: noindex, nofollow, noarchive`.
2. **Given** the same target, **When** `/robots.txt` is requested, **Then** it answers
   `Disallow: /`.
3. **Given** the gate serves its own page (link opening, the stack page, a refusal),
   **When** it answers, **Then** it carries the header, and its HTML carries a robots
   `noindex, nofollow` meta tag.
4. **Given** a target with the public flag, **When** it answers, **Then** no no-index
   header is added, and `robots.txt` follows the existing `robots` setting.

---

### User Story 5 - Share a stack page with live sign-in links (Priority: P2)

The owner wants a tester without tailnet access to work with a whole stack: the hub,
admin, renderer, mail and WordPress servers. They, an agent, or the Sandbox web panel
make one share link to the stack's page. The page lists each server with its health
and its sign-in links: the WordPress admin autologin, and the Hub QA sign-in if the
stack declares one. Each sign-in link is minted when the page is viewed, works once,
and lives a few minutes.

**Why this priority**: "share the stack page from sandbox web that has autologin
links". It depends on Stories 1 to 3.

**Independent Test**: On a stack whose target declares the share gate, mint a stack
link. Open it in a fresh browser: the page lists the stack's servers with health and
sign-in links. Reload: the sign-in links change. Use the WordPress link: it signs in
once, and a second use is refused.

**Acceptance Scenarios**:

1. **Given** a recorded stack whose target declares the share gate, **When** a stack
   link is minted from the CLI or MCP, **Then** the result carries the link once.
2. **Given** a stack page session, **When** the page is viewed, **Then** it lists the
   stack's hostnames with health, and fresh single-use sign-in links that expire within
   10 minutes.
3. **Given** a stack page session, **When** it requests anything other than the page,
   **Then** it gets 404: a stack session never reaches the application.
4. **Given** the page is viewed, **When** logs and records are read, **Then** no sign-in
   token appears in any of them.
5. **Given** the stack link is revoked or expires, **When** the WordPress key it made is
   used, **Then** sign-in is refused.
6. **Given** a stack whose target does not declare the share gate, **When** a stack link
   is requested, **Then** it is refused with guidance on what to declare.
7. **Given** the Sandbox web panel, **When** it needs a "Share this stack" action,
   **Then** a documented command with JSON output gives it the link, without the
   panel code being part of this change.

### Edge Cases

- A link lifetime longer than the gate's remaining `enabled_until`: it is shortened to
  `enabled_until`, and the result reports the shortened expiry.
- A browser that opens several links on the same gate keeps every session it opened,
  up to a small cap. The oldest is dropped first.
- Encoded slashes, backslashes, encoded dots, dot segments and doubled slashes in a
  scoped request: refused with 404 before scope matching, so the upstream can never
  resolve a path outside the scope.
- Revoke while an open request is being forwarded: that request finishes, and the
  next one is refused. A websocket is rechecked every 30 seconds.
- A gate restarted or recreated: links and sessions on its private state survive a
  restart and end on recreate (as in 075). Agents mint again after an apply that
  recreates it.
- A stack's WordPress target is private: its admin autologin link would land on a
  hostname that answers 404, so the page leaves that link out and says why.
- `enabled_until` passes: every gate route answers 404. Minting is refused.
- A manifest sets `robots: allow` without the public flag: refused, because it
  contradicts the no-index default.
- An MCP client: it has no terminal, so a target without the agent opt-in always
  refuses.

## Requirements *(mandatory)*

### Functional Requirements

**Agent-made links**

- **FR-001**: Sandbox MUST offer `create`, `list` and `revoke` (one link id, or `all`)
  for share links. A target is chosen by remote plus one of: project directory with
  environment, `--target <project>/<environment>`, or `--name <stack slug>`. A stack
  page link is chosen with `--stack <slug>`.
- **FR-002**: Matching MCP tools MUST exist for create, list and revoke, and return the
  same result as the CLI's JSON output.
- **FR-003**: `create` MUST return the link exactly once, in its JSON output or MCP
  result. The token MUST NOT be written to logs, the audit trail, Sandbox state files,
  server records or job output. Only a SHA-256 hash of it is stored.
- **FR-004**: A target MUST opt in to agent-made links in its hosting manifest.
  Without the opt-in, `create` MUST keep the 075 rule: it shows the link only on the
  controlling terminal, and refuses `terminal_required` without one. MCP tools MUST
  refuse with `agent_mint_not_enabled`.
- **FR-005**: `create` MUST refuse inside a durable Sandbox job, whose output is stored.
- **FR-006**: Every create, list and revoke MUST be audited, on the controller and on
  the server, with: time, operation, controller, owner label, remote, target, link id,
  path scope, expiry and use limit (where they apply), and outcome. A record MUST NOT
  hold a token or a token hash.

**Link model**

- **FR-007**: A link's lifetime MUST default to 7 days, MUST be at most 30 days and at
  least 1 minute, and MUST end no later than the gate's `enabled_until`.
- **FR-008**: A link MAY carry a use limit, from 1 to 20 browsers. Without one it
  opens in any number of browsers until it expires, bounded by the gate's session cap.
- **FR-009**: A link MUST carry a path scope: the whole site (`/`), one folder (a path
  ending in `/`), or one page (an exact path). A session MUST be refused with 404 for
  any request outside its scope. Requests whose path holds encoded slashes, encoded
  backslashes, encoded dots, dot segments, doubled slashes, a backslash or control
  characters MUST be refused with 404 before scope matching.
- **FR-010**: Opening a link MUST swap the token for an HttpOnly, Secure,
  SameSite=Lax, host-only cookie, and redirect to the scoped path, so no token
  remains in the address bar. A GET on the link MUST NOT spend a use, so link
  previews that do not run scripts never open it.
- **FR-011**: Revoking a link, or all links, MUST end its sessions at their next
  request, and any websocket within 30 seconds.
- **FR-012**: Link opening MUST be rate-limited, with 429 past the limit.
- **FR-013**: Expired, spent, revoked, malformed and unknown links MUST all get the
  same 404.

**Random ids**

- **FR-014**: Link ids, session ids and tokens MUST come from a cryptographic random
  source, with at least 128 bits for ids and 256 for tokens, URL-safe.
- **FR-015**: A link URL MUST be `https://<gate hostname>/s/<random id>#<token>`. It
  MUST NOT carry the shared path, a slug or any readable name. The scope stays on the
  server.

**Private by default**

- **FR-016**: A manifest MUST be able to declare the gate with one short `share`
  block: the gate hostname, `private`, the agent opt-in, and the 075 safety fields,
  with no hand-written edge config.
- **FR-017**: Sandbox MUST render the gate routing itself: on both front doors, every
  gate hostname goes to the gate. With `private`, every other served hostname answers
  404 at the front door.
- **FR-018**: With `private`, apply MUST refuse when the application service
  publishes a port beyond loopback.
- **FR-019**: The documentation MUST say how the owner views a private site through
  the tailnet or a loopback forward, without depending on the unmerged
  `sb host tailnet-name` hardening.

**No indexing**

- **FR-020**: Both front doors MUST add `X-Robots-Tag: noindex, nofollow, noarchive`
  to every response of every hosted route, replacing any value from the application,
  unless the target sets the explicit public flag.
- **FR-021**: Without the public flag, `/robots.txt` MUST answer `Disallow: /`. A
  manifest that sets `robots: allow` without the public flag MUST be refused.
- **FR-022**: Every gate response MUST carry the same header. Every HTML page the gate
  serves MUST carry `<meta name="robots" content="noindex, nofollow">`.
- **FR-023**: A test MUST fail if any rendered hosted route, on either front door,
  can answer without the header.

**Stack page**

- **FR-024**: A share link MAY point to a stack page: a read-only page for one
  recorded stack, served by that stack target's gate. It lists the stack's hostnames
  with role (the first DNS label), health and sign-in links. A stack session reaches
  only the page.
- **FR-025**: Each page view MUST mint fresh sign-in links: the WordPress admin
  autologin for a stack target that declares `autologin` and is not private, and a
  link for each login-service entry the stack's `share` block lists (the Hub QA
  sign-in). Each is single-use and lives at most 10 minutes. No sign-in token may
  reach a log, record or URL request line.
- **FR-026**: The WordPress autologin a stack page mints MUST be verified by a key
  that only the stack link's lifetime allows. Revoking or expiring the stack link
  MUST stop new sign-ins.
- **FR-027**: The Sandbox panel MUST be able to make a stack link through the same
  command with JSON output. This change MUST NOT edit the panel files.

**Development-only**

- **FR-028**: Agent-made links and stack pages create public login bypasses. They MUST
  be limited to development targets through the 075 guards: `allowed_host_suffixes`
  covers every hostname, and `enabled_until` is required, unexpired, and within 30
  days.

### Key Entities

- **Share declaration**: the per-environment manifest block. Gate hostname, private
  flag, agent opt-in, the login-service safety fields, and optional stack-page sign-in
  entries.
- **Share link**: id, token hash, kind (site or stack), path scope, issue and expiry
  times, optional use limit. It lives on the gate's private state.
- **Share session**: id, the link it came from, creation and expiry times. Held by
  hash; one browser cookie can carry several.
- **Stack page record**: for a stack link, the hostnames, health probe targets, and
  the sign-in sources (WordPress key, login-service entries). Held on the gate's
  private state.
- **Audit record**: one line per create, list or revoke, on the controller and on the
  server.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An agent turns "share this mockup" into a working link with one command
  or one tool call, and needs no human step after the one-time manifest opt-in.
- **SC-002**: Canary tests find the token in 0 places other than the one returned
  result: logs, audit, state files, server records, job output.
- **SC-003**: 100% of rendered hosted routes on both front doors carry the no-index
  header, and the test that checks it fails when any route lacks it.
- **SC-004**: A scoped session gets 404 on 100% of out-of-scope paths in the
  traversal test set.
- **SC-005**: After a revoke, the next request from every session of that link answers
  404, and websockets close within 30 seconds.
- **SC-006**: A tester with no tailnet signs in to a stack's WordPress admin from the
  stack page in two clicks, and the same sign-in link fails on a second use.

## Assumptions

- The gate is the 075 `sandbox-login` service. The share links extend its `site_gate`
  link store rather than replace it. 075 `site_gate` entries move to the new link URL
  form, and `host login-url --service <site entry>` keeps working.
- The link lifetime (7 to 30 days) is separate from the session lifetime: a session
  ends at the earliest of the link's expiry, the gate's session length and
  `enabled_until`. A browser whose session ended can open an unspent link again.
- The rate limit stays per gate entry (30 attempts in 60 seconds), as in 075. A
  per-client limit would key on a header the client controls.
- The "owner label" in audit is the remote registration's owner label when present,
  else the controller's OS user. The "controller" is the controller's hostname.
- WordPress autologin on the stack page covers the stack target's own `autologin`
  declaration. WordPress sites linked to a stack by `sb deploy --stack` are listed with
  health only, because no Sandbox path can mint a one-time admin link for them today.
- Health on the stack page is probed by the gate: through the project's own network
  for the stack target's hostnames, and over public HTTPS for linked sites on allowed
  hostnames.
- No stack declares a Hub QA sign-in today. Support is generic: any login-service
  entry the stack's `share` block lists.
- Gate routing moves from the project's edge to the front door for every gate
  hostname. A 075 target that forwards its gate hostname in its own edge keeps
  working, because the front door now reaches the same service directly.
- The panel button, and the mockups target's manifest change, are written up for the
  orchestrator and not applied here.

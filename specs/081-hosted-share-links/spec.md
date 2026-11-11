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

### Session 2026-11-11 (panel follow-up)

The orchestrator relayed the panel button as a follow-up to this feature: "Show the
`link` once, with a copy button, `expires_at` and `uses`. Never store, log or cache it,
and keep it out of panel API responses that get polled or persisted." "The panel is
tailnet-only and auto-trusted, so the button must be a deliberate POST with a CSRF
check or same-origin check." These answers are read from it.

- Q: Which link options does the panel offer? → A: None. The panel mints with the
  defaults (7 days, no use limit, the target's default email); other options stay on
  the CLI and MCP.
- Q: May the panel revoke? → A: Yes, one link or session at a time from the live-links
  list. `all` stays on the CLI.
- Q: What does the panel do where the stack has no controller record (a server's own
  panel)? → A: It shows the refusal and the CLI command to run on the controller.

### Session 2026-11-11 (short links follow-up)

The owner approved a shorter link: "Today a link looks like
`https://<gate>/s/l-<32 hex id>#<43-char 256-bit token>` (112 chars for the mockups
gate). New format: `https://<gate>/s/#<token>`, with ONE 128-bit token (22 chars
base64url) after the `#`, and no id in the URL." The owner also said yes to a short
gate hostname. These answers are read from the approved brief.

- Q: Where does the token go? → A: After the `#`, as before, so it never reaches the
  server's request line, access logs or a Referer.
- Q: What id do list, revoke and the panel show? → A: `l-` and the first 12 hex
  characters of the token's SHA-256. The id must not be enough to rebuild the token.
- Q: What happens to links minted in the old form? → A: They keep working until they
  expire (30 days at most). The old path is marked for removal and not removed now.
- Q: Is the gate hostname a secret? → A: No. A short name such as `s.<zone>` is fine;
  the token is the only secret.
- Q: Does `/s/` answer while no link is live? → A: Yes, with the same opener page every
  time, so the page says nothing about which links exist. Every refusal happens on the
  POST, with one 404.

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
   **Then** a documented command with JSON output gives it the link.

### User Story 6 - Share a stack from the panel (Priority: P2)

The owner looks at a stack on the panel's Hosting page and wants to hand a tester its
stack page. They press "Share this stack" on the stack's card, see the link once with
a copy button, its expiry and its use limit, and paste it into a chat. Later they open
the stack's live links and revoke one.

**Why this priority**: "share the stack page from sandbox web that has autologin
links". It depends on Story 5.

**Independent Test**: On a controller panel with a recorded stack whose target sets
`share.agent_mint: true`, press "Share this stack": the link appears once, copies, and
opens the stack page. Reload the Hosting page: the link is gone, and no panel response
or browser storage holds it. Revoke it from the live-links list: the link answers 404.

**Acceptance Scenarios**:

1. **Given** a stack whose target allows agent-made links, **When** the owner presses
   "Share this stack" and confirms, **Then** the panel shows the link once, with a copy
   button, its expiry and its use limit.
2. **Given** the link was shown, **When** the panel polls, reloads, or the dialog is
   closed, **Then** the link appears in no panel response other than the one that
   minted it, in no browser storage, and in no panel log.
3. **Given** a target without `share.agent_mint: true`, **When** the button is pressed,
   **Then** the panel shows the refusal and explains that the target must set
   `share.agent_mint: true` and be applied again.
4. **Given** a stack whose manifest has no `share:` block, **When** the button is
   pressed, **Then** the panel explains that the stack template needs a `share:` block
   and a new `sb host stack up`.
5. **Given** a request that does not come from the panel page itself (no Origin, a
   foreign Origin, a form post, a missing panel header), **When** it reaches the share
   endpoint, **Then** it is refused before any link is minted, listed or revoked.
6. **Given** a stack with live links, **When** the owner lists them and revokes one,
   **Then** the list shows each link's id, expiry, uses and sessions, never a token,
   and the revoked link stops working.

### User Story 7 - Short share links (Priority: P2)

An agent or the owner makes a share link and pastes it into a chat. The link is short
enough to read and type: the gate hostname, `/s/`, and one token after the `#`. A link
made last week in the old, longer form still opens until it expires.

**Why this priority**: The owner asked for it after using the links: the old form is
112 characters for the mockups gate. It changes the link's shape, not who may make
one, so it depends on Stories 1, 2 and 6.

**Independent Test**: Mint a link: it reads `https://<gate>/s/#<22 characters>`. Open
it in a fresh browser: the site opens and the address bar holds no token. Open an old
`/s/l-<id>#<token>` link minted before the change: it still opens. Post a wrong,
expired, revoked, spent or malformed token: each gets the same 404.

**Acceptance Scenarios**:

1. **Given** a target that allows share links, **When** a link is made from the CLI,
   MCP or the panel, **Then** it reads `https://<gate hostname>/s/#<token>`, with a
   22-character token and nothing else after the hostname.
2. **Given** a new link, **When** it is opened, **Then** the token is swapped for the
   share cookie, the address bar is cleared, and the browser lands on the link's scope
   (or the stack page).
3. **Given** a link minted in the old form before the change, **When** it is opened
   before its expiry, **Then** it works as it did.
4. **Given** a token that is wrong, unknown, expired, revoked, spent or malformed,
   **When** it is posted to `/s/`, **Then** the answer is the same 404 with the same
   headers, and the gate takes the same kind of work to give it.
5. **Given** a new link, **When** the owner lists or revokes links, **Then** each link
   shows as `l-` and 12 hex characters, revoke by that id and `revoke all` work, and
   the id cannot be turned back into the token.
6. **Given** a new link, **When** it is made, opened or revoked, **Then** its token
   appears in no log, audit line, feedback record or progress output, and the panel
   shows it once and never in the page markup or browser storage.

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
- The panel always mints as an agent: it never falls back to the owner's terminal,
  because the panel process has none the owner is watching.
- The panel runs on a machine without the stack's controller record (a server's own
  panel): the refusal names the missing remote, and the panel shows the CLI command
  to run on the controller instead.
- Two presses in a row mint two links. Each is shown once; both stay revocable.
- Two live links whose hashes start with the same 12 hex characters: the second mint
  is refused and the caller mints again. With at most 128 kept links per entry this
  is about one in two trillion.
- A visitor opens `/s/` with no fragment, or a fragment of the wrong length: the page
  says the link is incomplete and posts nothing.
- An old-form link and a new-form link open in the same browser: both sessions live in
  the one share cookie, as with two old-form links.

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

- **FR-014**: Session ids and tokens MUST come from a cryptographic random source,
  URL-safe. A share link token MUST carry 128 bits (FR-035); a session id at least 128.
  (The 2026-11-10 floor of 256 bits for a share token and 128 bits for a random link
  id is superseded by FR-035 to FR-041.)
- **FR-015**: A link URL MUST NOT carry the shared path, a slug or any readable name.
  The scope stays on the server. Its form is set by FR-035.

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
  command with JSON output. (The 2026-11-10 clause that kept the panel files out of
  this change is superseded by FR-029 to FR-034.)

**Panel button (follow-up, 2026-11-11)**

- **FR-029**: Each stack card on the panel's Hosting page MUST offer "Share this
  stack". It mints a stack link for that stack and remote, as an agent caller, with
  the default options, after one confirmation.
- **FR-030**: The panel MUST show a minted link once, with a copy action, its expiry
  and its use limit. The link MUST NOT be written to any polled or persisted panel
  response, panel log, browser storage or URL. The response that carries it MUST
  forbid caching.
- **FR-031**: The panel's share requests MUST be refused, before any effect, unless
  they pass the panel's access gate and also carry an Origin equal to the request's
  Host, a JSON body, and a panel-only request header. Remote and stack names MUST be
  validated against their naming rules, and a revoke target MUST be one link or
  session id.
- **FR-032**: A refusal MUST show its code and message. `agent_mint_not_enabled` and
  `stack_share_not_declared` MUST each come with a plain explanation of what to change.
  An unexpected error MUST show a generic message, never its detail.
- **FR-033**: The panel MUST list a stack's live links and sessions (id, kind, expiry,
  uses, used) and revoke one link or session at a time after a confirmation.
- **FR-034**: Every panel create, list and revoke MUST be audited exactly as the CLI's
  are, by calling the same share operations in process.

**Short links (follow-up, 2026-11-11)**

- **FR-035**: A newly made link MUST be `https://<gate hostname>/s/#<token>`, where the
  token is 128 random bits written as 22 URL-safe base64 characters, and nothing
  follows the hostname but `/s/#` and the token. The CLI, MCP, the panel, `host
  login-url --service <site entry>`, the documentation and the Sandbox CLI skill MUST
  show this form.
- **FR-036**: The gate MUST store only the token's SHA-256, find a link by that hash,
  and compare hashes in constant time.
- **FR-037**: A new link's public id MUST be `l-` and the first 12 hex characters of
  the token's SHA-256. List, revoke by id, `revoke all`, sessions, audit and the stack
  page's WordPress key MUST use that id. Minting MUST refuse an id that a live link of
  the same entry already has.
- **FR-038**: `GET` or `HEAD` on `/s/` MUST answer the same opener page whatever links
  exist. A `POST` to `/s/` MUST give one 404, with the same body and headers, to a
  token that is wrong, unknown, expired, revoked, spent or malformed, and MUST do the
  same lookup work for each before answering. The per-entry rate limit (FR-012) MUST
  cover these posts.
- **FR-039**: A link minted in the old form (`/s/l-<32 hex>#<43 characters>`) MUST keep
  working until it expires, revoked by its old id. The old form MUST NOT be minted
  any more, and its removal MUST be a listed follow-up task.
- **FR-040**: The token MUST stay out of logs, the audit trail, feedback records,
  progress output, panel markup and browser storage, as FR-003 and FR-030 require for
  the old form.
- **FR-041**: Nothing in Sandbox MAY require a gate hostname that looks random. The
  documentation MUST show a short gate hostname such as `s.<zone>` and say that the
  hostname is not a secret.

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
  times, optional use limit. It lives on the gate's private state. A new link's id is
  `l-` and the first 12 hex characters of its token hash; an old-form link keeps its
  random 32-hex id until it expires.
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
- **SC-007**: The owner makes a stack link from the panel in two clicks (share,
  confirm), and canary tests find the panel-minted token in 0 places other than the
  one minting response.
- **SC-008**: 100% of share requests without the panel's Origin, JSON body or panel
  header are refused before any link is minted, listed or revoked.
- **SC-009**: A newly made link for a gate hostname `s.<zone>` is the hostname plus 34
  characters (`https://`, `/s/#`, a 22-character token), down from 112 characters for
  the mockups gate.
- **SC-010**: In the bad-token test set (wrong, unknown, expired, revoked, spent,
  malformed), 100% of answers are byte-identical 404s.
- **SC-011**: 100% of links minted in the old form before the change still open until
  their expiry.

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
- The mockups target's manifest change is written up for the orchestrator and not
  applied here. The panel button was added as a follow-up (Story 6, 2026-11-11).
- A 128-bit token makes guessing hopeless at the gate's rate limit (30 posts per minute
  per entry), so the shorter token keeps the margin the 256-bit token gave in practice.
- The 12-hex id is 48 bits of a hash of a 128-bit secret: shown in lists and audit, it
  narrows nothing an attacker could search.
- The mockups site's manifest move to a short gate hostname is the orchestrator's,
  after the merge; this change only documents the short name.
- The panel runs on a tailnet or loopback address with no login. The share endpoint's
  Origin, JSON and header checks are what stop a page in the owner's browser from
  driving it.

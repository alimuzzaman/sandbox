# Feature Specification: Stack page access (per-stack panel page, shared services, one-link teammate access)

**Feature Branch**: `feat/stack-page-access`

**Created**: 2026-11-11

**Status**: Source integrated; independent review and runtime acceptance pending

**Input**: The ready PRD `specs/088-stack-page-access/prd.md` (Sol readiness review PASS,
2026-11-11), which is the authoritative feature description.

## Clarifications

### Session 2026-11-13

- Q: Who may create or revoke through the Tailnet panel? → A: Anyone admitted by the existing panel peer, Host, Origin, JSON and header checks, for stacks whose current applied `share.agent_mint` is enabled. No separate named-operator login is required; audit must not imply named-person attribution.
- Q: Does this policy approve the paused unified Tailnet sharing work? → A: No. This feature uses the fixed stack create/list/revoke and sign-in paths; paused 090 authority remains outside scope.
- Q: May recovery retry an operation whose original execution is unknown? → A: No. Retain its original identities and report pending cleanup until original completion is proven or its unused capabilities expire. A missing grant alone does not prove the original writer has stopped.

### Session 2026-11-11

- Q: How are route and inbox access declared? → A: route_sign_in names a declared stack_sign_in entry or `$wordpress`; inbox_sign_in explicitly names an inbox_gate. Unmapped means author-declared anonymous, subject to known restrictions and adequate metadata.
- Q: When may stack-page actions issue credentials? → A: Fresh sign-ins only on authenticated same-origin POST per click, never GET/HEAD; source grant/session/window bounds and eight pending per entry apply.
- Q: What happens on child gate loss or interrupted mint? → A: Probe availability without consumption, report unavailable, reconcile original durable identities and never remint under an uncertain attempt.

## User Scenarios & Testing *(mandatory)*

Stories 1 to 4 are independent of the short share links branch (`feat/short-share-links`)
and are built first. Stories 5 and 6 change the share gate, so they are built only after
that branch is in `latest`, on its short link format.

### User Story 1 - A tailnet user opens a stack's page (Priority: P1)

The owner is on the panel's Hosting page and wants everything about stack `12` in one
place. They open the stack and get a page of its own: every route with role, state,
health and the revision it was built from, the inbox, and the stack's actions. The
page has an address they can bookmark, reload and send to another tailnet user.

**Why this priority**: it is the page every other story adds to ("every hosted stack
gets its own detail page listing everything that stack is made of").

**Independent Test**: With a recorded, applied stack, open its page from the card and
by its address in a fresh tab. Every recorded route is listed with role, state, health
and revision; the revision matches `sb host stack list --json`; the inbox link opens the
inbox when a tailnet name for its port is recorded.

**Acceptance Scenarios**:

1. **Given** a recorded stack, **When** its card's "Open" is used or its page address is
   loaded directly, **Then** the stack page shows every route the stack records, each
   with hostname, role (the first DNS label), state and health.
2. **Given** a stack whose last delivery was recorded, **When** its page is shown,
   **Then** each own route shows the delivered commit that `sb host stack list` reports,
   and a linked site shows its own commit.
3. **Given** a stack delivered without runtime revision evidence, or never delivered,
   **When** its page is shown, **Then** the revision reads "unverified" or "not
   delivered" rather than a commit shown as proven.
4. **Given** a tailnet name recorded on the stack's remote whose upstream is the stack's
   inbox loopback port, **When** the page is shown, **Then** the inbox link opens that
   name.
5. **Given** no such tailnet name, **When** the page is shown, **Then** it says no inbox
   address is recorded and shows the `sb host tailnet-name add` command for that port,
   with no broken link.
6. **Given** a page address that names no recorded stack, **When** it is loaded,
   **Then** the page says so and links back to the Hosting page.

---

### User Story 2 - One-click WordPress admin from the panel (Priority: P1)

A tailnet user on a WordPress stack's page wants to be in wp-admin now. They press
"Sign in to WordPress admin"; a new tab opens signed in. Pressing again later works the
same way. Nothing they did leaves a usable link behind.

**Why this priority**: the owner named it ("a one-click WordPress admin auto-login,
mint a fresh one-time link per click").

**Independent Test**: On a stack whose template declares `autologin`, press the action
twice. Both links sign in once each, in either order, and are refused on reuse and
after their lifetime. No later panel response, log, browser storage or request line
holds either token.

**Acceptance Scenarios**:

1. **Given** a stack that declares `autologin`, **When** the sign-in action is pressed,
   **Then** a fresh one-time link is minted for that stack through the host autologin
   mu-plugin path and opened in a new tab, signed in to WordPress admin.
2. **Given** two presses, **When** both links are used in either order, **Then** each
   signs in once; neither press invalidates the other.
3. **Given** a used or expired link, **When** it is opened again, **Then** sign-in is
   refused with a message to press the action again.
4. **Given** the CLI, **When** `sb host login-url --remote R --name <stack> --confirm`
   runs, **Then** it mints for that stack's rendered manifest, never the manifest in the
   current directory.
5. **Given** a minted link, **When** it is inspected, **Then** its token is in the URL
   fragment, and the WordPress page posts it, so no request line carries it.
6. **Given** a request to the panel's sign-in route without the panel's Origin, a JSON
   body and the panel header, or with a foreign Origin, **When** it arrives, **Then** it
   is refused before anything is minted.
7. **Given** a stack without `autologin`, a private target, or a server's own panel with
   no controller record, **When** the page is shown, **Then** there is no sign-in
   action, and the page says why and what to declare or where to run the command.

---

### User Story 3 - A stack declares the stacks it uses (Priority: P1)

The template of a WordPress-only stack declares that it uses the shared `dev` Hub.
`stack up` checks the declaration and records it. The WordPress stack's page lists
`hub-dev`, `admin-dev`, `renderer-dev` and `dev`'s inbox in a "Shared" section, with the
same information and actions as on `dev`'s own page.

**Why this priority**: the owner named it ("must ALSO list hub-dev, admin-dev,
renderer-dev and mail-dev, marked shared, and grant access to them").

**Independent Test**: Bring up a stack whose template uses a recorded stack `dev`. The
plan and the record show the use. The stack's page lists `dev`'s routes and inbox in a
Shared section. Templates naming an unknown stack, itself, a repeat, or too many are
refused before any effect.

**Acceptance Scenarios**:

1. **Given** a template whose `stack` block uses `dev`, and stack `dev` recorded on the
   same remote, **When** `stack up` runs (plan or confirm), **Then** it accepts the use
   and, on confirm, records it with the stack; `stack list --json` shows it.
2. **Given** a use that names no stack recorded on that remote, the stack itself, a name
   twice, or more than 4 stacks, **When** `stack up` runs, **Then** it refuses before
   any write with a code naming the problem.
3. **Given** a recorded use, **When** the stack's panel page is shown, **Then** a Shared
   section per used stack lists that stack's routes (role, state, health, revision), its
   inbox link and its sign-in actions, marked "shared".
4. **Given** a used stack that has since gone down, **When** the page is shown, **Then**
   its Shared section says it is missing, and the rest of the page is unaffected.
5. **Given** `a` uses `b` and `b` uses `a`, **When** either page is shown, **Then** each
   lists only its direct uses.
6. **Given** a used stack, **When** `stack down` runs for it, **Then** the result names
   the stacks that use it; it does not refuse.

---

### User Story 4 - Each stack gets its own gate and sign-in hostnames (Priority: P2)

A template author wants every stack made from one template to have its own share gate
hostname and its own sign-in entry hostnames and origins, so two stacks never claim
the same hostname.

**Why this priority**: without it a stack template cannot carry a share gate at all,
which Stories 5 and 6 need. It does not change the gate.

**Independent Test**: Render one template for two stack names. Their gate hostnames,
sign-in hostnames and origins differ by the stack name, both pass validation and the
hostname ownership check, and each sign-in lands on its own stack's hosts.

**Acceptance Scenarios**:

1. **Given** a template whose share gate hostname, login-service entry hostnames and
   magic-link origins and link patterns use the stack placeholders, **When** two stacks
   are rendered from it, **Then** each gets its own values and both validate.
2. **Given** a placeholder in any other share or login-service field, **When** the
   template is read, **Then** it is refused as `stack_template_invalid`.
3. **Given** a rendered sign-in link pattern, **When** it is validated, **Then** the
   stack name in it matches only itself (no character in it acts as a pattern
   operator).

---

### User Story 5 - A teammate reaches everything through one link (Priority: P2)

The owner presses "Share this stack" for `wp-12`, which uses `dev`. A teammate without
tailnet access opens the one link in a fresh browser. From that page alone they open
`wp-12`'s site, sign in to its WordPress admin, open its inbox, then move to the shared
`dev` services and open the Hub, sign in to the admin console and use it, reach the
renderer and open `dev`'s inbox.

**Why this priority**: the owner's access model ("everything on the stack page ...
through the same single share link"). It needs the short link format.

**Independent Test**: On two fixture stacks where one uses the other and both declare
share gates and sign-ins, mint one link and drive a fresh browser through every route,
sign-in and inbox on both pages; then repeat with a stack that has one gap and see the
share refused with that gap named.

**Acceptance Scenarios**:

1. **Given** a stack whose every listed piece has an access path, **When** a stack link
   is minted, **Then** one link is handed out and, for each used stack, a companion link
   to that stack's page is minted on its own gate, ending no later than the parent.
2. **Given** the stack page from that link, **When** the visitor uses its Shared section,
   **Then** the used stack's page opens through the companion, with no second link from
   a person, and has no Shared section of its own.
3. **Given** a sign-in on either page, **When** it is clicked, **Then** a fresh
   single-use sign-in is minted for that click and lands on the route it serves.
4. **Given** two visitors on one page who click the same sign-in and then use theirs in
   click order, **When** they use them, **Then** both sign in.
5. **Given** a route the template marks as served by a sign-in, **When** the page is
   shown, **Then** that route offers the sign-in; other routes are open links.
6. **Given** a stack, or a stack it uses, with a route behind Basic Auth, a private
   non-gate hostname, an inbox without an inbox sign-in, `autologin` on a private
   target, or a used stack without a share gate that allows agent-made links, **When**
   a stack link is requested, **Then** it is refused before anything is minted, and the
   refusal lists every gap with what to declare.
7. **Given** a used stack whose development window ends before the link would, **When**
   the link is minted, **Then** it is minted with the earlier expiry and the result
   says so.
8. **Given** a share-link visitor, **When** they try any panel route, **Then** none is
   reachable: the gate serves only the stack page, its sign-ins and its companions.

---

### User Story 6 - Revoking and recovering a shared link (Priority: P2)

The owner revokes `wp-12`'s link. Both the `wp-12` page and the shared `dev` page stop
answering for the teammate, and unused sign-ins from either are refused. When part of
that cannot be done, the owner is told what is still live, and the same revoke finishes
it later, even if a gate restarted in between.

**Why this priority**: a link that reaches several gates must be revocable as one.

**Independent Test**: Mint a link with a companion, induce a failure on the companion's
revoke, revoke, read the result, restore the companion's gate and revoke again. Then
interrupt a mint between the companion and the parent and check the next share cleans
it up.

**Acceptance Scenarios**:

1. **Given** a live link with companions, **When** it is revoked, **Then** both pages
   stop answering for its sessions and every unused sign-in minted from either page is
   refused; application sessions already started keep their own lifetime.
2. **Given** a companion that cannot be reached during the revoke, **When** the revoke
   ends, **Then** the result names each grant still live and until when, and running
   the same revoke again completes it.
3. **Given** the stack's own gate restarted and forgot the link, **When** the link is
   revoked, **Then** its companions are still revoked.
4. **Given** a share attempt interrupted after a companion was minted, or whose rollback
   failed, **When** the stack's live links are listed, **Then** the attempt is listed;
   the next share of that stack, or a revoke of the attempt, removes what it minted
   first, and no retry leaves two grants for one attempt.
5. **Given** a companion's gate restarted after the share, **When** the visitor opens the
   Shared section, **Then** it says that stack is unavailable and to ask for a new link.

---

### Edge Cases

- A stack record exists but its rendered manifest is missing on the controller: the page
  shows the routes it records and says sign-in needs `sb host stack up` again.
- The used stack was replaced by a new stack with the same name: the use follows the
  name; the page and new links reach the new stack.
- A route is listed by more than one used stack (two stacks share a hostname): it is
  listed under each, never merged.
- Health cannot be probed (timeout, refused): the route shows "unknown", not "down".
- The panel runs on the server itself (no controller records): the page lists what that
  server's records show and says sign-in and sharing run on the controller.
- The sign-in action is pressed while an apply of that stack runs: the press is refused
  with "another host apply or recovery owns this target", as `host login-url` is today.
- The outstanding sign-in bound is reached: the oldest unused sign-in for that entry is
  dropped and refused when used, with a message to click again.
- A companion's expiry would be later than the parent's: it is clamped to the parent's.

## Requirements *(mandatory)*

### Functional Requirements

**Stack page on the panel (Story 1)**

- **FR-001**: The panel MUST offer one page per recorded stack, reached from the stack's
  card and by an address that names the remote and the stack and loads directly.
- **FR-002**: The page MUST list every route the stack records with hostname, role (the
  first DNS label), state and health, using the panel's existing state and health
  sources.
- **FR-003**: Each own route MUST show the stack's delivered commit as `sb host stack
  list` computes it; a linked site MUST show its own commit; a delivery without runtime
  revision evidence MUST read "unverified", and no delivery MUST read "not delivered".
  The Hosting page's stack cards MUST use the same delivered commit.
- **FR-004**: The page MUST show the inbox link: a tailnet name recorded on the stack's
  remote whose upstream is the loopback port of the stack's inbox (the stack port named
  `mailpit`). With none, it MUST show the `sb host tailnet-name add` command for that
  port and no link.
- **FR-005**: An address naming no recorded stack MUST show a not-found state and a link
  back to the Hosting page.

**WordPress sign-in from the panel (Story 2)**

- **FR-006**: `sb host login-url` MUST accept `--name <stack>` and `--target
  <project>/<environment>` and resolve them to the recorded stack's rendered manifest,
  as `host status` does; it MUST NOT fall back to the current directory's manifest when
  either is given.
- **FR-007**: The panel MUST offer a WordPress admin sign-in action for a stack whose
  rendered manifest declares `autologin` and is not private. Each press MUST mint one
  link through the same operation the CLI uses, under the same writer lock, and return
  it only in that response, which MUST forbid caching.
- **FR-008**: The panel's sign-in route MUST refuse, before any effect, any request that
  does not pass the panel's access gate with an Origin equal to its Host, a JSON body,
  and the panel's own request header, exactly as the 081 share route does. Remote and
  stack names MUST be validated against their naming rules.
- **FR-009**: Minted WordPress sign-in links MUST carry the token in the URL fragment.
  The host autologin mu-plugin MUST accept the token only by POST from its own page,
  check it against its stored hash and expiry, and sign in once. The plugin's install
  path, user and `request_path` MUST stay as declared.
- **FR-010**: A press MUST NOT invalidate another press's unused link. The plugin MUST
  hold a bounded number of outstanding links (at most 8); past the bound the oldest
  unused one is dropped. Each link MUST expire within the manifest's `ttl_seconds`.
- **FR-011**: No WordPress sign-in link or token may be written to a polled or persisted
  panel response, panel log, browser storage, request line, host state, audit line or
  job output.
- **FR-012**: Where no sign-in is possible (no `autologin`, a private target, no
  rendered manifest, a server's own panel), the page MUST say why and what to declare
  or where to run the command.

**Uses (Story 3)**

- **FR-013**: A stack template's `stack` block MAY declare `uses`: a list of other stack
  names on the same remote.
- **FR-014**: `stack up` (plan and confirm) MUST refuse, before any write, a use that
  names no stack recorded on that remote, names the stack itself, repeats a name, or
  makes the list longer than 4, each with its own code.
- **FR-015**: On confirm, `stack up` MUST record the accepted uses with the stack;
  `stack list` (text and JSON) MUST show them.
- **FR-016**: The panel page MUST show one Shared section per recorded use, with the used
  stack's routes (FR-002, FR-003), inbox (FR-004) and sign-in actions (FR-007), each
  marked "shared". A used stack that is not recorded any more MUST show as missing.
- **FR-017**: Use MUST be direct only: a used stack's own uses are neither listed nor
  reachable from the using stack's page. A cycle MUST be accepted.
- **FR-018**: `stack down` of a stack that others use MUST name those stacks in its
  result and MUST NOT refuse.

**Templated gate and sign-in hostnames (Story 4)**

- **FR-019**: A stack template MUST accept the stack placeholders in the share gate
  hostname, in each login-service entry hostname, and in magic-link request and link
  origins, link patterns and required parameter values. Any other share or
  login-service field holding a placeholder MUST be refused as
  `stack_template_invalid`.
- **FR-020**: A placeholder rendered into a link pattern MUST match only its literal
  value.

**One-link teammate access (Story 5, after short share links merge)**

- **FR-021**: A stack link MUST open the stack's page on the stack's own gate, as spec
  081 defines, using the short link format.
- **FR-022**: The page MUST list each own route with one access path: open, or a named
  Sandbox sign-in (WordPress admin, a login-service entry, the inbox sign-in) that lands
  on it. An unmapped route is author-declared anonymous, but known authentication, private
  restrictions or missing access metadata MUST still refuse sharing.
- **FR-023**: Sign-ins on the page MUST be minted per click, single-use, living at most
  10 minutes, and a click MUST NOT invalidate another click's unused sign-in, within a
  bound of 8 outstanding per sign-in.
- **FR-024**: Before minting, a stack share MUST check that every piece its page and its
  used stacks' pages will list has an access path, and MUST refuse with every gap and
  its fix otherwise: a route behind Basic Auth, a private non-gate hostname, an inbox
  without an inbox sign-in, `autologin` on a private target, a used stack without a
  share gate allowing agent-made links.
- **FR-025**: For each used stack, the share MUST mint a companion link to that stack's
  page on its own gate, ending no later than the parent; when the used stack's window
  ends sooner, the whole share MUST take the earlier expiry and say so.
- **FR-026**: The parent page MUST show each used stack's routes in a Shared section and
  open that stack's page through its companion. A companion page MUST have no Shared
  section.
- **FR-027**: A share-link visitor MUST reach only the stack page, its sign-ins and its
  companion pages: never the panel, and never another stack.
- **FR-028**: No share, companion or sign-in token may reach a log, record, audit line,
  request line or job output; companion tokens live only on the parent gate's private
  state.

**Revoke and recovery (Story 6, after short share links merge)**

- **FR-029**: Before minting, the controller MUST record the share attempt: the parent
  and companion link ids and gates, never a token. The record MUST be listed with the
  stack's live links.
- **FR-030**: Revoking a parent MUST revoke its companions and every unused sign-in
  minted from either page, using the controller's record, so it works after the
  parent's gate lost its state.
- **FR-031**: A revoke that cannot reach a grant MUST report each grant still live and
  until when; repeating the revoke MUST complete it.
- **FR-032**: An interrupted share attempt MUST be removed (its minted grants revoked)
  by the next share of that stack or by a revoke of the attempt, and no retry may leave
  two grants for one attempt.
- **FR-033**: A companion page whose gate restarted MUST show as unavailable on the
  parent page, with a message to ask for a new link.
- **FR-034**: Every panel sign-in, share create, list and revoke, and every companion
  mint and revoke, MUST be audited as spec 081 audits share operations.

**Constraints**

- **FR-035**: The 081 development-only guards MUST apply to every link and companion:
  `allowed_host_suffixes` covers every hostname and `enabled_until` is required,
  unexpired and within 30 days.
- **FR-036**: Docs, specs and tests MUST use placeholder hostnames only.
- **FR-037**: The documentation MUST list the manifest fields a stack template uses for
  this feature (`stack.uses`, the inbox port name, share gate and sign-in hostnames, the
  route-to-sign-in mapping and explicit inbox_sign_in), with one worked example of a WordPress stack using a Hub
  stack.

### Key Entities

- **Stack use**: a stack's recorded list of other stack names on the same remote,
  direct only.
- **Stack page view**: what the panel shows for one stack: routes with role, state,
  health and revision; inbox address or how to make one; sign-in actions; Shared
  sections.
- **Route access path**: for each listed route, open or the named sign-in that serves
  it.
- **Panel sign-in**: a one-time WordPress link minted per press, shown once.
- **Companion link**: a stack link to a used stack's page, minted with and bounded by a
  parent stack link.
- **Share attempt record**: on the controller, the parent and companion link ids and
  gates for one share, never a token; the basis for complete revoke and cleanup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every recorded stack, its page loads from its own address and lists
  100% of the stack's recorded routes, and of each used stack's routes, with role,
  state, health and a revision that matches `sb host stack list` or reads "unverified" /
  "not delivered".
- **SC-002**: A tailnet user goes from the Hosting page to signed-in WordPress admin of a
  stack in at most two clicks.
- **SC-003**: Two sign-in presses yield two links that each sign in exactly once; 0
  tokens appear in later panel responses, logs, browser storage or request lines.
- **SC-004**: 100% of forged or foreign-origin panel sign-in requests mint nothing.
- **SC-005**: 100% of unknown, self, repeated or over-limit uses are refused before any
  write.
- **SC-006**: Two stacks from one template apply with distinct gate and sign-in
  hostnames, and each sign-in lands on its own stack.
- **SC-007**: From one link, a fresh browser without tailnet access completes every
  access in User Story 5 (including authenticated admin-console use) with no second link
  or separately supplied credential.
- **SC-008**: Two concurrent visitors who click the same sign-in both sign in.
- **SC-009**: After a revoke, 0 pages or unused sign-ins from the link answer; an induced
  partial failure is reported and a repeat revoke leaves 0 grants live, including after
  a gate restart.
- **SC-010**: A share for a stack with any access gap is refused with 100% of its gaps
  named, before anything is minted.

## Assumptions

- The shared `dev` Hub is recorded as a stack named `dev` on the same remote as the
  stacks that use it.
- A stack's inbox is the Compose service behind the stack port named `mailpit`, as the
  panel already assumes when it shows `mailpit_port`.
- The stack templates can declare the sign-ins their routes need (for the admin console,
  a Hub sign-in through the inbox), and mark which route each sign-in serves.
- Tailnet names are created with `sb host tailnet-name add`; `stack up` does not create
  them.
- `feat/short-share-links` merges into `latest` without changing the stack page's
  sign-in model.
- The panel stays tailnet-only and unauthenticated, protected by its same-origin checks.
- Moving the WordPress token to the fragment is acceptable for `sb host login-url`
  callers; agents open it with a browser (`visit`), not a plain HTTP fetch.
- This is an auth change: release needs the owner's sign-off.

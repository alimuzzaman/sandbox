# Product Requirements Draft: Stack page access (per-stack panel page, shared services, one-link teammate access)

**Status**: Ready

**Created**: 2026-11-11

**Last Refined**: 2026-11-11

**Input**: "On the Sandbox web panel every hosted stack gets its own detail page listing everything that stack is made of, with working access to each piece: every route with state, health and built-from commit; the stack's inbox; a one-click WordPress admin auto-login; and shared services (a stack that uses the shared `dev` Hub also lists and grants access to its routes and inbox, marked shared, through a manifest declaration validated at apply). Tailnet users get everything; teammates without tailnet access get everything on the stack page through one share link."

**Drafting Configuration**: Claude Opus 5.5 (main session), Haiku 5.5 read-only scouts for code inventory

**Final Validation**: `PASS` — GPT-6.1-Sol, effort high, Codex Office, read-only (round 4; rounds 1-3 REOPEN)

**Validated On**: 2026-11-11

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

The panel's Hosting page shows each stack as a card: its servers with state and health,
its commit, and since spec 081 a "Share this stack" button. Working on a stack still
takes several tools and knowledge the card does not hold:

- The inbox (Mailpit) listens only on the server's loopback port. It is reached through
  a tailnet name made by hand (`mail-<n>`), and the card does not say which.
- WordPress admin sign-in for a tailnet user means running `sb host login-url` on the
  controller with the right manifest. It has no working stack form (a stack name is
  accepted but ignored) and the panel has no button.
- A WordPress-only stack works against the shared `dev` Hub. Nothing records that, so
  neither the card nor the shareable stack page lists the Hub, admin console, renderer
  or inbox the stack actually uses.
- Teammates without tailnet access get one share link to a stack page (spec 081), but
  that page covers only the stack's own servers and sign-ins. They cannot reach the
  shared services the stack depends on, and nothing tells the person sharing which
  pieces of the stack the visitor will not be able to open.
- A stack template cannot give each stack its own share gate hostname, because stack
  placeholders are refused in the share and login-service fields. Every stack from one
  template would claim the same gate hostname.

The owner wants one place per stack that lists everything the stack is made of, with
access that works for whoever holds it.

## Users and Desired Outcomes

- **Tailnet user (owner, developers)**: opens a stack's page on the panel and reaches
  every route, the inbox and WordPress admin with one action each, shared services
  included.
- **Teammate without tailnet access**: receives one share link and, from the page it
  opens, reaches every piece listed on it: WordPress (admin sign-in included), the Hub,
  the admin console, the renderer, the inbox and the shared services. They never need a
  second link from a person.
- **Stack template author** (the sibling thread reworking the xSpeed stack templates):
  declares which other stacks a stack uses and how a visitor signs in to each route,
  with per-stack gate and sign-in hostnames, using documented fields.
- **Owner / orchestrator**: is told, before a link exists, when some piece of a stack
  would not be reachable through it, and what to declare to fix that; gets an audit
  trail for every link, companion and sign-in minted or revoked.

## Goals

- G1: Each recorded stack has a panel page with its own address that lists every route
  of the stack with hostname, role, state, health and revision.
- G2: The page shows the stack's inbox link when one is known, and how to make one when
  none is.
- G3: The page has a WordPress admin sign-in action that mints a fresh one-time link per
  click for a stack that declares `autologin`, through the existing host autologin
  mu-plugin path.
- G4: A stack template can declare the other stacks it uses. The declaration is
  validated when the stack is brought up and recorded with the stack.
- G5: The panel page lists each used stack's routes, inbox and sign-in actions in a
  section marked "Shared", with the same access as on the used stack's own page.
- G6: One share link to a stack page gives a teammate a working way into every route,
  sign-in and inbox listed on that page, shared services included; a stack whose pieces
  cannot all be reached that way is refused a link, with what to declare.
- G7: A stack template can give each stack its own share gate hostname and its own
  sign-in entry hostnames and origins.

## Non-Goals

- Exposing the panel, its mutating API, or unrelated stacks to share-link visitors.
- A cookie shared across the parent domain, or any change to how front doors check
  sessions.
- Reaching routes behind Basic Auth, or a private target's non-gate hostnames, through a
  share link. A stack that has any is refused a link (see G6).
- `stack up` creating tailnet names. Tailnet names stay managed by
  `sb host tailnet-name add`; the panel only reads them.
- Transitive use: what a used stack itself uses is not listed or reachable.
- Uses that point at hosted targets that are not stacks, or at another remote.
- Ending application sessions that a sign-in already started (a WordPress login, a Hub
  session, an inbox session) when a link is revoked; they keep their own lifetime, as
  in spec 081.
- Editing the xSpeed stack templates (`xspeed-dev-setup`), xspeed, xspeed-pro or
  xspeed-hub.
- Merging, migrating, deploying, applying live sites, or minting or revoking live links.

## Product Scenarios

### Scenario 1 — A tailnet user opens a stack's page

- **Starting state**: the controller records stack `12` on remote `R`, applied, with
  routes `hub-12`, `admin-12` and `renderer-12`, and a tailnet name for its inbox port.
- **User action**: on the Hosting page they open stack `12`.
- **Expected outcome**: a page for stack `12` lists every route with role, state,
  health and revision, links each route, links the inbox, and shows the stack's sign-in
  actions. The page address can be bookmarked and reloaded.

### Scenario 2 — One-click WordPress admin from the panel

- **Starting state**: stack `wp-12`'s template declares `autologin`.
- **User action**: they press "Sign in to WordPress admin".
- **Expected outcome**: a fresh one-time link opens WordPress admin signed in. A second
  press mints another link; each works once and only until its short lifetime ends. A
  link is shown once and never appears in a later panel response, a log, browser
  storage, or an HTTP request line.

### Scenario 3 — A WordPress-only stack uses the shared Hub

- **Starting state**: the template of stack `wp-12` declares that it uses stack `dev`,
  which is recorded on the same remote.
- **User action**: `stack up` for `wp-12`; then open its panel page.
- **Expected outcome**: `up` accepts the declaration and records it. The page lists
  `hub-dev`, `admin-dev`, `renderer-dev` and the `dev` inbox in a "Shared" section, each
  with its own state, health and revision, and the same sign-in actions `dev`'s own page
  offers.

### Scenario 4 — A teammate gets one link

- **Starting state**: `wp-12` and `dev` both declare a share gate that allows agent-made
  links, an inbox sign-in, and a sign-in for each route that needs one (`dev`'s admin
  console). The owner presses "Share this stack" for `wp-12`.
- **User action**: a teammate in a fresh browser, without tailnet access, opens the one
  link and works through the page.
- **Expected outcome**: from that page alone they open `wp-12`'s site, sign in to its
  WordPress admin, open its inbox, and move to the shared `dev` services, where they
  open the Hub, sign in to the admin console and use it, reach the renderer and open
  `dev`'s inbox. No credential reaches them any other way.

### Scenario 5 — Revoking the link

- **Starting state**: the teammate from Scenario 4 has used some sign-ins and left others
  unused.
- **User action**: the owner revokes the `wp-12` link.
- **Expected outcome**: the `wp-12` page and the shared `dev` page it opened stop
  answering for that teammate, and every sign-in minted from either but not yet used is
  refused. Application sessions already started keep their own lifetime. If part of the
  revoke cannot be completed, the result names each grant still live and until when,
  and running the same revoke again finishes it, even after the stack's own gate has
  restarted and forgotten the link.

### Negative scenarios

- **Unusable `uses`**: a template uses `dev` but no stack `dev` is recorded on that
  remote, or it names itself, repeats a name, or lists more than the limit: `stack up`
  refuses before any effect with a code naming the problem.
- **Used stack removed or replaced later**: `dev` goes down after `wp-12` was brought
  up: the `wp-12` panel page shows `dev` as missing; a new link for `wp-12` is refused
  until `dev` is back or the declaration changes. A new stack named `dev` is what
  `wp-12` uses from then on.
- **Cycle**: `a` uses `b` and `b` uses `a`: both are accepted; each page shows only its
  direct uses.
- **Incomplete one-link access**: a stack (or a stack it uses) has a route behind Basic
  Auth, a private non-gate hostname, an inbox without an inbox sign-in, an `autologin`
  on a private target, or a used stack without a share gate that allows agent-made
  links: the link is refused before anything is minted, and the refusal lists each gap
  with what to declare. The panel page still shows the stack in full to tailnet users.
- **No autologin**: a stack without `autologin` shows no WordPress sign-in action, and
  says what to declare.
- **No inbox address known**: the panel page says no inbox address is recorded and shows
  the `sb host tailnet-name add` command for the stack's inbox port.
- **Stale or used sign-in**: a sign-in that expired, was already used, was revoked, or was
  the oldest unused one dropped past the outstanding bound is refused with a message to
  press the sign-in action again. Newer clicks within the bound leave earlier sign-ins
  working.
- **Two visitors at once**: A clicks a sign-in, B clicks the same sign-in, A uses theirs,
  B uses theirs: both sign in. A click never invalidates another click's sign-in; each
  is single-use and short-lived, and the number outstanding per sign-in is bounded (the
  oldest unused one is dropped past the bound).
- **Companion trouble**: a used stack's gate cannot mint its link at share time: no link
  is handed out and anything already minted for the attempt is revoked. If the attempt
  is interrupted, or its rollback fails, the attempt stays recorded on the controller:
  it is listed with the stack's live links, the next share of that stack or a revoke
  of the attempt removes what it minted first, and no retry leaves two grants for one
  attempt. A used stack's gate restarted after the share: its part of the page says it
  is unavailable and to make a new link.
- **Parent gate restarted**: the stack's own gate loses its state; the stack link stops
  working, while companion links stay live until their expiry or a revoke of the parent
  link, which still reaches them.
- **Incomplete revoke**: until the repeat revoke succeeds, a companion it could not reach
  keeps its page and its sign-ins working, bounded by the companion's own expiry; the
  revoke result says so.
- **Shared stack expires sooner**: the used stack's development window (`enabled_until`)
  ends before the link would: the link is refused, or made with the earlier expiry, and
  the result states which.
- **Forged panel request**: a sign-in or share request without the panel's Origin, JSON
  body and panel header, or from a foreign Origin, is refused before anything is
  minted.
- **Server's own panel** (no controller record): the page says sign-in is minted on the
  controller and shows the command.

## Proposed Product Behavior

- **Stack page on the panel.** One page per recorded stack, reached from its card and by
  its own address. It lists the stack's routes (hostname, role from the first DNS label,
  state, health, revision), the inbox, the sign-in actions, the share actions spec 081
  already offers, and a "Shared" section per used stack.
- **Revision.** Each route shows the revision it was built from: the stack's delivered
  commit for its own routes, a linked site's own commit for that site. When delivery was
  recorded without runtime revision evidence, or nothing was delivered yet, the page
  says so instead of showing a commit as proven. The delivered commit is the one
  `sb host stack list` reports.
- **Inbox link.** The panel finds the inbox address from tailnet names recorded on the
  stack's remote whose upstream is the stack's inbox loopback port. With none, it shows
  the command that makes one.
- **Sign-in on the panel.** Each sign-in action mints one fresh one-time link per click.
  WordPress admin uses the existing host autologin path for that stack. The panel's
  request is refused unless it passes the panel's existing same-origin checks. The link
  is shown once, opened in a new tab, and never stored. The token never travels in a
  request line: Sandbox-minted WordPress sign-in links carry it in the URL fragment.
- **`uses` declaration.** A stack template lists the other stacks it uses by name. On
  `stack up` (plan and confirm) each name must be a stack recorded on the same remote,
  not the stack itself, listed once, and the list no longer than the limit. The accepted
  list is recorded with the stack and shown by `stack list`. Use is direct only.
- **Templated gate and sign-in hostnames.** A stack template may use the stack
  placeholders in the share gate hostname and in sign-in entry hostnames and origins.
- **Route access on a shared page.** Each listed route has one access path: open, a
  Sandbox sign-in that lands on it, or the inbox sign-in. Open means usable anonymously
  for what the route is for. Sandbox cannot see whether an application needs a login,
  so a route is open unless the template names the sign-in that serves it; the template
  author declares one for every route that needs a login, and the docs say so.
- **One-link teammate access.** The share link opens the stack's page on the stack's own
  gate. Sign-ins on that page are minted per click, like the panel's, and one click
  never invalidates another's. For each used
  stack, minting the link also mints a companion link to that stack's page on its own
  gate, ending no later than the parent. The page's Shared section opens the used
  stack's page through it. A companion page has no Shared section of its own. The
  controller records each share attempt and its companions (never a token) before
  minting, so a parent revoke reaches its companions even when the parent's gate has
  lost its state. Revoking the parent revokes its companions and every unused sign-in
  minted from either; partial failures are reported with what is still live, and a
  repeat revoke completes them.
- **Refuse rather than share partially.** Before minting, the share checks that every
  piece the page will list has an access path for a visitor, and refuses with the list
  of gaps otherwise.

## Constraints and Dependencies

- **Short share links first.** Branch `feat/short-share-links` changes the link format
  and the gate. Companion links, per-click sign-ins on the shared page, and any gate
  change are built only after that branch is in `latest`, on the short format.
  Everything else is independent of it.
- **Spec 081 guards stay.** Agent-made links and stack pages are limited to development
  targets (`allowed_host_suffixes`, `enabled_until` within 30 days). A used stack needs
  `share.agent_mint: true` for a companion link.
- **Panel exposure.** The panel stays tailnet-only and unauthenticated. Its mutating
  routes keep the 081 same-origin checks; none is reachable from a share link.
- **Secrets.** No sign-in, share or companion link is written to a polled or persisted
  panel response, panel log, browser storage, request line, audit line or job output.
- **Owner-named integration.** WordPress sign-in goes through the host autologin
  mu-plugin (`request_path: /wp-login.php`, `99-sandbox-host-autologin.php`); its token
  transport may change, its install path and user may not.
- **Public repository.** Docs, specs and tests use placeholder hostnames only.
- **Templates are external.** The xSpeed templates live in `xspeed-dev-setup`; this
  feature documents the fields they should use and changes nothing there.
- **Auth change.** It adds a sign-in route to the panel, changes how WordPress sign-in
  tokens travel, and lets one share link reach other stacks' pages, so it needs the
  owner's sign-off before release.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Who sees what | Tailnet: full panel and every action. Share-link visitor: the named stack's page and its declared used stacks' pages, nothing else. | Owner's access model; teammates are not outsiders, but the panel's mutating API stays private. | Owner (2026-11-11 brief) |
| How each host accepts a share-link visitor | Host by host: open routes need nothing; routes that need a login get a single-use Sandbox sign-in that lands on that host (WordPress autologin, login-service sign-in, inbox sign-in); shared stacks are reached through a companion link to their own page. No parent-domain cookie. | A parent-domain cookie would reach every application on the zone, is barred by the `__Host-` cookie rule, and would need one session store across gates. Host-by-host single-use sign-ins are the spec 081 model and already admit a visitor to WordPress, the inbox and the Hub sign-in; a companion link reuses it for shared stacks without a new trust link between gates. | Design call delegated by owner |
| Partial access | Refuse the link and list the gaps, rather than hand out a link that silently cannot reach some pieces | Owner asked for "everything" through one link; current templates use no Basic Auth or private targets, so the refusal only asks for declarations. | Design call; flagged for owner sign-off |
| When sign-ins are minted | Per click, on both the panel and the shared page; clicks never invalidate each other (bounded outstanding count) | Per view, and one token per sign-in, make two viewers overwrite each other's links; independent per-click links let concurrent teammates all sign in. | Design call delegated by owner |
| Companion lifetime | An independent grant on the used stack's gate, ending no later than the parent, revoked through the controller's record of the share | A gate cannot ask another gate whether a parent is live without a new cross-gate trust link; the controller record keeps revoke complete after a gate restart. | Design call; flagged for owner sign-off |
| What "open" means | Usable anonymously; a route that needs a login needs a declared sign-in | Reaching a login screen is not access; Sandbox cannot detect application logins, so the template declares them. | Design call delegated by owner |
| What revoke ends | Page access, companion pages, and unused sign-ins; not application sessions already started | Matches spec 081; ending application sessions would need each application's cooperation. | Existing policy (spec 081) |
| WordPress token transport | Fragment, posted by the mu-plugin's own page | Keeps tokens out of access logs; owner-named plugin path and user stay. | Design call; flagged for owner sign-off |
| Inbox for tailnet users | Read existing tailnet-name records; `stack up` does not create tailnet names | Tailnet names carry DNS and certificate work under their own broker; reading records needs no new authority. | Design call delegated by owner |
| What `uses` names | Other stacks on the same remote, by name, direct only | Owner said "referencing another stack name"; same remote keeps health, ports and gates local. | Owner |
| Validation point | `stack up` (plan and confirm), where the template is rendered and recorded | That is the stack's apply entry; plain `host apply` refuses templates. | Owner ("validated at apply") |
| Ordering | Panel page, `uses`, templated hostnames and panel sign-in first; gate, companion links and per-click shared-page sign-ins after short links merge | Avoids conflicting with the gate code under review. | Owner |

## Open Questions

- None blocking. The `uses` limit (proposed: 4), whether `stack down` of a used stack
  warns, and the earlier-expiry choice for a shared stack (refuse vs. shorten) are left
  to `speckit-clarify`.

## Acceptance Outcomes

- A1: For every recorded stack, the panel offers a page whose address loads it directly
  and which lists every recorded route of the stack and of each used stack, each with
  role, state, health and a revision that is either the recorded delivered commit or an
  explicit "not delivered" / "unverified".
- A2: With a tailnet name recorded for the inbox port, the inbox link opens it; without
  one, the page shows the command and no broken link.
- A3: Pressing WordPress sign-in twice yields two different links; each signs in once
  and is refused on a second use or after its lifetime; neither appears in any later
  panel response, log, browser storage or request line.
- A4: A panel sign-in or share request that lacks, or forges, the panel Origin, JSON body
  or panel header mints nothing; a share-link visitor has no route to any panel action.
- A5: `stack up` refuses an unknown, self, repeated or over-limit `uses` entry before any
  write, and records an accepted list; `stack list --json` shows it.
- A6: Two stacks from one template apply with independently expanded gate hostnames,
  sign-in hostnames and origins, and each stack's sign-ins land on that stack's own
  hosts.
- A7: From one share link, a fresh browser without tailnet access reaches every route,
  sign-in and inbox on the stack page and on each used stack's page (Scenario 4),
  including authenticated use of the admin console, with no second link or separately
  supplied credential.
- A7b: Two browsers on the same stack page each click the same sign-in, then each use
  theirs in click order: both sign in.
- A8: After the link is revoked, both pages stop answering for that browser and every
  unused sign-in from them is refused. An induced partial failure is reported with what
  is still live, and a repeat revoke completes it, also after the stack's own gate was
  restarted. An interrupted share attempt is listed and leaves no grant behind after the
  next share or a revoke of the attempt.
- A9: A share for a stack with any access gap is refused before anything is minted, and
  the refusal names every gap and its fix.

## Risks and Assumptions

- **Risk**: a companion link is a real share link to another stack's page; anyone holding
  the parent link can reach it. Accepted by the owner's access model; it is bounded by
  the parent's expiry and revoke.
- **Risk**: gate state lives on tmpfs; a used stack's gate restart drops its companion
  link until the next share.
- **Risk**: moving the WordPress token to the fragment changes `sb host login-url`
  output for any caller that fetches the URL without a browser.
- **Risk**: the panel's commit today reads the raw record; switching to the delivered
  commit may change what some cards show.
- **Assumption**: the shared `dev` Hub is recorded as a stack named `dev` on the same
  remote as the stacks that use it.
- **Assumption**: the stack templates can declare the sign-ins their routes need (for the
  admin console, a Hub sign-in through the inbox).
- **Assumption**: `feat/short-share-links` merges into `latest` without changing the
  stack-page sign-in model.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->

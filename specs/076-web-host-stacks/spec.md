# Feature Specification: Stacks and Hosted Sites View

**Feature Branch**: `feat/web-host-stacks`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "Stacks and hosted sites view in the `sb web` panel (read-only). Owner request 2026-11-10: 'by links i meant all the server created for the stack. like wp, hub, renderer, admin, mail etc'. Goal: nobody needs the CLI to find a stack's servers or details."

## Clarifications

### Session 2026-11-10

No owner questions were needed. The brief says the agent picks and documents the association mechanism, so these points were settled from it and from the code:

- Q: How are wildcard route hostnames (`*.demo.example.test`) shown? → A: As plain text, labelled "wildcard": no link and no health check.
- Q: In what order are a stack's server rows listed? → A: Primary route first, other routes by hostname, then linked sites, then name-matched sites.
- Q: Is a stale "applying" status shown differently? → A: No. It shows its start time, and the viewer judges. A process that dies mid-apply leaves "applying" until the next apply.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find every server of a stack (Priority: P1)

A developer or QA tester opens the Sandbox panel on the shared server (reached over the tailnet, no login) and wants the addresses of a stack, for example the one made for branch `demo`. They open "Stacks and hosted sites" and see the stack `demo` with one row per server created for it: the hub, admin and renderer hostnames, the mail hostname once the stack has one, and any later ones. Each row is a link that opens that server. They never run a CLI command to find them.

**Why this priority**: This is the owner's request word for word. Without it, people ask in chat or run `sb host stack list` on a controller.

**Independent Test**: With one stack applied on a server, open the panel on that server, open the view, and check that every route hostname of the stack appears as a link under the stack.

**Acceptance Scenarios**:

1. **Given** a stack `demo` whose hosted target serves `hub-demo.example.test`, `admin-demo.example.test` and `renderer-demo.example.test`, **When** a viewer opens the view on that server, **Then** the stack `demo` shows three server rows, each a link to `https://<hostname>/`.
2. **Given** the stack's template later adds a fourth route, **When** the stack is applied again, **Then** the view shows four rows with no panel change.
3. **Given** a route that redirects to another host, **When** it is listed, **Then** its row says it redirects and to where.

---

### User Story 2 - See the WordPress site made for a stack under that stack (Priority: P1)

A WordPress site is made for the stack (for example `sb deploy --remote R --ensure --expose --domain wp-demo.example.test --stack demo`). In the view it appears as a server row under stack `demo`, not as an unrelated project.

**Why this priority**: The owner's list names "wp" first; the WordPress site is part of the stack for everyone using it.

**Independent Test**: Deploy a WordPress site with `--stack demo`, open the view, and check the site is listed under `demo` and labelled as linked by Sandbox.

**Acceptance Scenarios**:

1. **Given** a site deployed with `--stack demo`, **When** the view loads, **Then** the site's hostname is a server row under `demo`, labelled "linked".
2. **Given** a site at `wp-demo.example.test` deployed without `--stack`, **When** the view loads on a server that knows that hostname, **Then** the site appears under `demo` labelled "matched by name".
3. **Given** `--stack` names a stack that does not exist for that remote, **When** the deploy runs, **Then** it refuses before any effect, with a message naming the missing stack.
4. **Given** a linked site whose instance was later removed, **When** the view loads, **Then** the row stays under the stack and says the instance is not found.

---

### User Story 3 - Read a stack's code and state details (Priority: P2)

A viewer wants to know what a stack runs and whether its last apply worked. Each stack shows its slug, project, ref (branch and commit), template path, last apply status with time and request id, the Mailpit loopback port and the other named ports, and its created time. A TTL appears once stacks have one.

**Why this priority**: These are the details people now get from `sb host stack list --json`.

**Independent Test**: After one apply, compare the view's stack details with the values the apply used.

**Acceptance Scenarios**:

1. **Given** a stack applied from branch `feature/demo` at commit `abc…`, **When** the view loads, **Then** it shows the branch, the short commit with the full commit available, and the template path relative to its checkout.
2. **Given** the last apply failed, **When** the view loads, **Then** the stack shows status "failed", the time it ended and its request id.
3. **Given** an apply is running, **When** the view loads, **Then** the stack shows status "applying" with its start time.
4. **Given** a stack with a named port `mailpit`, **When** the view loads, **Then** the Mailpit loopback port is shown as a number and labelled as loopback-only.

---

### User Story 4 - Know whether each server answers (Priority: P2)

Each server row shows its state and current health: whether the hostname answers, with what HTTP status, and when it was last checked. The page shows at once; health fills in when the checks finish.

**Why this priority**: A link list is more useful when it shows which links work, but it must never slow the page.

**Independent Test**: Load the view with one hostname that answers and one that does not; the page renders at once with health "checking", and within one refresh the two rows show "up" and "unreachable".

**Acceptance Scenarios**:

1. **Given** no health is cached, **When** the view loads, **Then** it renders right away with health "checking" on each row.
2. **Given** a hostname returned 200 within the last minute, **When** the view loads again, **Then** the cached result is shown and no new check runs for it.
3. **Given** a hostname that asks for a password (401), **When** it is checked, **Then** health reads "up (login required)", not "down".
4. **Given** a check times out, **When** results arrive, **Then** the row shows "unreachable" and the time of the check.

---

### User Story 5 - See plain hosted targets the same way (Priority: P3)

Projects hosted with `sb host apply` that are not stacks are listed in the same view, read-only, each with its hostnames as links, its state, health, commit and last apply.

**Why this priority**: The same question ("where is it, is it up") applies to every hosted project.

**Independent Test**: With one plain target applied, open the view and check it is listed apart from stacks with its hostnames.

**Acceptance Scenarios**:

1. **Given** a plain hosted target, **When** the view loads, **Then** it is listed under "Hosted sites" with every hostname as a link.
2. **Given** a target applied before this feature, **When** the view loads on the server, **Then** it is listed by project and environment with a note that its details are recorded from its next apply.

---

### Edge Cases

- No stacks and no hosted targets: the view says so and names the command that creates one.
- A summary file that is unreadable or malformed: that one target is skipped and the view reports it as partial; the rest still shows.
- Hundreds of hostnames: health checks are capped per refresh; rows past the cap show "not checked".
- The panel runs on a controller with several remotes: each stack and target is labelled with its remote.
- The `mail-<slug>` hostname is tailnet-only: it is probed like any other; from a machine outside the tailnet it shows "unreachable", which is expected.
- A stack destroyed with `sb host stack down`: it disappears from the view, because its target's runtime directory is removed.
- A name-matched site that matches two stacks (overlapping slugs): it is shown under the longest matching slug only.
- A wildcard route hostname: shown as text labelled "wildcard", never linked or checked.
- An apply process that dies mid-apply: the stack keeps "applying" with its start time until the next apply records a new status.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The panel MUST offer a "Stacks and hosted sites" view reachable from the sidebar and by its own URL.
- **FR-002**: For each stack, the view MUST list one server row per hostname of the stack's hosted target, each a clickable link opening `https://<hostname>/` in a new tab, ordered primary first, then other routes by hostname, then linked sites, then name-matched sites. Wildcard hostnames are shown as text, not linked.
- **FR-003**: The view MUST list a WordPress site made for a stack under that stack. An explicit association recorded by Sandbox (`sb deploy --stack <slug>`) MUST take precedence; a hostname of the form `<prefix>-<slug>.<zone>`, where `<zone>` is the zone of the stack's own hostnames, MAY be used only as a fallback and MUST be labelled "matched by name".
- **FR-004**: `sb deploy --stack <slug>` MUST refuse before any remote effect when the slug is malformed, when `--expose` is not given, or when the controller has no stack of that slug on that remote.
- **FR-005**: Each server row MUST show its state (serving, redirecting, applying, failed, or not found), its health and the ref it was built from (branch and short commit when known).
- **FR-006**: Health MUST come from at most one HTTP request per hostname per refresh interval (default 60 seconds), each with a fixed timeout (default 4 seconds), not following redirects and sending no credentials. Results MUST be cached and the page MUST never wait for a check.
- **FR-007**: Health checks MUST target only hostnames read from Sandbox's own state; no request parameter may add or change a probed host.
- **FR-008**: Each stack MUST show slug, project, environment, remote, ref (branch and full commit), template path, last apply (status, time, request id), named ports with the Mailpit port labelled, created time, and a TTL field only when the stack record carries one.
- **FR-009**: The view MUST list plain hosted targets (no stack) with their project, environment, remote, hostnames as links, state, health, commit and last apply.
- **FR-010**: On a Sandbox remote, the view MUST be built from that server's own files only. `sb host apply` MUST record on the remote, next to the target's runtime files, a secret-free summary holding exactly the fields this view needs, written when an apply starts (status "applying"), when it succeeds and when it fails. A failure to write the summary MUST NOT fail or change the apply.
- **FR-011**: `sb deploy --stack <slug>` MUST record a secret-free site link on the remote and on the controller's stack record. `sb host stack up` MUST keep existing links when it rewrites a stack record.
- **FR-012**: On a controller, the view MUST be built from the controller's stack records and hosting state, labelled per remote.
- **FR-013**: The view, its data endpoint and the health checks MUST NOT show any secret value, MUST NOT read the target's environment file, and MUST NOT include secret key names.
- **FR-014**: The view MUST be read-only: no new action, no new POST route, and nothing that applies, destroys, restarts or executes anything.
- **FR-015**: A malformed or unreadable record MUST affect only its own row; the view MUST report partial evidence and keep showing the rest.
- **FR-016**: Docs that describe `sb web`, `sb deploy` and `sb host apply` MUST be updated in the same change.

### Key Entities

- **Stack**: one slug on one remote, rendered from a template into exactly one hosted target. Carries project, environment, ref, branch, commit, template path, named ports, created time, last apply and optional TTL.
- **Hosted target**: one remote/project/environment served by `sb host apply`, with one or more routes (hostname, serve or redirect, primary flag), a loopback port, a commit and a last apply.
- **Target summary**: the secret-free record of a hosted target kept on the remote for the panel.
- **Site link**: the record that a WordPress site (hostname, instance) belongs to a stack, with when it was made and the commit deployed.
- **Server row**: one hostname shown to the viewer, with its origin (route, linked site, or name-matched site), state, health and ref.
- **Health result**: the outcome of one bounded check of one hostname: up, up with login, redirect, client error, server error, or unreachable, with HTTP status and time checked.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A viewer finds every server address of a named stack from the panel in under 30 seconds, with no CLI command.
- **SC-002**: The view renders its rows in under 1 second on a server with 20 hosted targets, whether or not any health check has completed.
- **SC-003**: Every hostname served by a stack's target on the server appears in the view after that target's next apply (100% coverage in a test with four routes and one linked site).
- **SC-004**: No secret value from a target's configuration appears in the view or its data, verified by a test that plants known secret values in the inputs and searches every output for them.
- **SC-005**: The panel sends no more than one health request per hostname per minute, however many viewers or refreshes there are.

## Assumptions

- A stack renders exactly one hosted target (spec 071); its servers are that target's routes.
- `mail-<slug>` and other future servers arrive as routes in the stack template; no special handling per name is needed.
- TTL does not exist yet; the view shows it only when a stack record carries a TTL field.
- The panel stays tailnet-only with no login, as today; this feature adds no new exposure beyond the read-only data it shows.
- Targets applied before this feature show on the server by project and environment only until their next apply writes a summary. No backfill is attempted.
- The server-side panel needs its checkout updated to show the view; recording summaries needs only the controller's checkout, because `sb host apply` runs on the controller and writes to the remote over its existing connection.
- `sb deploy --expose` is refused on nginx front doors today; a WordPress site for a stack on such a server is out of scope until that changes, and the fallback name match still applies wherever the server knows the hostname.

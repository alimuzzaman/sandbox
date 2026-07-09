# Feature Specification: Remote VPS hosting for sandbox instances

**Feature Branch**: `014-remote-vps-hosting`

**Created**: 2026-07-09

**Status**: Draft

**Input**: User description: "Bring remote VPS hosting for sandbox instances into the
tool as a first-class, opt-in capability, per docs/remote-hosting-prd.md's now-resolved
design (see that doc's §0 'Resolved follow-up decisions' for the authoritative design
this spec should encode)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register and provision a remote VPS (Priority: P1)

A developer has an already-running VPS they manage themselves and wants sandbox to run
their project's instance there instead of on their local machine — for example, to free
up local resources, to test against a Linux server environment, or to keep an instance
reachable when their laptop is closed. They register the VPS with sandbox by giving it a
name and an SSH connection string, then provision it with one command, without having to
manually SSH in and install anything themselves.

**Why this priority**: Nothing else in this feature is usable without a registered,
provisioned remote target. This is the foundation every other story depends on.

**Independent Test**: Can be fully tested by registering a VPS, provisioning it, and
confirming sandbox reports it as ready and reachable — without yet running any project
on it.

**Acceptance Scenarios**:

1. **Given** a developer has SSH access to a VPS, **When** they register it with a name
   and connection string, **Then** sandbox stores it as a known remote target and
   confirms the registration succeeded.
2. **Given** a registered remote target, **When** the developer provisions it, **Then**
   sandbox installs everything the remote needs to run instances (without the developer
   manually running install commands themselves) and reports success or a clear,
   actionable failure.
3. **Given** a provisioned remote target, **When** the developer asks to see their
   configured remotes, **Then** sandbox lists each one along with whether it's currently
   reachable.
4. **Given** a remote target the developer no longer needs, **When** they remove it,
   **Then** sandbox stops treating it as a valid deploy/instance target (existing
   instances already running there are unaffected by the removal itself — the developer
   is responsible for tearing those down first).

---

### User Story 2 - Deploy local code to a remote target on demand (Priority: P1)

A developer has been editing a plugin locally and wants their latest work — including
changes they haven't committed yet — running on their remote VPS so they can test it
there. They run a single command to push their current code to the remote, and from
that point on, the remote reflects exactly what they just deployed until they deploy
again.

**Why this priority**: This is the other half of the MVP alongside User Story 1 — a
remote target with no way to get code onto it is not useful. Together, Stories 1 and 2
are the minimum slice that delivers real value (register a VPS, get code there).

**Independent Test**: Can be fully tested by making a local change (including an
uncommitted one), deploying it to a provisioned remote, and confirming the remote's
copy of the code now matches the local working tree exactly.

**Acceptance Scenarios**:

1. **Given** a provisioned remote target and a local project with committed changes not
   yet on the remote, **When** the developer deploys, **Then** the remote's code is
   updated to match the local commit.
2. **Given** a local project with uncommitted changes (both edits to already-tracked
   files and brand-new files not yet tracked by version control), **When** the developer
   deploys, **Then** the remote reflects those uncommitted changes too, not just the
   last commit.
3. **Given** a developer has deployed once, made further local edits, and deploys again,
   **When** the second deploy completes, **Then** the remote reflects ONLY the current
   local state — nothing left over from the first deploy's uncommitted changes lingers
   if it was since reverted or changed locally.
4. **Given** a project whose current branch has never been pushed to any code-hosting
   service (GitHub, etc.), **When** the developer deploys, **Then** the deploy still
   succeeds — deploying does not require the code to exist anywhere except the
   developer's own local machine and the remote target.

---

### User Story 3 - Run a full sandbox instance on the remote and use it exactly like a local one (Priority: P2)

A developer with a provisioned, deployed remote wants to boot a full WordPress instance
there and interact with it — run WP-CLI commands, read logs, take screenshots, run
tests — the same way they would with a local instance, without needing to learn a
different set of commands or tools for the remote case.

**Why this priority**: This is what makes the remote genuinely useful day-to-day, but it
depends on Stories 1 and 2 already working, and a developer could still get real value
from just registering/provisioning/deploying without this (e.g. to prepare a remote in
advance of needing it).

**Independent Test**: Can be fully tested by booting an instance on a provisioned,
deployed remote target and successfully running a representative set of the same
operations available for a local instance (a WP-CLI command, a log read, a screenshot).

**Acceptance Scenarios**:

1. **Given** a provisioned remote target with deployed code, **When** the developer
   boots an instance there, **Then** they get back a working, reachable instance the
   same way they would locally.
2. **Given** a running remote instance, **When** the developer runs a WP-CLI command,
   reads a file, or takes a screenshot against it, **Then** the result reflects the
   REMOTE instance's actual state — not stale or empty data (this is the specific
   failure this feature exists to avoid; see Assumptions).
3. **Given** a project that already has a local instance running, **When** the developer
   also wants a remote instance for the SAME project, **Then** they must explicitly ask
   for a second, distinctly-named instance — the system never silently runs both a local
   and a remote copy of what looks like "the same" instance.
4. **Given** a project configured to use sandbox's macOS-native, Docker-less local mode,
   **When** the developer attempts to target a remote with it, **Then** sandbox refuses
   clearly rather than attempting something that cannot work remotely.

---

### Edge Cases

- What happens when a developer runs a deploy but the remote target is unreachable (VPS
  down, network issue, SSH auth failure)? The deploy must fail clearly, naming the
  actual cause, and must NOT leave the remote in a half-updated state if a prior deploy
  had partially applied.
- What happens if two people deploy to the same remote target for the same project at
  around the same time? Out of scope for this feature (Assumptions) — the remote is
  understood to be single-developer per the resolved design; this is not a scenario the
  feature needs to protect against yet.
- What happens when provisioning is attempted a second time against a remote that's
  already provisioned? It must be safe to re-run — either it recognizes the remote is
  already set up and confirms that, or it re-applies the same setup without creating
  duplicate or broken state.
- What happens to an already-running remote instance if its remote target is removed
  from sandbox's configuration? The instance itself keeps running on the VPS (removal is
  a local bookkeeping action, not a teardown command) — the developer is told this
  plainly so they aren't surprised later by an orphaned instance still consuming
  resources.
- What happens when a developer tries to deploy to a remote target that was never
  provisioned? Deploy must fail with a clear message pointing at provisioning as the
  missing step, not a confusing lower-level error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to register a named remote VPS target by providing an
  SSH connection string, and have that registration persist across sandbox sessions.
- **FR-002**: Users MUST be able to list their registered remote targets along with
  whether each is currently reachable.
- **FR-003**: Users MUST be able to remove a registered remote target; removal MUST NOT
  attempt to tear down or affect any instance already running on that VPS.
- **FR-004**: Users MUST be able to provision a registered remote target with a single
  action that installs everything required to run sandbox instances there, without the
  user manually running install commands on the remote themselves.
- **FR-005**: Provisioning MUST be safe to run more than once against the same remote
  target without creating broken or duplicated state.
- **FR-006**: Users MUST be able to deploy their current local project state — both
  committed and uncommitted changes, including files not yet tracked by version control
  — to a remote target with a single action.
- **FR-007**: Each deploy MUST result in the remote's code reflecting EXACTLY the local
  working tree at the moment of that deploy — no leftover state from a previous deploy's
  uncommitted changes may persist once superseded by a newer deploy.
- **FR-008**: Deploying MUST succeed even when the local project's current branch has
  never been pushed to any external code-hosting service.
- **FR-009**: A deploy that fails partway through (e.g. the remote becomes unreachable)
  MUST fail clearly and MUST NOT leave the remote in an inconsistent, half-updated state
  that a later successful deploy can't cleanly recover from.
- **FR-010**: Users MUST be able to boot and use a full sandbox instance on a
  provisioned, deployed remote target, with the same set of operations (running
  commands, reading logs/files, taking screenshots, running tests) available as for a
  local instance.
- **FR-011**: Every such operation against a remote instance MUST reflect that
  instance's actual current state on the remote — operations MUST NOT silently return
  stale, empty, or local-machine data when targeting a remote instance.
- **FR-012**: A single project MUST be able to have a local instance and a remote
  instance running at the same time ONLY when the user has explicitly asked for two
  distinctly-identified instances; the system MUST NEVER silently run or conflate a
  local and a remote instance under what looks like a single, unqualified identity.
- **FR-013**: The system MUST refuse cleanly, with a clear explanation, when a user
  attempts to target a remote with a project configured for a local-only runtime mode
  that has no remote equivalent.
- **FR-014**: The system MUST NOT expose the remote VPS's container-management socket,
  or any unauthenticated management interface, to the public internet at any point in
  registration, provisioning, or normal use.
- **FR-015**: Existing local-only usage of sandbox (no remote registered or targeted)
  MUST behave identically to today — this feature MUST introduce zero behavior change
  for users who never opt into a remote.

### Key Entities

- **Remote target**: A named, user-registered VPS that sandbox can provision and deploy
  to. Attributes: a user-chosen name, connection details, and whether it has been
  successfully provisioned. Secrets/credentials associated with it are never displayed
  back to the user once stored.
- **Deploy**: A one-time, on-demand action that transfers a local project's current
  state (committed + uncommitted) to a specific remote target. Not a persistent or
  ongoing process — each deploy is a discrete, completed event with its own success/
  failure outcome.
- **Remote instance**: A sandbox instance (the same concept that exists today for local
  projects) whose runtime happens to live on a remote target rather than the local
  machine. Carries the same identity/behavior guarantees as a local instance from the
  user's perspective, distinguished only by where it actually runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer with SSH access to a fresh VPS can go from "nothing set up" to
  a running, usable remote sandbox instance using only sandbox's own commands — no
  manual SSH session to install anything themselves.
- **SC-002**: After deploying, 100% of the developer's local uncommitted changes (edits
  to tracked files and brand-new untracked files alike) are present and correct on the
  remote — verified by direct comparison of local and remote file contents.
- **SC-003**: Running the same representative operation (a command execution, a log
  read, a screenshot) against a freshly deployed remote instance returns results that
  reflect the just-deployed code, not stale or placeholder data, on 100% of attempts in
  normal operation.
- **SC-004**: Existing local-only sandbox workflows show zero measurable behavior
  change (same commands, same output, same performance) when no remote is registered or
  targeted.
- **SC-005**: A user can always determine, from a single command, whether a given remote
  target is currently reachable and whether it has been provisioned — without needing to
  attempt an actual deploy or instance boot just to find out.

## Assumptions

- The remote VPS is a persistent, already-running server the user manages themselves
  (its own provider account, its own uptime) — this feature never starts, stops, or
  otherwise manages the VPS's power state.
- One remote target is used by one developer at a time. Multiple developers sharing one
  VPS concurrently, with per-user isolation, is explicitly out of scope for this
  feature — it is a substantially larger effort noted as future work in
  `docs/remote-hosting-prd.md`.
- Deploying is a manual, on-demand action the user takes deliberately — there is no
  continuous or automatic background synchronization of local changes to a remote. A
  remote's code is only ever as current as the user's last deploy.
- The user's local machine has outbound network reachability to the remote target (the
  reverse — the remote initiating a connection back to the user's local machine — is not
  assumed or relied upon, since local machines are commonly behind NAT/firewalls).
- A local-only, Docker-less runtime mode that exists today for one specific host
  platform has no remote equivalent and is out of scope for remote targeting entirely.
- Requires the user to have valid SSH credentials to their own VPS; sandbox does not
  provision or sell VPS capacity itself.
- This feature's user-facing surface (registering/provisioning/deploying/running
  instances) is the same regardless of the underlying transport, security boundary, or
  co-location strategy chosen to implement it — those are planning-level decisions, not
  part of this specification.

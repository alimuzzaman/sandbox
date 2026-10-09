# Feature Specification: Remote Runtime Revision Coexistence

**Feature Branch**: `061-remote-runtime-revision-coexistence`

**Created**: 2026-10-09

**Status**: Draft

**Input**: `specs/061-remote-runtime-revision-coexistence/prd.md` (READY FOR SPECKIT; independent PASS 2026-10-09; feedback e41bef3b, be5a6353, f475f422, 2a88da50, 3263de8e)

## Clarifications

### Session 2026-10-09

Root decisions by Claude Opus 5.5 under delegated authority (user; no
sub-agents, so not routed to Fable). The user may overturn any of them.

- Q: What identifies a pin holder? → A: The controller's local Sandbox home identity plus the checkout path; the same checkout on the same machine is the same holder, so its next strict invocation renews rather than adds.
- Q: How is the acknowledgment to break a pin given? → A: A repeatable `--break-pin HOLDER` naming each unexpired pin's holder id as listed by the plan; a confirmed migrate or non-holder release refuses unless every unexpired unbroken pin is named.
- Q: Where are the protocol numbers declared? → A: In one checked-in declaration that ships with the runtime, so the runtime revision digest already covers it; the runtime reports both numbers in `remote service status`.
- Q: What happens to strict callers against a runtime that declares a protocol but cannot store pins? → A: That combination cannot ship: pins and the protocol declaration land in the same runtime revision, so any runtime that declares a protocol understands pins; an undeclared runtime fails strict callers with `strict_pin_unverifiable`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Compatible checkouts share one remote (Priority: P1)

Several local Sandbox checkouts at different revisions operate the same remote.
When a checkout's declared control protocol is served by the installed
runtime, its remote commands run without a migrate, and no other checkout is
affected. Only a real protocol incompatibility refuses.

**Why this priority**: This is the cost the feature exists to remove: every
migrate today breaks every other session's preflight, and the only remedy
(another migrate) breaks the next one.

**Independent Test**: Install a runtime from revision M on a test remote; from
a checkout at revision F that differs by an unrelated change and declares the
same protocol, run a remote test job and a hosting status command; then do the
same from M. Both succeed with zero migrates and zero mismatch refusals.

**Acceptance Scenarios**:

1. **Given** a remote running revision M and a checkout at revision F that
   declares the same control protocol, **When** F runs a remote test job,
   **Then** the job is accepted, the preflight reports verdict `compatible`,
   and no migrate is proposed.
2. **Given** the same remote, **When** M and F each run a hosting status
   command in either order, **Then** both succeed and neither changes the
   installed runtime.
3. **Given** a checkout whose spoken protocol is newer than the installed
   runtime's, **When** it runs `sb host apply`, **Then** it is refused with
   verdict `protocol_newer`, naming installed revision and protocol, local
   revision and protocol, and a complete migrate command for the named remote;
   nothing on the remote changes.
4. **Given** a checkout whose spoken protocol is older than the runtime's
   oldest served protocol, **When** it runs any remote command covered by the
   verdict, **Then** it is refused with verdict `protocol_too_old` in the same
   shape.
5. **Given** a non-strict compatible controller at a revision different from
   the installed one, **When** it runs remote WP-CLI and then
   `remote service status`, **Then** remote WP-CLI is refused
   `runtime_revision_mismatch` in the shared refusal shape with the complete
   migrate remedy, and `remote service status` succeeds and reports
   `compatible`.
6. **Given** a remote whose runtime predates protocol declarations, **When** a
   new controller operates it, **Then** the controller applies exact-match
   only, says so explicitly, offers the migrate remedy, and registers no pin.

---

### User Story 2 - Strict mode keeps the production guarantee and is visible (Priority: P1)

A production deploy wrapper selects strict mode. Its commands still require
the exact installed revision, and each strict invocation registers or renews a
time-limited pin on the remote so that every other controller can see that the
installed revision is depended on.

**Why this priority**: Relaxing the exact check for everyone would silently
remove the production wrapper's guarantee; strict mode must ship together with
the relaxation.

**Independent Test**: With `SANDBOX_STRICT_RUNTIME=1`, run two remote commands
at the installed revision and inspect `remote service status`; then run one at
a different revision; then let the pin expire.

**Acceptance Scenarios**:

1. **Given** no pin and a strict caller at the installed revision, **When** it
   runs a remote command, **Then** a pin is registered with holder identity,
   checkout path, revision, purpose derived from the command and checkout
   path, registration time, and expiry one hour later.
2. **Given** that pin, **When** the same holder runs another strict command,
   **Then** the pin is renewed (expiry moves to one hour after this
   invocation), not duplicated.
3. **Given** a strict caller at a revision different from the installed one,
   **When** it runs a remote command, **Then** it is refused, registers no pin,
   and, if its earlier pin was marked broken, reports who broke it and when.
4. **Given** strict mode selected against a remote that is degraded,
   unreachable or predates pins, **When** any remote command runs, **Then** it
   fails closed with `strict_pin_unverifiable` and a remedy and never proceeds
   under the compatible verdict.
5. **Given** a pin with no further strict invocations, **When** one hour
   passes, **Then** the pin is reported as expired and no longer blocks a
   migrate.
6. **Given** the local Sandbox MCP server process started with
   `SANDBOX_STRICT_RUNTIME=1`, **When** any remote tool call runs through it,
   **Then** that call is strict exactly as the CLI flag would make it.
7. **Given** a pin, **When** its holder runs `remote pin release`, **Then** the
   pin is removed at once; **When** another controller runs it, **Then** it is
   refused unless the same explicit acknowledgment required to migrate over a
   pin is given.

---

### User Story 3 - Migrate shows and protects what it would break (Priority: P2)

An operator planning a migrate sees every unexpired strict pin the new
revision would break, plus one line naming the protocol range of
compatible-mode controllers that stop being served. A confirmed migrate over
an unexpired pin needs an explicit acknowledgment, and the broken pin records
who broke it.

**Why this priority**: Makes the remaining breaking migrates deliberate and
attributable; depends on the pins from User Story 2.

**Independent Test**: Register a strict pin from one checkout; from another
checkout at a different revision, run migrate plan, dry run, confirmed without
acknowledgment, and confirmed with acknowledgment; inspect the remote after
each.

**Acceptance Scenarios**:

1. **Given** an unexpired strict pin on revision M, **When** a checkout at
   revision N runs `sb remote service migrate <remote>` (plan or dry run),
   **Then** the plan lists the pin under "would break" with holder, checkout
   path, pinned revision, purpose, registered and expiry times, and one line
   stating that compatible-mode controllers below protocol P (the oldest N
   serves) stop being served; nothing changes.
2. **Given** the same state, **When** the migrate runs with `--confirm` but
   without acknowledgment, **Then** it is refused with a typed result naming
   the pin and the acknowledgment needed, and makes zero writes to the
   installed runtime and its service record.
3. **Given** the same state, **When** the migrate runs with `--confirm` and the
   acknowledgment, **Then** it proceeds, the pin is marked broken with the
   installing controller's identity and time, and the strict holder's next
   preflight names that controller and time.
4. **Given** a plan produced with no pins, **When** a strict caller registers a
   pin before the migrate is confirmed, **Then** the confirmed migrate
   re-evaluates pins at the moment it acts and refuses without acknowledgment.
5. **Given** a pin marked broken, **When** a later migrate is planned, **Then**
   the broken pin is reported as broken and requires no acknowledgment.
6. **Given** a migrate to an older protocol, **When** it is planned, **Then**
   the plan reports the controllers it would make `protocol_too_old` and the
   pins it would break; downgrade policy is otherwise unchanged.

---

### User Story 4 - Every mismatch refusal gives a remedy that runs (Priority: P2)

Any caller refused for a runtime difference, on CLI or MCP, including
refusals the remote raises and the controller relays, receives one shared
shape: installed and local revision and protocol, the verdict and reason, and
remedies as complete commands for the named remote that the CLI accepts as
written.

**Why this priority**: Today's remedies are rejected by the CLI or contain
placeholders, which has already cost sessions.

**Independent Test**: Trigger each refusal kind in the fixed controller-state
set and paste every emitted remedy into the CLI.

**Acceptance Scenarios**:

1. **Given** any mismatch refusal, **When** the caller pastes each remedy
   verbatim, **Then** the CLI accepts it; no remedy contains a placeholder or
   an option the CLI rejects.
2. **Given** a remote whose last migrate ended
   `remote_service_rollback_indeterminate`, **When** any caller runs a remote
   command covered by the verdict, **Then** the verdict is `unknown`, the
   command is refused, no pin is marked broken, and the remedy names
   `sb remote service status <remote>` and `sb remote service diagnostics
   <remote>` with the remote's real name.
3. **Given** recovery create against a mismatched runtime, **When** it is
   refused, **Then** it reports the shared mismatch shape, not a generic
   observation failure.

---

### User Story 5 - Registration is scoped per remote (Priority: P3)

A hosted apply on one remote holds only that remote's registration lock.
Registering, re-registering, listing or inspecting any other remote is not
delayed by it.

**Why this priority**: Independent of the verdict work and small, but it
removes a 30-second wait-then-fail seen during long applies.

**Independent Test**: Hold remote A's registration lock as a hosted apply
does; run `remote list`, `remote up B --confirm` and
`remote service status B`; then attempt a registration change to A.

**Acceptance Scenarios**:

1. **Given** a hosted apply running on remote A, **When** another session runs
   `sb remote list`, `sb remote up B --confirm` or
   `sb remote service status B`, **Then** each completes without waiting on A.
2. **Given** the same apply, **When** another session changes A's
   registration, **Then** it waits at most 30 seconds and, on timeout, reports
   `remote_registration_busy` with the holder.
3. **Given** two sessions changing the registrations of two different remotes
   at the same time, **When** both finish, **Then** both changes persist and
   neither overwrites the other.

---

### Edge Cases

- A pin whose holder session ended without releasing it stays listed with its
  holder and still requires acknowledgment until it expires; nothing infers the
  holder is gone.
- A pin that expires while its holder's deploy is still running no longer
  blocks a migrate; the holder's exact check still runs at each preflight, so
  a migrate landing mid-deploy makes the holder's next step refuse.
- A strict invocation at the installed revision whose pin registration fails
  for any reason refuses with `strict_pin_unverifiable`; it never runs
  unpinned.
- Pin expiry requested above four hours is capped at four; renewals never
  extend a pin past four hours from the renewing invocation.
- One server hosting two remote Sandbox homes: pins, verdicts and migrate
  plans are per home; migrating one neither lists nor breaks the other's pins.
- After an indeterminate rollback the installed revision is unknown, so no pin
  is marked broken and every verdict is `unknown` until status is
  determinate.
- Checks that bind a remote-written artifact to its writer (deployment
  receipts, delivery traces, revision-keyed staging helpers) keep refusing a
  revision difference even when the verdict is `compatible`.
- Remote WP-CLI signatures and cleanup-routine enable keep refusing a revision
  difference in version one, in the shared refusal shape.
- Pins never carry secrets; holder identity is secret-free.

## Requirements *(mandatory)*

### Functional Requirements

**Protocol declaration and verdict**

- **FR-001**: Each Sandbox checkout MUST declare two control-protocol
  numbers: the version it speaks and the oldest version it serves when
  installed as a runtime. The installed runtime MUST report both to
  controllers.
- **FR-002**: The system MUST compute one compatibility verdict from those
  declarations only: `compatible` when the controller's spoken version lies
  between the runtime's oldest served and spoken versions inclusive;
  `protocol_newer` when above the runtime's spoken version;
  `protocol_too_old` when below its oldest served version; `exact_only` when
  the runtime declares no protocol (compatible only on equal revision);
  `unknown` when the installed state is indeterminate. Runtime revision is
  reported as evidence, never used as the rule except for `exact_only` and
  strict mode.
- **FR-003**: Every controller-side check that today compares the
  controller's revision with the installed revision MUST consume that single
  verdict: workspace preflight, hosted apply, recovery materialization and
  create, remote resources commands, host memory, server capture, Postgres
  recovery, and the cleanup-broker install. No second compatibility rule may
  exist beside it.
- **FR-004**: Checks binding a remote-written artifact to the runtime that
  wrote it (deployment receipts, delivery traces, revision-keyed staging
  helpers) MUST remain exact-revision.
- **FR-005**: Remote WP-CLI request signatures and cleanup-routine enable
  MUST remain exact-revision in version one, and their refusal MUST use the
  shared refusal shape (FR-016) with a complete migrate remedy.
- **FR-006**: The declared protocol version MUST change whenever a
  controller-to-runtime transport payload or receipt shape changes; an
  architecture test MUST fail when such a shape changes without a protocol
  change.

**Strict mode and pins**

- **FR-007**: Strict mode MUST be selectable per invocation by the flag
  `--strict-runtime` or the environment setting `SANDBOX_STRICT_RUNTIME=1`;
  for the local MCP server, the environment setting on the server process
  applies to every remote tool call, with no per-call selection.
- **FR-008**: In strict mode the controller MUST require the exact installed
  revision, as today.
- **FR-009**: A strict invocation whose revision equals the installed one MUST
  register the holder's pin or renew it before its remote effect, with holder
  identity, checkout path, revision, purpose derived from the command and
  checkout path, registration time and expiry. Default expiry is one hour
  after the invocation, maximum four hours. A pin MUST replace the same
  holder's previous pin.
- **FR-010**: A refused strict invocation MUST register nothing. If the
  holder's pin is marked broken, the refusal MUST report who broke it and
  when.
- **FR-011**: A strict caller whose pin cannot be registered or verified
  (degraded, unreachable, or pin-unaware runtime) MUST fail closed with
  `strict_pin_unverifiable` and a remedy; it MUST NOT fall back to the
  compatible verdict.
- **FR-012**: `remote pin release` by the holder MUST remove the pin at once;
  release by any other controller MUST require the same `--break-pin HOLDER`
  acknowledgment as migrating over the pin.
- **FR-013**: Expired pins MUST be reported as expired and never honored;
  expiry is the only stale-holder detection. Pins MUST be bounded in number
  per holder (one) and MUST never carry secrets.
- **FR-014**: `remote service status` MUST list unexpired, expired and broken
  pins with their attributes.

**Migrate**

- **FR-015**: `remote service migrate` plan and dry run MUST list every
  unexpired pin the target revision would break, individually, and one line
  naming the protocol range of compatible-mode controllers the target stops
  serving. A confirmed migrate MUST re-evaluate pins at the moment it acts and
  refuse, with zero writes to the installed runtime and its service record,
  over any unexpired unbroken pin unless the caller acknowledges each one
  with `--break-pin HOLDER`. With acknowledgment, each such pin MUST be marked broken
  with the installing controller's identity and time. A broken pin MUST NOT
  require acknowledgment again. After an indeterminate rollback no pin is
  marked broken.

**Refusals and remedies**

- **FR-016**: Every mismatch refusal on CLI and MCP, including refusals the
  remote raises and the controller relays, MUST carry one shared shape:
  installed revision and protocol, local revision and protocol, verdict,
  reason, and at least one remedy.
- **FR-017**: Every remedy MUST be a complete command for the named remote,
  accepted by the CLI as written, with no placeholder. A test MUST parse every
  emitted remedy against the CLI's argument definitions.
- **FR-018**: An `unknown` verdict MUST refuse with remedies
  `sb remote service status <remote>` and
  `sb remote service diagnostics <remote>`, filled in with the real name.
- **FR-019**: Against a runtime that predates protocol declarations, the
  controller MUST report exact-match mode explicitly, offer the migrate
  remedy, and register no pin.

**Registration lock**

- **FR-020**: The local remote-registration lock MUST be per remote. A hosted
  apply MUST hold only its own remote's lock; registering, re-registering,
  listing or inspecting any other remote MUST NOT wait on it.
- **FR-021**: A wait on the same remote's registration lock MUST be bounded at
  30 seconds and, on timeout, report `remote_registration_busy` with the
  holder.
- **FR-022**: Concurrent registration changes to different remotes MUST both
  persist; neither may be lost or overwritten.

**Documentation**

- **FR-023**: `docs/remote-hosting.md`, `docs/remote-job-runtime.md` and the
  CLAUDE.md gotcha requiring `runtime_revision_state: match` MUST be updated
  to describe the verdict, strict mode and pins.

### Key Entities

- **Control-protocol declaration**: spoken version and oldest served version
  of a checkout or installed runtime.
- **Compatibility verdict**: one of `compatible`, `protocol_newer`,
  `protocol_too_old`, `exact_only`, `unknown`, with reason and both sides'
  revision and protocol.
- **Strict pin**: holder identity, checkout path, pinned revision, purpose,
  registered time, expiry, state (`active`, `expired`, `broken`), and for a
  broken pin the breaking controller and time. One per holder per remote
  Sandbox home.
- **Mismatch refusal**: the shared refusal shape (FR-016) with remedies.
- **Registration lock**: one per registered remote; holder reported on
  timeout.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Two checkouts at different revisions declaring the same protocol
  each run a remote test job and a hosting status command against one remote,
  in either order, with zero migrates and zero mismatch refusals.
- **SC-002**: For the fixed controller-state set (same protocol, newer
  protocol, older than minimum, undeclared runtime, indeterminate status,
  strict at a different revision), every check named in FR-003 returns the
  expected verdict for each state, and every check named in FR-004 and FR-005
  refuses a revision difference.
- **SC-003**: A strict invocation with no prior pin registers one expiring in
  one hour; a later one renews it; with no further strict invocation it lapses
  at expiry and no longer blocks a migrate.
- **SC-004**: Migrate plan, dry run and confirmed-without-acknowledgment each
  make zero writes to the installed runtime and its service record when an
  unexpired pin exists; 100% of pins registered between plan and confirm cause
  a refusal without acknowledgment.
- **SC-005**: 100% of strict-mode invocations against an undeclared,
  degraded or unreachable runtime fail closed with `strict_pin_unverifiable`.
- **SC-006**: 100% of mismatch refusals carry both revisions, both protocols,
  a verdict and at least one remedy, and 100% of remedies are accepted by the
  CLI as written with no placeholder.
- **SC-007**: While a hosted apply holds remote A for fifteen minutes,
  `remote list`, `remote up B --confirm` and `remote service status B` each
  complete without waiting on A; a wait on A's registration never exceeds 30
  seconds.
- **SC-008**: Two concurrent registration changes to different remotes both
  persist in 100% of runs of the concurrency test.

## Assumptions

- The remote control protocol can carry a version field without a breaking
  change; the migrate that installs this feature is the last
  exact-match-only migrate for a remote.
- A remote Sandbox home owns one installed runtime and one pin set, even when
  two homes share a server.
- Lenzora's deploy wrapper adds `SANDBOX_STRICT_RUNTIME=1` to its invocation
  environment before this feature reaches the remote it deploys to; no other
  wrapper change is required. Bumping its Sandbox pin follows the remote
  install protocol and needs the owner's approval.
- Feature 060 owns hosting operation locks; this feature changes only the
  local remote-registration lock and must not redefine 060's locks.
- Out of scope, as follow-ups: remote runtime history, compatible-mode
  controller registration, capability-level degradation, protocol-verdict
  remote dispatch. Out of scope entirely: side-by-side runtimes, changes to
  migrate install or rollback, automatic migrate, third-party MCP clients,
  downgrade policy.

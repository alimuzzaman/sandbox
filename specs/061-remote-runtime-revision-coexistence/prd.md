# Product Requirements Draft: Remote Runtime Revision Coexistence

**Status**: Refined

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "Let several local Sandbox checkouts at different revisions operate one remote without each one's runtime migrate breaking the others: a declared compatibility rule instead of exact-revision match, a migrate plan that names the controllers it would break, and mismatch output that gives the exact command"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); refined by Claude Opus 5.5 on 2026-10-09 against an independent Opus readiness review (verdict `REOPEN`) and Fable decisions delegated by the user, with every cited code fact re-read on `origin/latest` `1325a8b`. Evidence: the feedback backlog, `docs/remote-hosting.md`, `docs/remote-job-runtime.md`, `TODO.md`, the Lenzora deploy wrapper, and the 2026-10-08 roadmap.

**Final Validation**: `PENDING` — fresh independent readiness review after the 2026-10-09 revision

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A remote runs exactly one installed Sandbox runtime (the controller service
behind `/mcp`, the staging helper, the durable-job supervisor). Every local
checkout that talks to that remote compares its own runtime revision with the
installed one and refuses on any difference: workspace commands require
`runtime_revision_state=match`, `sb host apply` refuses with
`remote_runtime_revision_mismatch`, remote resources commands, server capture,
recovery materialization and the cleanup-broker install each refuse on a
mismatch, and recovery create reports it only as a generic
`materialization_observe_failed` (feedback `3263de8e`).

The runtime revision is a content digest of the shipped control surface:
`VERSION`, the `sb` entry point, one privileged-helper provisioning script, and
every Python file under the Sandbox package and the bundled WordPress MCP
server. Documentation commits do not change it; any Python change does,
including fixes that do not touch the controller-to-runtime protocol.

The operator does not run one checkout. On 2026-10-06 and 2026-10-07 there
were thirteen Sandbox worktrees on the machine, several active at once: the
main checkout on `latest`, a pinned deploy checkout for Lenzora
(`approved-2751-migration`), feature branches for the nginx front door, the
server migration, and feedback fixes. Each of them, when it needed the remote,
ran `sb remote service migrate` to install its own revision. Each migrate made
every other checkout's preflight fail (feedback `e41bef3b`, `be5a6353`, both
blocked). The record on 2026-10-07 is a 45-minute hold and two extra migrates,
coordinated by hand across sessions, and a production deploy whose preflight
failed because an unrelated session had migrated in a workspace fix.

Lenzora's deploy wrapper pins its own local Sandbox checkout to one commit and
requires that checkout to be clean; it does not compare against the installed
remote runtime itself. The guarantee that the installed runtime equals its
checkout comes from Sandbox's own exact-revision gates, so relaxing those
gates for everyone would silently remove the wrapper's guarantee.

Migrate warns only generically. When the installed revision differs from the
local one, its plan says that other checkouts or deploys pinned to the
installed revision will fail, but it cannot name them, because nothing records
which controllers depend on the installed revision. The mismatch refusals on
the other side do not give a working remedy: `sb host apply` tells the caller
to run `./sb remote up --remote NAME`, which the CLI rejects because the remote
name is positional (feedback `f475f422`, still emitted); the workspace remedy
contains a `<name>` placeholder that cannot be pasted; recovery says only
"sync the remote runtime revision". Finding which checkout matches the remote
required computing revisions across all thirteen worktrees by hand until
`remote service status` started reporting `runtime_revision_state` (feedback
`2a88da50`, resolved).

Separately, the local registration lock is registry-wide: a hosted apply holds
it for its whole build-and-deliver phase, so registering or re-registering a
different remote during that apply waits for the lock's 30-second budget and
then fails with `remote_registration_busy`.

The exact-match rule was chosen for safety: a controller and a runtime that
disagree about the control protocol can mis-stage a source tree or misread a
receipt. But the rule is applied to a digest that changes with every Python
change, so it refuses far more than it protects, and the only remedy it offers
(migrate) is the thing that breaks the next session. The cost is paid on every
parallel session that touches a remote, and it grows with the number of
worktrees and with the number of remotes that production wrappers depend on.

## Users and Desired Outcomes

- **Agent session on a feature branch**: can run remote tests, exec, status and
  hosting commands against a remote whose installed runtime is a different but
  compatible revision, without migrating and without breaking anyone else.
- **Production deploy wrapper (Lenzora)**: by selecting strict mode, keeps the
  exact-revision guarantee it relies on today, is never silently served by a
  different runtime, and is visible to any migrate that would break it.
- **Operator running a migrate**: sees, before confirming, every registered
  strict pin the new revision would break, plus which protocol range of
  compatible-mode controllers stops being served, and can choose to proceed or
  stop.
- **Any caller that hits a mismatch**: receives the installed revision, the
  local revision, the compatibility verdict and its reason, and remedy
  commands that work when pasted.

## Goals

- Compatibility between a local controller and an installed remote runtime is
  decided by a declared control-protocol version, not by runtime revision
  equality. Two checkouts whose protocol declarations are compatible both
  operate the remote without a migrate.
- Every controller-side check that today compares the local revision with the
  installed revision consumes that single compatibility verdict; no second rule
  exists beside it.
- Exact-revision matching remains available as strict mode, selected per
  invocation by a declared flag or environment setting. A strict caller
  registers a pin with the remote so other callers can see it; the pin is
  visibility, and the exact check is the guarantee.
- `remote service migrate` plan and dry run list every registered strict pin
  that the target revision would break, plus one line naming the protocol range
  of compatible-mode controllers that stop being served; the confirmed apply
  refuses to proceed over an unexpired strict pin unless the caller explicitly
  acknowledges breaking it.
- Every mismatch refusal across CLI and MCP names: installed revision and
  protocol, local revision and protocol, the compatibility verdict and reason,
  and each remedy as a complete command that runs as written.
- Registering, re-registering or inspecting one remote is not blocked by a
  hosted apply on another remote.

## Non-Goals

- Running two installed runtimes side by side on one remote. One remote has
  one installed runtime.
- Changing what a migrate installs or how it rolls back. The existing
  install, repair and `remote_service_rollback_indeterminate` behavior stands.
- Deciding compatibility for third-party MCP clients or the WordPress-side
  bridge; this feature covers the Sandbox controller to Sandbox runtime
  protocol only.
- Relaxing checks that bind a remote-written artifact to the runtime that
  wrote it (deployment receipts, delivery traces, cleanup-routine records,
  WP-CLI request signatures). Those stay exact-revision.
- Automatic migrate on mismatch. A migrate stays a confirmed operation.
- Per-target hosting locks and state partitioning (feature 060).
- Downgrade policy. Whether a remote may be migrated to an older revision stays
  as it is; this feature only reports what such a migrate would break.
- **Follow-up "remote runtime history"**: a bounded, secret-free record of who
  installed which revision from which checkout and when, with the pins at that
  time.
- **Follow-up "compatible-mode controller registration"**: registering every
  compatible-mode controller with the remote so a migrate plan can name each one
  individually. Version one registers strict pins only.
- **Follow-up "capability-level degradation"**: refusing a single command whose
  capability the runtime lacks while the controller is otherwise compatible.
  Version one decides on protocol version alone.

## Product Scenarios

### Scenario 1 — Compatible checkouts share one remote

- **Starting state**: `xcloud-london` runs a runtime installed from the main
  checkout at revision M. A feature-branch worktree is at revision F, which
  differs from M by one unrelated CLI fix and declares the same control
  protocol.
- **User action**: From F, run `sb test fast --remote xcloud-london`.
- **Expected outcome**: The job is accepted. The preflight reports
  `compatible` with the installed revision, and no migrate is proposed or
  needed. The main checkout continues to operate the remote unchanged.

### Scenario 2 — Incompatible checkout gets an exact remedy

- **Starting state**: As above, but F declares a newer control protocol that M
  does not serve.
- **User action**: From F, run `sb host apply`.
- **Expected outcome**: A typed refusal names installed revision M and its
  protocol, local revision F and its protocol, the reason (`protocol_newer`),
  and the migrate remedy as a complete command naming the remote. Nothing on
  the remote changes.

### Scenario 3 — Migrate plan names what it would break

- **Starting state**: Lenzora's deploy runs in strict mode and has registered a
  pin on revision M for `xcloud-london`. Other checkouts operate the remote in
  compatible mode and are not registered.
- **User action**: From a checkout at revision N, run
  `sb remote service migrate xcloud-london` (plan or dry run).
- **Expected outcome**: The plan lists the pin (holder identity, checkout
  path, pinned revision, purpose, registered when, expires when) under "would
  break", and one line stating that every compatible-mode controller below
  protocol P (the minimum N serves) stops being served. No change is made.

### Scenario 4 — Confirmed migrate over a strict pin

- **Starting state**: As in Scenario 3.
- **User action**: Run the migrate with `--confirm`.
- **Expected outcome**: Refused with a typed result naming the pin and the
  acknowledgment the caller must give to proceed. With the acknowledgment, the
  migrate proceeds, the pin is marked broken with the installing controller's
  identity, and the strict caller's next preflight reports who broke the pin
  and when, not just a mismatch.

### Scenario 5 — Strict pin registration and release

- **Starting state**: A deploy wrapper wants exact-revision behavior.
- **User action**: It runs with strict mode selected, registers a pin for its
  remote with an expiry and a purpose string, deploys, and later releases it.
- **Expected outcome**: The pin is visible in `remote service status` and in
  migrate plans while it exists; an expired pin is reported as expired, not
  honored. Release by the holder is immediate; release by another controller
  requires the same explicit acknowledgment as migrating over it.

### Scenario 6 — Strict pin cannot be registered or verified (negative)

- **Starting state**: A caller runs with strict mode selected against a remote
  that is degraded, or whose runtime predates pins.
- **User action**: Any remote command.
- **Expected outcome**: The command fails closed with
  `strict_pin_unverifiable` and a remedy; it never falls back to compatible
  mode. Without strict mode the same remote is served under the normal verdict.

### Scenario 7 — Stale or expiring pin (negative)

- **Starting state**: A pin's holder session ended without releasing it; a
  second pin expires while its holder's deploy is still running.
- **User action**: Another checkout plans a migrate; the deploying holder
  continues.
- **Expected outcome**: The unexpired stale pin is listed with its holder and
  still requires acknowledgment; nothing infers that its holder is gone. The
  pin that expired mid-deploy no longer blocks a migrate, but the holder's
  exact-revision check still runs at each of its preflights, so a migrate that
  lands mid-deploy makes the holder's next step refuse rather than run against
  a different runtime.

### Scenario 8 — Pin registered while a migrate is planned (negative)

- **Starting state**: A migrate plan was produced with no pins listed.
- **User action**: A strict caller registers a pin; then the migrate is
  confirmed.
- **Expected outcome**: The confirmed apply re-evaluates pins at the moment it
  acts, sees the new pin, and refuses without acknowledgment exactly as in
  Scenario 4.

### Scenario 9 — Older-protocol migrate and indeterminate rollback (negative)

- **Starting state**: A strict pin and compatible-mode controllers exist. A
  checkout whose protocol is older than theirs plans a migrate; separately, a
  migrate ends in `remote_service_rollback_indeterminate`.
- **User action**: Plan the older migrate; run any command after the
  indeterminate rollback.
- **Expected outcome**: The older migrate's plan reports the controllers it
  would make `protocol_too_old`-incompatible and the pins it would break, and
  migrate policy for downgrades is otherwise unchanged. After an indeterminate
  rollback, pins are not marked broken, because the installed revision is not
  known; every caller gets an `unknown` verdict and a refusal until status is
  determinate.

### Scenario 10 — Registration is not blocked by another remote's apply (negative today)

- **Starting state**: A hosted apply to `scaleway-sandbox` is seventeen
  minutes into its build.
- **User action**: Another session runs `sb remote up xcloud-london --confirm`,
  `sb remote list`, or `sb remote service status xcloud-london`.
- **Expected outcome**: Each completes within its normal bounds. Only an
  operation on the same remote's registration waits, that wait is bounded, and
  a timeout reports the holder.

### Scenario 11 — Every remedy runs as written (negative)

- **Starting state**: Any mismatch refusal.
- **User action**: The caller pastes the remedy command verbatim.
- **Expected outcome**: The command is accepted by the CLI and runs. A remedy
  the CLI would reject, or one that contains a placeholder, is a defect.

### Scenario 12 — Old runtime, new controller (negative)

- **Starting state**: A remote still runs a runtime that predates protocol
  declarations.
- **User action**: A new controller operates it.
- **Expected outcome**: The controller treats the undeclared runtime as
  exact-match only (today's rule), says so, and offers the migrate remedy. No
  guess about compatibility is made and no pin is registered.

### Scenario 13 — Two Sandbox homes on one host (negative)

- **Starting state**: One server hosts two remote Sandbox homes, each
  registered as its own remote with its own installed runtime.
- **User action**: A strict caller pins one; another checkout migrates the
  other.
- **Expected outcome**: Pins, verdicts and migrate plans are per installed
  runtime. Migrating one home neither lists nor breaks the other home's pins.

## Proposed Product Behavior

- Each Sandbox checkout declares a control-protocol version. Compatibility
  between a controller and an installed runtime is a function of those
  declarations only; the runtime revision is evidence, not the rule. The rule
  is monotone and explicit: a controller whose required protocol the runtime
  serves is `compatible`; one that requires a newer protocol is
  `protocol_newer`; one older than the runtime's minimum supported protocol is
  `protocol_too_old`; a runtime that does not declare a protocol yields
  exact-match only; an indeterminate status yields `unknown`.
- One verdict, two kinds of check. Any check that compares a controller's
  revision with the installed runtime revision consumes the single protocol
  verdict: workspace preflight, hosted apply, recovery materialization and
  create, remote resources commands, host memory, server capture, Postgres
  recovery, and the cleanup-broker install. Any check that binds a
  remote-written artifact to the runtime that wrote it stays exact: deployment
  receipts, delivery traces, cleanup-routine records, and WP-CLI request
  signatures.
- Strict mode is selected per invocation by a declared flag
  (`--strict-runtime`) or environment setting (`SANDBOX_STRICT_RUNTIME=1`). In
  strict mode the controller requires the exact installed revision, as today,
  and registers a pin (identity, checkout path, revision, purpose, expiry).
  The pin is visible to every other controller, listed by migrate plans, and
  protected by an explicit acknowledgment on confirmed migrate and on release by
  a non-holder. A strict caller whose pin cannot be registered or verified fails
  closed with `strict_pin_unverifiable`.
- Pins are bounded, replace the same holder's previous pin, expire, and never
  carry secrets. Expiry is the only stale-holder detection.
- Migrate plans list unexpired pins individually and state, in one line, the
  protocol range of compatible-mode controllers the target revision stops
  serving. The confirmed apply evaluates pins at the moment it acts.
- Mismatch refusals share one shape across CLI and MCP: installed and local
  revision and protocol, verdict, reason, and remedies as complete commands for
  the named remote.
- Registering or inspecting one remote is never blocked by a hosted apply on a
  different remote.

## Constraints and Dependencies

- Remote-side behavior takes effect only after a migrate that installs a
  runtime which declares a protocol and understands pins; until then the
  controller falls back to the exact-match rule for that remote (Scenario 12),
  and a strict caller fails closed (Scenario 6).
- Lenzora's deploy wrapper (Lenzora repository `scripts/deploy-sandbox.sh`,
  pinned to one clean Sandbox checkout commit) is the first strict-mode
  consumer. It keeps its exact guarantee by selecting strict mode through the
  declared flag or environment setting; no change to the wrapper's own logic is
  required.
- Feature 060 (per-target hosting operations) changes hosting locks; the
  registration-lock scoping here concerns the local remote registration lock
  and must be coordinated so the two features do not each redefine the other's
  lock.
- CLAUDE.md gotcha 23 and `docs/remote-hosting.md` require
  `runtime_revision_state: match` for remote resources commands; their text
  changes with this feature.
- Constitution and module boundaries: pins are new state and register through
  the project's explicit state contracts; consumers never read state files
  directly.
- Roadmap (`docs/roadmap/2026-10-08-next-features.md`) sizes 061 as small and
  first to specify; feature 063 depends on its compatibility verdict.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Runtimes per remote | Exactly one installed runtime; no side-by-side | Two control services on one host double resources and split ownership of state; compatibility solves the real problem at lower cost | Fable decision (delegated by user), 2026-10-09 |
| Compatibility rule | Declared control-protocol version alone in version one; revision is evidence only; capability-level degradation is a follow-up | Revision equality refuses unrelated Python changes; a protocol version refuses only real incompatibility and keeps the first version small | Fable decision (delegated by user), 2026-10-09 |
| Which checks relax | Controller-revision-versus-installed checks consume the one protocol verdict; checks binding a remote-written artifact (receipt, trace, cleanup record, WP-CLI signature) to its writer stay exact | Artifact binding protects integrity of records, not session compatibility | Fable decision (delegated by user), 2026-10-09 |
| Strict mode selection | Declared flag `--strict-runtime` or `SANDBOX_STRICT_RUNTIME=1`; no wrapper code change required; the pin is additive visibility, not the guarantee | Lenzora keeps its exact guarantee under a compatible default without depending on pin registration | Fable decision (delegated by user), 2026-10-09 |
| Unverifiable strict pin | Fail closed with `strict_pin_unverifiable` and a remedy | A strict caller must never be silently served under compatible rules | Fable decision (delegated by user), 2026-10-09 |
| Migrate over a pin | Plan lists it; confirmed apply refuses without an explicit acknowledgment; the broken pin records who broke it | Breaking a production pin must be a deliberate, attributable act | Fable decision (delegated by user), 2026-10-09 |
| Registration scope | Version one registers strict pins only; migrate plan adds one generic line for compatible-mode controllers below the served protocol | Keeps first contact free of remote writes and the feature small; individual registration is a follow-up | Fable decision (delegated by user), 2026-10-09 |
| Runtime history | Moved to follow-up "remote runtime history"; roadmap size "small" stands | Not needed to stop migrates breaking sessions | Fable decision (delegated by user), 2026-10-09 |
| Undeclared runtimes | Exact-match only until migrated | No inference about a runtime that cannot describe itself | Fable decision (delegated by user), 2026-10-09 |
| Remedy commands | Every emitted remedy is a complete command verified to run as written | A hint the CLI rejects has already cost a session | Fable decision (delegated by user), 2026-10-09 |
| Registration lock scope | Per remote, held only for registration mutations | An apply on one remote must not block registering another | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None blocking.

## Acceptance Outcomes

- Two checkouts at different revisions declaring the same protocol each run a
  remote test job and a hosting status command against one remote, in either
  order, with zero migrates and zero mismatch refusals.
- For a fixed set of controller states (same protocol, newer protocol, older
  than minimum, undeclared runtime, indeterminate status, strict at a
  different revision), every controller-side check named in Proposed Product
  Behavior returns the same expected verdict for each state, and every
  artifact-binding check still refuses a revision difference.
- With a strict pin registered, a migrate plan from another checkout lists
  that pin under "would break" with holder, checkout path and revision, and
  states the compatible-mode protocol range it stops serving. The plan, the dry
  run, and a confirmed migrate without acknowledgment each make zero writes to
  the installed runtime and its service record; with acknowledgment, the strict
  caller's next preflight names the breaking controller and time.
- A pin registered after a migrate plan and before its confirmation causes the
  confirmed apply to refuse without acknowledgment.
- A strict-mode caller against an undeclared or unreachable runtime fails
  closed with `strict_pin_unverifiable` in every case; none proceeds under the
  compatible verdict.
- Every mismatch refusal emitted by CLI or MCP contains installed revision,
  local revision, both protocols, a verdict, and at least one remedy; every
  remedy is accepted by the CLI as written and contains no placeholder.
- While a hosted apply runs on remote A for fifteen minutes,
  `remote list`, `remote up B --confirm`, and `remote service status B` each
  complete without waiting on A; a wait on the same remote's registration never
  exceeds 30 seconds and reports the holder on timeout.
- Against a runtime that predates this feature, a new controller reports
  exact-match mode explicitly and offers the migrate remedy; no pin is
  registered.

## Risks and Assumptions

- **Risk**: A compatibility rule that is too loose admits a controller that
  mis-stages a source tree. Mitigation: the protocol version is bumped on every
  change to a transport payload or receipt shape, enforced by a repository
  check, and the rule is monotone; artifact-binding checks stay exact.
- **Risk**: Pins that are never released accumulate and block migrates.
  Mitigation: mandatory expiry and visible holder identity; an expired pin is
  reported, not honored.
- **Risk**: Lenzora runs without strict mode selected and is served by a
  compatible but different runtime. Mitigation: strict mode is a declared
  setting the operator adds to the wrapper's invocation environment before
  this feature ships to the remote it deploys to.
- **Assumption**: The remote control protocol can carry a version field
  without a breaking change; the migrate that introduces it is the last
  exact-match-only migrate.
- **Assumption**: A remote Sandbox home is the unit that owns one installed
  runtime and one pin set, even when two homes share one server.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred (delegated
      decisions, recorded above; user may overturn).
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

Remaining: a fresh independent readiness review of this revision.

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->

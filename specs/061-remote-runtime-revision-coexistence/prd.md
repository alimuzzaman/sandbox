# Product Requirements Draft: Remote Runtime Revision Coexistence

**Status**: Refined

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "Let several local Sandbox checkouts at different revisions operate one remote without each one's runtime migrate breaking the others: a declared compatibility rule instead of exact-revision match, a migrate plan that names the controllers it would break, and mismatch output that gives the exact command"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); evidence from the feedback backlog, `docs/remote-hosting.md`, `docs/remote-job-runtime.md`, and `TODO.md` as of `origin/latest` `bce081f`. No independent readiness review has run.

**Final Validation**: `PENDING` — independent readiness review

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A remote runs exactly one installed Sandbox runtime (the controller service
behind `/mcp`, the staging helper, the durable-job supervisor). Every local
checkout that talks to that remote compares its own revision with the installed
one and refuses on any difference: workspace commands require
`runtime_revision_state=match`, `sb host apply` refuses with
`remote_runtime_revision_mismatch`, recovery materialization refuses with a
revision mismatch, and Lenzora's deployment wrapper pins a specific Sandbox
checkout and requires `local == installed` in its own preflight.

The operator does not run one checkout. On 2026-10-06 and 2026-10-07 there
were thirteen Sandbox worktrees on the machine, several active at once: the
main checkout on `latest`, a pinned deploy checkout for Lenzora
(`approved-2751-migration`), feature branches for the nginx front door, the
server migration, and feedback fixes. Each of them, when it needed the remote,
ran `sb remote service migrate` to install its own revision. Each migrate made
every other checkout's preflight fail (feedback `e41bef3b`, `be5a6353`, both
blocked). The record on 2026-10-07 is a 45-minute hold and two extra migrates,
coordinated by hand across sessions, and a production deploy wrapper whose
preflight failed because an unrelated session had fixed a workspace bug.

Migrate itself gives no warning. Its plan and dry run do not know that another
checkout or a deploy wrapper depends on the currently installed revision. The
mismatch refusal on the other side told the caller to run `./sb remote up
--remote NAME`, which argparse rejects (feedback `f475f422`); the working form
is positional. Finding which checkout matches the remote required computing
revisions across all thirteen worktrees by hand until `remote service status`
started reporting `runtime_revision_state` (feedback `2a88da50`, resolved).

The exact-match rule was chosen for safety: a controller and a runtime that
disagree about the control protocol can mis-stage a source tree or misread a
receipt. But the rule is applied to the Git revision, which changes with every
docs commit, so it refuses far more than it protects, and the only remedy it
offers (migrate) is the thing that breaks the next session. The cost is paid on
every parallel session that touches a remote, and it grows with the number of
worktrees and with the number of remotes that production wrappers pin.

## Users and Desired Outcomes

- **Agent session on a feature branch**: can run remote tests, exec, status and
  hosting commands against a remote whose installed runtime is a different but
  compatible revision, without migrating and without breaking anyone else.
- **Production deploy wrapper (Lenzora `./deploy`)**: can keep a strict pin on
  the exact installed revision for its own target, and is never broken by a
  migrate it did not ask for; if someone insists on migrating over its pin, the
  plan says so before anything changes.
- **Operator running a migrate**: sees, before confirming, every registered
  controller and pinned wrapper the new revision would stop serving, and can
  choose to proceed or stop.
- **Any caller that hits a mismatch**: receives the installed revision, the
  local revision, the compatibility verdict and its reason, and one command that
  works when pasted.
- **Reviewer of a remote incident**: can see which controller installed the
  current runtime, when, from which checkout, and which controllers were
  registered at the time.

## Goals

- Compatibility between a local controller and an installed remote runtime is
  decided by a declared control-protocol compatibility rule, not by Git
  revision equality. Two checkouts whose protocol declarations are compatible
  both operate the remote without a migrate.
- Exact-revision matching remains available as an explicit per-caller pin
  (strict mode) for callers that want it, such as production deploy wrappers.
  A pin is registered with the remote so that other callers can see it.
- `remote service migrate` plan and dry run list every registered controller
  and pin that the target revision would break, with the reason, and the
  confirmed apply refuses to proceed over a strict pin unless the caller
  explicitly acknowledges breaking it.
- Every mismatch refusal across CLI and MCP names: installed revision and
  protocol, local revision and protocol, the compatibility verdict and reason,
  and the exact positional command for each remedy (migrate, or switch to a
  compatible checkout). The command is validated against the real CLI surface.
- The local remote-registration lock is not held for the duration of an
  unrelated remote's hosted apply; registration of or status on one remote is
  not blocked by an apply on another.
- Remote runtime history is retained: who installed which revision from which
  checkout and when, and which controllers were registered then, bounded and
  secret-free.

## Non-Goals

- Running two installed runtimes side by side on one remote. One remote has
  one installed runtime.
- Changing what a migrate installs or how it rolls back. The existing
  install, repair and `remote_service_rollback_indeterminate` behavior stands.
- Deciding compatibility for third-party MCP clients or the WordPress-side
  bridge; this feature covers the Sandbox controller to Sandbox runtime
  protocol only.
- Automatic migrate on mismatch. A migrate stays a confirmed operation.
- Per-target hosting locks and state partitioning (feature 060).
- Automatic downgrade protection beyond refusing a strict pin; whether a
  remote may be downgraded at all is a migrate policy that stays as it is.

## Product Scenarios

### Scenario 1 — Compatible checkouts share one remote

- **Starting state**: `xcloud-london` runs a runtime installed from the main
  checkout at revision M. A feature-branch worktree is at revision F, two docs
  commits and one unrelated CLI fix ahead of M, declaring the same control
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
  and two pasteable commands: the migrate in its real positional form, and a
  hint naming a registered compatible checkout if one exists. Nothing on the
  remote changes.

### Scenario 3 — Migrate plan names what it would break

- **Starting state**: Lenzora's deploy wrapper has registered a strict pin on
  revision M for `xcloud-london`. Two other checkouts are registered as
  compatible-mode controllers.
- **User action**: From a fourth checkout at revision N, run
  `sb remote service migrate xcloud-london` (plan or dry run).
- **Expected outcome**: The plan lists the pin (holder identity, checkout
  path, pinned revision, registered when) under "would break", and the two
  compatible-mode controllers under "unaffected" or "would break" according to
  the compatibility rule. No change is made.

### Scenario 4 — Confirmed migrate over a strict pin

- **Starting state**: As in Scenario 3.
- **User action**: Run the migrate with `--confirm`.
- **Expected outcome**: Refused with a typed result naming the pin and the
  acknowledgment the caller must give to proceed. With the acknowledgment, the
  migrate proceeds, the pin is marked broken with the installing controller's
  identity, and the pinned wrapper's next preflight reports who broke the pin
  and when, not just a mismatch.

### Scenario 5 — Strict pin registration and release

- **Starting state**: A deploy wrapper wants exact-revision behavior.
- **User action**: It registers a pin for its remote with an expiry and a
  purpose string, deploys, and later releases it.
- **Expected outcome**: The pin is visible in `remote service status` and in
  migrate plans while it exists; an expired pin is reported as expired, not
  honored. Release by the holder is immediate; release by another controller
  requires the same explicit acknowledgment as migrating over it.

### Scenario 6 — Registration lock is scoped (negative today)

- **Starting state**: A hosted apply to `scaleway-sandbox` is seventeen
  minutes into its build.
- **User action**: Another session runs `sb remote up xcloud-london --confirm`
  or `sb remote list`.
- **Expected outcome**: The command proceeds. Only an operation on the same
  remote's registration record waits, and that wait is bounded and reports the
  holder.

### Scenario 7 — Mismatch hint is validated (negative)

- **Starting state**: Any mismatch refusal.
- **User action**: The caller pastes the remedy command verbatim.
- **Expected outcome**: The command parses and runs. A remedy that the CLI
  would reject is a defect, and a regression gate checks every emitted remedy
  against the real argument parser.

### Scenario 8 — Old runtime, new controller after this feature ships (negative)

- **Starting state**: A remote still runs a runtime that predates
  compatibility declarations.
- **User action**: A new controller operates it.
- **Expected outcome**: The controller treats the undeclared runtime as
  exact-match only (today's rule), says so, and offers the migrate remedy. No
  guess about compatibility is made.

### Scenario 9 — Runtime history for an incident

- **Starting state**: A deploy failed overnight after a migrate.
- **User action**: The operator asks for the remote's runtime history.
- **Expected outcome**: A bounded, secret-free list of installs with revision,
  protocol, installing controller identity and checkout path, time, and the
  pins and registered controllers at that time.

## Proposed Product Behavior

- Each Sandbox checkout declares a control-protocol version and a capability
  set. Compatibility between a controller and an installed runtime is a
  function of those declarations only; the Git revision is evidence, not the
  rule. The rule is monotone and explicit: a controller whose required protocol
  the runtime serves is `compatible`; one that requires a newer protocol is
  `protocol_newer`; one older than the runtime's minimum supported protocol is
  `protocol_too_old`. Specification sets the exact rule, including whether a
  capability missing on the runtime degrades a single command rather than the
  whole controller.
- Controllers register with a remote on first use (identity, checkout path,
  revision, protocol, mode `compatible` or `strict`, optional pin expiry and
  purpose). Registration is bounded, replaces the same controller's previous
  entry, and expires; it never carries secrets.
- A strict pin binds one controller to the exact installed revision. Pins are
  visible to every other controller, listed by migrate plans, and protected by
  an explicit acknowledgment on confirmed migrate and on release by a
  non-holder.
- Mismatch refusals share one shape across CLI and MCP: installed and local
  revision and protocol, verdict, reason, and remedies as exact commands. The
  remedy text is generated from the CLI definition and gated by a regression
  check, so a hint can never name a flag the parser rejects.
- Migrate records who installed what, from where, when, and which pins and
  registrations existed; `remote service status` shows the current entry and
  the history command shows the bounded list.
- The local registry lock protecting remote registrations is held per remote
  record and only for registration mutations; a hosted apply holds no
  registry-wide lock for its duration.
- Runtimes that predate compatibility declarations are treated as exact-match
  only; nothing is inferred about them.

## Constraints and Dependencies

- Remote-side behavior takes effect only after a migrate that installs a
  runtime which understands registrations and pins; until then the controller
  falls back to the exact-match rule for that remote (Scenario 8).
- Workspace, hosting, recovery and resources commands each have their own
  preflight today (`docs/remote-hosting.md` "remote service status" and the
  workspace preflight; `sandbox/recovery/hosted.py`'s revision check). All of
  them must consume one compatibility verdict; this feature must not add a
  second rule beside the first.
- Lenzora's deploy wrapper (`~/.lenzora-deployment`, pinned checkout
  `approved-2751-migration`) is the first strict-pin consumer and must keep
  working with no change in behavior other than the new visibility.
- Feature 060 (per-target hosting operations) changes hosting locks; the
  registry-lock scoping here concerns the local remote registration lock and
  must be coordinated so the two features do not each redefine the other's
  lock.
- CLAUDE.md gotcha 23 and `docs/remote-hosting.md` require
  `runtime_revision_state: match` for remote resources commands; their text
  changes with this feature.
- Constitution and module boundaries: registrations and history are new state
  and register through explicit manifests; consumers never read state JSON
  directly.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Runtimes per remote | Exactly one installed runtime; no side-by-side | Two control services on one host double resources and split ownership of state; compatibility solves the real problem at lower cost | Fable decision (delegated by user), 2026-10-09 |
| Compatibility rule | Declared control-protocol version and capability set; revision is evidence only | Revision equality refuses docs commits; protocol declarations refuse only real incompatibility | Fable decision (delegated by user), 2026-10-09 |
| Strict mode | Explicit per-controller pin on the exact revision, registered and visible, with expiry | Production wrappers keep the guarantee they rely on today without imposing it on everyone | Fable decision (delegated by user), 2026-10-09 |
| Migrate over a pin | Plan lists it; confirmed apply refuses without an explicit acknowledgment; the broken pin records who broke it | Breaking a production pin must be a deliberate, attributable act | Fable decision (delegated by user), 2026-10-09 |
| Undeclared runtimes | Exact-match only until migrated | No inference about a runtime that cannot describe itself | Fable decision (delegated by user), 2026-10-09 |
| Remedy commands | Generated from the CLI definition and regression-gated | A hint the parser rejects has already cost a session | Fable decision (delegated by user), 2026-10-09 |
| Registry lock scope | Per remote record, held only for registration mutations | An apply on one remote must not block registering another | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None blocking. The independent readiness review should confirm whether
  capability-level degradation (one command refused, controller otherwise
  compatible) is wanted in the first version or whether the first version uses
  protocol version alone.

## Acceptance Outcomes

- Two checkouts at different revisions declaring the same protocol each run a
  remote test job and a hosting status command against one remote, in either
  order, with zero migrates and zero mismatch refusals.
- With a strict pin registered, a migrate plan from another checkout lists
  that pin under "would break" with holder, checkout path and revision; a
  confirmed migrate without acknowledgment makes zero remote changes; with
  acknowledgment, the pinned wrapper's next preflight names the breaking
  controller and time.
- Every mismatch refusal emitted by CLI or MCP contains installed revision,
  local revision, both protocols, a verdict, and at least one remedy command;
  a regression gate parses every remedy against the real CLI and fails on any
  rejection.
- While a hosted apply runs on remote A for fifteen minutes, `remote list`,
  `remote up B --confirm`, and `remote service status B` each complete within
  their normal bounds.
- Against a runtime that predates this feature, a new controller reports
  exact-match mode explicitly and offers the migrate remedy; no registration is
  attempted.
- The runtime history for a remote lists every install since the feature
  shipped, with no secret or credential-shaped value in any field, and is
  bounded in count and bytes.

## Risks and Assumptions

- **Risk**: A compatibility rule that is too loose admits a controller that
  mis-stages a source tree. Mitigation: the protocol version is bumped on every
  change to a transport payload or receipt shape, enforced by a repository
  check, and the rule is monotone.
- **Risk**: Pins that are never released accumulate and block migrates.
  Mitigation: mandatory expiry and visible holder identity; an expired pin is
  reported, not honored.
- **Risk**: Registration adds a remote write to first contact, which may fail
  on a degraded remote. Mitigation: registration failure degrades to
  compatible-mode operation with a warning; it never blocks a read-only
  command.
- **Assumption**: Lenzora's wrapper can register a pin through a supported
  command rather than reading the remote record; the wrapper is maintained by
  the same operator.
- **Assumption**: The remote control protocol already has a version field or
  can carry one without a breaking change; the migrate that introduces it is
  the last exact-match-only migrate.

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

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->

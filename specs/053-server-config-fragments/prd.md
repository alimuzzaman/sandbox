# Product Requirements Draft: Instance-Scoped Server Configuration Fragments

**Status**: Refined

**Created**: 2026-08-31

**Last Refined**: 2026-08-31

**Input**: "Add a bounded server-config capability so agents can install, inspect, and revert the server configuration a WordPress plugin emits and then depends on."

**Drafting Model**: `gpt-5.6-luna` Max (configured orchestrator fallback)

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It does not
> define implementation architecture, tasks, or source changes.

## Problem and Motivation

Sandbox can switch a WordPress instance among Apache, nginx, OpenLiteSpeed, and
Herd, but it cannot safely install the server configuration emitted by a plugin.
Agents therefore cannot exercise server-owned product paths without hand-editing
generated runtime files or using raw container commands.

This blocks meaningful acceptance of caching plugins. For example, xSpeed emits
nginx rules that serve a generated static page directly and attach an
`X-XSpeed-Cache: HIT (nginx)` response header. It also emits Apache rules and
depends on OpenLiteSpeed cache behavior. Plugin-side code can currently be tested,
but an agent cannot prove that the active server served a cache hit without PHP,
or that a purge invalidated the server cache.

## Users and Desired Outcomes

- **Plugin developer**: Exercise the exact server configuration produced by a
  plugin on a disposable, isolated WordPress instance.
- **Testing agent**: Apply, inspect, replace, and remove a named fragment through
  supported Sandbox commands, without raw Docker, SSH, shell evaluation, or hand
  edits under generated runtime directories.
- **Sandbox operator**: Know that invalid configuration cannot leave an instance
  broken or alter another instance.
- **Reviewer**: Obtain evidence distinguishing a server-served response from a PHP
  response and showing whether apply, reload, rollback, and revert succeeded.

## Goals

- Provide CLI-first apply, list, show, and revert operations for named fragments.
- Bind every fragment to exactly one existing instance and the active server type.
- Validate the complete candidate configuration with the active server before it
  can replace the running configuration.
- Reload or restart only the selected instance's web tier after a successful change
  and report the observed outcome.
- Make apply and revert idempotent, bounded, replace-by-name, and reversible.
- Automatically restore the last known-good configuration if activation or
  post-activation readiness fails.
- Support nginx and OpenLiteSpeed in the first usable release; support Apache when
  it can meet the same safety and reversibility contract.
- Enable live evidence that the server, rather than PHP, served a cached response,
  and that revert restores PHP fallback.

## Non-Goals

- Managing host-global nginx, Apache, OpenLiteSpeed, Caddy, Herd, DNS, TLS, or
  ingress configuration.
- Accepting shell programs, command templates, arbitrary container paths, or
  commands embedded in a fragment.
- Accepting complete server blocks, listener/vhost ownership, arbitrary proxying,
  process execution, module loading, or unrestricted native-server syntax.
- Translating configuration automatically between server types.
- Installing a plugin, generating its fragment, warming its cache, or defining
  plugin-specific purge semantics.
- Providing a general-purpose privileged file editor or replacing `sb server`.
- Treating static validation alone as proof that server-cache behavior works.

## Product Scenarios

### Scenario 1 — Apply a valid nginx cache fragment

- **Starting state**: A ready nginx instance exists and a plugin has emitted a
  bounded fragment in a caller-provided file or standard input.
- **User action**: Apply it under a valid non-secret name.
- **Expected outcome**: Sandbox validates the full candidate nginx configuration,
  installs or replaces only that named fragment for the selected instance, reloads
  that instance's nginx service, and reports the active name, server type, content
  identity, and reload/readiness result.

### Scenario 2 — Reapply or replace by name

- **Starting state**: The instance already has a fragment with that name.
- **User action**: Apply identical content, then different valid content.
- **Expected outcome**: Identical content is a proven no-op. Different content
  atomically replaces the prior fragment rather than appending a duplicate, while
  the prior known-good state remains available until replacement is proven active.

### Scenario 3 — Inspect and revert

- **Starting state**: One or more fragments are active on the instance.
- **User action**: List fragments, show one exact name, then revert it.
- **Expected outcome**: List reports bounded metadata only for the selected
  instance. Show reports safe metadata by default; an explicit human-output option
  returns only that stored fragment on standard output and is incompatible with
  JSON. Revert validates the candidate without it, activates that candidate,
  reports reload/readiness, and removes active state only after proof.

### Scenario 4 — Reject an invalid fragment

- **Starting state**: The instance is healthy and the caller supplies invalid or
  incompatible syntax.
- **User action**: Apply the fragment.
- **Expected outcome**: Sandbox refuses before replacing running configuration,
  returns a clear bounded reason, records no active fragment, does not reload, and
  leaves the instance healthy.

### Scenario 5 — Roll back failed activation

- **Starting state**: A candidate passes validation but the web tier fails to
  reload, start, or become ready after activation.
- **User action**: Apply or revert a fragment.
- **Expected outcome**: Sandbox restores the exact previous known-good set, makes
  one bounded recovery activation, and reports both the original failure and
  rollback result. Unproven rollback is recovery-needed, never success.

### Scenario 6 — Exercise OpenLiteSpeed caching

- **Starting state**: A ready OpenLiteSpeed instance has plugin-compatible cache
  rules and a warmed page.
- **User action**: Apply rules, request the page, trigger plugin purge, request
  again, then revert.
- **Expected outcome**: Evidence identifies a server-cache hit without relying on
  PHP, demonstrates post-purge behavior, and shows revert returns to PHP fallback.

### Scenario 7 — Preserve isolation

- **Starting state**: Multiple instances run, possibly with different servers.
- **User action**: Change one instance, or try an nginx fragment on OpenLiteSpeed.
- **Expected outcome**: Only the selected instance changes. A server mismatch is
  refused. Other instances retain unchanged fragment state and readiness.

### Scenario 8 — Refuse unsafe server switching

- **Starting state**: The instance has an active fragment.
- **User action**: Switch it to another server type.
- **Expected outcome**: Sandbox refuses with guidance to revert fragments first.
  It never translates, carries forward, or later reactivates them silently.

### Scenario 9 — Serialize concurrent writers

- **Starting state**: Two callers overlap apply/revert operations on one instance.
- **User action**: Both attempt to mutate fragment state.
- **Expected outcome**: One mutation runs at a time. The second receives a bounded
  conflict or evaluates against the committed result; no update is lost or crosses
  into another instance.

### Scenario 10 — Recover interruption or drift

- **Starting state**: A process stopped during activation, or retained state is
  missing, corrupt, or disagrees with the running server.
- **User action**: Run list/show, apply, or revert.
- **Expected outcome**: Read-only operations expose degradation without writes.
  Mutation first proves or restores one exact known-good state. Ambiguous recovery
  or rollback timeout becomes recovery-needed and cannot authorize a new candidate.

### Scenario 11 — Delete and recreate an instance

- **Starting state**: An instance with fragments is destroyed and its display name
  is later reused.
- **User action**: Inspect or mutate the new instance.
- **Expected outcome**: The new instance never inherits or reactivates the deleted
  instance's fragments.

## Proposed Product Behavior

- `sb server config apply` accepts exactly one bounded regular file or bounded
  standard input plus a normalized, non-secret name. It treats bytes as
  configuration data and never executes them as shell text.
- The accepted profile is an instance-local WordPress cache-routing fragment in
  the existing vhost/server context. Full server/vhost declarations and directives
  that add listeners, escape the WordPress document root, proxy arbitrary targets,
  change process identity, load code, execute programs, or shadow protected
  readiness/autologin routes are refused.
- `list`, `show`, and `revert` use normal project/instance resolution and
  refuse ambiguous or missing ownership.
- Names are unique inside one instance and server type. Same-name content replaces;
  identical content is a no-op.
- Apply validates the complete effective candidate, not merely the fragment.
  Revert validates the effective candidate after removal. OpenLiteSpeed must prove
  the candidate in a bounded, network-isolated boot using the exact active image;
  silently ignored directives are validation failure.
- List and default show are strictly read-only. Exact content is emitted only by an
  explicit human-output option; it is refused with JSON and never copied into logs,
  errors, or routine envelopes. Optional file output is owner-only.
- Results report selected instance, active server type, operation, safe fragment
  identity, mutation state, validation, reload, readiness, and rollback when used.
- Validation, activation, readiness, and rollback each have finite bounds. Timeout
  or unavailable observation is not success.
- A failed candidate cannot displace the prior known-good set. Failure after
  activation begins triggers automatic restoration.
- Per-instance mutations are serialized and journal crash-recovery evidence. A
  stale transaction, corrupt state, or observed drift must resolve to one exact
  known-good set before another mutation starts.
- State survives ordinary stop/start and reconciliation for the same instance and
  server type, but never affects another instance or host-global ingress.
- CLI is required. MCP parity may follow later.

## Constraints and Dependencies

- Existing one-project/one-instance ownership and instance resolution remain
  authoritative.
- Docker/Caddy clean URLs remain unchanged and never consume these fragments.
- The feature uses supported Sandbox lifecycle boundaries; no raw Docker, SSH, or
  hand edits under generated runtime paths are required.
- Input, stored content, output, errors, and metadata are bounded. Symlinks, special
  files, traversal, invalid names, and oversized input are refused.
- Names, logs, normal list output, and JSON envelopes contain no secrets. Fragments
  are not a credential transport; secrets use existing brokered mechanisms.
- Validation runs through the selected instance's declared server implementation
  and version, not a host-global substitute.
- Reload/readiness targets only the selected instance's web service or services.
- OpenLiteSpeed needs a deterministic instance-local inclusion boundary. If its
  image cannot provide one and prove it through an isolated exact-image candidate
  boot, the operation refuses instead of mutating global vhost state.
- Apache cannot be claimed until it passes the same isolation, validation,
  replacement, rollback, and live-proof gates.
- Feedback `0df918a754a862fb10667b3b0d3f6855` is product evidence only.
  Feedback `80d1ef1465068665f33bf6afe97c4ef3` proves only that the separate
  LiteSpeed bootstrap defect was fixed.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Interface | CLI-first apply/list/show/revert; MCP optional | Matches policy and requested shape | User |
| Minimum servers | nginx and OpenLiteSpeed | Required cache paths | User |
| Apache | Include only if equally safe without delaying minimum support | Requested when cheap | User |
| Input | One regular file or stdin, data only | Prevents shell execution | User |
| Naming | Instance-local, server-bound, normalized non-secret name | Preserves isolation | User and constitution |
| Replacement | Same name replaces; identical content is no-op | Required idempotency | User |
| Activation | Validate full candidate, activate, reload, prove readiness | Prevents broken startup | User |
| Failure | Restore exact prior known-good set | Required rollback | User |
| Accepted authority | Versioned instance-local WordPress cache-routing profile; refuse full server/vhost and privileged directives | Arbitrary native config can escape instance boundaries | User isolation constraint and project safety policy |
| OpenLiteSpeed validation | Isolated candidate boot using the exact active image plus readiness proof | No assumed syntax validator or ignored-directive success | User validation requirement and reviewed server constraint |
| Show content | Metadata by default; explicit non-JSON human output for exact content | Keeps caller data out of JSON and logs | User secrecy constraint and reviewed safety choice |
| Concurrency/recovery | One writer per instance; reconcile exact known-good state first | Prevents lost updates and compounding crash state | User reversibility requirement and project safety policy |
| Server switching | Refuse while fragments are active | Avoids silent translation/reactivation | User current-server scope and project safety policy |
| Secrets | Fragments are not a secret channel | Keeps secrets brokered | Existing policy |

## Open Questions

- None.

## Acceptance Outcomes

- On live nginx, an xSpeed-compatible static-cache rule is applied through Sandbox
  and a request returns its server-owned hit header while a request-scoped PHP
  execution counter remains unchanged.
- Revert removes it from `server config list`, reloads nginx, and the route falls
  back to PHP.
- On live OpenLiteSpeed, the sequence is origin/PHP, warm, server HIT without a PHP
  counter change, plugin purge, not-HIT with PHP execution, rewarm, HIT, then revert
  to origin/PHP.
- Deliberately invalid syntax or refused-policy nginx and OpenLiteSpeed fragments
  are rejected before activation with no reload, active-state change, or rollback.
- A candidate that passes validation but fails activation or readiness restores the
  exact prior fragment set, proves rollback readiness, and reports rolled-back rather
  than success; incomplete rollback reports recovery-needed.
- Identical reapply is a no-op; changed same-name apply leaves exactly one fragment.
- List/show expose only selected-instance bounded state and active server type.
- A second running instance remains unchanged and ready through every operation.
- The control instance's fragment-set identity, runtime identity, response marker,
  and readiness endpoint are identical before and after target mutations.
- List and default show produce zero persistent writes. Explicit content show never
  places content in JSON or logs.
- Concurrent mutation, interrupted activation, corrupt/drifted state, rollback
  timeout, runtime identity change, deletion/recreation, and name reuse fail closed.
- A server switch attempted while fragments are active is refused without changing
  runtime identity, server type, or fragment state.
- No acceptance step uses raw Docker, SSH, shell evaluation, or hand edits.

## Risks and Assumptions

- **Risk**: Valid syntax may still shadow WordPress, security, autologin, health, or
  clean-URL routing.
- **Risk**: nginx and OpenLiteSpeed have different inclusion, validation, reload,
  and cache semantics.
- **Risk**: Reload acknowledgement can precede actual readiness.
- **Risk**: Plugin output may contain absolute paths or directives illegal in the
  safe inclusion context.
- **Risk**: Show can disclose caller-supplied sensitive text; routine list/log/JSON
  output must remain content-free and fragments must not be used for secrets.
- **Assumption**: Both minimum images expose deterministic instance-local inclusion
  points; OpenLiteSpeed is proven through isolated exact-image candidate boot rather
  than an assumed syntax-only validator.
- **Assumption**: A server hit marker plus independent PHP-bypass sentinel is enough
  to prove PHP did not run.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->

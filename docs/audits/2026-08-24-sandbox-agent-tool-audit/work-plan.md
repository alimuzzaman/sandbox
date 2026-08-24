# Proposed work plan

This is a review plan, not an authorization to implement or deploy. The audit
worktree contains no product-code changes.

## Slice 1 — Durable job observation

Define one observation envelope shared by CLI and MCP. Implement the smallest
read-only observer on top of existing `job-status` and bounded `job-output`.

Required decisions:

- terminal lifecycle set and timeout semantics;
- cursor ownership and output byte/event caps;
- `result` versus raw retained output;
- remote transport error versus incomplete evidence;
- single JSON envelope versus explicit JSONL stream.

Validation: unit tests for local/remote adapters, terminal and non-terminal
states, cursor continuation, and malformed transport payloads; CLI/MCP parity
fixtures; docs and skill examples updated together.

## Slice 2 — CI request identity

Thread `request_id` through CLI, MCP, aggregate parent, and child submissions.
Use the existing durable-job identity conflict behavior rather than inventing a
second registry. Preserve current matrix labels as display values, but make the
accepted parent the replay authority.

Validation: identical replay, conflicting replay, lost acceptance output, local
async compatibility, remote parent/child fan-out, and no duplicate instances.

## Slice 2b — Matrix request identity

Apply the same durable identity contract to generic `job-matrix` and MCP
`job_matrix`. Persist the parent request ID and make child fan-out replay from
that parent. Do this as a shared identity seam rather than a CI-only workaround.

Validation: matrix replay/conflict, local and remote targets, child dependency
fan-out, and lost parent acceptance output.

## Slice 3 — Remote readiness receipt

Compose the existing service status, revision, ownership, and capability checks
into one bounded read-only receipt. Return stable recovery/plan identifiers from
dependent commands. Keep migration, provisioning, cleanup, and deployment
confirmation boundaries unchanged.

Validation: match, mismatch, unavailable, unproven ownership, protocol skew, and
stale receipt cases; verify no credential/path leakage.

As part of this slice, align resource calls with the same control-service/runtime
readiness contract. Add provision/up endpoint/auth readiness probes and make
remote service status return a typed transport-unavailable state on non-zero SSH.

## Slice 3b — Resource contract and documentation reconciliation

Correct the resource-monitoring docs/skill/spec examples for the actual control
transport, directory-walk depth, and cleanup eligibility. Add a stale-runtime
resource test and a docs-facing contract check; do not expand cleanup authority.

Clarify Spec 036's distinction between a persisted diagnostic directory index and
durable cleanup/mutation state.

Add a separate provisioning-convergence slice: preserve an existing healthy
credential on retry, and reserve credential rotation for an explicit confirmed
operation.

## Slice 4 — Bootstrap context and target policy

Add a compact, cacheable context receipt and an explicit remote-only workflow
guard. Update the CLI-first skill and guide to use one bootstrap call, declare the
target, and avoid `ensure_instance` before CI.

Validation: local, remote, inferred-target, ambiguous-target, and explicit local
override cases.

## Slice 5 — Guidance and low-risk ergonomics

Prefer `feedback export --format jsonl`, document selected test groups, and add
examples for bounded observation. Consider `feedback --all` and test-plan helpers
only after measuring continued repetition post-Slice 1–4.

Validation: docs examples run as read-only commands; no new storage or mutation
authority is introduced.

## Slice 6 — Fail-closed command and retention contracts

Make `secrets run` propagate trusted-child exit state, normalize malformed
job-input errors, and put `job-retention` behind a preview/confirmation gate.
Keep redaction, bounded output, and scheduled internal sweep policy intact.

Validation: child exit 0/11/timeout, malformed IDs and limits across every job
verb, retention preview/apply/replay/interruption, and CLI/MCP envelope parity.

## Slice 7 — Resource and remote capability readiness

Reuse the workspace-grade ownership/revision preflight for remote resource
observe and reclamation. Add capability-scoped Hermes readiness and typed
`install_needed` versus broken/transport states without weakening aggregate
health or mutation confirmation.

Validation: healthy, absent, stale, ambiguous, mismatched, unavailable, and
partial remote states; verify no resource mutation is dispatched before proof.

## Slice 8 — Durable Hermes operations and session recovery

Separate bounded host maintenance from repository runs, then give remote clone
and dashboard/session attachment request IDs, progress/terminal receipts, and
safe resume semantics. Do not introduce an arbitrary-shell escape hatch.

Validation: lost SSH response, partial clone, duplicate retry, blank dashboard,
PTY disconnect, snapshot-only session, and successful reconnect.

## Slice 9 — Delegated validation receipts

Make child-agent validation reports provisional, tree-bound, and machine-readable.
Require the root integration gate to rerun relevant imports/compile/tests after
composition and mark receipts stale after any bound file changes.

Validation: matching SHA, changed tree, contradictory child/root result, failed
receipt, and concurrent worktree edits.

## Definition of done

- Each accepted change has source, tests, and matching docs/skill updates.
- Existing lower-level commands remain backward-compatible.
- No command silently changes local/remote targeting.
- Replay, timeout, partial evidence, and revision mismatch remain explicit.
- Remote mutation, cleanup, deployment, publishing, and feedback submission still
  require their existing authority/confirmation gates.
- Secret-child failures, destructive retention, resource preflight, and delegated
  validation remain fail-closed and source-verifiable.

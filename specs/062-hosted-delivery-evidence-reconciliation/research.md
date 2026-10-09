# Research: Hosted Delivery Evidence Reconciliation

## R1. Identity scope

- **Decision**:
  - For `target_kind: hosted`, `request_scope` uses
    `{remote_name, project_name (declared), environment, target_kind}`.
  - `project_identity`, which today is derived from the checkout root, and the
    hosting declaration digest are stored in the operation document as
    evidence.
  - Lookups by request id search the hosted scope.
  - A collision warning appears when two operations in one scope carry
    different declaration digests.
  - Deploy and preview scopes are unchanged.
- **Rationale**: FR-001..FR-004, with the smallest change to the scope digest
  domain.

## R2. Conversion

- **Decision**: Feature 060 owns the conversion (`host convert-state`). Its
  062 step:
  1. For each `request_bindings` row whose operation is hosted, it recomputes
     the hosted scope from the stored target fields.
  2. It inserts the new binding.
  3. It keeps the old binding as an alias, marked `converted_to`.
  4. It records progress per batch of 500 rows in the same SQLite
     transaction.

  The operation is re-runnable. Reads try the new scope first, then the
  alias. A controller predating this feature reads only old scopes and finds
  converted operations marked `converted`, never `succeeded` (FR-006).
  Hosting mutations from it are refused through 060's `protocol_too_old`.
- **Rationale**: FR-005 and FR-006, with no data loss and a resumable
  conversion.

## R3. Observation and classification

- **Decision**: One observation reuses `_observe_host_runtime`, which reads
  the revision, the configuration digests, health, the topology and the
  initializer receipts. The requested-outcome proofs are:
  - the edge route and certificate (the 064 verify evidence when present;
    otherwise one bounded edge probe);
  - DNS answers (the 064 authoritative check);
  - initializer receipts.

  The checks run in this order:

  | Order | Check | Result |
  |---|---|---|
  | 1 | job running under a live supervisor | `authority_pending` |
  | 2 | observation incomplete or timed out | `unavailable` |
  | 3 | a later attempt exists for the target | `diverged` (`later_attempt`) |
  | 4 | the revision or configuration differs from the attempt's request | see "Mismatch" below |
  | 5 | everything matches | see "Match" below |

  - **Mismatch.** If the observation equals the pre-attempt state recorded at
    admission and the phase evidence shows no transfer, edge, DNS or Compose
    phase started, the result is `no_effect_proven`. Otherwise it is
    `diverged`, or `insufficient_evidence` when no pre-attempt state was
    recorded.
  - **Match.** The result is `adopted_by_observation` only when every
    applicable proof is present and no 064 journal for the target is
    unresolved. Otherwise it is `insufficient_evidence`, naming what is
    missing.
- **Images**: The request-bound terminal record maps as follows, and both
  results need corroboration by one complete observation:
  - `committed` → adopt;
  - `recovery_no_effect` → no-effect.

  A contradicting observation gives `diverged`.
- **Rationale**: FR-007..FR-013, following the spec's order.

## R4. Single adoption under concurrency

- **Decision**: A writing result runs under 060's `TargetLease` for the
  target, using operation `reconcile` with `--wait 30`. It then makes one
  SQLite `BEGIN IMMEDIATE` transaction that re-reads the attempt and writes
  only if no reconciliation exists yet. A second caller re-reads and reports
  the stored result.
- **Rationale**: FR-015. The lease prevents a racing apply, and the
  transaction prevents double adoption.

## R5. Phase evidence

- **Decision**:
  - At admission, the attempt records the pre-attempt revision and
    configuration from the admission observation that already exists.
  - Each remote phase records `started` before dispatch and `finished`
    afterwards: `transfer`, `edge`, `dns`, `compose` and `initializers`.
  - Edge and DNS rollback outcomes come from the 064 transaction result:
    `rollback_complete` or not.
  - Image attempts use the activation record's `entered` flags.
- **Rationale**: FR-024, FR-025 and FR-027 need positive facts.

## R6. Pre-admission reconcile and jobs

- **Decision**:
  - **Pre-admission.** `host apply` checks the last outcome. Only when it is
    uncertain does it run reconcile, with one read bounded at 15 s, and print
    the result. `--no-reconcile` skips it. A read failure leaves today's
    fence behavior.
  - **Lost supervisor.** The job registry records
    `transport_event: supervisor_lost`, and the job stays `interrupted` with
    `delivery_outcome` set.
  - **Waiters.** A `job-start --wait` waiter that sees `interrupted` with an
    uncertain outcome runs one reconcile. It exits 0 only on terminal
    `succeeded`.
- **Rationale**: FR-019..FR-022.

## R7. Retry linking

- **Decision**: Applying the same revision after a failed attempt generates a
  new request id and writes `attempt_links(new, failed)`. Replaying an
  existing request id is unchanged (054).
- **Rationale**: FR-029.

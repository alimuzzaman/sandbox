# Implementation Plan: Hosted Delivery Evidence Reconciliation

**Branch**: `latest` (feature dir `062-hosted-delivery-evidence-reconciliation`) | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/062-hosted-delivery-evidence-reconciliation/spec.md`

## Summary

This feature makes the local delivery record converge read-only to what the
remote actually did:

- **Reconciliation.** A new `sandbox/delivery/reconcile.py` runs one bounded
  live observation of the target. It reuses the existing observation-only
  path (`_observe_host_runtime`, the same probe `host recover` uses). It
  compares the observation with the attempt's retained admission facts, its
  phase evidence and its requested-outcome proofs, and classifies the result
  into six classes.
- **Writes.** Only `adopted_by_observation` and `no_effect_proven` write,
  through spec 054's repository and spec 051's state path, under 060's target
  lease. Every other class writes nothing.
- **Fence release.** Release rules rest on retained phase and rollback facts.
  The phase facts are recorded starting with this feature, and the rollback
  facts come from 064's journal results.
- **Scope.** The delivery identity scope moves from the checkout-derived
  `project_identity` to (remote, declared project, environment). The
  declaration identity is kept as evidence and used only for collision
  warnings.
- **Conversion.** Existing records move through the conversion that feature
  060 owns (its 062 scope-key step).

## Technical Context

**Language/Version**: Python 3.12+ (CLI venv)

**Primary Dependencies**:
- `sandbox.delivery.repository.DeliveryRepository` (SQLite, scope-keyed);
- `sandbox.delivery.models.request_scope`;
- `sandbox.delivery.admission`;
- the observation-only probe in `sandbox/commands/hosting.py`
  (`_observe_host_runtime`, `_host_observation_is_exact_ready`,
  `_host_observation_has_stable_contradiction`);
- the image request-bound records in `sandbox/hosting/images` and
  `sandbox/hosting/recovery`;
- durable jobs (`sandbox/jobs`);
- 060's `TargetLease` and conversion;
- 064's journal results.

**Storage**: Existing delivery SQLite gains these tables:

- `attempt_phase_evidence(operation_id, phase, state, at)`;
- `attempt_reconciliations(operation_id, result, observation_digest, evidence, at, release_rule)`;
- `attempt_links(operation_id, previous_operation_id)`.

There are no new files, and job state gains a `delivery_outcome` field
written through the job registry.

**Testing**:
- `unittest`, using fixture remotes with a scripted observation and a
  scripted image request record.
- 054 fixtures, plus 060's shared conversion fixtures.
- Two-checkout tests with two clones of one project.

**Target Platform**: macOS/Linux controller; Linux remote

**Project Type**: CLI + MCP server (single project)

**Performance Goals**: each reconciliation makes one remote read bounded at
15 s. Pre-admission reconciliation adds at most 15 s, and only when the last
outcome is uncertain.

**Constraints**:
- zero remote writes;
- adoption never on revision and configuration equality alone;
- release only on retained positive facts;
- no direct state-file reads by consumers.

**Scale/Scope**: one target per reconciliation. History retention is
unchanged from 054.

**Sequencing**: implementation starts after 054 T040-T047 (acceptance of the
outcome model) and after 060's conversion (T027/T028) lands. US1-US2 also
need 064's journal result for edge rollback facts. Until 064 lands, an edge
rollback without a journal result counts as "not recorded complete", so the
fence is kept.

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Per-project instance model | Pass | Identity becomes (remote, declared project, environment); checkouts are evidence |
| II. Registry is the source of truth | Pass | Reconciliation has an explicit contract (FR-030). Records are read and written only through `DeliveryRepository` and the job registry |
| III. Single entry, modular package | Pass | New `sandbox/delivery/reconcile.py`, `sandbox/delivery/phase_evidence.py`, `sandbox/delivery/fence_release.py`; hosting orchestrates |
| IV. Live-stack proof | Pass (planned) | Quickstart kills a client mid-phase on a disposable remote |
| V. Idempotency, docs with code | Pass | Concurrent reconciles record exactly one adoption (SQLite transaction plus the 060 lease). Docs in `docs/remote-hosting.md` and `docs/remote-job-runtime.md` |
| VI. Parity before removal | Pass | Checkout-scoped lookup stays readable until conversion; no fence semantics are removed, only release paths added |

## Project Structure

### Documentation (this feature)

```text
specs/062-hosted-delivery-evidence-reconciliation/
├── prd.md, spec.md, plan.md, research.md, data-model.md, quickstart.md
├── contracts/reconcile.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/delivery/reconcile.py        # classify(attempt, observation) -> ReconciliationResult; run(target, request_id)
sandbox/delivery/phase_evidence.py   # record phase started/finished per attempt; pre-attempt revision/config at admission
sandbox/delivery/fence_release.py    # FR-023..FR-028 rules over retained facts
sandbox/delivery/models.py           # hosted request_scope from (remote, project, environment); declaration identity evidence
sandbox/delivery/repository.py       # new tables; single-adoption transaction; scope lookup across checkouts
sandbox/delivery/admission.py        # pre-admission reconcile (FR-019/FR-020); auto-release; notice line
sandbox/commands/hosting.py          # phase evidence calls; `host reconcile`; retry linking (FR-029)
sandbox/commands/delivery.py or equivalent  # inspect shows reconcilable; refusals name callable commands
sandbox/jobs/registry.py             # delivery_outcome field; supervisor_lost transport event
sandbox/commands/jobs (job-start --wait)    # bounded reconcile on interrupted+uncertain
mcp/wp-server/tools/                 # host_reconcile
docs/remote-hosting.md, docs/remote-job-runtime.md, CLAUDE.md, CHANGELOG.md
tests/test_delivery_reconcile.py, tests/test_delivery_fence_release.py, tests/test_delivery_identity_scope.py,
tests/test_delivery_job_outcome.py, tests/test_delivery_retry_link.py
```

**Structure Decision**: reconciliation is a delivery-layer service fed by an
observation port. Hosting supplies the port by wrapping the existing
observation-only probe, so delivery never imports hosting internals.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Recording phase evidence on every attempt from now on | Auto-release must rest on retained positive facts (FR-027) | Inferring "Compose never started" from the absence of a failure record is exactly what FR-027 forbids |

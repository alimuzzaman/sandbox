# Implementation Plan: One-Click Host Storage Reclamation

**Branch**: `latest` | **Date**: 2026-08-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/042-host-storage-reclamation/spec.md`

## Summary

Extend the existing resource-monitoring stack (specs 035/036, commits `116a63b`/`cad5b59`)
with a lifecycle classification of the managed deployment root, a tiered plan/apply
reclamation path, a durable deletion manifest, and workspace retention leases. All policy
lives in one pure module (`sandbox/resources/reclaim.py`); all host-side evidence collection
and mutation lives in the existing shipped remote probe, so the same code path serves local
and remote targets and no host runtime upgrade is required for the new commands.

## Technical Context

**Language/Version**: Python 3.11+ (host CLI) and Python 3 stdlib-only (shipped probe —
the probe runs on the target host and may not assume the repo's dependencies).

**Primary Dependencies**: none new. Existing `sandbox.resources.{models,plans,service,
remote,adapters,attribution}`, `sandbox.registry`, `sandbox.application.workspace_service`.

**Storage**: cleanup plans in `$SANDBOX_HOME/runtime/resource-plans/` (existing `PlanStore`);
deletion manifests in `$SANDBOX_HOME/runtime/resources/deletions/<run_id>.jsonl` on the
target host; workspace leases in `$SANDBOX_HOME/runtime/resources/leases/<name>.json` on the
target host.

**Testing**: `pytest` under `tests/`, plus read-only live verification against the real
remote `scaleway-sandbox`.

**Target Platform**: Linux remote hosts (ext4, Docker with the containerd image store) and
the operator's macOS/Linux machine for local targets.

**Project Type**: CLI + shipped probe (single project).

**Performance Goals**: `status --fast` under 10 s including the new classification (served
from the cached directory index); `plan` under 60 s; `cleanup` bounded per candidate with a
finite overall budget.

**Constraints**: must produce correct output with 0 bytes free; must never require a
writable scratch file on the filesystem being reclaimed; one bounded SSH session per
operation, not per candidate.

**Scale/Scope**: ~200 deployment entries, ~80 volumes, ~200 GB filesystems.

## Constitution Check

| Principle | Assessment |
|---|---|
| I. Per-project instance model | No new instance concept. Reclamation acts on host storage and consults the registry as evidence only. PASS |
| II. Registry is source of truth | The registry is one of five evidence inputs and is reconciled after removal; drift is reported rather than assumed away. PASS |
| III. Single entry file, modular package | New logic is `sandbox/resources/reclaim.py` + `reclaim_service.py`, registered through the existing command registry. `sb` untouched. PASS |
| IV. Live-stack verification | `plan` and `--dry-run` are verified read-only against the real remote; evidence recorded in `implementation-evidence.md`. PASS |
| V. Idempotency and docs-with-code | Execution is explicitly idempotent (FR-016) and README/CLAUDE.md/SKILL/docs land in the same commit. PASS |
| VI. Feature parity before removal | Nothing is removed or stubbed. `--scope cache|stale` keeps working unchanged; tiers are additive. PASS |
| Module boundaries | Policy is a pure module with no I/O; the service is constructed through `sandbox/resources/context.py`; the CLI command is registered via `CommandSpec`. No new consumer of `sandbox_core.py` or registry JSON. PASS |

No violations; Complexity Tracking omitted.

## Project Structure

### Documentation (this feature)

```text
specs/042-host-storage-reclamation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── reclaim-policy.md
│   └── probe.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── resources/
│   ├── reclaim.py            # NEW: pure policy — classes, protection rules, tiers,
│   │                         #      leases, growth exclusion, disk pressure, manifest shape
│   ├── reclaim_service.py    # NEW: status extension, tier plan, tiered apply, reaper
│   ├── remote.py             # EXTENDED: probe emits reclaim evidence; new `reclaim`
│   │                         #      and `lease` probe actions; local runner reuse
│   ├── context.py            # EXTENDED: build the reclaim service for local/remote
│   ├── models.py             # EXTENDED: tier scopes in PLAN_SCOPES
│   └── attribution.py        # EXTENDED: disk_capacity_pressure alongside network pressure
├── commands/
│   ├── resources.py          # EXTENDED: --tier for plan/cleanup, reclaim rendering
│   └── workspaces.py         # EXTENDED: release / ttl / reap actions
tests/
├── test_resource_reclaim_policy.py    # NEW: every safety rule
├── test_resource_reclaim_service.py   # NEW: plan/apply/manifest/resume/idempotency
└── test_workspace_retention.py        # NEW: release/ttl/reap
docs/resource-monitoring.md            # EXTENDED
```

**Structure Decision**: single project, existing package layout. Policy is isolated in a
pure module so every safety rule is unit-testable without a host, a container, or a disk.

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

1. **One implementation, two targets.** The reclaim evidence collector and mutator live in
   the shipped probe program (`_REMOTE_PROGRAM`). Remote targets run it over SSH exactly as
   today; local targets run the same source through `sys.executable`. This avoids a second
   classification implementation in `adapters.py` drifting from the remote one.
2. **Reuse `PlanStore`.** Tier plans are `CleanupPlan`s with scope `safe|tmp|all`; the
   existing atomic write, flock, expiry, `begin`/`finish` state machine, and run receipts
   give resumability and single-writer semantics for free.
3. **Batch the apply.** `ResourceService.cleanup` issues one SSH round trip per candidate.
   For 150 candidates that is unusable, so the tiered apply sends the reviewed candidate set
   in one bounded session; the probe revalidates each candidate host-side before removing it.
4. **Manifest before deletion, not after.** Written with `O_APPEND` + `fsync` per record to
   a directory created before the run begins, so an out-of-space or killed run still leaves
   a record of everything it intended to remove.
5. **Growth detection by mtime.** Comparing two `du` samples raced against our own walk
   during the manual audit. The plan captures `(size, mtime)`; the apply re-reads `mtime` and
   refuses if it advanced.

## Phase 1 — Design

- [data-model.md](./data-model.md): entities and their fields.
- [contracts/reclaim-policy.md](./contracts/reclaim-policy.md): the normative classification,
  protection, tier, liveness, and lease rules — the file tests assert against.
- [contracts/probe.md](./contracts/probe.md): probe request/response contract for the
  `reclaim` and `lease` actions and the `reclaim` block of an `observe` response.
- [contracts/cli.md](./contracts/cli.md): command surface and output contract.
- [quickstart.md](./quickstart.md): the operator walkthrough.

## Phase 2 — Tasks

Produced by `/speckit-tasks` into [tasks.md](./tasks.md).

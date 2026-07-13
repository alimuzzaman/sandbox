# Implementation Plan: Reliable Hermes Scheduled Work

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/025-hermes-scheduler-reliability/spec.md`

## Summary

Turn Hermes scheduling from ad hoc remote state into a Sandbox-owned, observable control plane. A pure scheduler feature module owns desired cron definitions, route validation, classification, reconciliation planning, and false-success evaluation. The remote adapter installs committed scripts, inventories bounded evidence, converges gateway ownership, applies confirmed cron replacement, waits for verified runs, and preserves dirty worktrees. CLI and MCP remain thin adapters over the same service behavior.

## Technical Context

**Language/Version**: Python 3.11+ and POSIX shell used by the existing Sandbox remote controls

**Primary Dependencies**: Python standard library, existing Sandbox remote/secret adapters, upstream Hermes CLI and file-backed cron store, Git, systemd user services on the managed Linux server

**Storage**: Committed JSON cron catalog and scripts; remote `~/.hermes/cron` metadata/output, bounded request-error artifacts, Sandbox remote state, and managed Git worktrees

**Testing**: `python3 -m unittest` for pure/service/CLI/MCP tests; `./sb selftest`; live `./sb hermes ... --json` acceptance against the configured remote

**Target Platform**: macOS/Linux Sandbox operator controlling a Linux Hermes server over the configured Sandbox remote

**Project Type**: Modular CLI and MCP control plane with remote service integration

**Performance Goals**: Read-only health under 30 seconds; preview under 30 seconds; reconciliation under 5 minutes excluding agent execution; verified run bounded by caller timeout

**Constraints**: No secrets or stored prompts in status output; no raw remote mutations outside Sandbox; external changes require confirmation; no destructive worktree cleanup with dirty state; support the pinned Hermes release while failing safely on storage/schema drift

**Scale/Scope**: One configured Hermes remote, fewer than 50 cron jobs, fewer than 50 managed repositories/worktrees, and bounded recent run evidence

## Constitution Check

*GATE: Passed before research and re-checked after design.*

| Principle | Result | Evidence |
| --- | --- | --- |
| Per-project instance model | Pass | Hermes operations remain explicitly remote-scoped and do not introduce a fallback WordPress instance. |
| Registry/source of truth | Pass | Existing remote configuration remains authoritative; desired cron state is committed product configuration, not machine credentials. |
| Single entry, modular package | Pass | Business rules live in `sandbox/hermes/`; CLI, core remote adapter, and MCP are composition layers. |
| Live-stack proof | Pass | Quickstart requires live gateway, cron reconciliation, verified run, and worktree inventory evidence. |
| Idempotency and docs-with-code | Pass | Reconciliation and gateway convergence are designed for repeat runs; docs/spec/tests ship together. |
| Feature parity before removal | Pass | Existing list/create/route/run commands remain; new controls compose them and legacy gateway ownership is removed only after a preview and confirmed convergence. |
| Secrets and authority | Pass | Status output is reduced/redacted; mutations require `--confirm`; dirty worktrees block cleanup. |

Post-design re-check: contracts preserve all gates. The only intentional broad operation—remove and recreate all cron jobs—is explicitly required by the user, previewed first, confirmed, and followed by exact catalog verification.

## Architecture Decisions

### AD-001 — One gateway owner: Sandbox systemd service

The gateway owns the upstream cron tick, so Sandbox must converge to one `hermes-gateway-sandbox.service`. A legacy unit or manual process is stopped and disabled during confirmed convergence. The managed unit uses upstream `gateway run --replace` defensively but health still treats multiple owners or restart growth as degraded.

### AD-002 — Desired cron catalog is committed and versioned

All base jobs, schedules, execution modes, scripts/prompts, route profiles, work targets, and enabled state live in `sandbox/hermes/cron-catalog.json`. Remote setup installs the committed scripts and reconciliation replaces observed jobs from this catalog. No credentials or host-specific IDs are stored; remote paths are rendered from the configured Sandbox home.

### AD-003 — Model and reasoning are separate validated fields

Catalog agent jobs select `luna`, `terra`, or `sol`; the route table resolves provider, model, and reasoning effort separately. The adapter rejects effort-like suffixes in observed or desired model identifiers. A verified live run remains the final compatibility check because provider entitlements and upstream runtime behavior cannot be proven from static validation.

### AD-004 — Health is evidence aggregation, not upstream status passthrough

Sandbox combines sanitized cron metadata, job execution type, latest output/error artifacts, gateway service/process ownership, restart counters, and Git worktree state. A newer provider/client error overrides an upstream `ok` marker and is reported as a false success.

### AD-005 — No-work and operational failure have different exits

Committed monitor/dispatcher scripts return zero only for a successful inspection or legitimate no-work state. Missing dependencies, unreadable state, timeout, command failure, or malformed output return nonzero. The TODO monitor never edits its own cron definition; catalog reconciliation owns lifecycle.

### AD-006 — Full replacement uses a two-phase plan/apply contract

Preview validates the catalog, scripts, target directories, dirty worktrees, and observed inventory. Apply removes all observed jobs, installs scripts, creates desired jobs, sets routes atomically, verifies the exact resulting catalog, and reports partial progress on failure. A second run is a no-op when the generated catalog fingerprint matches.

### AD-007 — Verified run observes completion and contradictory evidence

Triggering remains available for asynchronous operation. Verified execution snapshots the prior run marker, starts the job, polls bounded metadata until it changes and reaches terminal state, then checks bounded output/request-error evidence. Success requires agreement between metadata and evidence.

### AD-008 — Agent work is preserved before cleanup, not blindly committed

Sandbox inventories every managed repo/worktree and blocks destructive cleanup while any is dirty. This task may commit and push reviewed work because the user explicitly authorized it; recurring jobs do not receive standing Git authority and must leave changes for a later approved preservation action.

## Project Structure

### Documentation (this feature)

```text
specs/025-hermes-scheduler-reliability/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── catalog.md
│   └── cli-mcp.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hermes/
├── scheduler.py                 # catalog models, validation, audit, plans
├── cron-catalog.json            # desired non-secret cron definitions
└── cron_scripts/
    ├── todo_md_monitor.py
    ├── codex_quota_requeue.py
    └── lenzora_kanban_dispatch.py
sandbox/core/_hermes.py          # remote evidence, apply, gateway and run adapter
sandbox/commands/hermes.py       # CLI composition
sandbox/cli.py                   # arguments/help only
mcp/wp-server/tools/hermes.py    # MCP wrappers
scripts/install-remote.sh        # fresh-server integration
docs/hermes-agent.md             # operator runbook and failure semantics
tests/test_hermes.py             # pure and remote-adapter behavior
tests/test_mcp.py                # MCP contract
tests/test_cli.py                # CLI contract
```

**Structure Decision**: Extend the existing `sandbox/hermes` feature module rather than adding more behavior to the already large remote adapter. Keep SSH/process mechanics in `_hermes.py`; keep deterministic policy and catalog logic independently testable.

## Implementation Strategy

1. Add failing pure tests for catalog validation, script classification, false-success evidence, exact reconciliation plans, and worktree cleanup blocking.
2. Commit the desired catalog and monitor scripts; render host paths from remote configuration during installation.
3. Add read-only health and reconciliation preview in the scheduler module and remote adapter.
4. Add confirmed gateway convergence, cron apply/remove/recreate, and verified run.
5. Expose identical CLI and MCP operations; integrate setup/restore/fresh-server paths.
6. Run local tests and live remote preview before any mutation.
7. Preserve reviewed dirty worktrees, push valid changes, synchronize Sandbox, converge gateway, replace all jobs, and run acceptance.

The desired catalog includes one bounded `sandbox-approved-spec-task` agent entry routed through the Terra/Medium implementation profile. Reconciliation creates its dedicated managed Git worktree when absent; the worker never writes in the primary checkout. It may execute at most one unchecked task from the explicitly selected Spec-Kit feature, must run the task's checks, and must leave changes uncommitted for review. Spark is not used for implementation; Luna remains the read-only evidence worker. If no approved task exists, the job returns an explicit no-work result.

## Rollback

- Capture the sanitized observed cron inventory and copy the raw remote jobs file into the existing encrypted/permission-restricted Hermes backup area before apply.
- On catalog apply failure, report created/removed IDs and retain the backup; rerunning apply reconstructs the desired catalog.
- Gateway convergence writes/backs up units through the existing rollback-capable install helper; if health fails, restore the previous Sandbox unit and leave legacy state stopped rather than start competing owners.
- Git worktree changes are committed only after repository checks; uncommitted failing work remains untouched.

## Verification Plan

- Pure: route/catalog validation, exact plan determinism, no-work vs failure exits, redaction, false-success precedence.
- Adapter: command construction, confirmation gates, partial apply reporting, gateway conflict detection, bounded polling and timeout.
- Contract: CLI help/options/JSON envelopes and MCP tool parity.
- Full: complete unittest suite plus `./sb selftest` and plugin check.
- Remote: health detects current conflict/failure; gateway convergence stabilizes one owner; reconciliation yields exact catalog; verified harmless job returns terminal evidence; second reconciliation is a no-op; all managed worktrees are accounted for.

## Complexity Tracking

No constitutional violations require justification.

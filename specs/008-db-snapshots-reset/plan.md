# Implementation Plan: DB-Only Snapshots & Reset-to-Fresh-Install

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-db-snapshots-reset/spec.md`

## Summary

Extend the existing snapshot/restore system (`sandbox/commands/data.py`): add a
`--db-only` capture mode (skip `uploads.tgz`; record `mode` in META), auto-capture a
reserved DB-only **`@install` baseline** at the end of `cmd_install`, and add
`./sb reset` (+ `wp_reset` MCP + a dashboard button) that restores the baseline — a
fast in-place DB rollback to the post-install state. Restore already does
`wp db reset --yes` then import and tolerates a missing `uploads.tgz`, so DB-only
needs no restore-side behavior change.

## Technical Context

**Language/Version**: Python 3 (`sandbox/` package + `mcp/wp-server/`) + a small PHP/JS addition to the spec-002 snapshot mu-plugin.

**Primary Dependencies**: `cmd_snapshot`/`cmd_restore`/`cmd_snapshots` in `sandbox/commands/data.py`; `_valid_snapshot_name`/`_slug_snapshot_name` in `sandbox/core/_bridge.py` (analysis F3); the ensure/onboard flow (`_instances.py` wiring + `_onboard_instance` in `_misc.py`) for the baseline hook (analysis F1); the snapshot dir `runtime/snapshots/<instance>/<name>/`; the spec-002 dashboard mu-plugin template + the `_bridge_handle` routes in `_bridge.py` (analysis F6). NOTE: there is **no existing snapshot/restore/reset MCP tool** — `snapshot(db_only)` and `wp_reset` are **net-new** MCP tools (analysis F2).

**Storage**: snapshots unchanged (`db.sql`, optional `uploads.tgz`, `META`); new `META` key `mode=db-only|full`; reserved baseline under a protected dir (e.g. `runtime/snapshots/<instance>/__install__/`).

**Testing**: live-stack verification (constitution IV) — db-only snapshot omits uploads + restores; dirty DB → reset → post-install state; dashboard reset round-trip.

**Target Platform**: macOS/Linux; Docker (herd snapshots remain unsupported in v1, consistent with the existing feature).

**Project Type**: host CLI/MCP extension + a small dashboard mu-plugin addition.

**Performance Goals**: db-only capture meaningfully faster than full on instances with non-trivial uploads; reset is seconds (in-place DB import).

**Constraints**: reset is destructive (drops the DB) → gated; the reserved baseline is protected from ordinary overwrite/delete; herd unsupported in v1.

**Scale/Scope**: `--db-only` flag + `mode` in META/listing; baseline capture hook; `./sb reset` + `wp_reset`; dashboard toggle + button + 2 bridge routes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Only** — PASS. Snapshots/baseline/reset are per-instance; resolved via the registry.
- **II. Registry SoT** — PASS. Same instance-resolution as existing snapshot commands.
- **III. Single Entry, Modular** — PASS. Extends `sandbox/commands/data.py`; adds `cmd_reset`; `sb` single-entry.
- **IV. Live-Stack Verification** — PASS. quickstart resets a dirtied DB + verifies db-only restore on a live instance.
- **V. Idempotency & Docs-With-Code** — PASS. Baseline capture idempotent; CLAUDE.md snapshot section + skill land with code.
- **VI. Parity Before Removal** — PASS. Additive; full snapshots unchanged.
- **Boundaries / Secrets** — PASS. Snapshots in `runtime/snapshots/` (gitignored); no secrets.

No violations — proceed.

## Project Structure

### Documentation (this feature)

```text
specs/008-db-snapshots-reset/
├── plan.md
├── research.md          # db-only is near-free; baseline timing; reserved-name protection; reset vs recreate
├── data-model.md        # snapshot (mode), baseline, reset
├── quickstart.md        # db-only + reset + dashboard verification
├── contracts/
│   └── cli-contract.md  # snapshot(db_only), ./sb reset, wp_reset, bridge routes
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/commands/
├── data.py              # cmd_snapshot --db-only + META mode; cmd_restore mode-aware msg; cmd_reset; snapshots listing shows mode
└── lifecycle.py         # cmd_install → auto-capture @install baseline (db-only)
mcp/wp-server/tools/
└── data.py (or existing)# snapshot(db_only=…); NEW wp_reset(confirm)
runtime/snapshots/<instance>/
├── <name>/{db.sql,[uploads.tgz],META}   # META gains mode=
└── __install__/{db.sql,META}            # reserved protected baseline (db-only)
# spec-002 snapshot mu-plugin + sb web bridge: + "DB only" toggle, "Reset to fresh install" button, routes
```

**Structure Decision**: Extends the existing snapshot feature in place; reuses the
spec-002 dashboard mu-plugin + bridge. No new subsystems.

## Complexity Tracking

No constitution violations — none.

## Phase 0 — Research

See [research.md](./research.md): why db-only is nearly free (restore already
resets+imports and tolerates missing uploads), baseline capture timing, reserved-name
protection, and reset-vs-recreate.

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): snapshot `mode`, the `@install` baseline, reset.
- [contracts/cli-contract.md](./contracts/cli-contract.md): flags/tools/routes.
- [quickstart.md](./quickstart.md): live db-only + reset + dashboard verification.
- Agent context: SPECKIT block points at this plan.

## Phase 2 — Tasks

Generated by `/speckit-tasks`.

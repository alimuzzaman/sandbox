# Implementation Plan: Scheduled storage-pressure monitor and safe-tier reaper

**Branch**: `latest` (spec dir `043-storage-pressure-scheduler`) | **Date**: 2026-08-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/043-storage-pressure-scheduler/spec.md`

## Summary

Feature 042 built the measurement, the tiers, the manifest, and a pure
`disk_capacity_pressure()` classifier with an `auto_tier` gate — and then called none of
it periodically. This feature adds the periodic caller and everything it needs to be safe
and visible:

- a resolved, manifest-registered **storage-monitor policy** (machine defaults overridden
  per remote) that supplies the thresholds and the off-by-default automatic switch;
- one new **`sb resources monitor`** action that measures, classifies, writes a durable
  last-run record, optionally runs the safe tier, and optionally reaps — the exact command
  the timer runs;
- a **schedule renderer** that produces an activatable bounded systemd unit on Linux or a
  render-only launchd plan on macOS; launchd activation is refused because its rendered
  form cannot enforce the configured timeout;
- **warning output** on `sb resources status`, on `sb doctor` (from the record, never over
  the network), and in the monitor run's own output, always carrying free bytes, free
  share, total, threshold crossed, and the next command;
- **MCP tier parity**: `resource_cleanup_plan` / `resource_cleanup_apply` accept `tier`.

The safety invariant is unchanged and re-asserted in a second place: the automatic path
can only ever produce the `safe` tier, and a configured non-safe tier is a hard refusal at
policy-resolution time, before any host contact.

## Technical Context

**Language/Version**: Python 3.11+ (the `sb` CLI venv), stdlib only

**Primary Dependencies**: existing `sandbox.resources.reclaim` (pure policy),
`sandbox.resources.reclaim_service.ReclaimService`, `sandbox.resources.context`,
`sandbox.core._config` (`_local_yaml`, machine `sandbox.yml`), `sandbox.registry`
command registry, MCP `tools/manifest.py` group manifest, `sandbox.recovery.scheduler`
as the precedent for render-then-confirm unit handling.

**Storage**: `$SANDBOX_HOME/runtime/resources/monitor/<target_digest>.json` — one durable
owner-only (0600) last-run record per target. Deletions continue to be manifested by 042 at
`$SANDBOX_HOME/runtime/resources/deletions/<run_id>.jsonl`.

**Testing**: `python -m unittest tests.test_storage_monitor_policy
tests.test_storage_monitor_schedule tests.test_storage_monitor_runner
tests.test_mcp_resource_tier` — targeted patterns only. A repo-wide
`unittest discover` aborts on a pre-existing `sb` argparse error (feedback `6ef03d44`).

**Target Platform**: the operator's controlling machine (macOS or Linux); the monitored
target is local or a configured remote reached by the existing SSH-shipped probe.

**Project Type**: CLI + MCP tooling (single Python package, `sandbox/`).

**Performance Goals**: a scheduled monitor run answers from the cached host directory index
(`--fast`-equivalent, `directory_cache="cache_only"`) so a cadence of one hour costs one
bounded SSH round trip; `sb doctor`'s storage section costs zero network and one small file
read per target.

**Constraints**: the automatic path is `safe`-only and off by default; timer activation is a
protected operation requiring explicit confirmation; scheduled runs must not overlap; no
warning may be emitted without its numbers; nothing in the default configuration deletes.

**Scale/Scope**: a handful of configured targets per machine; one record per target; four
new/changed policy surfaces and one new CLI action.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Per-project is the only instance model | Not engaged. This feature is host-scoped (`scope="global"` command, like `resources` already is) and never resolves or boots an instance. The `sb doctor` addition reads records only and adds no instance dependency. |
| II. The registry is the single source of truth | Respected. Target resolution goes through the existing remote registry (`sandbox.core._remote.get_remote`); an unknown target is an error naming the target, never a fallback to local (FR-006). |
| III. Single entry file, modular package | Respected. New code is `sandbox/resources/monitor.py`, `sandbox/resources/schedule.py`, `sandbox/config/storage_monitor.py`; the CLI surface is a new action on the already-registered `resources` command spec. `sb` is untouched. |
| IV. Live-stack verification is the only proof of done | PENDING (T023). Local tests prove rendering and refusal paths only. No `scaleway-sandbox` monitor or schedule command has been run as evidence, and no timer has been activated. |
| V. Idempotency and docs-with-code | Respected. The monitor is re-runnable (it rewrites one record); activation is idempotent (an identical existing schedule reports itself); `docs/resource-monitoring.md`, `README.md`, `CLAUDE.md`, and `skills/sandbox-cli/SKILL.md` land in the same commit. |
| VI. Feature parity before removal | Nothing is removed. `--scope cache|stale` keeps working on CLI and MCP; `tier` is added beside it. `disk_capacity_pressure()` keeps its existing default arguments so 042's callers are unchanged. |

**Module boundaries** (CLAUDE.md): the new configuration registers through an explicit
manifest tuple in `sandbox/config/manifest.py` rather than being read ad hoc; the MCP tools
register through `mcp/wp-server/tools/manifest.py` with a declared dependency key; no new
consumer of `sandbox_core.py`, `sandbox.registry.COMMANDS`, `sandbox.hermes.facade`, or the
MCP `app.py` helper namespace is introduced; no registry or state JSON is read directly
(the record store is owned by `sandbox/resources/monitor.py`, the one module that writes it).

**Result**: PASS. No entries in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/043-storage-pressure-scheduler/
├── plan.md              # This file
├── spec.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md           # `sb resources monitor|schedule` contract
│   ├── config.md        # storage-monitor policy keys, defaults, validation
│   └── mcp.md           # tier parameters on the resource tools
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks output
```

### Source Code (repository root)

```text
sandbox/
├── config/
│   ├── manifest.py            # + MACHINE_CONFIG_PROVIDERS entry (registration)
│   └── storage_monitor.py     # NEW: normalize_storage_monitor(), pure validation
├── resources/
│   ├── reclaim.py             # + resolve thresholds from policy (defaults unchanged)
│   ├── monitor.py             # NEW: policy resolution, run record store, doctor checks
│   ├── schedule.py            # NEW: pure rendering, transactional activation, installed-plan receipt
│   └── reclaim_service.py     # + monitor() orchestration entry point
├── commands/
│   ├── resources.py           # + `monitor` and `schedule` actions, warning rendering
│   └── lifecycle.py           # + one "Storage pressure" doctor section (7 lines, read-only)
mcp/wp-server/
├── tools/resources.py         # + tier on plan/apply
├── tools/manifest.py          # + reclaim_service_factory dependency for the group
└── server.py                  # + one factory entry
tests/
├── test_storage_monitor_policy.py    # NEW: thresholds, boundaries, non-safe refusal
├── test_storage_monitor_schedule.py  # NEW: rendering + confirmation gate
├── test_storage_monitor_runner.py    # NEW: run behaviour, record, dry-run default
└── test_mcp_resource_tier.py         # NEW: MCP tier plumbing and refusals
docs/resource-monitoring.md            # + monitoring/scheduling section
```

**Structure Decision**: the feature stays inside the existing `sandbox/resources/` package
with policy kept pure (no I/O) so every safety test runs without a host, mirroring 042's
`reclaim.py` / `reclaim_service.py` split. The only files touched outside the resources
package and its docs are the config manifest (registration), the MCP resource group
(registration + tier), and a single read-only section added to `sb doctor`.

## Complexity Tracking

No constitution violations; table intentionally empty.

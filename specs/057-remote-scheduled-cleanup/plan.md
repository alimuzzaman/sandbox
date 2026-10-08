# Implementation Plan: Scheduled Safe Cleanup on a Remote

**Branch**: `latest` (feature 057) | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/057-remote-scheduled-cleanup/spec.md`

## Summary

Add a per-remote cleanup routine. It is enabled, disabled and inspected through
the existing authenticated `/resources` control route, runs on the host as a
`systemd --user` timer, and on each run performs one safe-tier reclamation
pass through the existing `ReclaimService` with a new `scheduled_routine`
trigger. A shared non-blocking host reclaim guard, taken inside the probe's
`reclaim_action`, serializes every reclaiming path on the host and also probes
the existing host-side apply transaction locks. Routine config and the last 30
run records live under `$SANDBOX_HOME/runtime/resources/cleanup-routine/` on the
host. No restricted file (`sandbox/commands/hosting.py`,
`sandbox/delivery/hosting.py`, `sandbox/core/_remote.py`, `tests/test_hosting.py`)
is modified; the client reuses the public `remote_resource_request`.

## Technical Context

**Language/Version**: Python 3.11+ (the `sandbox/` package; host runs the same source under `~/sandbox/sb-src`)

**Primary Dependencies**: stdlib only (`fcntl`, `fnmatch`, `subprocess`, `json`); `systemctl --user`, `systemd-analyze` and `loginctl` on the host

**Storage**: owner-only (0600) JSON files under `$SANDBOX_HOME/runtime/resources/cleanup-routine/` on the host; the existing deletion manifests under `runtime/resources/deletions/`

**Testing**: `python3 -m unittest`; systemd tools faked by patching `_run_bounded`/`subprocess.run` (pattern from `tests/test_storage_monitor_schedule.py`); the probe exercised through `LocalProbeAdapter(home=tmp)` (pattern from `tests/test_resource_reclaim_service.py`); control route via `service_request=` injection (`tests/test_resource_remote.py`)

**Target Platform**: operator on macOS or Linux; host is Linux with systemd and user lingering

**Project Type**: CLI plus a host control-plane service

**Performance Goals**: enable, disable and status each finish within the existing control request budget (seconds); a run finishes within its bound (default 30 min)

**Constraints**: bounded payloads (`/resources` body is capped at 64 KB); no raw SSH (FR-025); no secrets or SSH targets in status (FR-026); restricted files untouched

**Scale/Scope**: one routine per host; 30 run records; one run per cadence period

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Per-project instance model | Pass | Host-level resource feature; no instance resolution involved |
| II. Registry is the source of truth | Pass | The classifier keeps reading the registry and job evidence through existing typed repositories |
| III. Single entry, modular package | Pass | New `sandbox/resources/cleanup_routine/` package; one new `resources routine` action in the existing command module |
| IV. Live-stack proof | Pass (planned) | Quickstart includes a live disposable-remote run; this needs a remote service migrate, coordinated with remote owners |
| V. Idempotency, docs with code | Pass | Enable replaces settings atomically (FR-021); docs listed under Structure land in the same changes |
| VI. Parity before removal | Pass | Nothing is removed; pressure-triggered cleanup and the local monitor schedule are unchanged apart from taking the shared guard |

Re-check after design: unchanged, all pass.

## Project Structure

### Documentation (this feature)

```text
specs/057-remote-scheduled-cleanup/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/routine-control.md
└── checklists/requirements.md
```

### Source Code (repository root)

```text
sandbox/resources/cleanup_routine/
├── __init__.py
├── contract.py      # ACTIONS, request/response validation, cadence/bound/exclusion validators
├── units.py         # host unit+timer render, install, remove (reuses schedule.py helpers)
├── store.py         # routine config + run records (keep 30), finalize stale records
├── run.py           # the timer's entry: guard pre-check, plan, cleanup, run record
└── host.py          # host-side handler for the /resources routine actions
sandbox/resources/host_guard.py   # shared reclaim guard + host-apply lock probe

# minimal edits
mcp/wp-server/server.py            # dispatch routine actions in _resource_contract
sandbox/resources/remote.py        # guard in probe reclaim_action; client routine_* methods
sandbox/resources/reclaim_service.py  # trigger kwarg on reap(); host_reclaim_busy -> skipped
sandbox/resources/schedule.py      # promote reused helpers to public names (no behavior change)
sandbox/commands/resources.py      # `resources routine` action + hidden --routine-run

tests/
├── test_cleanup_routine_contract.py
├── test_cleanup_routine_units.py
├── test_cleanup_routine_store.py
├── test_cleanup_routine_run.py
├── test_cleanup_routine_cli.py
└── test_host_reclaim_guard.py     # includes lock-path parity with hosting/nginx/_remote constants (read only)

docs/resource-monitoring.md, CLAUDE.md (gotcha 23), README.md,
skills/sandbox-cli/SKILL.md (+ .agents copy), docs/remote-hosting.md
```

**Structure Decision**: a new subpackage under the existing `sandbox/resources/`,
registered through the existing `resources` command manifest entry and the
existing `/resources` control route. That route already follows the host-memory
contract pattern (`sandbox/resources/host_memory/remote.py`), which this feature
copies.

## Complexity Tracking

No constitution violations.

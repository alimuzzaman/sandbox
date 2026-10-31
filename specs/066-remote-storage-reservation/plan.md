# Implementation Plan: Remote Storage Reservation

**Branch**: `latest` | **Date**: 2026-10-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/066-remote-storage-reservation/spec.md`

## Summary

Phase A protects hosted production on a shared remote from test workloads with
three software controls and one recovery command, all without production or
daemon changes:

1. **Storage-floor admission**: a per-remote `storage_floor_gb`; every remote
   deploy checks a fresh free-space probe, minus live reservations, minus a
   per-repository estimate, against the floor before any remote write, and
   records a short-lived reservation under a remote lock. Refusals surface as
   `remote_storage_floor` through the existing admission-error path.
2. **Finished-job idle reap**: a workspace held only by terminal, retained jobs
   whose last `finished_at` is older than the idle threshold (default 6 h) is
   no longer `active_job`; it becomes a `safe`-tier `finished_job_idle`
   candidate. Queued, running, or undeterminable jobs still protect it.
3. **Complete teardown**: reclaiming a workspace removes every container of its
   exclusively owned compose project(s), then their volumes and networks, then
   the source; any teardown failure keeps the source and reports the run partial.
4. **`sb host heal`**: lists hosted containers that exited after exhausting an
   `on-failure` restart limit; `--confirm` restarts exactly those when free
   space is strictly above the floor.

Phase B is a plan document only (`phase-b-isolation.md`).

## Technical Context

**Language/Version**: Python 3 (stdlib only; remote programs run under the host's python3)
**Primary Dependencies**: existing `sandbox.core._remote` ssh helpers, `sandbox.resources` reclaim policy/remote runtime, `sandbox.hosting`
**Storage**: remote registration record (`storage_floor_gb`), remote `$SANDBOX_HOME/runtime/storage/reservations.json` under a flock
**Testing**: `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test*.py'`; live verification on xcloud-london
**Target Platform**: macOS/Linux controller, Linux remote
**Project Type**: CLI + remote runtime programs
**Performance Goals**: admission probe adds <= 30 s worst case (bounded `du` on <= 3 workspaces, else default estimate)
**Constraints**: fail closed when a floor is configured and measurement fails; no change to hosted production containers or volumes except heal on `--confirm`
**Scale/Scope**: ~45 workspaces, one remote with a floor; other remotes unchanged until a floor is set

## Constitution Check

- **I. Per-project instances**: unaffected.
- **II. Registry as source of truth**: floor stored in the remote registration via `put_remote`; reservations are remote runtime state, not registry.
- **III. Single entry, modular package**: new modules `sandbox/resources/storage_floor.py` and `sandbox/hosting/heal.py`; CLI wiring only in `cli.py`/command modules.
- **IV. Live-stack verification**: admission, reap and heal dry run verified on xcloud-london after `remote service migrate`.
- **V. Idempotency and docs-with-code**: reservation write and teardown replay are idempotent; README/CLAUDE.md gotcha/CHANGELOG land with code.
- **VI. Parity before removal**: nothing removed; reclaim semantics only widen for the new `finished_job_idle` reason.

Gate: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/066-remote-storage-reservation/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── phase-b-isolation.md
├── contracts/cli.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/resources/storage_floor.py      # new: decision + remote probe/reserve program
sandbox/core/_remote.py                 # deploy_exact_working_tree calls the floor gate
sandbox/transports/remote_jobs.py       # translate StorageFloorAdmissionError
sandbox/commands/remote.py, cli.py      # remote add --storage-floor-gb, remote set-storage-floor
sandbox/resources/remote.py             # finished-job idle evidence; project teardown in reclaim_run
sandbox/resources/reclaim.py            # finished_job_idle tier rule
sandbox/hosting/heal.py                 # new: heal candidates + restart
sandbox/commands/hosting.py, cli.py     # sb host heal
tests/test_storage_floor.py, tests/test_reclaim_finished_idle.py,
tests/test_reclaim_project_teardown.py, tests/test_host_heal.py
```

**Structure Decision**: extend existing modules at the seams the network-capacity admission and reclaim runtime already use; no new transport payload keys (no control-protocol bump).

## Complexity Tracking

None.

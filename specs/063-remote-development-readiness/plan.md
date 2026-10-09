# Implementation Plan: Remote Development Execution Readiness

**Branch**: `latest` (feature dir `063-remote-development-readiness`) | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/063-remote-development-readiness/spec.md`

## Summary

Give remote development execution a capacity path that needs no Docker daemon
restart, one readiness answer before any submission, and honest selection
refusals.

1. **Development ranges.** The operator assigns a Sandbox-owned address range
   to a remote with a confirmed `remote network-range assign`. The
   authoritative range and allocation table live on the remote, under the
   remote Sandbox home, managed by a fixed program over the existing
   authenticated SSH transport under a flock. This is the same pattern as
   061's pins. Every network Sandbox creates for a workspace, job, preview or
   CI cell gets an explicit subnet from the range. The subnet is written into
   a generated Compose override (`networks.<name>.ipam.config`) before
   `compose up`.
2. **Capacity admission.** The admission adds unallocated range capacity to
   configured or proven pool capacity. Allocation happens at admission, so
   the gate and the reservation are one atomic step.
3. **Readiness.** `sb remote readiness` and the MCP tool `remote_readiness`
   compose six rows: registration, reachability, runtime compatibility,
   capacity, ownership repair and handoff. Each row comes from an existing
   read-only probe; the shared module runs them under one 60-second deadline.
   Every submission path calls the same gate before transfer and reuses a
   proof within its window, bound to the installed runtime revision.
4. **Selection.** `TargetService` already raises `unknown_remote` and
   `ambiguous_remote`. They now carry the source of the name, the registered
   list and the remedy, and every result reports `remote_selection`. A refused
   remote submission exits non-zero and never falls back to a local run.
5. **Daemon-pool plan.** `remote docker-pool` lists hosted targets and the
   count of other running containers with a digest. `--confirm` must carry
   that digest.

## Technical Context

**Language/Version**: Python 3.12+ (CLI venv); POSIX shell and `python3` on the remote host

**Primary Dependencies**: standard library (`ipaddress`, `fcntl`, `json`, `hashlib`); existing `ssh_run`, `remote_network_capacity_admission`, `TargetService`, `WorkspaceService`, hosting inventory

**Storage**:
- Remote: `$SANDBOX_HOME/runtime/network-ranges/state.json` holds the ranges and allocations (file 0600, directory 0700), written under `state.lock`.
- Controller: the remote block in `sandbox.local.yml` keeps a read-only `network_ranges` echo of what was last assigned or listed, for display only.
- Readiness proofs: `$SANDBOX_HOME/runtime/readiness/<remote>.json` on the controller.

**Testing**:
- `unittest` through `./sb selftest`, plus `tests.test_architecture_boundaries`.
- The remote programs run for real against a temporary home through a local `sh -c` stand-in for `ssh_run`, as `test_remote_runtime_pins.py` does.
- Live proof needs a disposable remote.

**Target Platform**: macOS/Linux controller; Linux remote with Docker Engine and Compose v2

**Project Type**: CLI + MCP server (single project)

**Performance Goals**:
- Readiness returns within 30 s, with a hard stop at 60 s.
- A range allocate or release is one SSH call, bounded at 15 s.
- Admission adds no extra round trip: allocation rides the existing capacity probe call.

**Constraints**:
- Nothing restarts the daemon or hosted containers.
- Refusals carry no subnets, paths or probe output.
- Built-in default pools never count as capacity.
- A proof is never reused across a runtime revision change.
- Hosted Compose projects' networks are untouched.

**Scale/Scope**:
- One or more ranges per remote.
- Allocation listing is bounded at 256 rows and 64 KiB.
- The allocation table refusal is bounded at 32 rows.

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Per-project instance model | Pass | Allocations are owned by a workspace or job of one project; there is no shared instance |
| II. Registry is the source of truth | Pass | Remote registration stays in the existing block. Range state is remote-owned and read only through `sandbox/remote_network/` |
| III. Single entry, modular package | Pass | New `sandbox/remote_network/` package (ranges, allocator program, compose override) and `sandbox/readiness/` (rows, gate). The CLI adds the `remote network-range` and `remote readiness` actions through the remote command module and the MCP tool through a registered group |
| IV. Live-stack proof | Pass (planned) | Quickstart runs on a disposable remote. `xcloud-london` follows the remote install protocol and needs owner approval because it serves production |
| V. Idempotency, docs with code | Pass | Assigning the same range is a no-op; allocation is idempotent per owner. `docs/remote-hosting.md`, `docs/remote-job-runtime.md`, CLAUDE.md and CHANGELOG land with the code |
| VI. Parity before removal | Pass | Daemon pools keep working and the admission's fail-closed rules are unchanged; nothing is removed |

Re-check after design: unchanged, all pass. The remote programs change the
payload keys of `remote_jobs`. FR-006 of 061 therefore requires a
`CONTROL_PROTOCOL_SPOKEN` bump and a re-recorded `control_shapes.json` in the
same change (see research R7).

## Project Structure

### Documentation (this feature)

```text
specs/063-remote-development-readiness/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/network-ranges.md
├── contracts/readiness.md
├── checklists/requirements.md
└── tasks.md               # speckit-tasks
```

### Source Code (repository root)

```text
sandbox/remote_network/
├── __init__.py
├── ranges.py        # range validation (ipaddress), overlap classes, proposal, subnet math
├── program.py       # fixed remote program: inventory, assign, allocate, release, list (flock)
├── store.py         # RangeStore client over ssh_run; typed errors; bounded listing
└── override.py      # Compose override: explicit ipam subnet per created network; externals skipped
sandbox/readiness/
├── __init__.py
├── rows.py          # six row evaluators from existing probes; states ready/not_ready/unknown/not_applicable
├── check.py         # run rows under one deadline; remedies; reuse window; proof file
└── gate.py          # submission gate used by every remote submission path
sandbox/resources/network_capacity.py   # union of pool and range capacity; missing_pool_evidence wording
sandbox/core/_remote.py                 # admission calls allocate; docker-pool plan digest and hosted inventory
sandbox/application/target_service.py   # unknown_remote / ambiguous_remote detail; remote_selection
sandbox/application/workspace_service.py# release/reap/retention free allocations
sandbox/commands/remote.py, sandbox/cli.py   # network-range, readiness, docker-pool --confirm DIGEST
sandbox/commands/lifecycle.py           # doctor "Remote targets" uses readiness rows for declared remotes
submission paths: test/run_tests, e2e/run_e2e, ci/ci_run, exec --remote, job-start, ensure --remote
mcp/wp-server/tools/                    # remote_readiness tool (shared result shape)
docs/remote-hosting.md, docs/remote-job-runtime.md, CLAUDE.md, CHANGELOG.md
tests/test_remote_network_ranges.py, tests/test_remote_network_program.py,
tests/test_remote_network_override.py, tests/test_readiness.py,
tests/test_readiness_gate.py, tests/test_remote_selection_refusals.py,
tests/test_docker_pool_plan_digest.py
```

**Structure Decision**: two new packages own the new state and the readiness
rule. Existing modules only call them: the admission calls the allocator, the
submission paths call the gate, and `TargetService` enriches its refusals. The
range program follows 061's pin program pattern (fixed program, flock, owner-only
files, read-only `list` without mkdir).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Range state lives on the remote, not in the controller registry | Several controllers share one remote, so allocation must be atomic per remote (FR-005), and the remote must also respect allocations made by other controllers | A controller-local table cannot see another controller's allocations, so two controllers could hand out one subnet |
| Allocation rides the capacity probe call | Admission and reservation must be one step, or two submissions can both pass the gate for the last subnet | A separate allocate call after admission reopens the race the concurrency test (SC-006) forbids |

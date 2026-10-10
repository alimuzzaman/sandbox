# Implementation Plan: Per-Target Hosting Operations

**Branch**: `latest` (feature dir `060-per-target-hosting-operations`) | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/060-per-target-hosting-operations/spec.md`

## Summary

Hosted apply holds three locks today:

- the per-target effect lock;
- the controller-wide recovery `state.lock`, which guards the one shared
  `hosts.json` document;
- since 061, a per-remote registration lock.

It holds all three across build, transfer, delivery and verification. This
plan does four things:

1. **Per-target state.** It splits `hosts.json` into per-target documents, so
   no controller-wide lock is needed beyond a short read/modify/write.
2. **Remote coordination.** It moves the lease, the hold, the current
   operation and the build cap to one fixed coordination program on the
   remote. The program follows the 061 pins pattern: it is sent over the
   authenticated SSH transport, runs under a remote flock, and keeps
   owner-only files under the remote's `$SANDBOX_HOME/runtime/hosting-leases/`.
3. **Shared steps.** It routes the shared steps (edge and DNS mutation,
   shared ingress reload) through a remote-wide lease bounded at 60 seconds.
   Feature 064's executor consumes this through its `acquire`/`release` seam.
4. **Retained refusals.** It records busy, cap and authority-unavailable
   refusals as spec 054 retained pre-admission outcomes.

A one-way, re-runnable conversion moves the controller's state into the
per-target layout and registers the controller with the remote's
coordination store. Feature 062's scope-key change rides the same
conversion, which this feature owns.

## Technical Context

**Language/Version**: Python 3.12+ (CLI venv); the remote program is POSIX `sh` plus `python3` on the remote, as in 061 pins

**Primary Dependencies**: existing `sandbox.core._remote.ssh_run`, the 061 `remote_runtime` verdict and pins patterns, `sandbox.hosting.recovery.repository.RecoveryRepository`, `sandbox.delivery.repository.DeliveryRepository` (SQLite, already scope-keyed), and the 054 admission/retained-refusal API in `sandbox/delivery/admission.py`

**Storage**:
- **Remote**: `$SANDBOX_HOME/runtime/hosting-leases/` (directory 0700, files 0600):
  - `targets/<sha16>.json`: lease, hold, current operation, queue and history tail;
  - `remote.json`: build cap, cap history, build slots and the remote-wide lease;
  - `coord.lock`: flock.
- **Controller**: `$SANDBOX_HOME/runtime/host-targets/<sha16>.json`, one document per
  target, replacing `runtime/hosts.json`. `runtime/hosts-conversion.json`
  records the conversion progress per remote.
  (Not `runtime/hosts/`: a hosting remote already keeps per-target runtime
  directories at `runtime/hosts/<project>/<environment>`, and a controller can
  share its home with a host.)

**Testing**:
- `unittest` through `./sb selftest`, plus the architecture test.
- The remote program runs for real against a temp home through a local
  `sh -c` stand-in, as in the 061 pins tests.
- Multi-process tests use `multiprocessing` with two homes to simulate two
  controllers.
- 051/052/054 fixture state drives the conversion tests.

**Target Platform**: macOS/Linux controller; Linux remote

**Project Type**: CLI + MCP server (single project)

**Performance Goals**:
- Each coordination call is bounded at 15 s, and a typical call takes under 2 s.
- The listing returns in under 5 s.
- The remote-wide lease is held for at most 60 s.
- The lease TTL is 90 s with renewal every 20 s, so a live holder survives
  three missed renewals.
- Waiters poll every 2 s and start within 5 s of release.

**Constraints**:
- There is no controller-local fallback when the coordination program is
  unreachable or absent.
- Every result is bounded and secret-free.
- Holds have a 1 h default and a 4 h maximum from the claim.
- Waits have a 600 s default, a 3600 s maximum, and 0 means refuse
  immediately.
- The build cap defaults to 2.

**Scale/Scope**:
- up to 64 targets per remote;
- queues bounded at 32 per target;
- history tail of 64 entries per target.

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Per-project instance model | Pass | Serialization unit is the target (remote, project, environment) |
| II. Registry is the source of truth | Pass | Coordination state is registered through the state contract and read only via `sandbox/hosting/coordination/`. The controller's per-target documents are read only via `sandbox/core/_hosting.py` accessors. No consumer parses the files directly |
| III. Single entry, modular package | Pass | New `sandbox/hosting/coordination/` package (program, client, leases, holds, cap, listing) and `sandbox/hosting/state_partition/` (layout, conversion). `commands/hosting.py` orchestrates only |
| IV. Live-stack proof | Pass (planned) | Quickstart uses a disposable remote. `xcloud-london` follows the remote install protocol with owner approval |
| V. Idempotency, docs with code | Pass | The conversion is re-runnable, and claims, renewals and releases are idempotent per identity. The `docs/remote-hosting.md` lock section is rewritten, and CLAUDE.md gotcha, CHANGELOG and MCP tool docs land with the code |
| VI. Parity before removal | Pass | Removing `state.lock` from long phases and replacing `hosts.json` require parity tests on 051/054 fixtures first. A read-only legacy reader stays until the conversion is proven |

Re-check after design: unchanged. The new remote program and its payloads are
added to the 061 FR-006 shape sources, and `CONTROL_PROTOCOL_SPOKEN` is
bumped. This is coordinated with 063, which also bumps it: whichever lands
second takes the next number.

## Project Structure

### Documentation (this feature)

```text
specs/060-per-target-hosting-operations/
├── prd.md, spec.md, plan.md, research.md, data-model.md, quickstart.md
├── contracts/coordination.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/coordination/
├── __init__.py
├── program.py      # fixed remote program text (flock, ops: admit, renew, release, hold-claim/renew/release/break, cap-get/set, shared-acquire/release, build-acquire/release, list, phase-report)
├── client.py       # CoordinationClient over ssh_run: 15 s bound, validated output, typed errors, lease_authority_unavailable
├── lease.py        # TargetLease context: admit with wait/no-wait, background renewal, fencing token, lost-lease detection
├── shared.py       # RemoteWideLease (60 s bound) implementing the 064 acquire/release seam
├── holds.py        # hold identity, flag/env presentation (SANDBOX_HOLD_ID), break with reason
└── listing.py      # per-remote listing projection
sandbox/hosting/state_partition/
├── __init__.py
├── layout.py       # per-target document read/write (owned files, atomic replace, short per-target flock)
└── conversion.py   # one-way, re-runnable conversion; mixed-state detection; 062 scope-key step
sandbox/core/_hosting.py              # load/save per-target accessors; legacy reader for conversion only
sandbox/hosting/recovery/repository.py # state_lock narrowed to per-target documents; long-phase holders removed
sandbox/commands/hosting.py           # every target mutation admits through TargetLease; shared steps through RemoteWideLease; --wait/--no-wait, --hold-id; hold/list/cap actions
sandbox/delivery/admission.py         # busy/cap/authority refusals as retained pre-admission outcomes
sandbox/resources/*                    # reclaim yields to remote-wide lease (host_reclaim_busy)
sandbox/cli.py, mcp/wp-server/tools/  # host hold claim|renew|release, host operations (listing), remote build-cap
sandbox/remote_runtime/shapes.py, control_shapes.json, protocol.py
docs/remote-hosting.md, CLAUDE.md, CHANGELOG.md
tests/test_hosting_coordination_program.py, tests/test_hosting_coordination_client.py,
tests/test_hosting_target_lease.py, tests/test_hosting_holds.py, tests/test_hosting_build_cap.py,
tests/test_hosting_state_partition.py, tests/test_hosting_conversion.py,
tests/test_hosting_concurrency.py, tests/test_hosting_retained_refusals.py
```

**Structure Decision**: coordination logic lives in a new package owned by
hosting. The remote side is one fixed program, following the proven 061 pins
pattern, rather than a new control-service endpoint, so the authority works
over the existing SSH transport. Its absence on an old runtime is detected
exactly and reported as `lease_authority_unavailable`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Remote-side lease authority with renewal | Several controllers contend for one target, and controller-local locks cannot serialize them (FR-009) | A controller-local lock is today's bug. An NFS or shared-directory lock does not exist across machines |
| Fencing token on every protected effect | A returning holder whose lease expired must perform no forward effects (FR-017). Renewal alone cannot stop a paused process from resuming | Checking expiry only at the next renewal leaves a window in which a paused holder writes after a successor was admitted |
| Two-step state migration (layout plus scope key) in one conversion | 060 and 062 both rewrite the same retained records; two conversions would double the mixed-state surface | Separate conversions mean three states (old, half, new) per feature and an ordering dependency between features |

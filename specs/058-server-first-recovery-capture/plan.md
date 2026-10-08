# Implementation Plan: Server-First Recovery Capture and Later Drive Promotion

**Branch**: `058-server-first-recovery-capture` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/058-server-first-recovery-capture/spec.md`

## Summary

Split today's one-shot hosted capture (`recovery create --remote`) into three
recovery commands with MCP parity: `capture` starts a detached capture job on the
server and returns its identity; `status` reads the job's retained state; and
`promote` later moves the finished archive to the operator in 16 MiB resumable
chunks, re-verifies it against the receipt, and publishes it through the existing
023 encryption and manifest-last pipeline. A new stdlib helper is sent over the
same `ssh_process` path the hosted controllers use today. It runs the capture in
phases, adds a MariaDB table inventory compared against the dump, hashes the
archive and its members with streaming reads, and keeps per-capture files
owner-only under `runtime/recovery-captures/`. A per-remote retention bound
(default 7 days) blocks new captures while a complete, unpromoted capture is
older than the bound. Promote also finishes a `locally_pending` ciphertext.

## Technical Context

**Language/Version**: Python 3.12 locally (repository runtime); remote helper targets Python ≥3.8 stdlib only.

**Primary Dependencies**: existing `sandbox.recovery` package (catalog, planner, `StagingCaptureCoordinator`, `GpgCrypto`, `RcloneDrive`, `verify_manifest`), `sandbox.transports.remote_recovery`, `sandbox.core._remote.ssh_process` / `get_remote` / `resolve_sandbox_home` / `remote_mcp_service_status` (consumed, not modified), MCP `tools/recovery.py`.

**Storage**: owner-only files. Server: `<remote $SANDBOX_HOME>/runtime/recovery-captures/<slot>/`. Operator: `$SANDBOX_HOME/recovery/{promote,pending,materialized,staging}/`. Drive: existing `sets/<id>/` layout.

**Testing**: `python3 -m unittest`; injected fake transports (pattern of `tests/test_remote_recovery.py`); the helper is exercised for real against a temp root with fake `docker`/`tar`/`loginctl` executables on `PATH`.

**Target Platform**: operator on macOS/Linux; remote on Linux with Docker and the reviewed `amarsonar-bangla` production containers.

**Project Type**: CLI + MCP tool group inside the single-entry `sb` package.

**Performance Goals**: capture start < 30 s (SC-001); 2 GB archive promoted in 128 chunk calls over the multiplexed SSH socket.

**Constraints**: peak RSS < 256 MB on the server job and the promote process for a 2 GB archive (SC-007); helper source < 64 KiB; no secrets in argv, records, logs or output; no modification of `sandbox/commands/hosting.py`, `sandbox/delivery/hosting.py`, `sandbox/core/_remote.py`, `tests/test_hosting.py`.

**Scale/Scope**: one supported hosted profile (`amarsonar-bangla-prod` plus `control-plane`); one active capture per remote; listing bounded to 500 slots.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How |
|-----------|--------|-----|
| I. Per-project instance model | Pass | Recovery is global-scope (`scope="global"` in `sandbox/commands/recovery.py`); it targets registered remotes, never an implicit instance. |
| II. Registry single source of truth | Pass | Remote resolution through `_remote.get_remote`; no direct registry JSON reads. Retention override read through the config loader. |
| III. Single entry, modular package | Pass | New modules in `sandbox/recovery/` and `sandbox/transports/`; the command registers through the existing `CommandSpec`; MCP tools through `tools/manifest.py`. |
| IV. Live-stack proof | Conditional | Unit/contract tests prove logic; Definition of Done needs one approved live run (quickstart 3–7). Live approval is a pending decision. |
| V. Idempotency, docs with code | Pass | Start replays by slot; status is read-only; promote is resumable and idempotent; `mark-promoted` is idempotent. `docs/recovery.md`, the README "Scoped recovery" section and `workflows/recovery/WORKFLOW.md` land with the code. |
| VI. Parity before removal | Pass | `recovery create --remote` is untouched (FR-038). Nothing is disabled or stubbed. |
| Module boundaries (CLAUDE.md) | Pass | No new consumers of `sandbox_core.py`, `sandbox.registry.COMMANDS`, `sandbox.hermes.facade` or MCP `app.py` helpers beyond the existing `mcp` decorator import. Capability checks (confirm, credential, revision, retention, lock) run before side effects. |
| Secrets | Pass | DB password: brokered env → stdin → job memory. Passphrase: inherited env only. Neither touches argv, disk records or output. |

Post-design re-check (after Phase 1): unchanged, all pass; IV remains conditional on approval.

## Project Structure

### Documentation (this feature)

```text
specs/058-server-first-recovery-capture/
├── prd.md               # input (unchanged)
├── spec.md
├── plan.md              # this file
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli-mcp.md
│   ├── server-capture-helper.md
│   └── manifest-provenance.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
sandbox/recovery/
├── server_capture_helper.py   # NEW: stdlib remote helper (start/status/list/read-*/mark-promoted + job phases)
├── server_capture.py          # NEW: ServerCaptureService (start/status/list/promote orchestration, retention view, promote progress)
├── capture.py                 # CHANGE: manifest computed before upload; pending sidecar; publish_pending()
├── service.py                 # CHANGE: list() adds server captures and tolerates missing Drive with --remote; delegates capture/status/promote
├── context.py                 # CHANGE: always compose ServerCaptureService; promote uses capture coordinator when configured
└── errors.py                  # unchanged (envelope reused)

sandbox/transports/
├── remote_server_capture.py   # NEW: RegisteredServerCaptureTransport (helper delivery, revision gate, brokered stdin, chunk reads)
└── remote_recovery.py         # CHANGE: extract control-plane declaration builder and remote state probe for reuse; behavior unchanged

sandbox/commands/recovery.py   # CHANGE: actions capture, status, promote; human output
mcp/wp-server/tools/recovery.py   # CHANGE: recovery_capture, recovery_capture_status, recovery_promote
mcp/wp-server/tools/manifest.py   # CHANGE: tool names in BUILTIN_TOOL_NAMES["recovery"]
docs/recovery.md               # CHANGE: server-first section, amended 023 FR-012/014/016 behavior
README.md, workflows/recovery/WORKFLOW.md  # CHANGE: capture/status/promote pointers

tests/
├── test_server_capture_helper.py        # NEW
├── test_server_capture.py               # NEW
├── test_remote_server_capture.py        # NEW
├── test_recovery_promote.py             # NEW
├── test_recovery_cli_server_capture.py  # NEW
├── test_server_capture_memory.py        # NEW
├── test_recovery_capture.py             # CHANGE: pending sidecar
├── test_recovery_service.py             # CHANGE: list with server captures
├── test_remote_recovery.py              # CHANGE: extraction keeps existing behavior
└── test_mcp.py                          # CHANGE: schema snapshot
```

**Structure Decision**: Everything lives in the existing recovery package,
transport layer, recovery command and recovery MCP group. The files owned by
another thread (`sandbox/commands/hosting.py`, `sandbox/delivery/hosting.py`,
`sandbox/core/_remote.py`, `tests/test_hosting.py`) are only imported from, never
edited; no design step requires changing them.

## Design

### Capture start (US1)

`ServerCaptureService.start(remote, backup_id, profiles, confirm)`:
1. Gate on confirm, ids, supported profiles (`_SUPPORTED` in `remote_recovery.py`)
   and the brokered credential, before any network call.
2. Build the plan with `build_plan`; probe the remote with the shared state probe
   (`_state` logic from `RegisteredRemoteRecoveryController`) and require
   `runtime_revision_state == "match"` → else `remote_runtime_stale` (R13).
3. Build the control-plane declaration, then derive the request id from the hosted
   source-bound request id and canonical declaration hash; derive the slot (R4).
4. Read `list` from the helper. If the slot for this backup id exists, skip to the
   replay path (FR-004). Otherwise refuse `retention_exceeded` if a complete
   unpromoted slot is past its bound (R12).
5. Call helper `start` with the password then the declaration on stdin (R2). Map helper codes
   to the envelope.

### Status and list (US2, US5)

`status` and `list` resolve the remote entry and home only (no revision gate),
call the helper, and derive `incomplete` and the retention view operator-side
from the helper's raw facts (R3, R12). `RecoveryService.list(remote)` merges
`server_captures`/`legacy_server_archives` and reports `drive.configured=false`
instead of failing when Drive is absent and a remote is given.

### Promote (US3, US4)

`ServerCaptureService.promote(remote, backup_id, confirm)` follows the order in
contracts/cli-mcp.md: pending sidecar finish (R11) → Drive idempotency/conflict →
capture complete → operator space → chunked resumable transfer (R8) → declaration
fetch → `publish_files` under the owned `materialized` root with
`provenance.server_capture` (R9) → helper `mark-promoted` → remove promote
progress. Failures after a verified ciphertext keep the existing pending
preservation, now with a sidecar.

### Server job (US1, US2)

Phases and bounds per contracts/server-capture-helper.md; inventory per R5;
preflight per R6; streaming per R7.

## Complexity Tracking

No constitution violations. One accepted risk: detaching from an SSH session
depends on the host not killing user processes at logout; the helper detects
the known case (`KillUserProcesses=yes`) and refuses rather than starting a job
that would die (R2).

# Implementation Plan: Agent-Aware Remote Development Sync

**Branch**: `latest` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from
`/specs/033-agent-aware-remote-sync/spec.md` and the reviewed PRD in the same
directory.

## Summary

Add an opt-in, relationship-owned local-to-remote synchronization service for
disposable development workspaces. The service captures a stable, credential-
screened source generation, transfers it through an atomic remote staging
boundary, records durable accepted/pending state, and gates remote jobs on one
accepted generation. Existing deploy, host apply, and synchronization-off
behavior remain unchanged.

## Technical Context

**Language/Version**: Python 3.12+ (the repository's importable `sandbox/`
package; compatibility is tested with the supported Python runtimes used by CI)

**Primary Dependencies**: Standard-library `argparse`, `dataclasses`, `hashlib`,
`json`, `pathlib`, `sqlite3`, subprocess/transport seams already used by
`sandbox/core/_remote.py`; existing remote workspace/job transports; CLI/MCP
manifest registries.

**Storage**: Machine state under `SANDBOX_HOME/runtime` with a private,
transactional synchronization relationship journal; remote workspace metadata
and durable job repository remain authoritative for their existing domains.

**Testing**: `unittest` focused contract/unit suites, CLI parser tests, MCP
composition tests, and the disposable remote acceptance described in
`quickstart.md`.

**Target Platform**: macOS/Linux developer clients and provisioned Linux remote
Sandbox controllers; disposable remote development workspaces only.

**Project Type**: Python CLI plus MCP/control-plane services.

**Performance Goals**: Under the healthy profile, trigger acceptance through
remote durable generation acknowledgment in at most 10 seconds for a generation
of at most 10 MiB or 100 paths, including preflight, credential screening,
stable capture, transfer, and validation. Sustained transfer throughput is at
least 5 MiB/s and packet loss at most 1% for this claim.

**Constraints**: Local-to-remote only; off by default; no production/permanent
targets; no raw credentials, source contents, sensitive paths, or process
arguments in public output or persisted metadata; credential findings reject the
whole generation before remote mutation; one request identity is replay-safe;
remote jobs pin accepted generations and see managed source read-only.

**Scale/Scope**: One relationship per resolved project identity/remote/durable
workspace tuple; bounded aggregate status; one in-flight generation per
relationship; multiple participants and parallel-safe jobs may share an accepted
generation; no bidirectional or remote-first editing.

The current project identity resolver is path-derived: symlinks resolve
consistently, but relocation changes the identity. The implementation must use
the registry-owned durable identity/adoption seam for relocation, or fail closed
until explicit lifecycle adoption; it must not silently claim that a changed
path is the same owner.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-project instance model — PASS**: target resolution requires the
  selected project identity and registered workspace; no fallback instance is
  introduced.
- **II. Registry single source — PASS**: the relationship uses the existing
  project/workspace identity services and adds a feature-owned journal rather
  than reading registry JSON directly.
- **III. Modular entry file — PASS**: command, application service, transport,
  state, and MCP changes register through explicit module manifests; `sb` stays
  a single entry file.
- **IV. Live-stack proof — PASS WITH GATE**: unit/contract tests are necessary;
  completion requires the disposable remote acceptance in `quickstart.md`.
- **V. Idempotency/docs-with-code — PASS**: request replay, atomic journal
  writes, bounded transfer retries, and matching CLI/MCP/docs changes are part
  of the plan.
- **VI. Parity before removal — PASS**: the feature is additive; deploy, host
  apply, and off-mode behavior remain available and are explicitly re-tested.

The design uses the existing workspace operation lock before the sync journal
transaction and job transaction. Callers must not acquire those locks in reverse
order.

No constitution violation requires a complexity exception.

## Project Structure

### Documentation (this feature)

```text
specs/033-agent-aware-remote-sync/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
└── contracts/cli-mcp.md
```

### Source Code (repository root)

```text
sandbox/
├── sync/
│   ├── models.py              # relationship, generation, divergence values
│   ├── repository.py          # transactional local journal and replay lookup
│   ├── capture.py             # stable source capture and owner-only spool
│   ├── policy.py              # exclusions and fail-closed credential screen
│   ├── coordinator.py         # bounded live trigger/debounce and lock order
│   ├── projection.py          # staged generation publication and divergence
│   └── service.py              # mode, capture, queue, stop, status orchestration
├── transports/
│   ├── remote_sync.py         # staged generation transfer and remote envelope
│   └── remote_jobs.py         # generation pin/launch integration
├── application/
│   ├── sync_service.py        # application boundary used by CLI and MCP
│   └── job_service.py         # generation gate integration
├── commands/
│   ├── sync.py                # explicit CLI parser/handler registration
│   └── manifest.py            # command ownership registration
└── registry.py                # existing explicit CommandSpec seam

mcp/wp-server/
└── tools/sync.py              # parity tools over the same application service

tests/
├── test_sync_manifest.py
├── test_sync_state.py
├── test_sync_capture.py
├── test_sync_projection.py
├── test_sync_coordinator.py
├── test_sync_transport.py
├── test_sync_cli.py
├── test_sync_mcp.py
└── test_sync_live_acceptance.py  # bounded remote acceptance harness

docs/
├── remote-hosting.md          # explicit sync/apply interaction
└── CLAUDE.md/AGENTS.md context # current plan reference where applicable
```

**Structure Decision**: Use a feature-owned `sandbox/sync/` package with an
application service and transport adapters. The CLI and MCP are thin manifest-
registered callers. Existing remote deployment, workspace, job, and redaction
services own their mechanisms; synchronization owns only generation policy and
relationship state.

## Phase 0 Research Summary

The decisions and alternatives are recorded in [research.md](./research.md).
The existing deploy primitive remains the compatibility baseline, remote
workspace IDs remain authoritative, and durable job scheduling supplies the
generation lease. The key security decision is a generation-fatal credential
finding before any remote mutation; the key integrity decision is staged,
atomic publication rather than direct writes to the active workspace.

## Phase 1 Design Summary

- [data-model.md](./data-model.md) defines relationships, generations,
  participants, pinned jobs, divergence, and state transitions.
- [contracts/cli-mcp.md](./contracts/cli-mcp.md) defines the redacted parity
  envelopes and job-generation boundary.
- [quickstart.md](./quickstart.md) defines focused tests, disposable remote
  acceptance, negative security cases, recovery, parity, and cleanup.

## Implementation sequencing

1. Add pure manifest/capture models and credential/exclusion validation with
   negative tests; no remote mutation in this slice.
2. Add transactional relationship state and replay-safe request lookup with
   race/idempotency tests.
3. Add the staged remote source transport and atomic generation acknowledgment;
   keep all paths shell-safe and public envelopes redacted.
4. Add the application service, CLI/MCP manifest registration, modes, stop,
   status, and explicit once/start behavior.
5. Integrate job launch with generation pinning, newest-pending queueing,
   read-only source access, isolated-copy rejection/output semantics, and
   divergence detection.
6. Add docs, focused tests, and the disposable remote acceptance. Preserve and
   re-run existing deploy/off-mode compatibility gates before considering the
   feedback record verified.

## Complexity Tracking

No constitution violations are claimed. The relationship journal and staged
transfer are necessary because direct reuse of deploy or a process-local watcher
cannot provide replay-safe acceptance, atomic generations, shared ownership,
and credential refusal as one boundary.

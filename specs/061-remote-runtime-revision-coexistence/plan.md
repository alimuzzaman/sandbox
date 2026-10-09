# Implementation Plan: Remote Runtime Revision Coexistence

**Branch**: `latest` (feature dir `061-remote-runtime-revision-coexistence`) | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/061-remote-runtime-revision-coexistence/spec.md`

## Summary

Replace the controller-side exact runtime-revision gate with one compatibility
verdict computed from a declared control-protocol range, keep exact matching
as an opt-in strict mode that registers a visible, expiring pin on the remote,
make migrate list and protect those pins, give every mismatch refusal one
shape with remedies the CLI accepts, and scope the local remote-registration
lock to one remote.

The protocol range ships in one checked-in module that is part of the runtime
revision digest. Migrate writes it into the remote unit's environment next to
the existing `SANDBOX_REMOTE_MCP_RUNTIME_REVISION`, and the existing service
status probe reads it back. A unit without it is `exact_only`. Pins are small
JSON files under the remote Sandbox home, written and read by fixed programs
over the existing authenticated SSH transport (the same path deployment
receipts use), under a remote flock.

## Technical Context

**Language/Version**: Python 3.12+ (CLI venv), POSIX shell on the remote host

**Primary Dependencies**: standard library only; existing `sandbox.core._remote` transport (`ssh_run`), `sandbox.services.runtime_revision`

**Storage**: local `$SANDBOX_HOME/runtime/remote-registration/` lock files; remote `$SANDBOX_HOME/runtime/remote-pins/*.json` (one file per holder, 0600, directory 0700)

**Testing**: `unittest` (`./sb selftest`), `tests.test_architecture_boundaries`; live proof on a disposable remote

**Target Platform**: macOS/Linux controller; Linux remote with systemd user unit

**Project Type**: CLI + MCP server (single project)

**Performance Goals**: verdict adds no remote round trip beyond the existing status probe; pin register/renew is one bounded SSH call (timeout 15 s)

**Constraints**: no secrets in pins or refusals; remedies must parse against the CLI; legacy units keep today's exact behavior; no change to what migrate installs other than one added environment line

**Scale/Scope**: one pin per holder per remote home; bounded at 64 pins listed per remote

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Per-project instance model | Pass | Remote-runtime feature; no instance resolution changes |
| II. Registry is the source of truth | Pass | Registration stays in the existing remote block; pins are remote state read through one module, never parsed by consumers |
| III. Single entry, modular package | Pass | New `sandbox/remote_runtime/` package (protocol, verdict, refusal, pins); CLI adds `remote pin` through the existing remote command module |
| IV. Live-stack proof | Pass (planned) | Quickstart includes a disposable-remote run; reaching `xcloud-london` follows the remote install protocol and needs owner approval for the Lenzora pin bump |
| V. Idempotency, docs with code | Pass | Pin register is idempotent per holder; docs listed below land with the code |
| VI. Parity before removal | Pass | Exact matching stays as strict mode and as the legacy-unit rule; nothing is removed |

Re-check after design: unchanged, all pass.

## Project Structure

### Documentation (this feature)

```text
specs/061-remote-runtime-revision-coexistence/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/runtime-compatibility.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/remote_runtime/
├── __init__.py
├── protocol.py      # CONTROL_PROTOCOL_SPOKEN / CONTROL_PROTOCOL_OLDEST_SERVED, unit-env encode/parse
├── verdict.py       # compatibility(local, installed, strict) -> Verdict; the single rule
├── refusal.py       # shared mismatch refusal shape + remedy builders (validated against argparse)
└── pins.py          # holder identity, remote pin programs (register/renew/list/release/break), strict gate
sandbox/core/_remote.py            # status probe reads protocol; migrate writes it; per-remote registration lock
sandbox/commands/remote.py         # migrate plan lists pins, --break-pin; `remote pin release|list`; busy reports holder
sandbox/cli.py                     # --strict-runtime global flag, remote pin parser, --break-pin
consumers (FR-003): sandbox/application/workspace_service.py, sandbox/commands/hosting.py,
  sandbox/transports/remote_recovery.py, sandbox/transports/remote_postgres_recovery.py,
  sandbox/resources/context.py, sandbox/resources/host_memory/remote.py, server capture,
  cleanup-broker install
docs/remote-hosting.md, docs/remote-job-runtime.md, CLAUDE.md (gotcha 23), CHANGELOG.md
tests/test_remote_runtime_verdict.py, tests/test_remote_runtime_pins.py,
tests/test_remote_runtime_refusal.py, tests/test_remote_registration_lock.py
```

**Structure Decision**: one new package owns the rule, the refusal shape and
pins; existing check sites call it instead of comparing revisions. Artifact
binding checks (receipts, traces, staging helpers), remote WP-CLI signatures
and cleanup-routine enable keep their exact comparisons and only adopt the
refusal shape.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Remote pin files written over SSH rather than through the `/mcp` control service | Pins must be readable and writable when the installed runtime is older or degraded, to report `strict_pin_unverifiable` precisely and to list pins in a migrate plan before the new runtime exists | A control-service endpoint would only exist on new runtimes, and a migrate plan against an old runtime could not list pins |

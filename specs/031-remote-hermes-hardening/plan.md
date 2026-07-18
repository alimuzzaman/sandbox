# Implementation Plan: Remote and Hermes Operations Hardening

**Branch**: `031-remote-hermes-hardening` | **Date**: 2026-07-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/031-remote-hermes-hardening/spec.md`

## Summary

Replace detached, argv-token remote MCP lifecycle with a Sandbox-owned, confirmed
systemd user-service migration path; provide scoped service ownership evidence and
truthful component health; make cron replacement rollback-capable; and classify
documented Hermes terminal results without masking provider failures. The local code
and disposable fixtures are in scope. Any mutation of a registered remote stays
explicitly confirmation-gated and is not part of this implementation plan's live
acceptance.

## Technical Context

**Language/Version**: Python 3.11-compatible standard-library CLI and shell snippets

**Primary Dependencies**: argparse, subprocess, pathlib, FastMCP server entrypoint,
remote SSH helpers, systemd user manager on supported remote hosts

**Storage**: Existing owner-only `sandbox.local.yml` remote records; remote owner-only
credential file and protected reconciliation snapshots

**Testing**: Python `unittest`, mock SSH/process fixtures, targeted CLI tests; optional
explicitly approved disposable-remote acceptance

**Target Platform**: macOS/Linux developer workstation; Linux systemd user-service
remote; existing local stdio MCP remains supported

**Project Type**: Modular Python CLI plus MCP server

**Performance Goals**: Read-only service and health probes finish within existing
remote diagnostic timeouts; lifecycle work performs bounded status checks and never
streams unbounded logs/output

**Constraints**: No secret in argv/output/metadata; no public MCP bind; mutation only
after `--confirm`; process control scoped to the selected owned systemd unit; legacy
cron remains fail-closed; no automatic remote migration or job trigger

**Scale/Scope**: Registered remote records, one MCP service per remote, Hermes gateway
and catalog jobs; preserve existing remote CLI and MCP contracts during migration

## Constitution Check

| Principle | Plan response | Status |
|---|---|---|
| Per-project instance model | Remote service manages the remote control plane only; instance mapping remains remote-local. | Pass |
| Registry is source of truth | Reuse remote access APIs and existing secret store; no direct registry reads. | Pass |
| Modular CLI | Add service contract through the remote command/core modules and explicit parser registration. | Pass |
| Live-stack verification | Run focused tests and local CLI contract probes; disposable remote/reboot acceptance remains an explicit release gate. | Pass with gated live acceptance |
| Idempotency and docs | Service migration and reconciliation are plan/confirm/idempotent; docs and tests land with code. | Pass |
| Feature parity before removal | Legacy PID detection remains read-only until systemd path parity is tested; no unsafe fallback stop. | Pass |

## Project Structure

### Documentation (this feature)

```text
specs/031-remote-hermes-hardening/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remote-hermes-cli.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── cli.py                         # argparse registration
├── commands/
│   ├── remote.py                  # remote service command dispatch/output
│   └── hermes.py                  # health/reconcile/verify projections
└── core/
    ├── _remote.py                 # service records, plan/apply/status/ownership
    └── _hermes.py                 # health facts, cron transaction, classifier

mcp/wp-server/
└── server.py                      # environment-backed remote service token source

tests/
├── test_remote.py
├── test_hermes.py
├── test_hermes_gateway.py
└── test_mcp_server.py

docs/
├── remote-hosting.md
└── hermes-agent.md
```

**Structure Decision**: Extend the existing explicit remote and Hermes adapters;
the CLI command modules own presentation while core modules own remote policy,
service/rendering, health facts, and reconciliation mechanisms. No new registry or
MCP helper consumers are introduced.

## Delivery Design

1. Create the remote-service contract and pure rendering/validation helpers first.
   Rendered unit and credential paths must be separately testable, non-secret, and
   reject public/wildcard addresses.
2. Replace remote lifecycle use of detached `setsid` and broad `/proc` scanning with
   status/plan/apply helpers that prove a selected systemd unit owns the service.
   Preserve PID-file data only as read-only legacy evidence.
3. Add component-health facts for remote service/recovery and Hermes service,
   scheduler, cron, session, and worktree state; derive top-level status only from
   required facts.
4. Turn force replacement into a transaction: preflight, snapshot, exact mutation,
   postcondition, restore, verified rollback status. Do not change the existing
   confirmation boundary or trigger jobs.
5. Add a terminal-result classifier that requires valid transition evidence and gives
   provider/client errors precedence.
6. Update contracts, operator docs, and tests; validate locally before any separately
   authorized disposable remote acceptance.

## Release and Safety Gates

- No remote migration, credential write, service start/stop, linger change, cron
  replacement, or cron verification may happen in tests or as a side effect of plan
  or status commands.
- A protected apply returns `planned` without `--confirm`; unsafe/ambiguous ownership
  returns a stable error without invoking a process-wide kill.
- A release claim for reboot recovery requires a separately approved disposable remote
  test covering reboot, authentication, listener scope, and selected-unit stop.
- Existing remote metadata stays backward-compatible; only non-secret service facts
  may be added.

## Complexity Tracking

No constitution violations or additional project structures are required.

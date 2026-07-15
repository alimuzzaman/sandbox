# Implementation Plan: Hermes Authorization Controls

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/027-hermes-authorizations/spec.md`

## Summary

Add an authorization subcommand and MCP wrappers backed by the existing locked remote Hermes state. Requests are immutable records for one catalog-managed job. A confirmed approval validates lifecycle and expiry, writes an audit event, and edits only that job's prompt to the committed catalog prompt plus a sanitized approved-context block.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Existing Sandbox remote transport, Hermes cron CLI, Python standard library

**Storage**: `$SANDBOX_HOME/runtime/hermes.json` on the configured remote, owner-only and flock-protected

**Testing**: Python `unittest` in `tests/test_hermes.py` and MCP wrapper tests

**Target Platform**: Existing managed Linux Hermes remote; local macOS test runner

**Project Type**: CLI/MCP control-plane feature

**Performance Goals**: List/show within one bounded remote state read; approval within one state write, one bounded cron edit, and at most one compensating state write when prompt delivery fails

**Constraints**: Default deny; no credentials in state or output; no raw remote commands outside the existing facade; no job creation/removal; explicit confirmation for approval

**Scale/Scope**: Single trusted operator, catalog-managed cron jobs, bounded request/audit collections

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- Per-project ownership: passes; uses the existing named remote and state path.
- Registry source of truth: passes; remote resolution stays in the existing facade.
- Single entry/modular package: passes; changes are confined to Hermes facade, command dispatch, MCP wrapper, tests, and documentation.
- Live proof: required; run focused tests and read-only `authorization list` against the configured remote.
- Idempotency/docs: approval state transitions are guarded and documentation lands with code.
- Secrets and authorization: passes; credential-like fields are rejected before persistence and all output uses existing redaction.

## Project Structure

### Documentation

```text
specs/027-hermes-authorizations/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli-mcp.md
└── tasks.md
```

### Source Code

```text
sandbox/core/_hermes.py          # state model, validation, remote operations
sandbox/commands/hermes.py       # public CLI dispatch
mcp/wp-server/tools/hermes.py    # thin MCP controls
tests/test_hermes.py             # facade and command tests
docs/hermes-agent.md             # operator workflow
```

**Structure Decision**: Extend the existing Hermes facade and wrappers. Authorization state remains in the locked Sandbox state document, while scheduler prompts remain managed by Hermes cron. The facade coordinates the separate resources with state-first compare-and-swap and compensating rollback; no distributed transaction, daemon, datastore, or network endpoint is introduced.

## Complexity Tracking

No constitution violations require justification.

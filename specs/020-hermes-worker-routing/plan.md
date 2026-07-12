# Implementation Plan: Reproducible Hermes Worker Routing

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

## Summary

Extend `sb hermes setup` so it renders the established routed-worker profile on a fresh Hermes remote: Spark coordinates; Terra is the static direct-delegation worker; Luna, Terra, and Sol are named task-board workers. The setup remains non-secret and does not start the gateway service.

## Technical Context

**Language/Version**: Python 3.10+; remote POSIX shell and Hermes-managed Python runtime
**Primary Dependencies**: Existing Hermes launcher/config commands, upstream profiles and Kanban configuration
**Storage**: Existing non-secret `~/.hermes/config.yaml`, root `SOUL.md`, and managed named profile directories
**Testing**: `unittest` with mocked SSH commands; generated-command assertions; local syntax/diff checks
**Target Platform**: Existing supported Ubuntu remote with a Hermes installation
**Project Type**: CLI remote-provisioning extension

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| Single entry/modular package | Pass | Routing rendering remains in `sandbox/core/_hermes.py`. |
| Idempotency and docs-with-code | Pass | Managed markers, profile convergence, tests, and operator docs ship together. |
| Live-stack verification | Pass with gate | Focused mocked tests validate commands; a fresh remote requires separately approved provider authentication and gateway activation. |
| Secrets and explicit authority | Pass | No credentials, authentication, gateway start, or messaging connection is created by setup. |

## Architecture Decisions

### AD-001 — Keep Spark as the coordinator

The existing root model remains Spark. Its policy block requires non-trivial work to be delegated and makes task-board routing available without replacing the user-selected primary provider.

### AD-002 — Use static direct delegation only for Terra

Upstream Hermes supports one configured provider/model pair for direct subagents. Configure it for routine bounded implementation; use named profiles and Kanban only where role-specific routing is needed.

### AD-003 — Prepare, but do not activate, the gateway dispatcher

Set durable task-board configuration during setup but leave the existing allowlisted gateway install/start workflow as the activation boundary. This prevents setup from unintentionally connecting messaging integrations.

### AD-004 — Mark and converge only Sandbox-owned policy text

Replace a delimited coordinator policy block rather than overwriting unrelated root `SOUL.md` content. Named worker profiles are treated as Sandbox-managed routing profiles and receive their complete role policy.

### AD-005 — Document Luna's behavioral limitation

The upstream `file` toolset combines read and mutation operations. Enable it so Luna can inspect local evidence, but reinforce no-write behavior in its policy and document that this is not a hard permission boundary.

## Project Structure

```text
sandbox/core/_hermes.py       # routing constants and setup rendering
tests/test_hermes.py          # setup rendering/idempotency assertions
docs/hermes-agent.md          # operator model map and gateway activation
specs/020-hermes-worker-routing/
```

## Implementation Strategy

1. Define non-secret role, model, policy, and marker constants alongside the existing Hermes defaults.
2. Extend `render_profile()` and the remote setup script to configure delegation, task-board settings, named profiles, policy text, and Luna's file toolset idempotently.
3. Add focused tests for the generated remote command, marker replacement, profile convergence, and no gateway/auth activation.
4. Update operator documentation and run focused tests plus a generated-command review.

## Verification Plan

- Assert setup keeps Spark primary, configures Terra direct delegation, creates all three named profiles, and keeps gateway activation absent.
- Assert routing payloads omit secrets and provider-auth commands.
- Assert Luna receives `file` and `safe` toolsets plus no-write policy text.
- Assert coordinator policy markers are stable across repeated setup rendering.
- Run `python3 -m unittest tests.test_hermes -v` and `git diff --check`.

See [research.md](research.md), [data model](data-model.md), [setup contract](contracts/setup.md), and [quickstart](quickstart.md).

## Research

- Hermes profiles are isolated configurations and role descriptions guide Kanban assignment.
- Kanban's gateway-hosted dispatcher executes named profile workers; it must be activated through the gateway lifecycle.
- Direct delegation has a single configured provider/model override, making it appropriate for Terra but not dynamic per-role selection.
- The `safe` toolset is read-only, while `file` includes mutation-capable operations; no built-in read-only file-only toolset is available.

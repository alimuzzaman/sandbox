# Implementation Plan: Test Execution Modes

**Branch**: `028-test-execution-modes` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

## Summary

Add a conservative test-mode resolver and a pure-PHP runner while preserving the
existing WordPress integration path as the default fallback. Explicit CLI/MCP mode
overrides project configuration; `auto` reads only bounded project-local evidence and
selects `unit` only for an unambiguous Brain/Monkey-only project. Unit mode reuses the
registered project context and Composer/PHPUnit setup but never provisions the
WordPress suite, polyfills, test database, or WordPress environment. Integration mode
continues through the current harness unchanged.

## Technical Context

**Language/Version**: Python 3.11+ orchestration; PHP projects execute their existing PHPUnit version

**Primary Dependencies**: argparse, existing Sandbox project-config/registry services, Docker/Herd runners, Composer, PHPUnit

**Storage**: Existing project configuration and `$SANDBOX_HOME` test-tool cache; no new persistent state

**Testing**: Python `unittest` focused contract tests plus the repository-wide `unittest` suite; disposable PHP fixtures for runner isolation

**Target Platform**: macOS/Linux local Sandbox CLI and MCP server; Docker and existing Herd paths remain supported as currently implemented

**Project Type**: CLI and MCP developer tooling

**Performance Goals**: Unit-mode detection is bounded read-only local inspection; it must not incur WordPress suite or database provisioning.

**Constraints**: Project files are untrusted; detection must not execute them or follow paths outside the canonical project root. Existing `sb test` behavior, MCP keys, labels, passthrough arguments, and capability gates remain compatible.

**Scale/Scope**: One project root per invocation; no new host-PHP runtime, generic-project runner, remote test runner, or framework-specific adapter.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Per-project registry: **PASS** — unit mode still requires the registered project context and does not create an implicit instance.
- Registry as source of truth: **PASS** — project/label resolution remains unchanged and mode is an execution policy only.
- Modular package: **PASS** — logic stays in `sandbox/core/_tests.py`, command dispatch, config validation, and MCP tool forwarding; no `sb` directory or central runtime branch is added.
- Live-stack verification: **PASS with protected boundary** — fixture/contract tests prove unit isolation; existing integration/live checks remain the compatibility gate, while no production-like acceptance is implied.
- Idempotency and docs-with-code: **PASS** — runner setup remains rerunnable and CLI/MCP/config docs are updated with the implementation.
- Security constraints: **PASS** — no secrets, database writes, or unbounded project code execution during mode resolution.

## Project Structure

### Documentation (this feature)

```text
specs/028-test-execution-modes/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli-mcp.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/config/wordpress.py       # tests.suite validation/normalization
sandbox/core/_tests.py            # bounded detection and unit/integration runners
sandbox/core/_herd.py             # host-side pure PHPUnit runner
sandbox/commands/debug.py         # CLI mode resolution and dispatch
sandbox/cli.py                    # positional mode and help
mcp/wp-server/tools/wp.py         # MCP mode forwarding/result field
tests/test_project_config.py      # config contract coverage
tests/test_cli.py                 # parser/dispatch coverage
tests/test_runtime_test_modes.py  # detection and runner isolation fixtures
tests/test_mcp.py                 # MCP schema/forwarding coverage
```

**Structure Decision**: Extend the existing test orchestration seam. Detection is a
pure function in `_tests.py`; Docker and Herd runners share Composer setup but retain
their existing process boundaries. No new runtime adapter or central runtime-kind
branch is introduced.

## Implementation Phases

### Phase 0 — Research decisions

1. Preserve `sb test` with no mode as integration-compatible behavior.
2. Resolve mode with precedence explicit override → `tests.suite` config → `auto`.
3. Inspect only bounded project-local files; WP markers win over pure-unit markers;
   unknown/ambiguous/unsafe evidence falls back to integration.
4. Keep `unit` environment-only: Composer project dependencies plus PHPUnit, no WP
   suite, polyfills, DB, or `WP_TESTS_*` variables.

### Phase 1 — Design and contracts

- Validate `tests` as an object with `suite` in `auto|unit|integration`.
- Add additive CLI/MCP contracts and result `mode` observability.
- Factor Composer preparation from the current integration runner.
- Add disposable fixtures and prove no harness calls in unit mode.

### Phase 2 — Implementation and verification

- Implement config/CLI/MCP selection and bounded detection.
- Implement Docker and Herd unit runners.
- Preserve integration runner behavior and passthrough arguments.
- Run focused tests, full suite, `git diff --check`, and safe fixture checks.

## Complexity Tracking

No constitution violations or new architectural layers are required.

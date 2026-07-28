# Implementation Plan: Resource Monitoring and Safe Cleanup

**Branch**: `latest` | **Date**: 2026-07-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/035-resource-monitoring-cleanup/spec.md`

## Summary

Add a global, feature-owned `resources` command and an explicit MCP tool group
backed by one shared resource service. The service inventories local or named
remote host capacity, Sandbox-owned paths, lifecycle records, and container
engine resources through bounded providers. It classifies observations using
registry ownership plus live references, persists short-lived target-bound
cleanup plans, and applies only exact candidates after confirmation and
per-item revalidation. Broad host or engine prune operations are intentionally
excluded.

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: Python standard library; existing `CommandSpec`
registry; Sandbox project/job registries; bounded process and SSH transports;
FastMCP composition registry

**Storage**: Existing Sandbox registry and runtime directories, plus atomic
cleanup plan and receipt records under `$SANDBOX_HOME/runtime/resource-plans/`

**Testing**: Python `unittest`; CLI/MCP contract tests; fake local and remote
providers; live read-only CLI verification against an active Sandbox host

**Target Platform**: POSIX local development hosts and provisioned Linux remote
hosts with optional Docker/Compose

**Project Type**: CLI tooling with an optional MCP adapter

**Performance Goals**: At least 95% of healthy fast scans return useful capacity
and largest-category results within 15 seconds; thorough scans return complete
or explicit partial results within the selected overall budget

**Constraints**: No direct registry JSON reads; no broad `docker system prune`
or volume prune; no name/age-only ownership; no active, retained, unmanaged, or
ambiguous deletion; category-isolated timeouts; no automatic retry after an
ambiguous remote timeout; raw bytes retained for reconciliation; secret-safe
structured output

**Scale/Scope**: One local or named remote host per request; tens to low
hundreds of managed instances/worktrees/volumes and millions of files inside
slow dependency trees; three public operations across CLI and MCP

## Constitution Check

*GATE: PASS before research and PASS after design.*

- **Per-project ownership**: PASS. Host monitoring is deliberately global, but
  every managed candidate is attributed through public lifecycle services or a
  Sandbox-owned namespace. No fallback WordPress instance is introduced.
- **Registry authority**: PASS. Ownership providers consume public registry and
  lifecycle interfaces; they never read `runtime/registry.json` directly.
- **Modular command composition**: PASS. The new command owns its parser through
  `CommandSpec`; the MCP group registers through the explicit tool manifest.
- **Live-stack verification**: PASS BY PLAN. Unit and contract tests are followed
  by read-only status and plan calls against a live host. Mutating verification
  is limited to disposable, positively owned fixtures.
- **Idempotency and docs-with-code**: PASS. Plans are time-limited and
  single-use, each candidate is revalidated, already-absent candidates are safe,
  and the CLI/skill documentation lands with code.
- **Feature parity before removal**: PASS. Existing `sb cache` and MCP cache
  tools remain compatible; the new resource surface does not remove them.
- **Product packaging boundary**: PASS. Spec Kit artifacts remain outside the
  shipped package list; runtime changes live under shipped `sandbox/`, `mcp/`,
  skills, docs, and tests.

Post-design re-check: PASS. The data model, contracts, and validation guide
retain all gates and introduce no justified exception.

## Project Structure

### Documentation (this feature)

```text
specs/035-resource-monitoring-cleanup/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── resources.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── commands/
│   ├── manifest.py              # feature module ownership
│   └── resources.py             # CLI parser, renderer, service adapter
├── resources/
│   ├── __init__.py
│   ├── adapters.py              # bounded local/remote host providers
│   ├── context.py               # composition root for CLI/MCP
│   ├── models.py                # observations, scans, plans, outcomes
│   ├── plans.py                 # atomic plan store and freshness policy
│   └── service.py               # classification, planning, apply policy
└── services/
    └── process.py               # existing bounded process abstraction

mcp/wp-server/tools/
├── manifest.py                  # explicit resources group ownership
└── resources.py                 # thin MCP adapters over shared service

docs/
└── resource-monitoring.md       # operator command and safety reference

skills/sandbox-cli/
└── SKILL.md                     # CLI-first routing for resource operations

tests/
├── test_resource_adapters.py
├── test_resource_service.py
├── test_resource_interfaces.py
├── test_command_composition.py
├── test_cli.py
├── test_mcp.py
└── test_mcp_composition.py
```

**Structure Decision**: Resource policy, models, persistence, and providers form
a self-contained `sandbox.resources` package. CLI and MCP surfaces are thin
adapters that use the same composition root. Remote execution remains inside
the resource adapter and consumes the established named-remote SSH seam; no
new consumer reads internal registry files or MCP app helpers.

## Research

See [research.md](research.md). All technical unknowns are resolved.

## Design Artifacts

- [data-model.md](data-model.md)
- [contracts/resources.md](contracts/resources.md)
- [quickstart.md](quickstart.md)

## Complexity Tracking

No constitution violations require justification.

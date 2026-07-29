# Implementation Plan: Deep Disk Attribution

**Branch**: `latest` | **Date**: 2026-07-29 | **Spec**:
[spec.md](spec.md)

**Input**: Feature specification from
`/specs/036-deep-disk-attribution/spec.md`

## Summary

Extend `resources status` with an explicit deep, read-only mode that inventories
filesystem boundaries, chooses an installed high-performance directory scanner
or a standard host fallback, detects deleted-open allocation, consumes detailed
container storage accounting, and reconciles those observations against used
capacity without double counting. Local and named-remote adapters return the
same validated deep-attribution model; the shared service and CLI/MCP adapters
render one additive structured contract. No new cleanup mechanism or package
installation is introduced.

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: Python standard library; existing
`BoundedProcessRunner`, named-remote SSH transport, `CommandSpec`, and MCP
manifest; optional installed `gdu` and `lsof`; standard `du`, `df`, Linux mount
metadata, and Docker CLI fallbacks

**Storage**: No persistent feature state; one in-memory bounded scan result.
Existing cleanup plan records remain unchanged.

**Testing**: Python `unittest`; parser fixtures; fake bounded-process and SSH
providers; CLI/MCP contract tests; live read-only local and named-remote scans

**Target Platform**: POSIX local development hosts and provisioned Linux remote
hosts, with optional Docker/Compose

**Project Type**: CLI tooling with an optional MCP adapter

**Performance Goals**: Return within the selected budget plus five seconds;
bound ranked findings to 100 per filesystem; preserve completed category
evidence after partial failure

**Constraints**: Read-only; no package installation; no interactive privilege
prompt; `sudo -n` only for read probes; no cross-filesystem directory walk; no
raw path or command-line secret exposure; no overlapping logical values in
capacity attribution; no new cleanup eligibility

**Scale/Scope**: One local or named remote target; tens of mounts, millions of
files, hundreds of container records, and up to 3,600 seconds per request

## Constitution Check

*GATE: PASS before research and PASS after design.*

- **Per-project ownership**: PASS. Deep attribution is host-wide monitoring and
  does not create or resolve a fallback WordPress instance.
- **Registry authority**: PASS. Known managed roots continue to come through
  existing typed registry and job repositories; no new consumer reads registry
  JSON directly.
- **Modular command composition**: PASS. The existing feature-owned
  `resources` command and MCP group receive additive options; new parsing and
  reconciliation logic lives under `sandbox.resources`.
- **Live-stack verification**: PASS BY PLAN. Deterministic parser/service tests
  are followed by read-only `sb resources status --deep` calls locally and
  against the configured remote.
- **Idempotency and docs-with-code**: PASS. Deep status has no mutation, and
  contracts, operator documentation, and skill guidance land with code.
- **Feature parity before removal**: PASS. Existing fast/thorough status,
  planning, cleanup, and narrow cache commands remain compatible.
- **Product packaging boundary**: PASS. New runtime code is shipped under
  `sandbox/`; Spec Kit artifacts remain excluded from product packaging.

Post-design re-check: PASS. The data model, additive contract, and quickstart
preserve every gate and introduce no justified exception.

## Project Structure

### Documentation (this feature)

```text
specs/036-deep-disk-attribution/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── deep-attribution.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── commands/
│   └── resources.py             # --deep parsing and human rendering
└── resources/
    ├── attribution.py           # validated models and pure parsers/reconciliation
    ├── adapters.py              # bounded local deep collectors
    ├── models.py                # additive scan result field
    ├── remote.py                # compact bounded remote deep probe
    └── service.py               # deep request orchestration and result envelope

mcp/wp-server/tools/
└── resources.py                 # additive deep argument

docs/
└── resource-monitoring.md       # deep attribution and remediation guidance

skills/sandbox-cli/
└── SKILL.md                     # CLI-first deep scan routing

tests/
├── test_resource_attribution.py
├── test_resource_adapters.py
├── test_resource_remote.py
├── test_resource_service.py
└── test_resource_interfaces.py
```

**Structure Decision**: Keep capacity policy and normalized evidence in a new
feature-owned `sandbox.resources.attribution` module. Local and remote adapters
own host command selection and bounded execution; both normalize into the same
models. Service, CLI, and MCP remain thin consumers of those models. No new
consumer is added to a compatibility facade or central helper namespace.

## Research

See [research.md](research.md). All technical unknowns are resolved.

## Design Artifacts

- [data-model.md](data-model.md)
- [contracts/deep-attribution.md](contracts/deep-attribution.md)
- [quickstart.md](quickstart.md)

## Complexity Tracking

No constitution violations require justification.

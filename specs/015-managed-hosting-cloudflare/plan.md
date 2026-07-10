# Implementation Plan: Managed Hosting with Cloudflare DNS and TLS

**Branch**: `015-managed-hosting-cloudflare` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-managed-hosting-cloudflare/spec.md`

## Summary

Add a small, generic Compose hosting layer to Sandbox. It reads project-local
manifests, validates aliases and deployment policies offline, then can plan or,
only with explicit confirmation, apply a Caddy/Cloudflare-backed deployment to
an existing remote target. The implementation reuses Sandbox's remote SSH and
Caddy primitives rather than creating a second deployment system.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.10+ and YAML/JSON configuration

**Primary Dependencies**: Python standard library, existing PyYAML availability helper,
Docker Compose, Caddy, Cloudflare REST API

**Storage**: Project manifests; local secrets in `~/.zshrc.secrets`; remote deployment
state in `$SANDBOX_HOME/runtime/hosts.json`

**Testing**: `unittest`, mocked urllib and SSH calls, Compose config checks, Sandbox MCP

**Target Platform**: Existing local Sandbox and provisioned Linux remote VPS

**Project Type**: CLI and remote-hosting orchestration

**Performance Goals**: Offline manifest validation completes without remote or Cloudflare
access; health verification has bounded timeouts

**Constraints**: No live mutation without `host apply --confirm`; never print API tokens
or private keys; do not delete unmanaged DNS records

**Scale/Scope**: One developer-owned remote target hosting multiple named Compose
environments and the three supplied project configurations

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| Per-project instance model | Pass | Hosting manifests remain project-local and use an explicit remote. |
| Registry authority | Pass | WordPress instances remain unchanged; hosting state is isolated from the WP registry. |
| Modular command architecture | Pass | New command and core modules register through the existing CLI registry. |
| Live verification | Pass | Tests include local manifest checks and safe container/MCP verification; live apply is intentionally excluded. |
| Idempotency and docs | Pass | Apply records state, validates before Caddy reload, and ships docs with the CLI. |

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
sandbox/
├── commands/hosting.py       # host validate/plan/apply commands
├── core/_cloudflare.py       # token, DNS, zone settings, Origin CA client
└── core/_hosting.py          # manifest, normalization, Caddy/Compose rendering

tests/
└── test_hosting.py           # unit and orchestration coverage

specs/015-managed-hosting-cloudflare/
└── contracts/cli.md          # public CLI and manifest contract
```

**Structure Decision**: Keep public command parsing in `sandbox/cli.py`, with all
hosting behavior in focused modules beside the existing remote implementation.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Separate hosting state file | WordPress registry cannot represent non-WP Compose services. | Overloading instance registry would violate its single source of truth. |

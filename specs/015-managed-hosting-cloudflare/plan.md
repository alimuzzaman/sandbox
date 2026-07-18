# Implementation Plan: Managed Hosting with Cloudflare DNS and TLS

**Branch**: `015-managed-hosting-cloudflare` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-managed-hosting-cloudflare/spec.md`

## Summary

Add a small, generic Compose hosting layer to Sandbox. It reads project-local
manifests, validates aliases and deployment policies offline, then can plan or,
only with explicit confirmation, apply a Caddy/Cloudflare-backed deployment to
an existing remote target. The implementation reuses Sandbox's remote SSH and
Caddy primitives rather than creating a second deployment system.

Managed WordPress deployments additionally require a filesystem-ownership policy:
the web-server runtime user owns the updateable WordPress tree and persistent uploads
volume. A root-only, idempotent permissions job runs before the non-root WordPress
initializer on every deployment, so WordPress can use direct filesystem updates without
collecting FTP credentials.

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

**Constraints**: No live mutation without `host apply --confirm`; never print API tokens,
Basic Auth passwords, generated hashes, or private keys; do not delete unmanaged DNS
records

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

/Users/alim/Sites/git/amarsonar-bangla/
├── Dockerfile                 # bakes core and project files as www-data
├── docker-compose.yml         # runs permissions before WordPress initialization
└── scripts/wp-permissions.sh  # repairs named-volume ownership idempotently
```

**Structure Decision**: Keep public command parsing in `sandbox/cli.py`, with all
hosting behavior in focused modules beside the existing remote implementation.

Basic Auth is declared by an environment's `basic_auth.username` and
`basic_auth.password_secret`. The secret is resolved from the owner-only secret store,
streamed to the remote Caddy `hash-password` command, and never included in the Compose
environment or host state. The Caddy 2.6-compatible `basicauth` directive is rendered
only after the hash is returned and the full configuration is validated.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Separate hosting state file | WordPress registry cannot represent non-WP Compose services. | Overloading instance registry would violate its single source of truth. |

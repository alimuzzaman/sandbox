# Implementation Plan: Generic Remote Deploy

**Branch**: `029-generic-remote-deploy` | **Date**: 2026-07-18 | **Spec**: [spec.md](spec.md)

## Summary

Extend the established one-way remote deploy workflow to the explicit generic Compose
runtime. Transfer semantics and remote `sb ensure` are already kind-neutral; this work
replaces the WordPress-only capability preflight and branches only the two
WordPress-specific finishing steps: plugin activation and WordPress URL mutation.
Generic projects instead use the instance's returned `http_port` for Caddy exposure
and retain their declared application URL/health contract.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Existing Sandbox CLI, runtime adapter registry, SSH/Git
transport, Docker Compose, Caddy, MCP wrapper

**Storage**: Existing machine-local remote registry and remote project worktree;
existing generic instance registry fields (`kind`, `service`, `http_port`, `url`)

**Testing**: Python `unittest` remote command and adapter-contract coverage; local
generic Compose fixture smoke validation

**Target Platform**: Docker-backed macOS/Linux Sandbox clients and provisioned Linux
remote hosts

**Project Type**: CLI and MCP developer tooling

**Performance Goals**: No additional remote connection or deploy transfer pass; generic
deployment uses the existing single ensure and optional route step.

**Constraints**: Project declarations remain explicit and validated; no arbitrary
repository command discovery; no secrets in deploy results; no change to WordPress
activation, URL updates, ports, or JSON shape.

**Scale/Scope**: One declared generic Compose public service per project/label;
registered remotes only. Generic previews, managed DNS policy changes, snapshots, and
non-Compose deployment adapters are excluded.

## Constitution Check

- **Per-project registry**: Pass — remote ensure resolves the deployed project root
  through the existing project-specific runtime service.
- **Registry source of truth**: Pass — no local record describes remote ownership; the
  remote's registry remains authoritative.
- **Single entry/modular package**: Pass — edits stay in the deploy command, runtime
  capability composition, remote helper, MCP wrapper, and their tests.
- **Live-stack verification**: Pass with required gate — a generic Compose fixture
  must be locally ensured and probed; WordPress deploy tests retain compatibility.
- **Idempotency/docs-with-code**: Pass — repeat deploy/ensure stays replace-not-stack,
  and remote hosting/config docs are updated with code.
- **Parity before removal**: Pass — WordPress path is retained as an explicit
  compatibility branch and validated by existing tests.

## Project Structure

```text
specs/029-generic-remote-deploy/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/remote-deploy.md
└── tasks.md

sandbox/application/context.py       # generic deploy capability
sandbox/commands/deploy.py           # kind-aware post-transfer orchestration
sandbox/core/_remote.py               # kind-neutral public route helper
mcp/wp-server/tools/remote.py         # runtime-neutral MCP contract text/preflight
mcp/wp-server/tools/manifest.py       # project-scoped catalog profiles
mcp/wp-server/server.py               # scoped profile resolution at startup
sandbox/cli.py                        # runtime-neutral deploy help
tests/test_remote.py                  # WordPress and generic deploy contracts
tests/test_runtime_contracts.py       # capability coverage
docs/remote-hosting.md                # user workflow
docs/sandbox-config-reference.md      # generic prerequisites
```

**Structure Decision**: The runtime adapter exposes a `remote-deploy` capability and
the deploy command selects behavior from the already-normalized project kind. This is
the adapter-selection boundary; SSH transfer, remote ensure, Caddy routing, and JSON
result construction remain shared mechanisms.

## Implementation Phases

1. Add generic remote-deploy capability and a capability resolver that accepts the
   correct runtime-specific capability before any remote work.
2. Add failing remote-command tests for generic ensure/expose, WordPress regression,
   missing generic port, and MCP forwarding.
3. Implement kind-aware deploy finishing: WordPress keeps activation/home updates;
   generic exposure routes `http_port` and returns the public URL unchanged otherwise.
4. Update CLI/MCP and user docs, then run focused and full contract checks plus a
   local generic fixture ensure/probe.
5. Add project-scoped MCP catalog profiles so registration filters
   runtime-exclusive groups before tool discovery.

## Complexity Tracking

No constitution exception is required. The deploy command has one additional kind
branch, justified because plugin activation and WordPress options are runtime policy,
not shared deployment mechanisms.

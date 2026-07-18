# Research: CLI-first Sandbox operation

## Decision: expose the existing generic runtime execution path through `sb`

**Rationale**: The Compose adapter already validates and runs explicit argv in
the configured public service for MCP. Reusing that application operation gives
CLI and MCP parity without duplicating Docker policy.

**Alternatives considered**:

- Raw `docker compose exec`: rejected because it bypasses declared service,
  instance routing, bounded execution, and project policy.
- A new generic shell command: rejected because a shell would hide the command
  boundary; explicit argv is safer and already part of the runtime contract.

## Decision: retain MCP as an optional transport

**Rationale**: Existing MCP clients need live tool calls, but the CLI can serve
humans, skills, and non-MCP agents directly. Keeping both on the same runtime
service preserves parity.

**Alternatives considered**:

- Remove MCP: rejected because it breaks supported integrations.
- Keep MCP-first documentation: rejected because it leaves the alternate path
  undiscoverable and carries irrelevant tools into some sessions.

## Decision: use one runtime-aware guide plus a shipped skill

**Rationale**: The guide provides structured, current commands; the skill
provides operating rules and examples. Both avoid loading an MCP catalog.

**Alternatives considered**:

- Static README-only instructions: rejected because runtime kind matters.
- Duplicate per-runtime skills: rejected because the guide can select the
  correct runtime while one skill keeps maintenance small.

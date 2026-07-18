# Feature Specification: CLI-first Sandbox operation

**Feature Branch**: `030-cli-first-operation`

**Created**: 2026-07-18

**Status**: Complete

**Input**: User description: "Commit and push automatically after work is done; find more improvements; provide a skill plus CLI alternative to MCP."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate a generic project without MCP (Priority: P1)

A developer or agent can create, inspect, execute a command in, and deploy a
generic Compose project using Sandbox commands without registering or starting
an MCP server.

**Why this priority**: Generic projects otherwise have an MCP-only execution
gap even though their lifecycle and remote deployment are CLI-capable.

**Independent Test**: A configured generic Compose project reports a
Compose-specific guide and `sb exec` invokes an explicit argv list in its
declared public service.

**Acceptance Scenarios**:

1. **Given** a generic Compose project, **When** the user requests its guide,
   **Then** it receives lifecycle, explicit execution, and remote deploy
   commands without WordPress-only instructions.
2. **Given** a running generic Compose instance, **When** the user passes an
   argv list to the execution command, **Then** Sandbox runs that exact list in
   the declared service without inferring a shell.

---

### User Story 2 - Learn a CLI-first workflow from a skill (Priority: P2)

A developer or agent can load a shipped skill and choose commands appropriate
to the detected project runtime without needing MCP tool discovery.

**Why this priority**: It makes the alternate interface discoverable and keeps
runtime-specific tools from leaking into unrelated work.

**Independent Test**: `sb skill show sandbox-cli` and `sb guide` both identify
the CLI workflow and distinguish WordPress from generic Compose operations.

**Acceptance Scenarios**:

1. **Given** any Sandbox checkout, **When** the user shows the CLI skill,
   **Then** it describes local and remote workflows for each supported runtime.
2. **Given** a WordPress project, **When** the user requests the guide, **Then**
   it provides WordPress commands rather than generic service execution.

---

### User Story 3 - Ship completed work automatically (Priority: P3)

After required verification succeeds, agents commit and push completed relevant
work without a further confirmation step, while retaining protections for
destructive or release actions.

**Why this priority**: The requested delivery policy removes repetitive manual
handoff work while preserving clearly consequential operations.

**Independent Test**: Project operating guidance consistently instructs
automatic commit/push after verification and lists protected actions.

**Acceptance Scenarios**:

1. **Given** a verified completed change, **When** an agent reaches handoff,
   **Then** it stages, commits, and pushes the active branch.
2. **Given** a force push, tag, release, deploy, or PR action, **When** an
   agent considers it, **Then** explicit approval is still required.

### Edge Cases

- A WordPress project requests generic service execution: Sandbox rejects it
  before execution with a capability error.
- No project descriptor is available for `sb guide`: it supplies a safe generic
  Compose starter catalog rather than touching an instance.
- An execution command is empty or contains a NUL byte: Sandbox rejects it
  without invoking the runtime.
- An MCP client is present: the CLI path remains available and MCP stays
  runtime-scoped rather than being removed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST provide a CLI command catalog that is tailored to a
  detected project runtime and may be consumed without MCP.
- **FR-002**: Sandbox MUST provide a shipped CLI-first skill discoverable with
  the existing skill command.
- **FR-003**: Generic Compose projects MUST be able to execute an explicit argv
  list in their declared public service through the CLI.
- **FR-004**: CLI execution MUST reject empty or malformed argv input before a
  runtime side effect.
- **FR-005**: WordPress projects MUST NOT receive generic Compose execution
  permission.
- **FR-006**: MCP MUST remain optional and runtime-scoped; this feature MUST
  NOT remove current MCP integration.
- **FR-007**: After required checks pass, project guidance MUST require
  automatic commit and push of completed relevant work on the active branch.
- **FR-008**: Project guidance MUST continue to require explicit approval for
  force pushes, tags, releases, deployments, PR creation, and PR merges.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can obtain a runtime-specific CLI catalog with one local
  command and no MCP setup.
- **SC-002**: A generic project can execute a declared-service argv command
  through the CLI with no raw Docker command required.
- **SC-003**: The skill and guide contain no WordPress execution recommendation
  for a generic project and no generic execution recommendation for WordPress.
- **SC-004**: The command and composition test suites pass with the new CLI
  commands represented in their public command inventory.

## Assumptions

- The active branch is an approved destination for normal verified work, as
  explicitly requested; release and destructive Git operations remain separate.
- Existing local and remote deploy contracts remain the supported deployment
  path for both project runtimes.
- MCP continues to serve MCP-capable clients and is not a dependency of the
  CLI-first workflow.

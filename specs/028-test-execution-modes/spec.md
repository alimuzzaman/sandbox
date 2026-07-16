# Feature Specification: Test Execution Modes

**Feature Branch**: `028-test-execution-modes`

**Created**: 2026-07-16

**Status**: Draft

**Input**: User description: "Add explicit unit and integration test execution modes with a no-WordPress pure-PHP fast path for plugin test suites"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Pure Unit Tests Quickly (Priority: P1)

As a plugin developer, I want to run a pure PHP unit suite without provisioning WordPress so that fast feedback does not require a running site or test database.

**Why this priority**: Pure unit tests are the most frequent feedback loop and the current advertised fast path is not implemented.

**Independent Test**: A Brain/Monkey-only fixture with a registered Sandbox project context runs in unit mode through CLI and MCP, invokes PHPUnit with project dependencies, and performs no WordPress test harness, suite, database, or WordPress test-environment setup.

**Acceptance Scenarios**:

1. **Given** a project configured for unit tests and a registered project context, **When** the developer runs the test command, **Then** PHPUnit runs with its own dependencies and the command succeeds or reports PHPUnit failures normally without provisioning the WordPress test harness.
2. **Given** a unit-only project, **When** the developer requests unit mode through MCP, **Then** the response reports the resolved mode and no WordPress capability or harness side effect occurs.
3. **Given** an explicit unit request with `--provision-only`, **When** the command is parsed, **Then** it rejects the incompatible combination before provisioning or running anything.

### User Story 2 - Select the Existing Integration Harness (Priority: P1)

As a plugin developer, I want an explicit integration mode so that WordPress integration tests continue to use the isolated test database and externally supplied WordPress PHPUnit harness.

**Why this priority**: Existing WordPress behavior is the compatibility contract and must remain the safe default.

**Independent Test**: An integration fixture resolves to integration mode and exercises the current harness provisioning and PHPUnit invocation, including the isolated test database path.

**Acceptance Scenarios**:

1. **Given** an integration project, **When** the developer runs the default or explicit integration mode, **Then** the current WordPress harness path is used unchanged.
2. **Given** an unknown or ambiguous project, **When** auto mode is used, **Then** integration is selected conservatively for backward compatibility.

### User Story 3 - Observe and Configure Mode Resolution (Priority: P2)

As a developer or automation client, I want to configure and observe test mode resolution so that a test result is explainable and repeatable.

**Why this priority**: Mode selection changes setup side effects, so silent or ambiguous resolution would be unsafe.

**Independent Test**: Configuration, CLI overrides, MCP overrides, invalid values, and resolved-mode output are covered without booting a live stack.

**Acceptance Scenarios**:

1. **Given** configuration selects one of `auto`, `unit`, or `integration`, **When** no explicit override is supplied, **Then** that configured mode is used.
2. **Given** a valid explicit mode, **When** it is supplied through CLI or MCP, **Then** it overrides configuration and is reported additively in the result.
3. **Given** an invalid mode, **When** a client requests it, **Then** the request fails deterministically before capability checks or subprocesses.

### Edge Cases

- A project contains both WordPress and pure-unit markers; auto mode selects integration.
- A project contains no recognized marker; auto mode selects integration rather than changing current behavior.
- A project contains only a Brain/Monkey marker but its referenced files escape the project root; detection refuses to follow the path and selects integration.
- A project uses a stale or incompatible Composer lockfile; existing Composer behavior and diagnostics remain unchanged in the selected runner.
- A unit request is made for a registered generic or WordPress project; project registration and capability checks remain required, but no WordPress harness setup is performed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The test command MUST accept `auto`, `unit`, and `integration` modes while preserving the current default behavior when no mode is supplied.
- **FR-002**: Mode precedence MUST be explicit mode override, then project test configuration, then `auto`.
- **FR-003**: Auto detection MUST inspect only project-local configuration, declared bootstrap files, Composer metadata, and PHP test sources; it MUST NOT execute project code during detection.
- **FR-004**: Auto detection MUST select integration when WordPress markers are present, when markers are mixed, when no marker is found, or when referenced paths are unsafe or ambiguous.
- **FR-005**: Auto detection MAY select unit only for an unambiguous pure-unit marker set, including Brain/Monkey references without WordPress markers.
- **FR-006**: Unit mode MUST run PHPUnit using project-local Composer dependencies within the existing registered project execution context without cloning or mounting the WordPress test suite, creating or using the isolated WordPress test database, or setting WordPress test environment variables.
- **FR-007**: Integration mode MUST retain the existing external WordPress suite, polyfills, isolated database, and configuration provisioning behavior.
- **FR-008**: `--provision-only` MUST be valid only for integration mode and MUST fail before side effects when unit mode is selected.
- **FR-009**: CLI and MCP mode validation MUST reject values outside `auto`, `unit`, and `integration` before capability checks or subprocess execution.
- **FR-010**: MCP test results MUST preserve existing result keys and add the resolved mode as an additive field.
- **FR-011**: Test argument passthrough, project labels, timeout behavior, and existing capability gates MUST remain compatible in both modes.
- **FR-012**: Detection and mode selection MUST constrain all inspected paths to the canonical project root and MUST treat project files as untrusted input.
- **FR-013**: The feature MUST include unit and contract tests for detection, precedence, invalid inputs, runner isolation, CLI parsing, MCP forwarding, and legacy integration behavior.

### Key Entities

- **Test Mode**: The requested or resolved execution policy: `auto`, `unit`, or `integration`.
- **Mode Evidence**: Read-only project-local markers used to resolve `auto` without executing project code.
- **Test Run Result**: The existing test response plus the resolved mode and PHPUnit outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An unambiguous pure-unit fixture completes mode resolution and reaches its PHPUnit runner without any harness, database, or WordPress-environment setup in every verified CLI and MCP run.
- **SC-002**: Existing WordPress and unknown-project fixtures continue to resolve to integration when mode is omitted, with all existing integration tests passing.
- **SC-003**: Invalid mode values and unit-plus-provision-only combinations are rejected before any subprocess or harness mutation in all contract tests.
- **SC-004**: CLI and MCP callers can identify the resolved mode from the result without parsing human-readable output.
- **SC-005**: The feature adds no runtime-specific adapter or central runtime-kind branch; detection remains a shared test-policy concern.

## Assumptions

- PHPUnit remains the project test runner and Composer remains the source of project test dependencies.
- The existing WordPress harness remains the compatibility path and default fallback.
- Unit mode does not create a new host-PHP or generic-project runtime; it runs through the project’s registered runtime context.
- Named PHPUnit suites remain selected through existing passthrough arguments; this feature selects the environment, not individual test groups.
- Live WordPress acceptance remains protected and is not implied by fixture or unit evidence.

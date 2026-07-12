# Feature Specification: Reproducible Hermes Worker Routing

**Feature Branch**: `codex/hermes-public-access`
**Created**: 2026-07-12
**Status**: Draft
**Input**: User description: "Update Sandbox so the Hermes multi-model routing setup can be replicated on a fresh server."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Provision a Routed Hermes Profile (Priority: P1)

An operator can run the normal Sandbox Hermes setup on a fresh, authenticated remote and receive the same coordinator, worker roles, task-routing defaults, and policy instructions as the established server.

**Why this priority**: A rebuilt server must not silently fall back to Spark performing substantive work.

**Independent Test**: A mocked setup command contains the complete non-secret routing configuration, creates each named worker idempotently, and records no provider credential.

**Acceptance Scenarios**:

1. **Given** a fresh Hermes home, **When** the operator runs Sandbox Hermes setup, **Then** Spark remains the primary coordinator and Luna, Terra, and Sol worker profiles receive their declared model and role policies.
2. **Given** setup is run again, **When** the worker profiles already exist, **Then** their routing settings converge without duplicating profiles or toolset entries.
3. **Given** provider authentication has not been completed, **When** setup runs, **Then** it prepares configuration without attempting login or storing credentials.

---

### User Story 2 - Route Work Without Broadening Access (Priority: P2)

An operator can use the configured coordinator to delegate routine implementation to Terra and use named workers for role-specific work, while preserving existing approval and gateway safeguards.

**Why this priority**: Routing is useful only if the coordinator has a supported dispatch path and worker scope is explicit.

**Independent Test**: The rendered setup config makes direct delegation select Terra, enables the coordinator's task board tools, and configures the task dispatcher without installing or starting a messaging gateway.

**Acceptance Scenarios**:

1. **Given** a configured Hermes profile, **When** it delegates a bounded implementation task, **Then** the task uses Terra by default.
2. **Given** a task board dispatcher is installed later through the existing gateway workflow, **When** it receives role-specific work, **Then** Luna, Terra, and Sol are available as named assignees with clear descriptions.
3. **Given** setup runs on a remote with potential messaging credentials, **When** setup completes, **Then** it has not enabled a gateway service or contacted a messaging platform.

---

### User Story 3 - Preserve Evidence-Worker Boundaries (Priority: P3)

An operator can assign Luna evidence work that includes reading and searching local files while the worker policy continues to prohibit edits and commands.

**Why this priority**: Investigation needs repository context, but implementation must remain with Terra or Sol.

**Independent Test**: The Luna profile receives the upstream file and safe toolsets, and its role policy explicitly permits read/search while prohibiting mutation and command execution.

**Acceptance Scenarios**:

1. **Given** a Luna task requires repository evidence, **When** it begins, **Then** Luna can access file-reading and search capabilities.
2. **Given** Luna identifies that a change is needed, **When** it completes its investigation, **Then** it reports the blocker and routes the required mutation to Terra or Sol rather than claiming it performed the change.

### Edge Cases

- A configured provider does not offer one of the required worker model names after the remote is rebuilt.
- A named worker profile already exists with unrelated credentials or state.
- Repeated setup must not append duplicate coordinator toolsets or policy blocks.
- The upstream file toolset includes mutation-capable tools; policy must state that this is not a hard read-only enforcement boundary.
- Gateway setup is absent, incomplete, or has an unsafe allowlist.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox Hermes setup MUST keep Spark as the configured primary coordinator model.
- **FR-002**: Setup MUST configure direct bounded delegation to use Terra with a bounded, non-nested worker policy.
- **FR-003**: Setup MUST create or reconcile named Luna, Terra, and Sol profiles with the declared worker models and role descriptions.
- **FR-004**: Setup MUST configure the coordinator's durable task-routing settings and named-worker fallback without starting a gateway service.
- **FR-005**: Setup MUST configure task decomposition as coordinator work and task specification as high-judgment Sol work.
- **FR-006**: Luna MUST receive file-reading/search capability and a policy prohibiting writes, patches, command execution, and external changes; documentation MUST state that upstream toolset granularity does not technically enforce read-only file access.
- **FR-007**: Terra MUST receive an implementation policy requiring bounded scope, focused tests, evidence, and escalation of unresolved high-risk decisions.
- **FR-008**: Sol MUST receive a high-judgment policy for architecture and sensitive boundaries that requires a human checkpoint before high-impact changes.
- **FR-009**: Setup MUST preserve unrelated configuration, provider credentials, sessions, and existing non-routing content in the root policy file.
- **FR-010**: Setup MUST be idempotent and MUST not duplicate profiles, toolsets, policy blocks, or task-board initialization.
- **FR-011**: Setup MUST never authenticate providers, validate billing/entitlements, print secrets, or enable/start messaging integrations.
- **FR-012**: Operator documentation MUST state the model map, routing behavior, Luna limitation, provider-auth prerequisite, and explicit gateway activation step.

### Key Entities

- **Routing policy**: The non-secret coordinator and worker assignment rules applied by Sandbox setup.
- **Worker profile**: A named Hermes profile with a selected model, role description, tool scope, and role policy.
- **Coordinator policy block**: Sandbox-owned text within the root Hermes policy file that directs Spark to delegate substantive work.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of focused setup tests confirm the expected four model assignments without credential values in generated commands or results.
- **SC-002**: Re-running setup produces no duplicate routing policy markers, named profiles, or coordinator task-board entries in automated tests.
- **SC-003**: 100% of setup runs complete without issuing a provider-authentication command or gateway service activation command.
- **SC-004**: An operator can identify the assignee and escalation boundary for routine, evidence, and high-risk work from the documented routing map in under two minutes.

## Assumptions

- The configured `openai-codex` provider may authenticate the selected Spark, Luna, Terra, and Sol model names after the operator completes provider authentication.
- The existing gateway commands remain the only supported mechanism to install or start the dispatcher service.
- Named `luna`, `terra`, and `sol` profiles are reserved for Sandbox-managed routing on a configured Hermes remote.
- Upstream Hermes continues to store each profile under the same Hermes home and permits shared provider credential fallback without Sandbox copying credentials.

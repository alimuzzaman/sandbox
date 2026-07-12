# Research: Reproducible Hermes Worker Routing

## Decision: Use static delegation for routine implementation

**Rationale**: Hermes supports one configured provider/model pair for `delegate_task`; that target is appropriate for Terra as the routine implementation worker. This keeps direct delegation predictable and avoids inventing a dynamic router the upstream product does not supply.

**Source**: [Hermes configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/), [subagent delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation/).

**Alternatives considered**:

- Use Spark for direct delegation: rejected because it violates the coordinator-only policy.
- Add a custom dynamic router: rejected because named profiles and Kanban already provide a supported role-specific route.

## Decision: Use named profiles and Kanban for Luna, Terra, and Sol

**Rationale**: Profiles isolate each worker's configuration and description. Hermes's gateway-hosted Kanban dispatcher assigns durable tasks to named profiles, which supports role-specific work without fragile in-process orchestration.

**Source**: [Hermes profiles](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/profiles.md), [Hermes Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban).

**Alternatives considered**:

- Start a separate custom worker service: rejected because it duplicates the upstream dispatcher and introduces a second lifecycle.
- Automatically start the gateway from setup: rejected because an existing remote might contain messaging credentials; gateway activation remains the current explicit, allowlisted operator step.

## Decision: Give Luna file access with a documented policy boundary

**Rationale**: The upstream `safe` toolset lacks local file access. The `file` toolset enables evidence gathering but includes mutation-capable operations, so Sandbox must explicitly prohibit writes in Luna's role policy and document that it is not a hard permission boundary.

**Source**: [Hermes toolsets reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference).

**Alternatives considered**:

- Keep Luna on `safe` only: rejected because it cannot inspect local repository evidence.
- Build a custom read-only file plugin: deferred; it is a broader upstream extension outside this routing-replication feature.

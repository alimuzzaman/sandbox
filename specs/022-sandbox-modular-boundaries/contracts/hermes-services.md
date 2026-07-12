# Contract: Hermes Bounded Services

## State

Validates and persists Hermes state atomically; reports corruption; has no process, route, gateway, or recovery side effects.

## Routing

Resolves targets and evaluates routing policy without persistence, process, network, or gateway effects.

## Jobs

Owns run/worktree lifecycle, status, cancellation, and cleanup using injected state, routing, process, and clock services.

## Gateway

Plans and reversibly applies/removes public endpoint, tunnel/route, and authorization-related configuration without owning general jobs or backup policy.

## Backup

Creates/lists artifacts, validates integrity metadata, exposes retention hooks, and generates non-mutating restore plans. It cannot apply restore, delete artifacts, overwrite state, or select the later scoped-recovery policy.

## Composition service/facade

Builds concrete services and preserves current CLI, MCP, and Python function behavior. Each extraction can roll back to the matching legacy implementation without changing persisted formats.

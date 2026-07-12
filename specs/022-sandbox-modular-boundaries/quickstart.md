# Quickstart Validation: Sandbox Modular Boundaries

## Prerequisites

- Clean review of the intended diff and no unrelated user changes overwritten.
- Disposable registry/config fixtures plus one current WordPress project.
- Configured remote Sandbox/Hermes host for read-only/status and approved reversible smoke checks.
- No destructive production action; use existing snapshot/confirmation controls where state mutation is unavoidable.

## Scenario 1 — Descriptor and registry compatibility

1. Run descriptor tests for legacy WordPress, explicit test kind, duplicate kind, invalid path, and kind-before-default behavior.
2. Run the registry contract suite against memory and JSON repositories.
3. Round-trip v1/v2 and unknown-compatible-field fixtures.
4. Inject lock contention and interrupted write.

Expected: existing WordPress normalization is unchanged; generic names avoid WordPress slug rules; prior valid registry survives failed write; no eager rewrite occurs.

## Scenario 2 — Runtime dispatch and zero-side-effect rejection

1. Register a fake adapter and fake process/HTTP/proxy/path/registry-write recorders.
2. Dispatch a supported operation through the runtime service.
3. Request an unsupported capability through direct service, CLI, and MCP contracts.

Expected: supported result is stable; all unsupported requests return equivalent guidance; every side-effect recorder remains empty.

## Scenario 3 — CLI composition

1. Compose the built-in manifest twice and compare command/alias inventory and help groups.
2. Register a test command without editing `sandbox/cli.py`.
3. Inject duplicate names and aliases.
4. Replay representative existing commands, parse failures, JSON output, and exit codes.

Expected: deterministic inventory; duplicate failure before dispatch; existing public behavior remains compatible.

## Scenario 4 — MCP composition

1. Compose all groups twice with test dependencies and compare tool inventory/schema snapshots.
2. Register a test-only group without editing `server.py`.
3. Inject duplicate group and tool names.
4. Run representative tools from shared, WordPress, infrastructure, remote, and Hermes groups.

Expected: deterministic exact inventory; duplicates fail; public names/required parameters/results remain compatible.

## Scenario 5 — Shared service failures

Inject process timeout/non-zero exit, secret-bearing environment, HTTP timeout, port collision, invalid path, proxy apply failure, and rollback failure.

Expected: bounded diagnostics, no secret values, fail-closed paths, and no expanded mutation scope.

## Scenario 6 — WordPress live parity

Using Sandbox CLI/MCP tools, run ensure twice, status, WP-CLI, REST, focused tests, stop/start/apply, domain/HTTPS, current snapshot behavior, and disposable destroy behavior.

Expected: no unexplained output, state, URL, capability, or lifecycle drift.

## Scenario 7 — Hermes bounded behavior

Run isolated tests for state, routing, jobs, gateway, and backup planning. On the configured remote host, replay status, representative job lifecycle, gateway/public access, and existing backup list/create validation without applying restore or deletion.

Expected: each concern initializes only its dependencies; current authorization/protocol/state behavior remains compatible; backup planning is non-mutating.

## Scenario 8 — Final gates

Run focused suites, full unittest discovery, `./sb selftest`, `git diff --check`, boundary guards, inventory checks, and architecture/security/data-loss review.

Expected: every checklist and test passes, facades/deferrals are documented, and downstream features remain blocked until explicit human approval.

# Data Model: Miu Management and RexEnv Integration

This design maps the entities in [spec.md](spec.md) to persistent ownership and state. Records are scoped to a registered project, managed service, or accepted operation; unknown identity blocks mutation.

## Project runtime selection

- `project_id`: stable Sandbox registered project identity.
- `runtime`: `docker` (default) or explicitly selected `rexenv`.
- `runtime_config`: qualified build identity and adapter-specific nonsecret configuration.
- `site_id`: exact RexEnv site identifier when selected; unique mapping per project.
- `qualification`: platform/build/capability evidence and timestamp; missing/stale evidence is unknown, not ready.
- Validation: selection is explicit; existing projects retain Docker; no implicit migration or fallback.

## Managed site and host resource

- `site_id`, `project_id`, exact RexEnv build/provenance, owner state (`managed`, `adopted`, `unowned`, `unknown`), health/capabilities, site path and declared data coverage.
- Host resources (listener, route, resolver, certificate, service, account, data) record owner, dependencies and compatibility evidence.
- Validation: destructive action requires positively established ownership and no active operation/lease; conflicts fail before effects.

## Operation and lease

- `operation_id`: caller-stable unique identity; principal, canonical intent digest, operation kind, exact project/site/service target, timestamps, budget, state, partial effects, result reference, cleanup evidence.
- State: `accepted -> starting -> running -> succeeded|failed`; `accepted|starting|running -> cancel_requested -> cancelled` only after process cessation and cleanup proof. Any uncertain external effect becomes `unknown_effect`, retains lease/capacity and forbids replay under a new identity until resolved.
- Validation: same identity and intent returns existing record; changed intent conflicts; no PID/time stale takeover. Miu RequestStore additionally reserves one host run slot atomically before launch.

## Snapshot / restore point

- `snapshot_id`, exact `site_id` and project identity, capture time, DB export reference/checksum, owned-file manifest/reference, coverage status per component, retention state, restore operation and result.
- State distinguishes `complete`, `partial`, `unknown`, `corrupt`, and `retained`; only complete DB+files baseline qualifies as full.
- Validation: supported export/import and owned-file mechanism required; restore retains selected point and verifies resulting state. No private RexEnv state editing.

## Managed Miu service and credential principal

- Service: managed ownership, exact build/API schema version, provenance evidence, configuration metadata, health, dashboard origin, active operations and retained evidence.
- Principal: opaque id, salted secret digest, explicit scopes, repository allowlist, active/revoked status. Secret is provisioned via local stdin and never returned or logged; storage private and permission checked.
- Validation: credentials are separately scoped; every request authenticates even on loopback; revoke takes effect on next request; unsafe storage, foreign owner, malformed credentials or unknown provenance fail closed.

## GitHub App connection and QA run

- App connection: exact App identity, callback origin, installation/repository grants, observed scopes, separately recorded external-write authorizations; secret/private key remains Miu-owned.
- QA run: operation identity, repo, PR, pinned base/head SHA, mode, trust decision, App principal, attributable sites, status/verdict, bounded evidence references, write authorization and cleanup state.
- Validation: run must match current PR and checkout revisions before code/site mutation; allowlisted repo and required scopes checked before effects. No ambient fallback. Report-only by default. Evidence is bounded and path-safe; arbitrary host paths and unbounded raw logs are excluded.

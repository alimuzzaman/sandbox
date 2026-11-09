# Proposed Operation and Management Contracts

Status: design contract for implementation; not evidence of an existing or qualified API. The Miu HTTP API must be contributed to and reviewed in the authorized Miu fork before Sandbox depends on it. JSON examples are shape descriptions, not runnable requests.

## Common rules

- Every mutation carries a stable `request_id` and canonical intent. Exact replay resolves the existing operation; changed intent under the same identity conflicts. Unknown effect never becomes a fresh retry automatically.
- Require scoped authentication and explicit project/service/repository ownership before side effects. Return bounded, secret-free errors with stable code/status; do not expose stack traces, tokens, credential digests, private config, or arbitrary logs.
- Enforce finite request/response bytes, timeouts, execution/output budgets and capacity. Cancellation acknowledgement is not completion. Hold capacity and cleanup authority until cessation and cleanup are proven.
- Proposal and inspect operations are read-only. Confirm operations reference the reviewed proposal and its resource/version digest; stale proposals require a new review.

## Sandbox CLI and MCP surface (proposed)

Register commands and MCP groups through explicit manifests/contracts and existing shared services. Exact names are implementation-time compatibility decisions; do not claim an existing command until registered and documented.

| Capability | Required contract |
|---|---|
| Runtime support/selection | Inspect candidate build, host trust, per-capability qualification; explicit RexEnv selection only; Docker default unchanged. |
| Project/site lifecycle | Create/reconcile/start/stop/inspect/execute/test/remove against exact registered project and site. Refuse unknown mapping or unsupported operation before effects. |
| Miu service lifecycle | Inspect support; propose/confirm install, configure, upgrade, recover, remove; inspect health/version/dashboard/runs/evidence. Consent and affected/preserved resources visible before confirm. |
| GitHub App | Human-guided create/adopt/connect flow; exact identity and selected grants; no setup-code or key capture. |
| QA | Submit/status/cancel/evidence against pinned repo/PR/base/head; report-only default; separately authorized external writes only. |
| Snapshot/recovery | Full DB+owned-files snapshot and restore only when supported mechanisms and positive ownership are verified; explicit partial coverage otherwise. |

## Miu authenticated HTTP management API (proposed upstream)

- Existing listener, distinct bearer-token scopes: `health:read`, `app:read`, `runs:submit`, `runs:read`, `runs:cancel`, `evidence:read`, `runs:publish`, `runs:trusted`.
- Every run-related principal also has an explicit owner/repository allowlist. Enforce scope and repository on every request, including loopback. Reread credential/revocation state per request.
- Proposed operations: health/version and redacted App identity inspection; idempotent run submit; run list/detail; cancellation request; bounded allowlisted evidence retrieval; separately authorized publication. Exact route/method/version schema belongs in the reviewed Miu upstream API contract.
- Submit intent binds repository, PR, base SHA, head SHA, mode/model and trust authorization. Verify current PR and checkout exact object revisions before execution or site mutation.
- RequestStore atomically inserts principal/request identity+intent and reserves the single capacity slot before launch. Same identity/same intent returns its existing row, changed intent conflicts, a different active request reports busy. No stale lease expiry or identity reuse.
- Cancellation returns `cancel_requested`; child completion must attest cessation and cleanup. Missing proof becomes `unknown_effect` and holds capacity. Evidence retrieval validates run ownership and allowlisted paths, with finite size/time and no arbitrary content/URL access.

## Local credential provisioning

Miu-local management CLI provisions or revokes a principal. Secret input is stdin only, never an argv parameter; output contains redacted principal id/scopes/repository metadata only. Directory is private, database/credential files owner-only, symlinks/nonregular files/unsafe permissions/foreign ownership refused. No remote root shell, installer or uninstall HTTP endpoint.

## Required refusals before effects

Return safe typed refusals for unsupported/unqualified platform/build, unknown site/service ownership, stale proposal, resource conflict, insufficient capacity, stale/revoked grant, repository/revision mismatch, unsafe/untrusted execution, unknown prior operation, incomplete snapshot mechanism, or unsupported cancellation/cleanup proof. No fallback runtime, credentials, identity, or execution path.


## Implemented Miu prerequisite and remaining supported interfaces

Draft https://github.com/akashmdiu/miu/pull/1 implements the existing-listener `/management/v1` namespace: GET health/capabilities/app; POST requests with `Idempotency-Key`; GET request list/detail/evidence/result; POST request cancellation. Authentication is required even on loopback. Submissions are bound to pinned 40-character base/head revisions, repository, PR, model, trusted classification, mode and server-selected authentication mode. Exact replay retains its admitted mode after engine-policy changes, while current principal/repository grants still apply.

The API returns public metadata and a finite result projection. It does not provide arbitrary log/file retrieval, remote installation, verified GitHub App adoption or installed-build attestation. App/provenance unknown states must remain unknown in Sandbox. Read exact upstream documentation at the reviewed pin before integrating; this paragraph is not an API compatibility or Linux qualification claim.

## Feature service boundaries

`MiuService` owns support, service proposals and management calls; `RexEnvAdapter.invoke(OperationRequest)` owns runtime policy. Both receive feature metadata repositories, public project identity resolver, shared jobs, secret-use transport, capacity and recovery dependencies through composition. CLI and MCP may not bypass these services.

A proposal binds provider/action, exact registered target, installed and candidate build, affected and preserved resource references, active-work observation, recovery limits and explicit consent scope. Confirm requires matching current observations and the same proposal/request identity. Unknown effects are inspectable records, never implicit retry permission. Provider transport errors are redacted stable codes, not raw response bodies.

Runtime selection uses existing `RuntimeService` and canonical descriptors. Register RexEnv as explicit `incumbent_native`/`trusted_shared_host`; candidate support is unqualified until independent Ubuntu 24.04 evidence exists. The selection must not alter the default Compose/Caddy route or infer ownership from a matching hostname.

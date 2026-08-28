# Data Model: Agent-Aware Remote Development Sync

The model is intentionally path-light. A path may be used as a local locator
inside a process, but identity and durable ownership are based on opaque
project/workspace identifiers.

## SynchronizationRelationship

One shared local-to-remote source relationship.

| Field | Type | Rules |
|---|---|---|
| relationship_id | opaque string | Stable local identifier; generated once. |
| project_identity | opaque string | Required; resolved canonical project identity. |
| remote_name | safe name | Required registered remote. |
| workspace_id | opaque string | Required durable remote workspace identity. |
| mode | enum | `off`, `live`, or `checkpoint`. |
| lifecycle | enum | `active`, `stopped`, `conflicted`, `refused`, `indeterminate`. |
| owner_generation | integer | Monotonic accepted-generation sequence. |
| accepted_generation_id | opaque string/null | Latest complete remote generation. |
| pending_generation_id | opaque string/null | Latest requested but not accepted generation. |
| updated_at | UTC timestamp | Journal update time. |

Uniqueness is `(project_identity, remote_name, workspace_id)`. A human label or
local path is not part of the ownership key.

## SourceGeneration

One immutable source snapshot candidate or accepted source state.

| Field | Type | Rules |
|---|---|---|
| generation_id | opaque digest | Derived from the canonical manifest and content digest. |
| relationship_id | opaque string | Parent relationship. |
| sequence | integer | Monotonic within the relationship. |
| commit | full SHA/null | Commit identity when available. |
| dirty_digest | digest/null | Aggregate supported local change identity. |
| manifest_digest | digest | Digest of sorted, redacted manifest metadata. |
| file_count | bounded integer | Aggregate only; no names in public state. |
| byte_count | bounded integer | Aggregate only. |
| lifecycle | enum | `capturing`, `pending`, `transferring`, `accepted`, `refused`, `failed`, `diverged`. |
| request_id | replay-safe string | Idempotency key for the capture/accept request. |
| refusal_code | safe code/null | Credential, conflict, unstable-capture, or transport reason. |
| created_at/accepted_at | UTC timestamp/null | Lifecycle evidence. |

An accepted generation is immutable. A failed or refused generation never becomes
the accepted generation.

## Participant

An observing or triggering CLI/agent session. It contains only a bounded session
identifier, relationship ID, last-seen timestamp, and role (`owner`,
`participant`, or `observer`). It never changes ownership by disconnecting.

## PinnedJob

The generation boundary attached to a remote job.

| Field | Type | Rules |
|---|---|---|
| job_id | existing durable job ID | Required. |
| relationship_id | opaque string | Required for synchronized jobs. |
| generation_id | accepted generation ID | Immutable for the job lifetime. |
| source_access | enum | `managed_read_only` or `isolated_copy`. |
| parallel_safe | boolean | Existing job policy; sharing is allowed only after acceptance. |
| release_state | enum | `active`, `released`, `failed`. |

## DivergenceRecord

Redacted evidence that remote managed source changed outside the accepted local
generation. It contains relationship ID, affected aggregate count, comparison
generation, detected timestamp, and a safe resolution code. It does not contain
file names, diffs, source contents, or process arguments.

## State transitions

```text
stopped --start--> active
active --stop--> stopped
active --capture--> capturing --validated--> pending
pending --transfer--> transferring --remote_accept--> accepted
capturing/transferring --credential|conflict|unstable--> refused/failed
accepted --out_of_band_change--> diverged
diverged --explicit_resolution--> accepted or stopped
```

The relationship journal update and request identity are transactional. A lost
client response is resolved by reading the same request identity; it is never
replayed under a second identity.

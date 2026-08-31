# Data Model: Instance-Scoped Server Configuration Fragments

## Identity Conventions

- All durable identities are lowercase, fixed-format, and safe for routine output.
- `instance_incarnation_id` is random and opaque. It is not a display name, project
  path, credential, container ID, or reusable project identity.
- `content_id`, `fragment_set_id`, `generation_id`, and evidence digests are SHA-256
  identities over canonical, versioned inputs. Display prefixes may be bounded, but
  equality and recovery use the full value.
- Timestamps never choose a winner or authorize recovery.

## Instance Config Authority

One projection from the authoritative registry/config service.

| Field | Type | Rules |
|---|---|---|
| `instance_name` | string | Existing normalized display/runtime name; presentation only |
| `instance_incarnation_id` | opaque ID | Minted once on create; preserved until confirmed delete |
| `project_identity` | string | Existing canonical ownership evidence |
| `server_type` | enum | `nginx` or `litespeed` for config mutation |
| `runtime_mode` | enum | Must be local Compose in v1 |
| `server_config_mount_id` | digest | Binds Compose mount to incarnation and layout revision |
| `status` | enum | Existing lifecycle state; stopped/pending/unknown cannot authorize mutation |

The projection contains no fragment content. A record with no incarnation or mount
identity is legacy/unattached and can be inspected as unsupported but cannot mutate.

## Server-Config Fragment

| Field | Type | Rules |
|---|---|---|
| `name` | string | 1-64 lowercase ASCII letters/digits/single hyphens; no rewriting |
| `authority` | string | Exactly `wordpress-cache-v1` in v1 |
| `server_type` | enum | Exactly the active adapter type |
| `content_id` | digest | SHA-256 of exact accepted bytes |
| `content_size` | integer | 1-262,144 bytes |
| `content_locator` | private relative locator | Under incarnation repository; never routine output |
| `instance_incarnation_id` | opaque ID | Exact owner |
| `created_at` | timestamp | Audit only |
| `activated_at` | timestamp or null | Set only in a committed known-good generation |
| `policy_revision` | string | Exact common and adapter policy revision |

Validation rules:

- Exact bytes decode as strict UTF-8 server configuration and contain no NUL or
  forbidden controls.
- Name and exact content pass the existing high-confidence secret classifier before
  storage. A match is refused without echoing the match or bytes; false positives
  fail closed and require the caller to remove or replace the secret-like text.
- Common and adapter policy both accept; unknown directives are failure.
- A fragment cannot be copied or translated to another server type.

## Fragment Set

| Field | Type | Rules |
|---|---|---|
| `fragment_set_id` | digest | Canonical digest of layout revision, server, ordered fragment metadata |
| `instance_incarnation_id` | opaque ID | Exact owner |
| `server_type` | enum | One adapter |
| `fragments` | ordered tuple | Sorted by normalized `name`; no duplicates |
| `renderer_revision` | string | Binds adapter rendering semantics |
| `rendered_generation_id` | digest | Digest of all exact rendered files/manifest |
| `created_at` | timestamp | Audit only |

An empty set is valid and represents the Sandbox baseline without plugin fragments.
The complete set, not one fragment, is the unit of validation and activation.

## Runtime Observation

| Field | Type | Rules |
|---|---|---|
| `instance_incarnation_id` | opaque ID or unknown | Must match the request |
| `server_type` | enum or unknown | Must match the set |
| `runtime_id` | bounded opaque ID or unknown | Exact selected web service/container generation |
| `image_id` | content-addressed ID or unknown | Exact active image, not tag alone |
| `mount_id` | digest or unknown | Proves selected instance generation root is attached |
| `observed_generation_id` | digest or unknown | Adapter proves effective active generation |
| `readiness` | enum | `ready`, `stopped`, `degraded`, `unknown` |
| `observed_at` | timestamp | Must fall inside operation deadline/freshness bound |

Unknown, partial, stale, wrong-image, wrong-mount, or wrong-incarnation evidence cannot
authorize validation, activation, commit, or successful rollback.

## Validation Evidence

| Field | Type | Rules |
|---|---|---|
| `adapter` | enum | `nginx` or `litespeed` |
| `candidate_generation_id` | digest | Exact rendered candidate |
| `runtime_precondition_digest` | digest | Incarnation/server/runtime/image/mount facts |
| `policy` | phase result | `accepted` or bounded refusal code |
| `native_validation` | phase result | Exact-image result; raw output excluded |
| `inclusion_proof` | phase result | Every expected fragment exactly once and behavior canary for OLS |
| `started_at` / `ended_at` | timestamps | Within 60-second phase deadline |
| `evidence_digest` | digest | Canonical content-free evidence identity |

Validation evidence is usable only for the exact runtime precondition digest and is
invalidated by any re-observation mismatch.

## Known-Good State Receipt

| Field | Type | Rules |
|---|---|---|
| `schema` | integer | Versioned; unknown is degraded |
| `instance_incarnation_id` | opaque ID | Must match repository/mount owner |
| `server_type` | enum | Must match current instance |
| `fragment_set_id` | digest | Last committed active set |
| `generation_id` | digest | Exact render currently expected live |
| `runtime_image_id` | content-addressed ID | Image on which readiness was proven |
| `mount_id` | digest | Exact instance mount |
| `validation_evidence_id` | digest | Evidence for this generation |
| `readiness_evidence_id` | digest | Post-activation proof |
| `committed_at` | timestamp | Audit only |

The receipt is replaced atomically only after activation and readiness. It never names
a candidate merely because files were staged.

## Activation Transaction

| Field | Type | Rules |
|---|---|---|
| `transaction_id` | random opaque ID | One request attempt; retained through terminal result |
| `operation` | enum | `apply` or `revert` |
| `fragment_name` | string | Normalized requested name |
| `instance_incarnation_id` | opaque ID | Exact lock/repository owner |
| `server_type` | enum | Fixed for transaction |
| `prior_set_id` / `prior_generation_id` | digest | Exact known-good restore target |
| `candidate_set_id` / `candidate_generation_id` | digest | Exact requested target |
| `runtime_precondition_digest` | digest | Facts validated before activation |
| `phase` | enum | State machine below |
| `phase_evidence` | bounded map | Codes/digests/timestamps only; no content |
| `deadline_at` | timestamp | Whole operation maximum 180 seconds |
| `rollback_attempted` | boolean | Can transition false to true once |
| `terminal` | enum or null | `active`, `no_op`, `refused`, `rolled_back`, `conflict`, `recovery_needed` |

### Transaction State Machine

```text
requested
  -> prepared
  -> validated
  -> activating
  -> reloading
  -> observing_ready
  -> committed -> active

requested -> no_op
requested|prepared|validated -> refused
any pre-lock overlap -> conflict

activating|reloading|observing_ready failure
  -> restoring_prior
  -> recovery_reloading
  -> recovery_observing_ready
  -> rolled_back

restoring_prior|recovery_reloading|recovery_observing_ready failure/timeout
  -> recovery_needed
```

Rules:

- The journal is durable before `activating` begins.
- Only `committed` can replace the known-good receipt.
- `rollback_attempted=true` forbids another recovery activation for the transaction.
- Interrupted pre-activation phases may discard an unactivated candidate after exact
  observation. Interrupted post-activation phases must reconcile to the journal-bound
  prior generation or enter `recovery_needed`.
- A new mutation is forbidden while a nonterminal or recovery-needed journal exists.

## Inspection Projection

The read-only service derives one of:

- `healthy`: committed receipt, repository, mount, runtime generation, server, image,
  and readiness agree.
- `stopped`: exact ownership/state is readable, but web runtime is stopped.
- `degraded`: bounded mismatch or corrupt/unavailable evidence exists without enough
  proof to mutate.
- `recovery_needed`: journal proves possible live change without proven restoration.
- `unsupported`: server/runtime/mount does not support the capability.
- `absent`: exact normalized name is not in a healthy set.

Inspection never creates a lock file, repairs permissions, updates timestamps, prunes
generations, or rewrites a receipt.

## Behavior Evidence

| Field | Type | Rules |
|---|---|---|
| `instance_incarnation_id` | opaque ID | Target or control instance |
| `runtime_id` / `image_id` / `fragment_set_id` | identities | Before/after comparison |
| `request_id` | opaque ID | Request-scoped PHP sentinel correlation |
| `response_status` | integer | Bounded HTTP result |
| `server_marker` | bounded string or absent | Adapter/plugin-defined hit marker |
| `php_sentinel_before` / `php_sentinel_after` | integer/digest | Equality proves no PHP for that request |
| `readiness` | enum | Must remain ready for successful behavior proof |
| `observed_at` | timestamp | Evidence ordering only |

The acceptance bundle stores markers and identities, not response bodies, fragment
content, cookies, login URLs, headers unrelated to the proof, or raw logs.

## Retention and Deletion

- Healthy steady state retains the active known-good generation and enough exact prior
  material for any current transaction. Terminal superseded generations can be pruned
  only after no journal references them.
- Ordinary stop/start retains state. Server switching is blocked until the fragment set
  is empty and the journal healthy/terminal.
- Confirmed deletion unlinks the authoritative incarnation from the instance first,
  removes its runtime mount through the supported lifecycle, then deletes or tombstones
  its owner-only repository. Partial deletion remains visible and cannot be adopted.
- A new instance with the same name receives a new incarnation and an empty set.

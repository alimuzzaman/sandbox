# Data Model: Remote Host Swap and Memory Monitor Commands

## RemoteServiceEvidence

Authenticated, bounded proof attached to every control response.

| Field | Type | Validation |
|---|---|---|
| `remote_name` | string | exact registered remote selected by the controller |
| `target_identity` | opaque string | stable host identity; non-empty and bounded |
| `ownership_marker` | 24 lowercase hex | exact match with the registered service record |
| `runtime_revision` | 24 lowercase hex | exact match with the local shipped runtime digest |
| `resource_schema` | integer | supported resource envelope version |
| `host_memory_schema` | integer | must equal the implemented host-memory protocol version |
| `transport` | enum | `control`; no SSH/direct fallback value exists |

Any missing, malformed, or mismatched field makes evidence non-authorizing.

## RemoteSwapState

One strict observation of host-wide memory/swap and the owned lifecycle surface.

| Field | Type | Meaning |
|---|---|---|
| `observed_at` | UTC timestamp | bounded host observation time |
| `memory` | object | total and available bytes plus validity state |
| `swap_areas` | list | opaque area ID, `file|partition`, total/used bytes, active/persistent state, priority, ownership |
| `swappiness` | object | effective integer, owned persistence state, drift state |
| `monitor` | MonitorHealth | timer/service/sample/retention evidence |
| `container_eligibility` | object | aggregate `eligible|limited|mixed|unknown|unsupported`; no container identity |
| `reboot_verification` | enum | `verified|unverified|unknown` with separate evidence timestamp when verified |
| `operation_block` | object/null | active or rollback-incomplete operation identity/reason |
| `evidence_state` | enum | `known|unknown|stale|malformed|unsupported|unmanaged|partial|drifted` |
| `observation_digest` | SHA-256 | canonical digest of every apply-relevant field |

Validation rules:

- Every byte value is a non-negative integer; used cannot exceed total.
- A raw swap path, process/container name, PID, argv, environment value, or private path is
  not a model field.
- Any active or persistent area without one matching receipt is `unmanaged` and blocks all
  lifecycle mutation.
- Persistent configuration and reboot verification remain separate facts.

## SwapPolicy

Immutable requested/effective policy inside an enable plan.

| Field | Type | Default/rule |
|---|---|---|
| `size_gib` | integer | default 4; inclusive 1-8 |
| `swappiness` | integer | fixed 15 |
| `sample_interval_seconds` | integer | fixed 300 |
| `freshness_seconds` | integer | `2 * interval + 60` = 660 |
| `warning_swap_used_bytes` | integer | fixed 512 MiB |
| `warning_consecutive_samples` | integer | fixed 3 |
| `history_files` | integer | current plus at most 8 historical |
| `history_bytes` | integer | at most 32 MiB total |
| `sample_timeout_seconds` | integer | fixed 5 |

Eligibility calculations stored with the plan:

- requested bytes <= 50% physical RAM;
- requested bytes <= 10% filesystem capacity;
- post-allocation free bytes >= max(10 GiB, 15% filesystem capacity);
- all required observations complete and no unowned/conflicting state.

Equality is accepted for enable maximum/reserve bounds when every bound passes.

## SwapLifecyclePlan

Immutable controller-side review record.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | 1 |
| `plan_id` | SHA-256 | canonical identity of all fields except display text |
| `operation` | enum | `enable|disable` |
| `target` | RemoteServiceEvidence subset | remote/host/service/revision binding |
| `created_at` / `expires_at` | UTC timestamps | expiry is 15 minutes after creation; gates first acceptance, not same-operation reconciliation |
| `requested_policy` | object/null | enable input; null for disable |
| `effective_policy` | SwapPolicy/null | selected enable policy |
| `observation` | RemoteSwapState | exact reviewed prior state |
| `calculations` | list | name, observed inputs, threshold, comparator, pass/refusal |
| `intended_changes` | list | fixed logical artifact IDs and desired digests/states |
| `rollback_scope` | list | every prior-state element that must be restored/proven |
| `requires_confirmation` | boolean | always true |
| `state` | enum | `planned|in_progress|terminal|reconciliation_required` |

The repository is owner-only, atomic, and never silently rewrites an existing plan.
Apply accepts no replacement policy, operation, locator, or artifact list.

## ProtectedSwapOperation

Durable remote application of one confirmed plan.

| Field | Type | Meaning |
|---|---|---|
| `operation_id` | SHA-256 | stable identity derived from the plan/target; same on replay |
| `plan_id` | SHA-256 | confirmed plan identity |
| `phase` | enum | `accepted|preflight|staged|persistent|active|monitoring|verifying|rolling_back|terminal` |
| `prior_state_digest` | SHA-256 | canonical pre-mutation state |
| `last_observation_digest` | SHA-256 | latest apply-relevant host state |
| `phase_evidence` | bounded list | strict codes/timestamps/digests; no command output |
| `mutation_started` | boolean | whether any host state changed |
| `rollback` | RollbackEvidence/null | restoration record |
| `outcome` | enum/null | terminal result when proven |
| `unrelated_mutation_blocked` | boolean | true during work and after incomplete rollback |

State transitions:

```text
planned --confirm/current--> accepted -> preflight -> staged -> persistent
  -> active -> monitoring -> verifying -> applied
                                  |             |
                                  +-> rolling_back -> rollback_complete
                                                     rollback_incomplete

same operation replay -> observe journal -> continue safely or return proven terminal
different operation while active/rollback_incomplete -> refused
no mutation + failed preflight -> refused
ambiguous transport -> partial (journal remains authoritative)
```

Terminal outcomes are `applied`, `already_current`, `refused`, `partial`, `failed`,
`rollback_complete`, or `rollback_incomplete`. `planned` is the terminal result of a plan
request, not a protected operation.

## OwnershipReceipt

Root-owned proof for the one Sandbox-created configuration.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | 1 |
| `target_identity` | opaque string | exact host binding |
| `created_by_operation` | SHA-256 | first successful enable identity |
| `last_verified_operation` | SHA-256 | latest verified lifecycle identity |
| `policy` | SwapPolicy | expected effective policy |
| `artifacts` | map | fixed logical ID to type/mode/content digest/effective state |
| `swap_area_id` | opaque ID | owned area identity; no raw locator |
| `prior_swappiness` | object | bounded prior effective/persistence evidence for disable restoration |
| `verified_at` | UTC timestamp | latest complete verification |
| `reboot_verification` | object | separate authorized observation or `unverified` |

The receipt never authorizes adoption. Missing, foreign, duplicated, malformed, or
digest-drifted evidence is ambiguous and blocks mutation.

## AggregateMemorySample

Strict retained record with an allowlist only.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | 1 |
| `sampled_at` | UTC timestamp | host time with validity evidence |
| `status` | enum | `valid|partial|failed` |
| `memory` | object | total, available, free, buffers, cached bytes when observed |
| `swap` | object | total, free, used bytes |
| `pressure` | object/null | aggregate PSI `some`/`full` averages and totals, separately labeled |
| `vm_counters` | object | allowlisted cumulative swap-in/out and major-fault counters |
| `errors` | list | stable bounded reason codes only |

No free-form text, unknown key, source line, path, identity, PID, process/container name,
command, argument, or environment field is permitted. A partial/failed sample never fills
missing counters.

## MonitorHealth

| Field | Type | Meaning |
|---|---|---|
| `service_state` / `timer_state` | enum | `active|inactive|missing|unknown|drifted` |
| `interval_seconds` | integer/null | configured cadence |
| `latest_sample_at` / `age_seconds` | timestamp/number/null | observed freshness inputs |
| `freshness` | enum | `fresh|stale|missing|malformed|unknown` |
| `next_sample_at` | timestamp/null | bounded schedule evidence |
| `sustained_swap_use` | boolean/null | true only after three valid threshold samples |
| `pressure_state` | enum | separate `normal|pressured|unknown` classification |
| `retention` | object | current/history file counts, total bytes, compliance, truncation |

The exact freshness boundary (age <= 660 seconds by default) remains fresh. Clock
regression yields unknown, not freshness.

## HistoryWindow

| Field | Type | Validation |
|---|---|---|
| `requested_range` | UTC start/end or null | normalized, start <= end |
| `observed_range` | UTC start/end or null | derived only from returned valid timestamps |
| `samples` | list | newest matching samples; max 1,000 |
| `counts` | object | returned, valid, partial, failed, malformed, missing estimates |
| `freshness` | enum | latest-sample state |
| `complete` | boolean | false for gaps, malformed data, unavailable files, or truncation |
| `truncated` | boolean | true at sample or 1 MiB response bound |

## RollbackEvidence

Enumerates every prior-state element, restoration action code, observed result, digest, and
verification state. `rollback_complete` is legal only when all required elements are
verified restored. Any unknown element forces `rollback_incomplete`, preserves the journal
and receipt evidence, and keeps unrelated mutation blocked while status/history remain
read-only.

## HostMemoryStatusProjection

Read-only consumer projection exposed by Feature 046 for Feature 047 host governance.
It contains target/service evidence state, aggregate RAM/swap totals, monitor freshness,
sustained-use/pressure state, ownership state, operation block, and observation timestamp.
It contains no planner, plan ID, confirmation, provider, receipt locator, artifact content,
or mutation method. Unknown/partial/drifted/rollback-incomplete values are non-authorizing.

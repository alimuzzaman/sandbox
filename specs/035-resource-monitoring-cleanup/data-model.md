# Data Model: Resource Monitoring and Safe Cleanup

## StorageTarget

Identifies exactly one machine observed or mutated by a request.

| Field | Type | Rules |
|-------|------|-------|
| `kind` | enum | `local` or `remote` |
| `name` | string | `local`, or an existing configured remote name |
| `identity` | string | Stable non-secret host identity captured during resolution |
| `sandbox_home` | string | Normalized managed root; never accepted from an apply caller |
| `observed_at` | timestamp | UTC timestamp associated with identity evidence |

Two targets are equal only when `kind`, `name`, and `identity` match. A plan
cannot be applied when the current target identity differs.

## CapacityObservation

Raw host capacity at one point during a scan.

| Field | Type | Rules |
|-------|------|-------|
| `total_bytes` | integer | Non-negative |
| `used_bytes` | integer | Non-negative and not greater than total |
| `available_bytes` | integer | Non-negative |
| `reserved_bytes` | integer | Non-negative reconciliation remainder |
| `measured_at` | timestamp | UTC |

`used + available + reserved` must reconcile to total within the source
filesystem's reported precision. Category totals are not forced to equal used
bytes because observations may overlap or be partial.

## ResourceObservation

One discovered resource or aggregate category.

| Field | Type | Rules |
|-------|------|-------|
| `resource_id` | string | Stable, non-secret identifier within target |
| `kind` | enum | `worktree`, `volume`, `container`, `image`, `network`, `build_cache`, `download_cache`, `job_artifact`, `backup`, `snapshot`, `runtime`, `log`, `package_cache`, `other` |
| `locator` | string | Internal exact path or engine ID; redacted/omitted from human output when sensitive |
| `display_name` | string | Non-secret operator label |
| `owner_kind` | enum | `project`, `host`, `instance`, `workspace`, `job`, `backup`, `sandbox`, `unknown`, `unmanaged` |
| `owner_id` | string or null | Validated managed identifier |
| `classification` | enum | `active`, `retained`, `disposable_cache`, `stale_candidate`, `unverified`, `unmanaged` |
| `size_state` | enum | `measured`, `not_measured`, `timed_out`, `unavailable` |
| `size_bytes` | integer or null | Present only when measured |
| `reclaimable_bytes` | integer | Zero unless eligibility evidence is complete |
| `capacity_accounted` | boolean | True only when this observation contributes to host-capacity attribution; nested detail remains visible without double counting |
| `age_seconds` | integer or null | Non-negative when trustworthy |
| `references` | list | Registry/runtime/job/backup/mount references that protect or explain the resource |
| `evidence` | list | Typed ownership, liveness, retention, and boundary facts |
| `errors` | list | Bounded non-secret category errors |

### Classification transitions

```text
discovered
  ├─ authoritative unmanaged evidence ───────────────> unmanaged
  ├─ insufficient ownership or measurement ─────────> unverified
  └─ positive Sandbox ownership
       ├─ live reference or mount ───────────────────> active
       ├─ retention/permanent reference ─────────────> retained
       ├─ disposable cache policy + unused ──────────> disposable_cache
       └─ persistent scope + every non-use check ────> stale_candidate
```

A cleanup action never changes an observation in place. Revalidation produces a
new observation used for the outcome.

Thorough host-root observations are capacity-accounted. Nested Docker,
workspace, volume, image, and build-cache observations are detail-only so they
can be ranked without inflating attributed host bytes.

## StorageScan

Aggregates capacity and resource observations for one target.

| Field | Type | Rules |
|-------|------|-------|
| `scan_id` | string | Opaque unique ID |
| `target` | StorageTarget | Required |
| `mode` | enum | `fast` or `thorough` |
| `started_at` / `completed_at` | timestamp | UTC and monotonic order |
| `budget_seconds` | number | Positive finite overall budget |
| `status` | enum | `complete`, `partial`, `failed` |
| `capacity` | CapacityObservation or null | Null only when host capacity failed |
| `resources` | list[ResourceObservation] | Deterministically ordered |
| `attributed_bytes` | integer | Sum policy documented with overlap caveat |
| `unknown_bytes` | integer | Capacity gap, never inferred as reclaimable |
| `reclaimable_bytes` | integer | Sum of eligible measured candidates |
| `confidence` | enum | `high`, `medium`, `low` |
| `drift` | object or null | Capacity/resource change evidence |
| `category_outcomes` | list | Per-category completion/timeout/error |

`partial` is required when any requested category times out, is unavailable, or
cannot be measured within the budget.

## CleanupCandidate

Immutable candidate copied from a scan into a plan.

| Field | Type | Rules |
|-------|------|-------|
| `resource_id` | string | Matches observation |
| `kind` | enum | Allowed for plan scope |
| `locator_digest` | string | Binds exact internal locator without exposing it |
| `expected_owner` | object | Positive owner identity |
| `expected_absence` | list | References/mounts that must still be absent |
| `expected_size_bytes` | integer or null | Estimate only |
| `expected_reclaimable_bytes` | integer | Conservative estimated physical reclamation; may be lower than logical size |
| `evidence_digest` | string | Digest of canonical eligibility evidence |

Candidate locators are stored only in the protected local plan record, not
accepted from an apply request.

## CleanupPlan

No-write proposal persisted atomically.

| Field | Type | Rules |
|-------|------|-------|
| `schema_version` | integer | `1` |
| `plan_id` | string | Opaque, filename-safe, globally unique |
| `target` | StorageTarget | Required |
| `scope` | enum | `cache` or `stale` |
| `created_at` / `expires_at` | timestamp | Default expiry 15 minutes after creation |
| `scan_id` | string | Source scan |
| `candidates` | list[CleanupCandidate] | Exact reviewed scope |
| `exclusions` | list | Resource ID plus reason |
| `estimated_reclaimable_bytes` | integer | Non-negative |
| `state` | enum | `planned`, `in_progress`, `completed`, `indeterminate`, `expired` |
| `confirmation_required` | boolean | Always true |

### Plan state transitions

```text
planned ── expired by time ─────────────> expired
planned ── confirmed + target match ────> in_progress
in_progress ── all outcomes captured ───> completed
in_progress ── remote timeout/unknown ──> indeterminate
```

`completed`, `expired`, and `indeterminate` plans cannot start a new automatic
apply. An operator must rescan and create a new plan after an indeterminate
outcome.

## CleanupItemOutcome

Result of revalidating and optionally acting on one candidate.

| Field | Type | Rules |
|-------|------|-------|
| `resource_id` | string | Candidate identity |
| `status` | enum | `removed`, `skipped`, `failed`, `timed_out`, `already_absent` |
| `reason` | string | Stable non-secret reason code |
| `observed_bytes` | integer or null | Best available estimate |
| `revalidated_at` | timestamp | UTC |
| `evidence_changed` | boolean | True when expected eligibility changed |

## CleanupRun

One confirmed attempt to apply a plan.

| Field | Type | Rules |
|-------|------|-------|
| `run_id` | string | Opaque unique ID |
| `plan_id` | string | Required |
| `target` | StorageTarget | Must exactly match plan |
| `started_at` / `completed_at` | timestamp | UTC |
| `status` | enum | `completed`, `partial`, `indeterminate`, `refused` |
| `capacity_before` / `capacity_after` | CapacityObservation or null | Recorded when available |
| `outcomes` | list[CleanupItemOutcome] | Exactly one terminal outcome per candidate when determinate |
| `planned_bytes` | integer | From plan |
| `observed_reclaimed_bytes` | integer or null | Derived from capacity with drift disclosure |
| `errors` | list | Bounded non-secret run errors |

## Validation invariants

- Unknown, unmanaged, unmeasured, active, or retained observations have zero
  reclaimable bytes.
- Cache plans cannot contain named volumes or persistent worktrees.
- Stale plans cannot contain candidates without positive ownership and complete
  non-use evidence.
- An apply request supplies only plan identity, target selection, and explicit
  confirmation; candidate locators come from the stored plan.
- Every candidate is re-observed immediately before action.
- Plan and run records use atomic replace and restrictive permissions.
- Structured results redact secrets, file contents, userinfo, credentials, and
  sensitive mount options.

## Convergence amendment — 2026-08-13 (workspace ownership projection)

### WorkspaceResourceBinding

Resource monitoring consumes this typed projection from the workspace/job service; it
does not inspect workspace SQLite or legacy files directly.

| Field | Type | Rules |
|---|---|---|
| `workspace_id` | opaque string | Required stable owner identity; never a path or display label. |
| `project_identity` | string | Required owner tuple component; must agree with the workspace service. |
| `workspace_label` | string | Display/filter value; not sufficient for control or deletion. |
| `owner_kind` | enum | `workspace`, `unknown`, `foreign`, or `unmanaged`; only `workspace` with complete evidence can attribute. |
| `lifecycle` | enum | `provisioning`, `ready`, `resetting`, `destroying`, `destroyed`, `indeterminate`, or `unknown`. |
| `alias_evidence` | list | Typed alias kind/digest and quality; collisions are explicit. |
| `active_references` | object | Leases, containers, jobs, mounts, and retention references; missing fields are unknown, not zero. |
| `locator_digest` / `evidence_digest` | string | Non-secret digests binding the observed resource and ownership evidence. |
| `index_generation` | integer | Generation observed from the service; drift invalidates persistent ownership. |
| `observed_at` | timestamp | UTC observation time and target identity. |

Bindings with unresolved/conflict/invalid decisions, duplicate aliases, missing index,
stale generation, or unavailable remote evidence classify resources as `unknown` or
`indeterminate`, set `reclaimable_bytes=0`, and carry a bounded error. The resource
service may display such observations but never repairs them or promotes them to a plan.

### Metadata-only migration invariant

Workspace index migration/relocation changes only owner metadata and path-bearing
locators. Resource observations must preserve their non-secret resource IDs and report
unchanged network, container, job, volume, upload, snapshot, and project-file counts.
An observed locator change is not a lifecycle release and cannot make a network or volume
eligible for cleanup. A fresh typed rescan is required after generation drift.

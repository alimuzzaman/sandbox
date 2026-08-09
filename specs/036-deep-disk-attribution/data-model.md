# Data Model: Deep Disk Attribution

## DeepAttribution

Optional additive evidence attached to a thorough storage scan only when deep
mode is requested.

| Field | Type | Rules |
|-------|------|-------|
| `status` | enum | `complete` or `partial` |
| `filesystems` | list[FilesystemObservation] | Deterministic mount ordering |
| `findings` | list[AttributionFinding] | Bounded, descending allocated bytes |
| `capabilities` | list[CapabilityObservation] | One per attempted collector |
| `reconciliation` | AttributionReconciliation | Required |
| `coverage` | list[CoverageObservation] | One per category/boundary |
| `capacity_scope_id` | string or null | Opaque identity for the reconciled selected capacity scope set; must match the enclosing status capacity scope before totals are combined |

It is absent from fast and ordinary thorough status responses, preserving the
existing contract.

## FilesystemObservation

| Field | Type | Rules |
|-------|------|-------|
| `filesystem_id` | string | Stable digest of non-secret device and mount identity |
| `display_name` | string | Redacted mount label |
| `filesystem_type` | string | Bounded safe type |
| `total_bytes` | integer | Non-negative |
| `used_bytes` | integer | Non-negative and no greater than total |
| `available_bytes` | integer | Non-negative |
| `writable` | boolean | Observed mount mode |
| `selected` | boolean | Whether deep directory measurement was requested |
| `selection_reason` | enum | `root`, `sandbox_home`, `container_data`, `managed_root`, `unrelated`, `virtual`, or `unavailable` |
| `status` | enum | `complete`, `partial`, `not_selected`, `timed_out`, or `unavailable` |
| `observed_allocated_bytes` | integer or null | Present only when measured |
| `hardlink_deduplication` | enum | `confirmed`, `partial`, or `unavailable` |
| `limitations` | list[string] | Bounded stable codes |
| `mount_id` / `parent_mount_id` | string or null | Opaque safe mount-topology identities |
| `capacity_scope_id` | string or null | Opaque capacity boundary identity; duplicate/nested mounts in one scope are measured and counted once |
| `mount_flags` | list[string] | Bounded safe mount classification only; raw mount options and sources are excluded |

Managed roots are typed, existing read-only repository evidence used only to
select a containing filesystem. Their paths and other locators are not public
fields; neither are raw device sources or mount options.

## AttributionFinding

| Field | Type | Rules |
|-------|------|-------|
| `finding_id` | string | Stable non-secret digest |
| `kind` | enum | `directory`, `deleted_open`, `container_image`, `container`, `volume`, `build_cache`, or `filesystem_overhead` |
| `display_name` | string | Redacted category/owner label |
| `filesystem_id` | string or null | Related filesystem |
| `owner_kind` / `owner_id` | string or null | Minimized remediation owner |
| `observed_bytes` | integer | Non-negative |
| `capacity_accounted` | boolean | True only for non-overlapping allocation |
| `overlap` | enum | `none`, `directory_root`, `shared_layers`, `logical_cache`, or `unknown` |
| `activity` | enum | `active`, `inactive`, `mixed`, or `unknown` |
| `guidance` | enum | `existing_cache_scope`, `existing_stale_scope`, `manual`, `monitoring_only`, or `non_cleanable` |
| `evidence` / `limitations` | list[string] | Stable, bounded, secret-safe codes |
| `unique_bytes` / `shared_bytes` / `potentially_reclaimable_bytes` | integer or null | Docker logical diagnostics; never capacity-accounted by themselves |

Rank truncation never changes aggregate totals.

## CapabilityObservation

| Field | Type | Rules |
|-------|------|-------|
| `category` | string | Directory, deleted-open, mount, or container accounting |
| `name` | string | Capability name, not an executable path |
| `version` | string or null | Bounded and redacted |
| `fallback` | boolean | True when the preferred capability was unavailable |
| `privilege` | enum | `elevated`, `unprivileged`, or `unavailable` |
| `status` | enum | `complete`, `partial`, `timed_out`, or `unavailable` |
| `limitations` | list[string] | Stable codes |

## CoverageObservation

| Field | Type | Rules |
|-------|------|-------|
| `category` | string | Stable category identifier |
| `boundary_id` | string or null | Filesystem or engine boundary |
| `status` | enum | `complete`, `partial`, `not_selected`, `timed_out`, `cancelled`, `disconnected`, or `unavailable` |
| `duration_ms` | integer | Non-negative |
| `confidence` | enum | `high`, `medium`, or `low` |
| `privilege_sufficient` | boolean | Whether requested visibility was available |
| `reason` | string or null | Stable non-secret reason |

## AttributionReconciliation

| Field | Type | Rules |
|-------|------|-------|
| `used_bytes` | integer | Target capacity snapshot |
| `directory_allocated_bytes` | integer | Capacity-accounted selected filesystem allocation |
| `deleted_open_bytes` | integer | Not directory-visible |
| `observable_overhead_bytes` | integer | Reserved/metadata only when measured |
| `overlapping_logical_bytes` | integer | Diagnostic only |
| `accounted_bytes` | integer | Capped at used bytes |
| `residual_unexplained_bytes` | integer | `max(used - accounted, 0)` |
| `overage_bytes` | integer | `max(raw accounted - used, 0)` |
| `drift_bytes` | integer | Absolute capacity drift during scan |
| `drift_material` | boolean | Greater of 1% used or 64 MiB |
| `capacity_drift_bytes` / `capacity_drift_material` | integer / boolean | Capacity snapshot drift and its threshold result |
| `attributed_drift_bytes` / `attributed_drift_material` | integer / boolean | Allocated-attribution baseline drift and its threshold result |

## Validation invariants

- Deep attribution is read-only and cannot create cleanup candidates.
- Every discovered writable local filesystem has one filesystem and coverage
  record, even when not selected.
- A finding marked overlapping never contributes to `accounted_bytes`.
- Deleted-open records are deduplicated by stable file identity where
  available, map allocated blocks only to selected filesystems, and never
  include file names or process arguments. Missing privilege or allocated-block
  visibility makes this category partial rather than zero.
- Residual and overage are never negative.
- Missing, partial, unavailable, or timed-out bytes remain residual; they are
  never reclaimable.
- Timeout/cancellation preserves previously valid structured evidence. Deep
  status is partial whenever coverage is not complete; cancellation is carried
  by the enclosing terminal status and coverage state.
- Directory allocation is observed allocated-block evidence, not a claim of
  exact physical ownership; copy-on-write, shared allocation, hard links,
  metadata, and live drift remain limitations.
- Existing cleanup scopes and their eligibility evidence remain authoritative.

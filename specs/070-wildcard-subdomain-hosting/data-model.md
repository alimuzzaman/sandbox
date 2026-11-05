# Data Model: Wildcard Subdomain Hosting

## ZoneCoverage (remote document)

One per zone, `schema_version: 1`. A reader that sees a higher version refuses (`coverage_schema_unsupported`).

| Field | Meaning |
|---|---|
| `zone`, `zone_id` | Normalized name, provider id |
| `state` | See transitions |
| `generation` | Integer, raised on each completed enable, disable or repair |
| `operation` | `{id, kind, phase, controller, started_at, lease_expires_at}` or null |
| `interrupted_kind` | The kind that left `incomplete`, or null |
| `shared_records` | List of `{id, type, content, proxied}` as last applied |
| `fallback` | `{file, digest, marker, certificate: {path, not_after, id}}` |
| `exceptions` | Exact routes reported at enable: `{hostname, file, owner}` |
| `dependents` | Map of state key to Dependent |
| `dependents_revision` | Integer, raised on every dependent change, so a plan cannot go stale unnoticed |
| `history` | Last 64 `{at, kind, result, operation_id}` |

No secret values, no credentials and no route bodies.

### State transitions

```text
absent, retired ──enable──> enabling
enabling ──stop-before-publish──> fallback_only
enabling ──done──> active
enabling ──failure──> incomplete(enable)
enabling ──resume──> enabling
disabling ──resume──> disabling
incomplete(enable) ──retry──> enabling
incomplete(disable) ──retry──> disabling
incomplete(enable) ──rollback ok──> fallback_only
fallback_only ──enable──> enabling
active ──disable──> disabling ──done──> disabled
disabling ──failure──> incomplete(disable)
disabled ──enable──> enabling
active, incomplete(enable) ──repair──> active or incomplete(enable)
disabled, fallback_only ──remove-fallback──> retired
```

`incomplete` retries only its `interrupted_kind`, so retrying a failed disable can never republish DNS. To reverse direction, the operator finishes the interrupted kind first.

### Operations

One `operation` at a time. Starting another refuses `coverage_operation_active`, naming the holder. `phase` renews a 15-minute lease, and a holder runs no provider or route write without a successful `phase` call first. After the lease ends, the **same kind** may resume with `resume: true`: the store issues a new operation id, and the old id's `phase` and `finish` then refuse. Resuming observes before writing. A different kind keeps refusing until that kind finishes. A crash at any phase therefore cannot lock the zone for good.

## Dependent

| Field | Meaning |
|---|---|
| `hostname` | The covered name |
| `state` | `pending` before effects, `bound` after verified delivery, `recovered` when a rebuilt record inferred it |
| `operation_id`, `generation`, `updated_at` | The last writer |

Bound with `pending` before the first target effect. A failed apply leaves `pending`, and a pending dependent counts as dependent. Removed by destroy, or by the confirmed `--release-dependent` for one named stale entry.

## Target DNS policy

Manifest key `cloudflare.dns`: `auto` (default) or `exact`. Any other value refuses at validation. `exact` needs no coverage. `dns` is not part of the manifest version: version stays 1.

## CoverageDecision (in-memory)

`{mode: "exact"|"shared", zone, generation, reason}`, stored as `validated["dns_coverage"]`.

## CoverageEvidence

In the host state `edge` block, the apply result and `host status`:

```json
{"origin": {"result": "passed"}, "public_edge": {"result": "passed|failed|unverified", "reason": "..."}, "resolver": {"result": "resolved|negative|error|skipped"}, "observed_at": "..."}
```

`resolver` never fails delivery. `unverified` is never shown as passed.

## Name eligibility rules

`eligible(hostname, zone, zone_records)` is true only if `hostname` is exactly one label above `zone`, the label is not `*` and does not begin with `_`, and no record in `zone_records` is named `hostname` or ends with `.hostname`.

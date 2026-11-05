# CLI Contract: `sb remote coverage`

```sh
sb remote coverage <remote> --zone <zone> [--json]                       # status, read-only
sb remote coverage <remote> --zone <zone> --enable --plan
sb remote coverage <remote> --zone <zone> --enable --confirm --plan-digest D [--stop-before-publish]
sb remote coverage <remote> --zone <zone> --disable --plan
sb remote coverage <remote> --zone <zone> --disable --confirm --plan-digest D
sb remote coverage <remote> --zone <zone> --repair --plan
sb remote coverage <remote> --zone <zone> --repair --confirm --plan-digest D
sb remote coverage <remote> --zone <zone> --renew-certificate --confirm
sb remote coverage <remote> --zone <zone> --remove-fallback --confirm
sb remote coverage <remote> --zone <zone> --release-dependent <state-key> --confirm
```

The digest covers the action, zone, generation, dependents, exceptions and planned record writes. A changed value refuses `coverage_plan_stale`. `--disable --confirm` with dependents succeeds only through a digest from a plan that named them, and that plan's output states that they stop resolving.

## Status output

```json
{"ok": true, "zone": "asb.bd", "state": "active", "generation": 1,
 "configured": {"shared_records": [], "fallback": {}},
 "observed": {"shared_dns": "match|absent|changed|unavailable", "fallback": "match|missing|changed|unavailable",
              "certificate": {"not_after": "...", "renewal_overdue": false},
              "zone_tls_mode": "strict|other|unavailable", "edge_tls": "passed|failed|unavailable"},
 "safe": true, "dependents": [], "exceptions": [], "drift": [], "operation": null}
```

`safe` is false when shared DNS may exist and the fallback is not observed matching. `edge_tls` comes from a fresh random label requiring the fallback marker. With no document, status still lists any `*.zone` record and any fallback file as unmanaged.

## Fixed refusal codes

A Caddy remote refuses with the existing `front_door_capability_unavailable`.

`coverage_operation_active`, `coverage_record_missing`, `coverage_plan_stale`, `coverage_conflict`, `coverage_zone_unavailable`, `coverage_ssl_not_strict`, `coverage_record_exists` (a `*.zone` record that differs from what would be written; an equal one is adopted and shown in the plan), `coverage_dependents_unknown`, `coverage_fallback_unverified`, `coverage_schema_unsupported`, `coverage_not_active`, `coverage_drift`, `coverage_name_ineligible`, `coverage_disabled_dependent`, `managed_record_drift`, `coverage_store_unavailable`.

## Changes to existing commands

- `sb host plan`: gains `dns_coverage` with mode, reason and evidence state. A shared target shows empty `records`.
- `sb host apply`: gains `--allow-record-overwrite`, and the result gains `evidence`.
- `sb host status`: shows the coverage mode and evidence.
- `sb host destroy`: reports `shared_coverage_retained` and releases the dependent. Release failure is reported, and does not undo the destroy.
- `sb remote edge`: reports a fallback file as `coverage_fallback: true`, and does not list its exempt overlaps as conflicts.
- `--remove-route` refuses a fallback file and points to `--remove-fallback`.

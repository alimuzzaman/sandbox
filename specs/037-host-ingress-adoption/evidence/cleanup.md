# Cleanup and recovery (macOS, partial)

**Scope**: T052 — normal / repeated / drift / unavailable cleanup with foreign-route
health. Captured on darwin. Two of the four scenarios are provable today; the two that
need an actually-owned adopted route are not, because no ingress adapter is adoptable on
any platform yet.

**Host**: macOS 15 (Darwin 25.6.0). 2026-08-02.

## Repeated cleanup with nothing owned

```text
domains ingress cleanup --json   (run 1)
  {"ok": true, "state": "ready", "mutated": false,
   "reason": {"code": "already_absent"}, "cleanup": {"complete": true, "residual": []}}

domains ingress cleanup --json   (run 2)
  identical result
```

Repeating the operation produces the same final state and reports no mutation (FR-015,
SC-006).

## Reconcile with nothing owned

```text
domains ingress reconcile --json
  {"ok": true, "state": "ready", "reason": {"code": "already_absent"},
   "recovery": {"residual": []}, "mutated": false}
```

## Foreign state preserved

The default provider was serving throughout. After the cleanup and reconcile sequence:

```text
https://templately-staging.tst/   200
dscacheutil templately-staging.tst -> 127.0.0.77
```

No foreign listener, route, or resolver entry changed, and the default provider's own
routes were not treated as cleanup targets (FR-027: only attributable owned routes are
removed).

## Not covered — needs an adoptable adapter

- **Normal cleanup of an owned route**: add a route through a proven incumbent, remove it,
  and show the incumbent's pre-existing routes still healthy.
- **Drift**: edit an owned route externally, then show ensure/destroy leaves it untouched
  and reports drift with a retained recovery record.
- **Unavailable incumbent**: stop the incumbent mid-cleanup and show incomplete cleanup
  plus a non-secret retry record.

Unit coverage for all three exists (`tests/test_ingress_cleanup.py`,
`tests/test_ingress_recovery.py`); this file records that the LIVE half is still open, and
T052 stays unchecked until it is captured.

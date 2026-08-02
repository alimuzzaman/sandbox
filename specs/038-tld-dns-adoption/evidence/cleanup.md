# Cleanup and recovery (macOS, partial)

**Scope**: T050 — normal / owner-change / drift / unreachable / repeated cleanup. Captured
on darwin. Only the no-owned-binding and repeat cases are provable today: no resolver
adapter is adoptable on any platform, so Sandbox owns no binding to clean up.

**Host**: macOS 15 (Darwin 25.6.0). 2026-08-02.

## Repeated cleanup with nothing owned

```text
./sb domains cleanup --project-dir . --json   (run 1)
  {"ok": true, "state": "ready", "mutated": false,
   "reason": {"code": "already_absent", "message": "No owned resolver binding remains."},
   "resolver": {"owner": "macos:scoped-resolver", "tier": "implemented_unproven"}}

./sb domains cleanup --project-dir . --json   (run 2)
  identical result
```

Repeating converges on the same state and reports no mutation (FR-026, SC-005).

## Foreign and default-provider state preserved

After the cleanup runs:

```text
https://templately-staging.tst/            200
dscacheutil templately-staging.tst      -> 127.0.0.77
/etc/resolver/test                         still Herd's, unmodified
/etc/resolv.conf                           still the macOS-managed symlink
```

Cleanup of composed state did not touch the incumbent's suffix, the machine resolver, or
the default provider's own scoped entry (FR-019, FR-020).

## Not covered — needs an adoptable adapter

- **Normal cleanup of an owned binding**: apply a scoped route, then remove it and show the
  rule and authority gone with unrelated answers unchanged.
- **Owner change**: switch the active resolver after applying, then show status reporting
  `resolver_owner_changed` without mutating either resolver.
- **Drift**: modify the owned rule externally, then show cleanup refusing and retaining a
  non-secret recovery record.
- **Unreachable resolver**: stop the authority mid-cleanup and show incomplete cleanup plus
  retry state.

Unit coverage exists in `tests/test_domain_cleanup.py`; this file records that the LIVE
half is still open, and T050 stays unchecked until it is captured.

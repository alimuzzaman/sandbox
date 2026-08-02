# Cleanup and recovery (Ubuntu 24.04 + macOS)

**Scope**: T052 — normal / repeated / drift / unavailable cleanup with foreign-route
health. The owned-route cases ran live against system Caddy on Ubuntu; the
nothing-owned cases were captured on macOS.

**Host**: Ubuntu 24.04.4 LTS, Caddy 2 under systemd with 16 pre-existing fragments.
Harness: `python3 tests/live_ingress_recovery.py --project-dir ~/git/templately
--label tmp-logo --evidence-id 037-t052-ubuntu-2404`. 2026-08-02.

## External drift is preserved, not removed

```text
apply                    ok=True  ready
fragment edited outside Sandbox:  "# edited outside sandbox" appended

cleanup_with_drift       ok=False cleanup_incomplete  residual=[<route>]
fragment_preserved       "# edited outside sandbox"      (byte-for-byte intact)
recovery_records         [{"reason_code": "route_drifted", "status": "drifted"}]
```

Sandbox refused to remove a route it could no longer prove it owned, left the file exactly
as the operator edited it, and retained a non-secret retryable record (FR-014, FR-028).

## Incumbent unavailable during cleanup

```text
systemctl stop caddy
incumbent_state_during        inactive
cleanup_with_incumbent_down   ok=False cleanup_incomplete residual=[<route>]
systemctl start caddy
incumbent_state_restored      active
baseline_after_restart        200
```

Cleanup reported incomplete rather than claiming success, and nothing was left in a
half-removed state (FR-028).

## Normal and repeated cleanup

```text
cleanup_normal    ok=True  ready  cleanup_complete
fragment_gone     True
domain_cleanup    cleanup_complete
baseline_after    200
```

From the T044 run, repeating cleanup is safe:

```text
ingress_cleanup_first   ok=True  cleanup_complete   mutated=True
ingress_cleanup_second  ok=True  already_absent     mutated=False
```

And with nothing owned at all (macOS):

```text
domains ingress cleanup   x2  -> ok already_absent mutated=false, identical both runs
domains ingress reconcile     -> ok already_absent residual=[]
```

## Foreign routes stay healthy

The incumbent's pre-existing baseline route answered `200` before adoption, while the owned
route was live, during the drift refusal, after the incumbent restart, and after final
cleanup. `caddy validate` reported `Valid configuration` throughout, and the fragment count
returned to its original 16.

## Defect this run found and fixed

Adopting a route makes Caddy open a second socket on the same address and port for the new
site's server. The incumbent ownership digest listed endpoints, so that duplicate read as a
different product and cleanup declared the incumbent replaced — orphaning the route the
apply had just created, with `incumbent_replaced` recorded instead of `route_drifted`. The
digest now covers the endpoint SET; a genuinely new address or port still counts as a
replacement.

## Not covered

- An incumbent that is replaced by a DIFFERENT product between apply and cleanup (unit
  coverage only: `tests/test_ingress_cleanup.py`).
- Cleanup during a failed reload, where the fragment is removed but the service refuses to
  reload.

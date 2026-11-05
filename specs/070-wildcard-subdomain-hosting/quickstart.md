# Quickstart: Live Validation

Real run, on `xcloud-london`. Save evidence under `/Users/alim/Sites/git/.scratch/sandbox/wildcard-070/live/`. Use `./sb` from the checkout under test.

## Stage A: read-only

0. Smoke after each code stage, read-only: `./sb remote edge xcloud-london --json` stays healthy with no new conflicts, and `./sb host plan` for an existing asb.bd target matches its earlier output.
1. `./sb remote coverage xcloud-london --zone asb.bd --enable --plan --json`. Expect: every exact route listed as an exception, no blocker, planned proxied A record at `*.asb.bd`, the consequence for older checkouts stated. Zone records unchanged.

## Stage B: fallback only, no DNS

**Approval needed**: from this step every checkout without this feature refuses applies under `asb.bd`, including `~/.local/bin/sb` (`sandbox-main`) and any thread on an older controller. Say so when asking.

2. `--enable --confirm --plan-digest D --stop-before-publish`. Expect state `fallback_only`, and `sb remote edge xcloud-london --json` healthy.
3. From the host, through a supported probe: a random label over loopback returns 404, marker, `X-Robots-Tag`, same body on HTTP and HTTPS, on `/`, `/robots.txt`, `/.well-known/acme-challenge/x`, POST and HEAD. `a.b.asb.bd` returns the same.
4. Exact Sandbox routes, panel routes and `sb-xcloud-london.asb.bd` respond exactly as recorded before step 2. A Host outside any covered zone behaves as before. Zero records changed.
   Older checkout: `~/.local/bin/sb host plan` for an existing asb.bd target reports the conflict, and `host apply` refuses with no effect.
   Renewal: `--renew-certificate --confirm` works with no target change, and the probe still passes.
   Removal rehearsal, optional: `--remove-fallback --confirm`, then install again.

## Stage C: publish (needs the owner's explicit go)

5. Before: list the zone. `--enable --plan`, then `--enable --confirm --plan-digest D`. Expect state `active`, and exactly the planned record added.
6. From outside: a random label returns the fallback 404 through Cloudflare over HTTPS, with no TLS failure. Fixtures for `www.asb.bd`, `asb.bd` and the exact sites are unchanged.

## Stage D: a covered target

7. Apply a throwaway static target at an unused name, with no record. Expect: zone identical before and after; HTTPS works; evidence reports origin, public edge and resolver separately. For SC-002: query that name once from the controller's resolver immediately before step 5, and run this step within 1800 seconds, so the cached "does not exist" answer is still held. If the gap is longer, record SC-002 as not proven live.
8. Add a TXT record at another unused name: plan says ineligible. Add an A record at a third name to another address: it resolves there. Remove both.
9. Destroy the target: zero record changes, and the name returns the fallback 404.

## Stage E: drift and disable

10. Repoint a disposable managed exact record out of band: apply refuses, and destroy keeps it.
11. Delete and then repoint the wildcard by hand, in turn. Status shows drift and `safe`, apply refuses, and `--repair` restores it after its plan.
12. Disable with a dependent: refuses, then succeeds through the named plan. A cached client at Cloudflare cannot be simulated; a loopback request with that host still shows the site. Re-enable and confirm no target change.

## Not runnable live, tests only

A controller crash at each phase and lease takeover (forced in `tests/test_zone_coverage_service.py`), and a second controller's concurrent start. A mid-apply runtime upgrade does not apply, because no coverage code runs from the installed runtime (research R7).

## Cleanup

Remove throwaway targets and test records. Whether coverage stays enabled afterwards is the owner's decision.

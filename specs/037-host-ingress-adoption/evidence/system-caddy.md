# System Caddy route lifecycle (Ubuntu 24.04)

**Scope**: T044 — live add / request / update / remove through an incumbent system
Caddy, with the incumbent's pre-existing routes preserved throughout.

**Host**: Ubuntu 24.04.4 LTS, systemd 255. Caddy 2 owns `*:80` and `*:443` under systemd,
runs from `/etc/caddy/Caddyfile`, and imports `/etc/caddy/conf.d/*.caddy` with 16
pre-existing fragments serving live sites. Project `~/git/templately`, label `tmp-logo`,
backend `127.0.0.1:8188`. 2026-08-02.

**Harness**: `python3 tests/live_ingress_acceptance.py --project-dir ~/git/templately
--label tmp-logo --baseline-url http://localhost/ --consent --evidence-id
037-t044-ubuntu-2404`. At capture time, the harness constructed typed ingress and resolver
attestations for that single invocation. Production ingress qualification is now fixed in
source to this evidence ID; the harness argument remains only resolver proof input.
`--consent` records operator approval, given by the repository owner for this run.

## Historical harness promotion evidence

```text
advertised without attestation:  system-caddy adoptable = False
advertised with attestation:     system-caddy adoptable = True
```

The current registry no longer accepts an ingress attestation. It advertises only the
source-owned Linux exact-HTTP qualification and selection additionally requires live Caddy
process identity, proven socket ownership, and successful fixed-helper preflight. Normal
live CLI adoption using that production path is not yet captured; T078 remains open.

## Detection identifies the incumbent

```text
observed: system-caddy | Caddy | implemented_unproven
          endpoints: ('::', 80, 'caddy', 'proven'), ('::', 443, 'caddy', 'proven')
selection: system-caddy | selected | accepted addresses ['127.0.0.1', '127.0.0.77', '::1']
```

## Lifecycle

```text
apply_first            ok=True  ready            mutated=True
apply_second           ok=True  ready            mutated=True     (no duplicate fragment)
ingress_cleanup_first  ok=True  cleanup_complete mutated=True
ingress_cleanup_second ok=True  already_absent   mutated=False
domain_cleanup         ok=True  cleanup_complete mutated=True

fresh lookup:  127.0.0.1  templately-tmp-logo.test
request through the incumbent:  HTTP 200
```

The served page is the instance behind `127.0.0.1:8188`, reached through Caddy — Sandbox's
own proxy was never started for this hostname.

## The incumbent is preserved

```text
baseline route http://localhost/   200 before, 200 during, 200 after
caddy.service                      active before, during, after
caddy validate                     "Valid configuration" after
fragments in /etc/caddy/conf.d     16 before, 17 during, 16 after
```

The owned fragment is the only file added, it carries the
`# sandbox-ingress v1 route=<id>` marker, and it is removed by cleanup.

## Defects this run found and fixed

1. **Unprivileged attribution** — Linux listener attribution walks `/proc/<pid>/fd`, which
   cannot see a root-owned process, so system Caddy was permanently `unidentified` and
   unselectable. Added a read-only `listeners` verb to the ingress helper.
2. **Wildcard vs `/proc` address form** — `ss` prints a dual-stack wildcard as `*` while
   `/proc` reports `::`, so attribution never matched the observation.
3. **Process start time** — the helper omitted it, so the pinned identity was incomplete
   and route planning refused.
4. **Loopback-only assumptions** (three places: selection, the adapter plan, the helper's
   listen and socket validation) rejected a wildcard listener outright, which is exactly
   how the documented conformance host runs. A wildcard bind is now allowed, and the
   rendered site restricts itself to loopback clients with a `remote_ip` matcher so
   adoption never widens reachability.
5. **Caddyfile import policy** — the helper demanded a bare `conf.d/*` and rejected Caddy's
   own packaged `conf.d/*.caddy`, failing preflight on the target host.
6. **Renderer parity** — root re-renders the fragment and compares digests, so the
   loopback-client restriction had to be taught to both renderers.
7. **Wildcard health probe** — post-activation health connected to `::`, which is not a
   destination; it now probes the loopback address that socket serves.
8. **Naming answers** — a successful resolver apply reported the pre-apply observation, so
   spec A read a healthy adoption as `naming_address_mismatch` and rolled it back.
9. **Incumbent identity for cleanup** — comparing the full observation fingerprint made
   the incumbent look replaced after the reload that adoption itself performs, orphaning
   the route it had just created. Now compared on a product/endpoint ownership digest.
10. **Cleanup route id** — cleanup passed the repository record's identity digest where the
    helper keys receipts by the adapter's plan route id, so removal of a healthy owned
    route always failed as unauthorized.
11. **Absent artifacts** — a record whose fragment was already gone reported an eternal
    residual that no operation could clear.
12. **Diagnostics** — helper stderr was truncated to its first 1000 characters, which is
    all `caddy validate` warnings; the actual refusal was invisible.

Each has unit coverage; see the commits referencing this file.

## Not covered

- HTTPS and wildcard hostnames through the incumbent (the adapter advertises exact HTTP
  only, and refuses both).
- The transaction-failure matrix against a live incumbent: invalid current config, invalid
  candidate, reload failure, foreign hostname collision, incumbent disappearance. Unit
  coverage exists in `tests/test_ingress_transactions.py`.
- nginx, Apache, Traefik, Herd/Valet adapters — each needs its own run.

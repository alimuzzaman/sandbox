# Live listener detection (macOS)

**Scope**: T026 — live free / exact / wildcard / Sandbox-owned / foreign listener
classification with before-and-after proof that detection mutates nothing. Captured on
darwin; the Linux `/proc` + `ss` observer path is still unproven.

**Host**: macOS 15 (Darwin 25.6.0), OrbStack Docker, Sandbox proxy running. 2026-08-02.

## Fixtures

| Shape | How it was created | Endpoint |
|---|---|---|
| Sandbox-owned | the running `sandbox-proxy` container | `127.0.0.77:80`, `127.0.0.77:443` |
| Wildcard foreign | `docker run -p 8081:80 nginx:alpine` | `*.8081` |
| Exact loopback foreign | `docker run -p 127.0.0.1:8082:80 nginx:alpine` | `127.0.0.1:8082` |
| Free | every other port in the sampled set | — |

Host listeners while detection ran:

```text
  *.443
  *.80
  *.8081
  127.0.0.1.8082
```

## Observed

```text
requested: [('127.0.0.77', 80, 'sandbox_owned', 'unavailable'),
            ('127.0.0.77', 443, 'sandbox_owned', 'unavailable')]
mutated:   False
plan:      state=fallback ingress=None accepted_addresses=[] reason=no_live_proven_ingress
adoptable: none
```

- Sandbox's own published endpoints classify as `sandbox_owned`, not as a foreign
  conflict (FR-002, US1 scenario 3).
- The wildcard and exact-loopback foreign listeners on unrelated ports do not affect the
  requested-endpoint verdict: bind scope is evaluated per address and port, not per port
  number (FR-004).
- With zero adoptable adapters, planning returns `fallback` / `no_live_proven_ingress`
  rather than claiming an ingress (FR-011).
- `mutated: False`, and the plan/support calls likewise report no mutation (FR-003).

## Non-mutation proof

The listener set was captured immediately before and immediately after
`domains ingress detect|plan|support`:

```text
diff listeners-before listeners-after  ->  identical
```

The clean URL served by the default provider was unaffected: `https://templately-staging.tst`
still returned `200` after the sequence, and both fixture containers were removed.

## Defect found and fixed during capture

Before this run, `requested_endpoints` reported `conflict` for the proxy's own
`127.0.0.77:80/443`. Docker publishes those ports, so listener evidence attributes them to
the container runtime's helper process and the service read its own ingress as foreign.
Fixed by an injected ownership probe (`proxy_endpoint_owned`, wired in
`sandbox/application/context.py`); regression tests in
`tests/test_ingress_sandbox_ownership.py`.

## Not covered

- Linux listener observation (`/proc/net/tcp` + `ss` path).
- IPv6 dual-stack wildcard overlap on a host that reports effective dual-stack behavior.
- Split HTTP/HTTPS owners across two products (covered by unit fixtures only).

# Sandbox Caddy and per-port parity (macOS)

**Scope**: T069 — live parity for the existing Sandbox Caddy ingress and the per-port
fallback, plus the corrected conflict diagnosis. Captured on darwin.

**Host**: macOS 15 (Darwin 25.6.0), 8 registered instances. 2026-08-02.

## Both URLs serve while the default provider runs

```text
clean https  200      https://templately-staging.tst/
per-port     200      http://localhost:8188/
```

`./sb instances` shows every running instance on its clean URL
(`https://<name>.tst`) with no port, and each container still publishes its own
`localhost:<port>`.

## Stopping the provider degrades to the per-port URL, nothing else

```text
./sb domains down
  clean https  000  (connection refused — expected, the ingress is stopped)
  per-port     200
  site_url() while proxy down: http://localhost:8188
  persisted domain still:      templately-staging.tst

./sb domains up
  clean https  200
```

Two properties matter here and both hold:

- `site_url()` falls back to `http://localhost:<port>`, never to
  `http://<domain>:<port>` — the latter is not served by the proxy and would hang a
  browser on a clean machine.
- The persisted hostname is untouched by stopping and starting the provider, so the
  instance returns to its clean URL with no reassignment (FR-026, SC-010).

## Corrected conflict diagnosis (FR-029, FR-034)

A confirmed port conflict is reported as a listener conflict naming the owner, never as
Docker being unavailable. Live case captured during this feature: OrbStack with
`docker.expose_ports_to_lan: true` widened the proxy's published `127.0.0.77:80` to a
wildcard bind, which lost to Herd's nginx on `127.0.0.1:80`. `docker ps` showed the
mapping, `netstat` showed no listener, and requests were refused.

`./sb doctor` now reports that state directly:

```text
✗ proxy published on 127.0.0.77:80,443 but nothing accepts there
  (80 held by nginx (127.0.0.1:80))
  → free the port (stop the owning service), or select an adopted ingress with
    `./sb domains use <provider>`; per-port URLs keep working meanwhile
```

and reports the healthy state on the same host once the port is free:

```text
✓ proxy endpoints accepting on 127.0.0.77:80,443
```

## Cleanup is safe to repeat with no owned route

```text
domains ingress cleanup   -> ok state=ready reason=already_absent mutated=false (twice)
domains ingress reconcile -> ok state=ready reason=already_absent residual=[]
```

The clean URL still served `200` afterwards and DNS still answered `127.0.0.77`, so
cleanup of composed state left the default provider untouched.

## Not covered

- Linux parity for the same sequence.
- Cleanup of an actually-owned adopted route (needs an adoptable adapter).

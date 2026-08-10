# Default provider live proof (macOS)

**Scope**: live proof that the default Docker/Caddy ingress serves clean URLs with zero
adoptable incumbent adapters. Covers T075 on darwin only; Linux remains unproven.

**Host**: macOS 15 (Darwin 25.6.0), OrbStack Docker, Herd installed. Date 2026-08-02.

## Sequence

```text
sb domains setup tst
  -> one interactive sudo: installs /usr/local/libexec/sandbox-proxy-helper (root-owned)
     and /etc/sudoers.d/sandbox-proxy-<uid> naming only that path
  -> mkcert CA trusted (verified)
  -> assigned domains to 8 instances, minted per-instance certs
sb domains up            # recreate proxy so the host publish is re-established
```

## Observed

- Resolution: `dscacheutil -q host -a name templately-staging.tst` -> `127.0.0.77`.
- Route: `~/sandbox/runtime/proxy/Caddyfile` carries `http://templately-staging.tst` plus
  the TLS block; certs exist under `runtime/proxy/certs/`.
- Ingress: `docker ps` -> `127.0.0.77:80->80/tcp, 127.0.0.77:443->443/tcp`.
- Requests: `http://templately-staging.tst/` -> `308` to https; `https://templately-staging.tst/`
  -> `200` with the trusted local CA (no `-k`); autologin URL -> `200`.
- `sb ensure` (via the project's `scripts/sandbox-env.js start`) now reports
  `https://templately-staging.tst` and writes it to `.wp-env-port`.
- `./sb domains support --json` still reports every ingress adapter as
  `implemented_unproven` / `adoptable: false` — the default path is unaffected by that
  gate, which is the property under test (spec FR-007, FR-031).

## Host finding (recorded, not a Sandbox defect)

With `orb config` `docker.expose_ports_to_lan: true`, OrbStack widens a published
`127.0.0.77:80` to a wildcard `0.0.0.0:80` bind. While Herd's nginx held `127.0.0.1:80/443`
that bind failed silently: `docker ps` showed the mapping, `netstat` showed no listener,
and connections were refused. Non-privileged ports on the same alias (81, 8099) worked.
Stopping Herd cleared it. This is the FR-034 case — a foreign listener owning a required
endpoint. This capture predates the diagnostic fix: `sb doctor` now detects a
published-but-not-listening state and names the observed owner. That behavior has focused
regression coverage; it is not additional live proof from this historical capture.

## Linux (Ubuntu 24.04, 2026-08-02)

On the Ubuntu conformance host the default provider is genuinely unavailable, and Sandbox
says so rather than pretending:

```text
./sb domains ingress detect
  requested endpoints: [('127.0.0.77', 80, 'conflict'), ('127.0.0.77', 443, 'conflict')]
  owner:               system-caddy (Caddy)
```

System Caddy owns `*:80`/`*:443`, so the Sandbox proxy cannot take those endpoints. This is
the FR-034 path: report the owner, never steal, and offer adoption. Adoption through that
incumbent is proven in `system-caddy.md`, and the per-port URL kept working throughout.

## Provider round trip (Ubuntu 24.04)

```text
./sb domains use --project-dir ~/git/templately   -> sandbox-caddy   (default)
./sb domains use system-caddy                     -> system-caddy    provider_selected
./sb domains use sandbox-caddy                    -> sandbox-caddy   provider_selected
```

Switching in both directions is a machine-local selection change: no reprovisioning, no
hostname change, and the instance stayed reachable at its per-port URL throughout (FR-032).

## Not covered

- A Linux host where the required endpoints are FREE, which is where the default provider
  would actually serve; the conformance host deliberately runs an incumbent.

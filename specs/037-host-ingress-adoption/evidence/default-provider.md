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
endpoint — and `sb doctor` does not yet detect the published-but-not-listening state.

## Not covered

- Linux (systemd-resolved / NetworkManager hosts).
- Switching to an adopted incumbent and back (`./sb domains use <adapter>` round trip);
  no ingress adapter is adoptable yet, so the switch cannot be proven end to end.

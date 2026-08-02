# Linux adoption proof runbook

Every remaining task in specs 037, 038, and 039 is blocked on the same thing: no adapter
is `adoptable`, and the only adapters that can ever be promoted live on Linux —
`IngressProofAttestation` accepts `system-caddy` alone, `ResolverProofAttestation` accepts
`systemd-resolved` alone. Until one of those is proven on a real Linux host, adoption
cannot serve a single clean URL, and the dependent cleanup, drift, wildcard, and
round-trip tasks have nothing to operate on.

This runbook is the exact sequence to run when such a host is available. It does not
change what is advertised: promotion still requires the captured evidence plus the
invocation-scoped attestation.

## Host requirements

| Feature | Host | Preconditions |
|---|---|---|
| 038 T034 | Ubuntu 24.04 | systemd-resolved owns `/etc/resolv.conf` via its stub symlink; `dnsmasq` binary present and root-owned |
| 037 T044 | Ubuntu 24.04 | system Caddy owns `:80`/`:443`, runs from `/etc/caddy/Caddyfile`, already imports `/etc/caddy/conf.d/*.caddy` |
| 039 T047, T072, T077 | Ubuntu 24.04, normally booted | package manager available; unrelated nginx/Apache/MySQL services running, to prove coexistence |

A container is not a substitute for 038 or 039: nested systemd/DBus does not behave like a
booted host, which is precisely the case the adapters must be honest about.

## Order

038 T034 first. Ingress adoption (037 T044) needs a verified hostname from the resolver
side before it will activate a route at all, so proving the resolver unblocks the ingress
run, which in turn unblocks every cleanup and round-trip task.

## 038 T034 — systemd-resolved exact name

```bash
# baseline, all read-only
./sb domains support --json
./sb domains status --project-dir <project> --json
ls -l /etc/resolv.conf ; resolvectl status | head -40
dig +short example.com ; resolvectl query example.com

# adoption
./sb domains apply --project-dir <project> --json     # interactive on first use
./sb domains status --project-dir <project> --json
./sb visit http://<assigned-name>

# idempotence and cleanup
./sb domains apply --project-dir <project> --json     # no duplicate rule/authority/state
./sb domains cleanup --project-dir <project> --json
./sb domains cleanup --project-dir <project> --json
```

Capture into `specs/038-tld-dns-adoption/evidence/systemd-resolved.md`: resolver owner and
`/etc/resolv.conf` relationship before and after, unrelated sampled answers before and
after, the fresh lookup, the HTTP request through the selected ingress, both apply runs,
and both cleanup runs.

## 037 T044 — system Caddy lifecycle

```bash
./sb domains ingress detect --json
./sb domains ingress plan --project-dir <project> --json
./sb domains apply --project-dir <project> --json      # A→B→A, adds one owned fragment
curl -sS -o /dev/null -w '%{http_code}\n' http://<hostname>/
./sb domains apply --project-dir <project> --json      # update, still one fragment
./sb domains cleanup --project-dir <project> --json
./sb domains cleanup --project-dir <project> --json
```

Capture into `specs/037-host-ingress-adoption/evidence/system-caddy.md`: a bounded sample
of pre-existing Caddy routes healthy before and after, the owned fragment appearing and
disappearing, the foreign-collision refusal, and a rollback case (invalid candidate or
failed reload) restoring prior state byte-for-byte.

## Then, unblocked

- 037 T052 remainder — owned-route cleanup, external drift, incumbent unavailable.
- 037 T068 remainder — the quickstart's live incumbent lifecycle section.
- 037 T075 remainder — Linux default-provider run plus a `./sb domains use <adapter>`
  round trip in both directions.
- 038 T050 remainder — owner change, drift, unreachable resolver.
- 038 T055 — wildcard zone: unseen subdomain, shared owners, final-owner removal, public
  refusal.
- 038 T065 remainder — Linux run of the default strategy.

## 039 — managed native

Separate host, or the same one after the ingress work, because it installs packages:

```bash
./sb native install-plan --json        # preview: sources, versions, owned roots, effects
./sb native preflight --json
./sb ensure --project-dir <project> --runtime managed-native
# hostile probe suite, sibling exhaustion, then destroy and repeat destroy
```

Capture into `ubuntu-nginx.md`, `ubuntu-apache.md`, `ubuntu-package-coexistence.md`, and
`cleanup.md`. Unrelated system services must be healthy before and after, and destroy must
remove only Sandbox-owned state.

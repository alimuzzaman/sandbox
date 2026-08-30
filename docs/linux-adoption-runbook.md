# Linux adoption proof runbook

The remaining tasks in specs 037, 038, and 039 need live Linux host proof. Spec 037 now
contains a source-owned qualification for one incumbent shape: system Caddy, Linux, exact
HTTP. Selection succeeds only when the fixed helper proves that the observed listener's
PID, start time, socket inode, executable digest, and listen endpoint belong to the active
`caddy.service` MainPID before DNS mutation. HTTPS, wildcard naming, foreign or second
Caddy owners, and executables outside the supported system binary roots remain refused.

That source gate is local/static evidence, not the still-missing normal CLI proof. A real
Linux run must still show `./sb domains use system-caddy` and setup applying a working
route within those constraints. The resolver and managed-native work retain their own
live host gates.

This runbook is the exact sequence to run when such a host is available. It does not
broaden what is advertised: only the checked-in exact-HTTP system-Caddy qualification may
adopt, and its helper proof is invocation-scoped.

## Host requirements

| Feature | Host | Preconditions |
|---|---|---|
| 038 T034 | Ubuntu 24.04 | systemd-resolved owns `/etc/resolv.conf` via its stub symlink; `dnsmasq` binary present and root-owned |
| 037 T044 | Ubuntu 24.04 | system Caddy owns `:80`/`:443`, runs from `/etc/caddy/Caddyfile`, already imports `/etc/caddy/conf.d/*.caddy` |
| 039 T047, T072, T077 | Ubuntu 24.04, normally booted | package manager available; unrelated nginx/Apache/MySQL services running, to prove coexistence |

A container is not a substitute for 038 or 039: nested systemd/DBus does not behave like a
booted host, which is precisely the case the adapters must be honest about.

## Order

Use a host that satisfies both naming and ingress prerequisites. Establish the verified
hostname first, then run the Spec 037 exact-HTTP lifecycle. This preserves the A→B→A
sequence: ingress qualification is read-only, B proves naming, and only then may A add a
route.

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

## 037 T078 — source-qualified system Caddy lifecycle

```bash
./sb domains ingress detect --json
./sb domains ingress plan --project-dir <project> --json
./sb domains use system-caddy
./sb domains apply --project-dir <project> --json      # A→B→A, adds one owned fragment
curl -sS -o /dev/null -w '%{http_code}\n' http://<hostname>/
./sb domains apply --project-dir <project> --json      # update, still one fragment
./sb domains cleanup --project-dir <project> --json
./sb domains cleanup --project-dir <project> --json
```

Capture into `specs/037-host-ingress-adoption/evidence/system-caddy.md`: the selected
listener and active `caddy.service` identity match before naming changes; a bounded sample
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

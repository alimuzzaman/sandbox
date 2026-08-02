# Persisted `.tst` identity and per-port compatibility (macOS)

**Scope**: T061 — live verification that existing persisted `.tst` identities and the
Compose per-port URL keep working under the composed resolver. Captured on darwin.

**Host**: macOS 15 (Darwin 25.6.0), Herd installed (it owns `.test`). 2026-08-02.

## Suffix ownership is shared, not taken

```text
/etc/resolver/
  test      <- Herd's, untouched
  tst       <- Sandbox's, added by `./sb domains setup tst`

/etc/resolv.conf -> ../var/run/resolv.conf   (unchanged symlink, still macOS-managed)
```

Sandbox added a scoped resolver entry for its own suffix only. It did not replace, rewrite,
or take ownership of `/etc/resolv.conf`, and it left the incumbent's `.test` entry alone
(FR-008, FR-009).

## Unrelated resolution is unaffected

```text
dscacheutil -q host -a name example.com
  ipv6_address: 2606:4700:10::ac42:93f3
  ipv6_address: 2606:4700:10::6814:179a

dscacheutil -q host -a name templately-staging.tst
  ip_address: 127.0.0.77
```

Internet names still resolve through the machine's own upstreams; only the Sandbox suffix
answers locally (FR-028, SC-002).

## Persisted identity survives and is reported

```text
./sb domains status --json
  hostname: templately-staging.tst
  fallback: https://templately-staging.tst
```

The eight registered instances all kept their `.tst` identities across setup, provider
restart, and cleanup runs (FR-011).

## Per-port fallback parity

```text
proxy up    clean https 200   per-port 200
proxy down  clean https  -    per-port 200   site_url() -> http://localhost:8188
proxy up    clean https 200
```

Instance provisioning and the per-port URL never depended on resolution succeeding
(FR-025), and the fallback is the per-port URL rather than an unserved `http://<domain>:<port>`.

## Defect found and fixed during capture

`domains status` reported a synthesized `templately-staging.test` while the instance served
`templately-staging.tst`: the persisted identity was not fed into the policy, so the
composed service invented a `.test` twin nobody serves. Fixed in
`sandbox/application/domain_service.py` (registry `domain`, then the recorded URL host,
outrank the synthesized default) and in `sandbox/core/_instances.py` (ensure now records
`domain`/`tld` alongside the URL). Regression tests:
`tests/test_domain_identity_lifecycle.py::TestPersistedIdentityWins`.

## Not covered

- Linux (`/etc/resolv.conf` plain-file path, and the systemd-resolved decline).
- A confirmed `.tst` → `.test` migration (deliberately never automatic).

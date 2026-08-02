# Default resolution strategy live proof (macOS)

**Scope**: live proof that Sandbox-owned scoped resolution is the default strategy and works
with zero adoptable resolver adapters. Covers T065 on darwin only; Linux remains unproven.

**Host**: macOS 15 (Darwin 25.6.0). Date 2026-08-02.

## Sequence

```text
sb domains setup tst   # installs the root-owned helper, then dns-up per TLD
```

The helper writes `/etc/resolver/tst` plus a Homebrew dnsmasq entry mapping `*.tst` to
`127.0.0.77`, and flushes the macOS resolver cache. No global resolver mode is changed.

## Observed

- Fresh lookup: `dscacheutil -q host -a name templately-staging.tst` -> `127.0.0.77`.
- Request through the selected ingress: `https://templately-staging.tst/` -> `200`.
- `./sb domains support --json` reports every resolver adapter as `implemented_unproven`
  with `adoptable: false`, and `./sb domains status --json` still reports the composed
  service as `resolver_not_selected`. The default strategy resolves regardless, which is
  the property under test (spec FR-029, FR-030, FR-033).
- Unrelated resolution unaffected: internet names continue to resolve through the machine's
  own upstreams; only the `tst` suffix is routed.

## Not covered

- Linux (own dnsmasq + plain `/etc/resolv.conf`, and the systemd-resolved decline path).
- Switching to an adopted resolver and back; no resolver adapter is adoptable yet.
- Wildcard subdomain multisite under the default strategy.

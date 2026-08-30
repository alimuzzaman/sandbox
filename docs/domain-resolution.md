# Scoped domain resolution

The DEFAULT resolution provider is Sandbox's own scoped bootstrap that serves the
Docker/Caddy clean URLs (`./sb domains setup`); everything on this page describes the
OPT-IN adoption of a host-owned resolver, selected with `./sb domains use <provider>`.
Adapter proof tiers gate that adoption only — never the default path. See
[the clean-URL default](clean-url-default.md).

Sandbox treats a clean hostname as optional project identity, not as permission to
replace machine DNS. New omitted identities use `.test`; persisted identities such as
`.tst` remain byte-for-byte stable. New `.local` names are rejected because that suffix
belongs to mDNS. Public FQDNs are verify-only and are never shadowed locally.

## Inspect and adopt

```bash
./sb domains support --json
./sb domains status --project-dir . --json
./sb domains plan --project-dir . --json
./sb domains apply --project-dir . --json
./sb domains cleanup --project-dir . --json
```

Read-only operations finish within two seconds per external probe. Mutations use a
30-second bound. First mutation of a user-owned resolver requires recorded consent and
may prompt only on an interactive terminal. That interaction performs the one-time
installation of `/usr/local/libexec/sandbox-resolver-helper` plus a caller-scoped sudoers
rule. Subsequent mutations use only its fixed, schema-validating verbs; project files are
never privileged mutation candidates. A separate passworded `authorize` call creates a
root-owned receipt binding the caller UID, project-owner digest, local suffix, loopback
endpoint, and rendered-fragment digest. `authorize` is excluded from NOPASSWD; apply,
status, and cleanup fail without the exact receipt. Authorization is not ownership: the
helper creates a separate applied-state receipt only after a successful install/reload,
and an identical fragment without a valid applied receipt remains foreign. Shared-owner
applied receipts keep a suffix route alive until the final successful CAS cleanup. MCP and
CI return `pending_consent` or
`pending_privilege`; they never hang for input. A resolver pin in the machine-local
override beats project configuration, which beats detection, and status reports that
source.

Source-owned qualification implements one candidate host-owned path: Linux
`systemd-resolved`, exact names only, constrained by historical checked-in evidence
`038-t034-ubuntu-2404`. It remains `implemented_unproven` and non-adoptable in ordinary
support until the normal live CLI gate is captured. No string, mapping, typed object, CLI option, config, environment,
MCP input, or harness input can add or widen that qualification. Before endpoint or DNS
mutation, Sandbox installs or upgrades the fixed versioned helper after consent. The
helper then performs a read-only preflight and binds the observed
`systemd-resolved` owner to the active unit's PID, process start identity, owner UID, and
control group. That identity is included in the root authorization receipt and rechecked
inside the helper immediately before its write. A missing helper, inactive or replaced service, second resolver owner,
NetworkManager owner, wildcard request, non-Linux platform, or foreign authority state
fails closed. The executable name and a boolean readiness result are not qualification.
An unselected host resolver is never auto-adopted; Sandbox-owned Docker/Caddy resolution
remains the default. Explicit `./sb domains use systemd-resolved` changes only the
resolver selection, preserving the persisted hostname and existing instance.

The fixed helper reports `sandbox-resolver-helper-v2`. An older installed copy is not
treated as ready: after consent it is upgraded from the checked-in helper, and an exact
legacy resolved authorization receipt may be replaced only during the interactive
authorization step with the new identity-bound receipt.

Failure never blocks the per-port URL. Status distinguishes owner changes, binding drift,
authority failure, answer mismatch/stale cache, and selected-ingress diagnostic failures.
Use `domains reconsider --resolver ID` to clear remembered consent after reviewing a
resolver change.

`domains status` is read-only. For an owned binding, it first performs a bounded fresh DNS
validation and checks that the hostname has exactly one answer accepted by the selected
ingress. Only after that check succeeds does it make the selected-ingress HTTP health
probe. Status does not install, update, flush, or remove DNS, ACME, resolver, ingress, or
application state.

## Ownership and cleanup

Sandbox writes only marked fragments through fixed, schema-validated helper verbs. It
does not replace resolver-managed `resolv.conf`, foreign dnsmasq fragments, hosts entries,
or public DNS. Apply validates complete configuration and rolls back on reload or fresh
DNS/HTTP verification failure. Cleanup compares the observed state with the stored
receipt; drift and unavailable managers produce durable `cleanup_incomplete` recovery
instead of deleting ambiguous state. Recovery remains retryable after instance deletion.

Exact records are preferred. A wildcard is created only for a declared feature and only
below its declared local name. Shared zones are reference-counted; the resolver rule and
non-forwarding authority survive until the final owner leaves.

All suffix routes share one locked authority endpoint. Adding a project regenerates the
authority from the complete owned binding set, while cleanup removes a suffix route only
after its final binding is gone. A racing plan cannot move an active endpoint or replace
another project's fragment.

The status health target is the attributable selected-ingress probe: a concrete loopback
address, port, and protocol supplied by the selected adapter, with the address belonging
to its accepted listener set. `fallback_url` is recovery/display information only; it is
never used as the status health target. The probe sends the requested hostname explicitly
as the HTTP `Host` value and uses it as TLS SNI when an adapter supplies an HTTPS probe;
without an adapter-owned SNI policy, HTTPS is reported unavailable rather than downgraded.
It connects directly to the selected endpoint, with no DNS lookup, proxy discovery, or
redirect following, and does not disclose response bodies or headers.

The public selected-ingress diagnostic is a closed envelope containing only component
states and one stable reason code:

```json
{
  "ingress": {"state": "reachable"},
  "application": {"state": "ready"},
  "reason": {"code": "ready"}
}
```

Its stable reason codes are exactly `fresh_dns_unavailable`, `answer_mismatch`,
`ingress_listener_unreachable`, `ingress_connect_timeout`,
`application_response_timeout`, `application_http_unhealthy`,
`ingress_probe_unavailable`, and `ready`. Endpoint details, exceptions, response bodies,
and response headers never cross this public boundary.

## Support and threat boundary

`adoptable` means the exact adapter/platform combination has live fresh-lookup, unrelated
answer, HTTP, rollback, and cleanup evidence. `implemented_unproven` is deliberately
non-mutating. `detect_only`, `external`, and `outside_platform` never mutate.

The scoped dnsmasq authority listens on a collision-checked unprivileged loopback UDP/TCP
pair, has no upstream resolver, ignores host resolver files, and answers only owned names.
Resolver adoption does not provide workload isolation; managed native runtimes enforce
their own network namespace and default-deny egress separately.

The historical disposable conformance harness used an invocation-scoped attestation to
capture evidence. Production qualification no longer accepts any proof input. Evidence
review and a checked-in manifest/qualification change are required before another
adapter, platform, or capability can be advertised.

Legacy `domains setup|up|down|teardown` remains a rollback control while adoption is
unadvertised. Instance lifecycle entry points first offer the composed ingress→DNS→ingress
handoff; fallback retains the existing per-port/proxy path. Removing that compatibility
path requires separate live parity approval.

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

Failure never blocks the per-port URL. Status distinguishes owner changes, binding drift,
authority failure, answer mismatch/stale cache, and an ingress verification failure.
Use `domains reconsider --resolver ID` to clear remembered consent after reviewing a
resolver change.

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

Fresh HTTP verification parses the stored fallback only to recover an explicit loopback
address and port, then uses a no-DNS, no-proxy, no-redirect route probe with the intended
Host header. Public, link-local metadata, credential-bearing, and HTTPS fallback URLs are
never ambient-probed.

## Support and threat boundary

`adoptable` means the exact adapter/platform combination has live fresh-lookup, unrelated
answer, HTTP, rollback, and cleanup evidence. `implemented_unproven` is deliberately
non-mutating. `detect_only`, `external`, and `outside_platform` never mutate.

The scoped dnsmasq authority listens on a collision-checked unprivileged loopback UDP/TCP
pair, has no upstream resolver, ignores host resolver files, and answers only owned names.
Resolver adoption does not provide workload isolation; managed native runtimes enforce
their own network namespace and default-deny egress separately.

The disposable live conformance harness may inject an in-memory typed proof attestation.
No CLI flag, project setting, machine override, string, or mapping can promote an adapter.
The attestation affects only that composed service object and never changes the built-in
manifest, MCP behavior, or later commands. Evidence review and a separate manifest change
are required before support is advertised.

Legacy `domains setup|up|down|teardown` remains a rollback control while adoption is
unadvertised. Instance lifecycle entry points first offer the composed ingress→DNS→ingress
handoff; fallback retains the existing per-port/proxy path. Removing that compatibility
path requires separate live parity approval.

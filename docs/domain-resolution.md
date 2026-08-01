# Scoped domain resolution

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
may prompt only on an interactive terminal. MCP and CI return `pending_consent` or
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

## Support and threat boundary

`adoptable` means the exact adapter/platform combination has live fresh-lookup, unrelated
answer, HTTP, rollback, and cleanup evidence. `implemented_unproven` is deliberately
non-mutating. `detect_only`, `external`, and `outside_platform` never mutate.

The scoped dnsmasq authority listens on a collision-checked unprivileged loopback UDP/TCP
pair, has no upstream resolver, ignores host resolver files, and answers only owned names.
Resolver adoption does not provide workload isolation; managed native runtimes enforce
their own network namespace and default-deny egress separately.

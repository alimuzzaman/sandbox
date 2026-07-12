# CLI Contract: Hermes Public Dashboard Access

```text
./sb hermes dashboard exposure-status --remote NAME [--json]
./sb hermes dashboard expose --remote NAME --fqdn hermes.asb.bd --plan [--json]
./sb hermes dashboard expose --remote NAME --fqdn hermes.asb.bd --confirm [--json]
./sb hermes dashboard unexpose --remote NAME --plan [--json]
./sb hermes dashboard unexpose --remote NAME --confirm [--json]
./sb hermes dashboard basic-auth set --remote NAME --user USER --secret NAME --confirm [--json]
./sb hermes dashboard basic-auth remove --remote NAME --confirm [--json]
```

`exposure-status` is read-only. `expose --plan` is read-only even if all credentials
are configured. Every mutation requires `--confirm` and a current V2 gate before any
credential lookup, Cloudflare call, or remote command.

`--fqdn` is mandatory for expose and must equal the supported initial hostname.
`--secret` names an approved local secret reference; the CLI never accepts a password
value or token value argument. The secret name, not its value, may be shown in a local
plan only when it is safe to do so.

Pre-created Access application, Access policy, tunnel, and exact proxied DNS record references are read from
the operator's non-secret local configuration. The first release validates and attaches
to those references; it does not create, edit, or delete Cloudflare resources.

All JSON responses use the existing Hermes envelope and include action, remote, status,
commit, structured public-layer data, and sanitized structured errors. They exclude
tokens, password verifiers, cookies, raw Access claims, private keys, connector command
arguments, and SSH targets.

Protected operations fail with stable codes before mutation:

| Condition | Code |
|---|---|
| V2 gate stale/missing | `v2_gate_required` |
| Invalid/non-supported hostname | `invalid_dashboard_fqdn` |
| Missing confirmation | `confirmation_required` |
| Missing secret reference | `public_exposure_secret_missing` |
| Broad/invalid policy | `unsafe_access_policy` |
| Unmanaged conflict | `public_exposure_conflict` |
| No owned public route | `dashboard_not_exposed` |
| Failed health/rollback | `public_exposure_failed` |

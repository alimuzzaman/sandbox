# Data Model: Hermes Public Dashboard Access

## PublicExposure

| Field | Type | Validation |
|---|---|---|
| `schema_version` | integer | Migrated atomically |
| `fqdn` | hostname | Exact initial value `hermes.asb.bd` |
| `mode` | enum | `ssh-only`, `planned`, `public`, `degraded` |
| `hermes_commit` | 40-hex | Must match the current V2 evidence |
| `sandbox_commit` | 40-hex | Captured for diagnosis |
| `proxy_port` | integer | Loopback-only, default 9120 |
| `dashboard_port` | integer | Existing loopback port, default 9119 |
| `basic_auth` | object | Enabled flag, username, verifier reference; never plaintext |
| `access` | object | Account/app/policy references and observed policy shape; never tokens |
| `tunnel` | object | Tunnel/route references and service name; never connector token |
| `dns` | object | Pre-created zone/record reference and observed proxied status |
| `caddy` | object | Fragment path/content hash and prior fragment snapshot |
| `last_health` | object | Layered non-secret status and timestamp |
| `rollback` | object/null | Current immutable rollback reference |

State transitions:

```text
ssh-only -> planned -> public
                 |         |
                 v         v
              degraded <- rollback
```

`public` is set only after all public health checks pass. Any failed mutation becomes
`degraded` until rollback proves the route is removed or restored.

## AccessReference

| Field | Description |
|---|---|
| `account_id` | Cloudflare account identifier |
| `application_id` | Exact self-hosted application |
| `policy_id` | Exact reusable/attached policy |
| `hostname` | Exact protected hostname |
| `mfa_required` | Observed policy/application requirement |
| `session_duration` | Observed configured duration |
| `owned` | Always false in the first attach-only release |

## TunnelReference

| Field | Description |
|---|---|
| `tunnel_id` | Named tunnel identifier |
| `tunnel_name` | Deterministic integration name |
| `ingress_hostname` | Exact public hostname |
| `service_target` | Exact loopback Caddy URL |
| `service_unit` | Connector systemd user unit |
| `owned` | Always false in the first attach-only release |

## RollbackRecord

One immutable record is created before a confirmed apply or unexpose. It contains
previous integration-owned Caddy and connector service values plus observed external
references and active/enabled states, never credential values. Successful rollback
retains the record as a sanitized audit reference; unsuccessful rollback reports the
remaining layer and containment.

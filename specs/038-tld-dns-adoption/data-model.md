# Data Model: TLD and DNS Adoption

## HostnameIntent

| Field | Type | Rules |
|---|---|---|
| `project_root` | canonical path | Must resolve through the project registry |
| `label` | string | Existing validated instance label |
| `hostname` | FQDN | Lowercase ASCII/IDNA-normalized, no trailing dot in stored form |
| `source` | enum | `persisted`, `machine_override`, `project`, `default` |
| `suffix_class` | enum | `test`, `legacy_private`, `mdns_reserved`, `public` |
| `wildcard_required` | bool | True only for a declared capability such as subdomain multisite |
| `migration_state` | enum | `none`, `required`, `confirmed`, `failed` |

Identity selection order is persisted hostname, machine-local override, committed project
pin, then new `.test` default. A new `mdns_reserved` intent is invalid. A public intent is
verify-only. Omission is retained as provenance through normalization; it must not be
materialized as legacy `tst` before this selection occurs.

## ResolverObservation

| Field | Type | Rules |
|---|---|---|
| `owner_id` | string | Stable adapter-specific identity, not process name alone |
| `manager` | enum | `resolved`, `networkmanager`, `macos`, `dnsmasq`, `herd`, `valet`, `hosts`, `external`, `unknown` |
| `mode` | string | Effective resolver mode and delegation path |
| `support_tier` | enum | `adoptable`, `conditional`, `implemented_unproven`, `detect_only`, `external`, `outside_platform`, `unavailable` |
| `extension` | object/null | Documented scoped control surface and prerequisites |
| `current_answers` | address list | Freshly observed answers for requested name |
| `fingerprint` | digest | Canonical non-secret observation used as plan precondition |
| `evidence` | list | Bounded commands/files/signals supporting the classification |

Observation is immutable and read-only. A changed fingerprint invalidates an unapplied
plan.

## ResolutionBinding

| Field | Type | Rules |
|---|---|---|
| `binding_id` | digest | Stable digest of owner, project/label, name/zone, and strategy |
| `kind` | enum | `exact`, `zone`, `incumbent`, `external` |
| `name` | FQDN/suffix | Least-wide declared namespace |
| `target` | address | Must be in A's accepted listener-address set |
| `adapter_id` | string | Manifest-registered adapter |
| `owners` | set | One owner for exact records; one or more for shared zones |
| `desired` | object | Canonical adapter-neutral desired state |
| `last_applied` | object/digest | Exact successful mutation result |
| `observed` | object/digest | Current adapter state |
| `lifecycle` | enum | `planned`, `applied`, `healthy`, `drifted`, `pending_cleanup`, `removed` |

Transitions: `planned → applied → healthy`; any mismatch yields `drifted`; unchanged state
may enter `pending_cleanup → removed`. Drifted state is never overwritten or removed by
ordinary ensure/destroy.

## AnsweringAuthority

| Field | Type | Rules |
|---|---|---|
| `authority_id` | string | Machine-scoped Sandbox authority identity |
| `endpoint` | address + port | Loopback only, UDP and TCP, collision-checked |
| `binary` | path/version | Positively identified dnsmasq executable |
| `config_path` | path | Below `$SANDBOX_HOME/runtime/network/authority/` |
| `bindings` | binding IDs | Only active exact/zone records |
| `pid_identity` | PID + start evidence | Must match before signal/stop |
| `health` | enum | `stopped`, `starting`, `healthy`, `unhealthy`, `foreign_collision` |
| `config_digest` | digest | Last successfully activated generated configuration |

The authority never has an upstream server and does not load host resolver files. It stops
when `bindings` becomes empty.

## ConsentRecord

| Field | Type | Rules |
|---|---|---|
| `owner_id` | string | Current resolver/incumbent identity |
| `decision` | enum | `accepted`, `declined` |
| `decided_at` | timestamp | Machine-local audit time |
| `policy_version` | integer | Re-consent required after material privilege expansion |
| `reconsidered_at` | timestamp/null | Set only by explicit user action |

No credential material is stored.

## CleanupRecovery

| Field | Type | Rules |
|---|---|---|
| `binding_id` | string | Residual owned binding |
| `adapter_id` | string | Adapter needed for retry |
| `expected_digest` | digest | Last applied state, no secrets |
| `observed_digest` | digest/null | Present for drift |
| `reason_code` | string | Stable actionable failure code |
| `retry_after` | timestamp/null | Optional bounded retry guidance |
| `status` | enum | `pending`, `drifted`, `unavailable`, `resolved` |

## ResolverPolicy

Normalized project configuration contains `enabled`, optional `hostname`, optional
`strategy`, and `wildcard`. Source metadata is retained so status can report machine
override versus project pin. Unknown keys and unsafe suffixes fail validation before any
host observation or mutation.

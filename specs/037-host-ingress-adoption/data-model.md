# Data Model: Host Ingress Adoption

## ListenerEndpoint

| Field | Type | Rules |
|---|---|---|
| `family` | enum | `ipv4` or `ipv6` |
| `address` | IP | Normalized exact or wildcard address |
| `port` | integer | 1–65535 |
| `wildcard` | bool | True for `0.0.0.0` or `::` |
| `dual_stack` | bool | Effective only for IPv6 wildcard listeners |
| `socket_id` | string/null | Kernel inode/identifier when observable |
| `process` | object/null | PID/start identity/executable evidence, never required for conflict truth |
| `service` | object/null | Manager/unit/container evidence |
| `owner_confidence` | enum | `proven`, `probable`, `unknown` |

Overlap is a pure relation over family/address/wildcard/dual-stack/port. An exact address
does not overlap another exact address in the same family; a wildcard overlaps all
addresses in its effective families.

## IngressObservation

| Field | Type | Rules |
|---|---|---|
| `adapter_id` | string | Manifest identity or `unidentified` |
| `product` | string | User-facing product/family |
| `product_identity` | object | Version, binary, service/config identity evidence |
| `endpoints` | list | ListenerEndpoint records attributable to this candidate |
| `support_tier` | enum | `sandbox_owned`, `adoptable`, `conditional`, `credential_pending`, `implemented_unproven`, `detect_only`, `outside_platform`, `unidentified` |
| `capabilities` | object | HTTP, HTTPS, wildcard, backend types, validation/reload |
| `control` | object/null | Documented effective control prerequisite |
| `fingerprint` | digest | Canonical observation used as plan precondition |

## IngressSelection

| Field | Type | Rules |
|---|---|---|
| `required_protocols` | set | Protocols the product will advertise for this hostname |
| `required_capabilities` | set | TLS/wildcard/backend requirements |
| `pin` | string/null | Explicit adapter or `disabled` |
| `pin_source` | enum/null | `machine_override`, `project`, `none` |
| `adapter_id` | string/null | One adapter only |
| `accepted_addresses` | address set | Addresses B may resolve to |
| `reason_code` | string | Stable selection/fallback reason |
| `observation_fingerprint` | digest | Invalidates stale activation |

## RouteRecord

| Field | Type | Rules |
|---|---|---|
| `route_id` | digest | Stable adapter + project root + label + hostname digest |
| `owner` | object | Canonical project root, label, instance identity |
| `hostname` | FQDN | Must equal B's verified hostname |
| `backend` | object | Loopback endpoint or adapter-specific document root from C |
| `protocols` | set | Served by the same adapter |
| `capabilities` | object | Effective TLS/wildcard behavior |
| `owned_fragment` | path/object/null | Adapter-specific attributable state |
| `last_applied` | object/digest | Exact successful route state |
| `observed` | object/digest | Current incumbent route state |
| `lifecycle` | enum | `planned`, `applied`, `healthy`, `drifted`, `pending_cleanup`, `removed` |

Transitions: `planned → applied → healthy`; health or state mismatch yields `drifted` or a
rolled-back plan. Only unchanged `healthy/applied` state may update or remove.

## RouteTransaction

| Field | Type | Rules |
|---|---|---|
| `transaction_id` | string | Unique bounded operation ID |
| `precondition` | digest | Selection plus incumbent observation |
| `current_validation` | result | Complete config valid before candidate mutation |
| `prior_owned_state` | bytes/digest/null | Exact rollback input, secret-free |
| `candidate` | bytes/digest | Validated owned route representation |
| `candidate_validation` | result | Complete config valid with candidate |
| `activation` | result | Atomic fragment/API action plus graceful reload |
| `baseline_health` | samples | Bounded previously healthy incumbent routes |
| `post_health` | samples | Baseline plus new route through verified hostname |
| `rollback` | result/null | Mandatory on any activation/health failure |

## IncumbentConsent and CredentialReference

Consent is keyed by machine plus stable product identity and policy version, with accepted
or declined state. Credential references name an existing machine-local secret key; route
state and output never contain the credential value.

## CleanupRecovery

Stores route ID, adapter/product identity, last-applied digest, current digest when known,
reason code, and retry status. It contains enough evidence to retry but no credentials or
foreign config bodies.

## SupportDeclaration

Manifest entry with platform, detection signature, documented control surface,
capabilities, consent/credential prerequisites, initial tier, evidence profile, and live
evidence identifier. Only a declaration with complete evidence may return `adoptable`.


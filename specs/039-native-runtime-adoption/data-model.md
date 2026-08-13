# Data Model: Native Runtime Adoption

## RuntimeSelection

| Field | Type | Rules |
|---|---|---|
| `project_root` | canonical path | Registry-owned project only |
| `label` | string | Valid existing/new explicit label |
| `mode` | enum | `compose`, `incumbent_native`, `managed_native` |
| `adapter_id` | string | Manifest-registered adapter |
| `source` | enum | `default`, `project_requirement`, `machine_override`, `persisted` |
| `versions` | object | Exact requested PHP/database/web/WordPress versions |
| `isolation_level` | enum | `compose`, `managed_container`, `trusted_shared_host` |
| `capabilities` | set | Effective required/optional operations |

Compose is the only default. Native activation requires machine-override provenance. A
persisted populated mode cannot transition through ordinary ensure.

## RuntimeCapabilityDeclaration

Manifest entry with adapter/platform/version matrix, isolation level, required operations,
optional operations and limitation codes, prerequisites, package/control authority, and
live evidence identifier. Detection state and adoption state are distinct.

## ManagedIsolationPolicy

| Field | Type | Rules |
|---|---|---|
| `policy_version` | integer | Invalidates stale applied policy |
| `machine_id` | string | Deterministic non-user-controlled nspawn identity |
| `uid_map` | range | Unique private user namespace mapping |
| `root_image` | path/digest | Fixed-size owned image; no foreign path |
| `read_only_mounts` | list | Resolved sources and container targets |
| `writable_mounts` | list | Explicit subpaths only, no escaping symlink/parent |
| `network` | object | Unique veth/subnet, allowed inbound backend, egress grants |
| `syscalls` | profile | Effective seccomp/no-new-privileges/capability policy |
| `devices` | set | Minimal virtual devices only |
| `resources` | object | CPU/memory/PIDs/time/disk/inodes/FDs/connections/I/O |
| `credentials` | refs | Instance-only injected credential identities |
| `digest` | digest | Canonical desired policy |
| `proof` | object | Effective namespace/cgroup/mount/firewall probe results |

Policy transitions: `planned → applied → verified → running`; any preflight/policy drift
yields `blocked` before payload execution. There is no downgrade transition.

## PackageTransactionPlan

| Field | Type | Rules |
|---|---|---|
| `matrix_id` | string | Exact advertised host/runtime matrix |
| `host_packages` | list | Installed/candidate exact versions and actions |
| `image_packages` | list | Exact Noble package closure and actions |
| `sources` | list | Existing configured signed APT source identities |
| `service_effects` | list | Maintainer units/scripts and suppression behavior |
| `owned_roots` | list | Host image/config/policy paths to create |
| `privilege_actions` | list | Fixed helper verbs only |
| `simulation_digest` | digest | Binds confirmation to current package plan |
| `confirmation` | object/null | Current TTY decision, time, policy version |

Any source/version/closure drift after confirmation invalidates the plan and requires a
new preview.

## NativeBackendRecord

| Field | Type | Rules |
|---|---|---|
| `owner` | object | Project root, label, instance ID |
| `mode/adapter` | strings | Must match persisted RuntimeSelection |
| `backend` | object | Managed veth address/port or incumbent document-root requirement |
| `machine/processes` | object | nspawn/cgroup identity or incumbent observations |
| `php` | object | Requested and observed web/CLI/exec/test versions |
| `database` | object | Owned production/test identities, socket/ref, data digest |
| `files` | object | Root image/mount/config/log ownership and last applied digests |
| `health` | enum | `pending`, `ready`, `unhealthy`, `blocked`, `drifted` |
| `last_applied` | digest | Exact state allowed for reconcile/cleanup |

## DatabaseBoundary

Managed mode uses one MariaDB service/data root and production/test databases inside its
container; credentials never leave its policy. Incumbent mode uses user-supplied connection
authority and uniquely attributed databases. Names alone never establish ownership.

## EgressGrant

Grant ID, instance owner, kind (`public_cidr_tcp` or `hostname_https`), destinations/ports,
expiry, broker/firewall rule identity, counters, last applied digest, and revoked state.
Private/loopback/link-local/metadata/control/sibling ranges are invalid in every grant.

## NativeCleanupRecovery

Records owned object type/identity, expected last-applied digest, observed digest when
available, drift/unavailability reason, and retry state without database passwords,
credential bytes, or foreign file content.

## PhpExtensionRequirement

| Field | Type | Rules |
|---|---|---|
| `profile` | string/null | `null` or immutable `wordpress@1`; unknown profiles fail before mutation |
| `extensions` | map | Canonical extension name → `{state, version?}` |
| `state` | enum | `enabled` or `disabled`; input `true`/string/`false` normalizes to this form |
| `version` | string/null | Exact version, `X.Y.*`, or `php`; only enabled requirements may carry one |
| `catalog_revision` | string | Checked-in profile/catalog revision selected by the resolver |
| `normalized_digest` | digest | Requirement/profile/image/PHP/server/platform/architecture fingerprint |

The immutable `wordpress@1` expansion requires `curl`, `dom`, `exif`, `fileinfo`,
`hash`, `json`, `mbstring`, `mysqli`, `openssl`, `pcre`, `xml`, and at least one of
`gd`/`imagick`; `intl`, `sodium`, `zip`, and `opcache` are recommended warnings.

## PhpExtensionResolution

| Field | Type | Rules |
|---|---|---|
| `requested` | object | Normalized requirements and profile revision |
| `parent_image` | digest/object | Validated official image identity or validate-only observation |
| `artifact_provenance` | list | Exact package/artifact/image identities; no secrets or arbitrary inputs |
| `cache_path` | absolute path | `$SANDBOX_HOME/runtime/build/php-extensions/<digest>/` only |
| `planes` | map | Fresh web, WP-CLI, bounded exec, PHPUnit observations |
| `state` | enum | `ready`, `blocked`, `unsupported`, `version_mismatch`, `plane_drift`, `unavailable` |
| `mutated` | boolean | False for validation-only/refusal/failing preflight results |

The resolution digest is content-addressed and changes whenever any input that can
alter an extension artifact or observation changes. Runtime-reported module versions
and package/artifact versions are retained as distinct fields.

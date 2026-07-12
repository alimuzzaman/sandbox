# Data Model: Generic Project Instances

## Project Descriptor

Represents the effective, validated configuration for one canonical project root.

| Field | Type | Rules |
|---|---|---|
| `root` | absolute path | Canonicalized; never accepted from the user-global config layer |
| `kind` | enum | `wordpress` or `compose`; missing means `wordpress` |
| `display_name` | string | Defaults to directory name; may contain dots; excludes control characters and path separators |
| `runtime` | object | Validated by the selected adapter; WordPress and Compose schemas never merge |
| `source` | string | Existing effective-config provenance |
| `label_overrides` | object | Existing per-label layer, validated under the same kind |

Changing `kind` while an instance exists is invalid. The user must destroy the old Sandbox-owned instance state first; project-owned data is preserved under generic semantics.

## Compose Runtime Descriptor

| Field | Type | Rules |
|---|---|---|
| `compose_file` | project-relative path | Required; must resolve inside the project root and exist |
| `service` | string | Required; must exactly match a service in the resolved Compose model |
| `container_port` | integer | Required, 1-65535 |
| `health_path` | string | Required; absolute HTTP path, default `/` only during guided initialization |
| `health_timeout_seconds` | integer | Optional; 1-600, default 120 |
| `environment` | string/list | Optional Compose profile/environment selector; no secret values persisted by Sandbox |

Sandbox does not copy the project Compose model into the registry. The descriptor remains committed project configuration and is revalidated on ensure/apply.

## Runtime Adapter

| Field | Type | Rules |
|---|---|---|
| `kind` | enum key | Unique adapter selector |
| `version` | positive integer | Used for diagnostics and future adapter-state migrations |
| `capabilities` | immutable set | Derived from code; not accepted from project config |
| lifecycle operations | functions | Validate, ensure, status, start, stop, logs, exec, apply, destroy |

Adapter selection is one-to-one with descriptor kind. Unknown kinds fail before registry or runtime mutation.

## Instance Record

Additive fields extend the existing registry entry.

| Field | Type | Rules |
|---|---|---|
| `root` + `label` | identity | Existing registry key; canonical root and validated label |
| `instance` | string | Globally unique runtime-safe ID |
| `display_name` | string | Human-readable project name |
| `kind` | enum | Missing legacy value reads as `wordpress` |
| `adapter` | string | Adapter name/version for diagnostics |
| `service` | string/null | Declared public service for generic projects; derived from the validated descriptor and returned in status |
| `http_port` | integer/null | Shared public host port; WordPress reads fall back to `wordpress_port` |
| `wordpress_port` | integer/null | Preserved legacy field; not written for new Compose records |
| `url` | URL | Existing localhost or clean URL |
| `artifact_dir` | absolute path | Must be within `$SANDBOX_HOME/runtime/projects/` for generic instances |
| `status` | enum | `pending`, `starting`, `ready`, `stopped`, `unhealthy`, `error` |
| `source` | string | Project configuration provenance |

### State transitions

```text
absent -> pending -> starting -> ready
                      |          |
                      v          v
                    error    stopped -> starting
                                  |
                                  v
                               absent
```

An unhealthy health probe moves `starting` to `unhealthy`; a later idempotent ensure may recover it. Destroy removes the record only after adapter-owned runtime cleanup succeeds or returns a clearly reported partial-cleanup state.

## Capability Set

Common capabilities include `instance.ensure`, `instance.status`, `instance.start`, `instance.stop`, `instance.logs`, `instance.exec`, `instance.apply`, `instance.destroy`, `http.probe`, and `proxy.secure`.

WordPress adds namespaced capabilities such as `wordpress.cli`, `wordpress.rest`, `wordpress.database`, `wordpress.files`, `wordpress.mail`, `wordpress.tests`, `wordpress.plugins`, and `wordpress.debug`.

Capability sets are returned to callers but derived from the adapter so stale registry data cannot grant operations.

## Astro Preset

An initialization-only value proposal with package manager, development command, bind flag, port, health path, and output Compose/config paths. It is never persisted as an instance kind; the resulting project descriptor is `compose`.

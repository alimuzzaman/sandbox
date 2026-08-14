# Data model: CLI-first Sandbox operation

## CLI guide

| Field | Meaning | Validation |
|---|---|---|
| `mode` | Interface preference | always `cli-first` |
| `project_kind` | Selected runtime kind | `compose` or `wordpress` |
| `project_root` | Descriptor root when known | optional canonical path |
| `skill` | Entry point for operating guidance | shipped skill command |
| `commands` | Runtime-specific command catalog | non-empty command/purpose entries |
| `mcp` | MCP availability statement | optional transport guidance |

## Execution request

| Field | Meaning | Validation |
|---|---|---|
| instance | Resolved project instance | must have registered project owner |
| command | argv list | non-empty strings, no NUL byte |
| capability | runtime authorization | `compose.exec` before invocation |
| service | declared Compose public service | resolved by existing descriptor adapter |

The guide is read-only. Execution uses existing runtime result data and does
not introduce persistent state.

## WordPress extension status

When a WordPress project declares `phpExtensions`, the CLI guide/status model may
include this additive, secret-free summary. Omission means the field is absent and
legacy output remains unchanged.

| Field | Meaning | Validation |
|---|---|---|
| `phpExtensions.ok` / `exit_code` | Process/report parity | `true`/`0` only when every required plane verifies; otherwise `false`/`1` |
| `phpExtensions.desired.profile` | Selected profile | `null` or immutable `wordpress@1` |
| `phpExtensions.desired.catalog` | Catalog identity | Integer revision plus immutable SHA-256 digest |
| `phpExtensions.desired.requirements` | Canonical extension states/constraints | Sorted `{name,state,version}` rows; `enabled`/`disabled`; exact, `X.Y.*`, or `php` versions |
| `phpExtensions.desired.resolution_digest` | Normalized requirement identity | Always emitted after read-only catalog resolution |
| `phpExtensions.desired.build_digest` | Verified build identity | Emitted only when the read-only cache receipt is complete and matches its digest |
| `phpExtensions.provenance` | Safe artifact identities | State plus allowlisted recipe-catalog digest, parent digests, and recipe IDs; never paths, image URLs, commands, or raw process output |
| `phpExtensions.observed` | Per-plane state/version | Exactly web PHP, WP-CLI, bounded exec, and PHPUnit; each freshly probed or marked unavailable |
| `phpExtensions.readiness` | Aggregate readiness | `ready`, `blocked`, or `unavailable` |
| `phpExtensions.staleness` / `drift` | Freshness and parity | Explicit `fresh`/`stale` and `ready`/`drift`/`unknown` states |
| `phpExtensions.issues` | Stable failure classes | `missing`, `version_mismatch`, `version_unobservable`, `unsupported_provisioning`, `unsupported_disable`, or `plane_drift` only |

The summary is diagnostic data, not permission to mutate a generic Compose image. A
failed or unsupported resolution must retain a nonzero exit status in JSON and text
surfaces while keeping stdout parseable.

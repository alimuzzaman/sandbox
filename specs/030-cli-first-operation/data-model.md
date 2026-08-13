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
| `phpExtensions.profile` | Selected profile | `null` or immutable `wordpress@1` |
| `phpExtensions.requested` | Canonical extension states/constraints | `enabled`/`disabled`; exact, `X.Y.*`, or `php` version constraints |
| `phpExtensions.observed` | Per-plane state/version | web PHP, WP-CLI, bounded exec, and PHPUnit; each must be freshly probed or marked unavailable |
| `phpExtensions.digest` | Normalized build/resolution digest | Includes profile/catalog, parent image digest, PHP, server, platform, and architecture |
| `phpExtensions.provenance` | Safe source/artifact/package identities | Redacted, no credentials or private source contents |
| `phpExtensions.readiness` | Resolution result | `ready`, `blocked`, `unsupported`, `version_mismatch`, `plane_drift`, or `unavailable` |

The summary is diagnostic data, not permission to mutate a generic Compose image. A
failed or unsupported resolution must retain a nonzero exit status in JSON and text
surfaces while keeping stdout parseable.

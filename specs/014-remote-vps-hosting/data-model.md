# Phase 1 Data Model: Remote VPS hosting for sandbox instances

## RemoteTarget

Per-machine config, read from `sandbox.local.yml`'s `remotes:` block (see
`research.md`'s config-schema decision). A plain resolved dict, same shape convention as
every other `sandbox_core`-adjacent config section.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | `str` | — | User-chosen identifier (the `remotes:` block's key), used in `--remote <name>` and the second MCP server's registered name `sandbox-<name>`. Must be a valid identifier (same character class as `_project_slug`'s validation — lowercase letters, numbers, hyphen, underscore). |
| `ssh` | `str` | — | `user@host[:port]` connection string, required at registration (spec FR-001). |
| `control_transport` | `"https" \| "tailscale"` | `"https"` | Control-plane transport selected during `provision`. HTTPS is the default; Tailscale is opt-in. |
| `control_host` | `str \| None` | `None` | Bare public hostname for HTTPS mode, e.g. `sandbox-control.example.com`. Required for HTTPS provisioning. |
| `control_url` | `str \| None` | `None` | URL users register as their second MCP server, e.g. `https://sandbox-control.example.com` or `http://100.64.1.2:9174`. |
| `tailscale_host` | `str \| None` | `None` | The VPS's tailnet address, recorded only when `control_transport=tailscale`. |
| `mcp_port` | `int \| None` | `None` | Port the remote MCP server listens on locally; assigned at provision time. In HTTPS mode Caddy proxies the public hostname to loopback on this port. |
| `bearer_token` | `str \| None` | `None` | Minted at provision time, stored. Shown to the user exactly ONCE, in `provision`'s own success output (needed to register the second MCP server) — same "reveal once" pattern as an AWS access key or GitHub PAT. Never echoed again afterward by any OTHER command that reads the stored entry back (`remote list`, etc.) — corrected via `/speckit-analyze`, which caught that the original "never echoed back... in any output" phrasing here was too strict and left no way to actually complete setup. |
| `provisioned` | `bool` | `False` | Whether `provision` has completed successfully at least once. |

Validation rules:
- `name` uniqueness: registering a name that already exists is an update, not an error —
  matches how re-running `remote add` for the same name should behave (idempotent,
  consistent with FR-005's provisioning idempotency expectation applied to registration
  too).
- `ssh` MUST be present for `provision`/`up`/`deploy` to proceed; a missing/malformed
  value is a `die()`-style actionable error, not a silent no-op.
- `bearer_token` is generated (not user-supplied) — a fresh cryptographically random
  token, matching the same secrecy bar as `_bridge.py`'s existing snapshot-bridge token.

## Deploy

Not a stored entity — a one-time, discrete action (spec's Key Entities section already
frames it this way). Represented here for its INPUT/OUTPUT shape, since `sb deploy`'s
CLI/MCP contract needs one.

| Field | Type | Notes |
|---|---|---|
| `remote` | `str` | Which registered `RemoteTarget` to deploy to. |
| `project_dir` | `str` | Local project being deployed (same convention as `./sb e2e`/`./sb ci`'s `--project-dir`). |
| `pushed_commit` | `str` (output) | The SHA that was pushed and reset-to on the VPS. |
| `uncommitted_files_applied` | `int` (output) | Count of tracked-modified + untracked files applied on top, for the user-facing confirmation message (spec SC-002's "100% present and correct" needs a way to show what "everything" means). |

Validation rules:
- `remote` MUST refer to a `RemoteTarget` with `provisioned: true` — deploying to an
  unprovisioned remote is a `die()`-style error naming provisioning as the missing step
  (spec's Edge Cases section, "deploy to a remote target that was never provisioned").
- A deploy that fails partway (spec FR-009) MUST leave the VPS at either its PREVIOUS
  fully-consistent state or the NEW one — never a half-applied mix. Concretely: the
  `git reset --hard` + diff-apply sequence only proceeds to the next step if the previous
  one succeeded; a failure before the reset never touches the VPS's existing checkout,
  and a failure after the reset but before the diff-apply completes is recoverable by
  simply re-running deploy (idempotent by construction — see research.md).

## RemoteInstance

Not a new entity type — per spec's Key Entities section and this plan's key
architectural decision, a "remote instance" is the SAME `Instance` concept sandbox
already models today (registry entry: root, label, ports, status, secrets), just living
in the VPS's own independent registry rather than the local one. No new fields, no new
schema. What's new is only WHICH machine's registry and MCP server a given call resolves
against — determined entirely by which of the two registered MCP servers (`sandbox` vs
`sandbox-<remote-name>`) the call came in on, never by inspecting a field on the entry
itself.

## Relationships

- One `RemoteTarget` (registered locally) ↔ one provisioned VPS ↔ that VPS's OWN,
  entirely independent registry of `Instance` entries (zero, one, or more — same
  multi-instance-per-root rules already apply, just scoped to that machine).
- One project (a local git working tree) ↔ zero-or-one deploy-target git repo per
  `RemoteTarget` it has ever been deployed to (lazily created on first deploy — see
  research.md's path-resolution decision). A project can have deploy-target repos on
  MULTIPLE different remotes simultaneously; each is independent.
- `Deploy` actions reference exactly one `RemoteTarget` and exactly one local project;
  they do not persist as their own record — only their EFFECT (the VPS's updated working
  tree) persists.

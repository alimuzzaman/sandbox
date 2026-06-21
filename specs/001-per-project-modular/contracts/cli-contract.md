# Contract: CLI Instance Resolution & Command Registry

The user/automation-facing behavioral contracts for this feature. Two surfaces: the
instance-resolution behavior (observable from any command) and the internal command-registry
interface (Stage C).

## C1 — Instance resolution

For any instance-targeting command:

```
resolved = --instance | $SANDBOX_INSTANCE | registry_instance(cwd_project) | ERROR
```

- **Precedence** (first match wins): explicit `--instance <name>` → `$SANDBOX_INSTANCE` →
  the registry instance owning the cwd's project → **error**.
- **Validation**: a chosen name MUST be a known instance (registry ∪ the merged config's
  `instances:`, which is sourced from `sandbox.local.yml` — `sandbox.yml` holds no instance
  blocks); unknown → exit non-zero, message lists valid instances.
- **No-project error**: from a dir that is not a registered project, a non-project-routed
  command MUST exit non-zero with guidance ("cd into a registered project, or run `sb init` /
  `sb ensure`") and perform NO side effects. It MUST NOT boot or target any fallback.
- **Project-routed exemption**: `init`, `ensure`, `test`, `mcp`, `smoke`, and `apply
  --project-dir` carry their own `--project-dir` and are not gated by this resolver.
- **Invariant**: `main` is never synthesized or selected; `DEFAULT_INSTANCE` does not exist.

## C2 — `resolve_instances()` output

- Returns `{instance_name: instance_config}` sourced from the registry + each instance's
  `sandbox.local.yml` block. MUST NOT inject a `main` key.
- Each config dict retains the existing keys consumers rely on (ports, admin, server, domain,
  wp_config, multisite, images, …) so downstream code is unchanged.

## C3 — App-password location

- Read/write `instances.<name>.app_password` for EVERY instance, in both `sb` and
  `mcp/wp-server/server.py`. The legacy `mcp.wp.application_password` key MUST NOT be read or
  written.

## C4 — Command module interface (Stage C)

Each `sandbox/commands/<group>.py` exposes:

```python
def register(subparsers) -> None:   # declare this group's subcommand(s) + flags
def run(cfg, args) -> None:         # handle the dispatched command
```

- `cli.py` discovers modules, calls each `register(...)`, builds argparse, applies C1
  resolution, then dispatches to the matching `run(...)`.
- Adding/removing a feature is adding/removing a module — NO edit to a central hand-maintained
  dispatch dict.
- EVERY CLI command (all 39 + the `ui` alias, including `open`) MUST map to exactly one
  `commands/*` module — no command left unmapped (FR-012).

## C4b — MCP tool-group interface (Stage D)

Each `mcp/wp-server/tools/<group>.py` registers its tools and reuses `sandbox/core/*` for
config/registry/docker/herd resolution (no duplicated helpers). `server.py` is a thin entry
that imports the groups. The MCP tool surface (names, params, behavior) is UNCHANGED — pure
refactor. The CLI and MCP server share one implementation of instance resolution and
per-instance app-password.

## C5 — Packaging invariants

- `sb` remains a single file resolvable by the global symlink, `bin/sandbox.js`, and the
  release tarball; the installed CLI runs identically from any directory.
- `sandbox/` ships in `package.json` `files`; `.specify/` and `skills/speckit-*` are excluded
  from `files` and pruned by `make-release.sh`.

## Non-goals (unchanged)

Per-instance on-disk layout, the MCP tool surface, snapshots-on-herd, and plugin behavior are
NOT changed by this feature.

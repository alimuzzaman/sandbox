# Research: Remote Hermes Agent Integration

**Date**: 2026-07-10

## Decision Summary

| Area | Decision | Rationale |
|---|---|---|
| Runtime placement | Install Hermes host-native under the existing remote Sandbox user. | It needs the same filesystem, Docker group, Git credentials, `$SANDBOX_HOME`, and direct `sb` access; a nested container would add bind mounts and a second privilege boundary without reducing the requested full access. |
| Supported release | Start with signed tag `v2026.7.7.2` (Hermes v0.18.2) and resolve/store its full commit before installation. | It was the latest signed release during planning; pinning the tag and commit avoids tracking `main`. |
| Installer | Use the official installer non-interactively with explicit `--branch`, `--commit`, `--skip-setup`, `--non-interactive`, `--dir`, and `--hermes-home`. | Current upstream script supports immutable commit checkout and separates install from secret-bearing interactive setup. |
| Sandbox access | Configure the remote `sb mcp` as a local stdio MCP server and retain direct terminal execution of the absolute remote `sb` path. | Hermes natively discovers stdio MCP tools/resources/prompts, and both access paths share the same Sandbox home and registry. |
| MCP concurrency | Set `supports_parallel_tool_calls: false`. | Sandbox tools mutate shared repositories, registries, databases, containers, and files; upstream warns parallel calls need a race-safety review. |
| MCP filtering | Do not set an include/exclude restriction; keep resource and prompt utilities enabled. | The user explicitly requires complete Sandbox tool/CLI access. This is a trusted single-operator profile, not a least-privilege multi-tenant boundary. |
| Terminal approvals | Use manual mode, deny dangerous cron commands, confirm MCP reload, and confirm destructive slash commands. | These are upstream-supported defense-in-depth defaults. They protect shell/slash operations but do not technically authorize arbitrary MCP tools. |
| Repositories | Clone below `$SANDBOX_HOME/hermes-repos`; reject embedded credentials and escaping paths; use provider device authentication. | It supports any authorized Git repository without copying a local private key or allowing repository names to become arbitrary filesystem paths. |
| Worktrees | Isolate every coding session by default and preserve dirty worktrees. | Upstream recommends one worktree per experiment and provides `hermes -w`, which creates worktrees below `.worktrees/`. Sandbox must add lifecycle metadata/locking around it. |
| Gateway | Use upstream gateway commands behind a generated systemd service and require explicit allowlists. | Upstream supports install/start/stop/restart/status; Sandbox adds fail-closed policy validation and bounded logs. |
| Updates | Wrap immutable updates with a Sandbox plan/confirm/backup/health/rollback state machine in V2. | Upstream `hermes update` follows a configured branch and may modify the working tree; the integration needs stronger reproducibility and rollback guarantees. |
| Dashboard | Defer until V2, use upstream dashboard, bind loopback, access through SSH forwarding, and never use `--insecure`. | Upstream supports a web/PTY dashboard on port 9119. Public authentication is OAuth-based, not a local password feature. Operational recovery must exist first. |
| Public dashboard | Optional V3 operation through feature 015, gated by explicit FQDN, plan, confirmation, OAuth, TLS, health check, and rollback. | This avoids duplicating Caddy/Cloudflare management and ensures public exposure uses the separately reviewed hosting workflow. |

## Remote Environment Audit

The existing `scaleway-sandbox` host was audited read-only during architecture research:

- Ubuntu 24.04 x86_64 with systemd.
- Docker 29.6.1 and Compose v5.3.1.
- Python 3.12.3 and Git 2.43.0.
- Existing Sandbox staged runtime and working Docker access for the remote account.
- Approximately 142 GB free disk and 11.7 GiB RAM at audit time.
- Hermes, GitHub CLI, Git provider authentication, and the managed repository root were not yet present.

These observations establish feasibility but are not durable configuration. `sb hermes doctor` must re-run capability, storage, memory, path, Docker, Git, systemd, port, and version checks before each relevant action.

## Primary Source Findings

### Installation and release pinning

- The official [installer source](https://github.com/nousresearch/hermes-agent/blob/main/scripts/install.sh) exposes `--branch`, `--commit`, `--skip-setup`, `--non-interactive`, `--dir`, and `--hermes-home`. It defaults non-root data to `~/.hermes` and installation code to `~/.hermes/hermes-agent` unless overridden.
- The official [release page](https://github.com/nousresearch/hermes-agent/releases/tag/v2026.7.7.2) identifies signed tag `v2026.7.7.2`, Hermes v0.18.2, and commit prefix `9de9c25`. Implementation must resolve and record the full commit and verify the checked-out `HEAD`.
- The [installation guide](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/getting-started/installation.md) states the standard installer provisions Python 3.11, Node.js, ripgrep, and related dependencies without requiring a system-wide Python replacement.

### MCP integration

- The official [MCP guide](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md) supports stdio `command`/`args`, environment values, connection/tool timeouts, resources/prompts, include/exclude filtering, and a per-server `supports_parallel_tool_calls` flag.
- Upstream defaults MCP calls to sequential and warns that parallel calls must be enabled only after reviewing shared-state races. Sandbox therefore remains sequential.
- Hermes adds MCP tool names to its own catalog after discovery; diagnostics must compare the discovered Sandbox set with a direct `sb mcp` listing rather than relying on static names.

### Security and configuration

- The [security guide](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/security.md) documents `approvals.mode: manual`, `cron_mode: deny`, `mcp_reload_confirm: true`, and `destructive_slash_confirm: true`.
- The [configuration guide](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md) distinguishes the real user home from profile-isolated homes. This integration chooses the real home so existing Git/provider credentials and Sandbox paths are visible to Hermes.
- Manual terminal approvals do not wrap external MCP authorization. Because full Sandbox MCP is required, the Hermes profile is explicitly a high-trust operator profile and must not become a shared multi-tenant surface.

### Worktrees and services

- The official [worktree guide](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/git-worktrees.md) recommends one worktree per experiment and documents `hermes -w`, which creates an isolated branch/worktree below `.worktrees/`.
- The [CLI reference](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md) documents gateway lifecycle commands and the upstream update behavior.
- Dirty worktrees are deliberately retained. Git itself refuses normal worktree removal when uncommitted changes exist; the integration must never add force removal to automatic cleanup.

### Dashboard

- The [dashboard guide](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md) documents the upstream web dashboard, loopback default, hosted OAuth gate, and `--insecure` behavior.
- The [CLI reference](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md) documents `hermes dashboard`, default port 9119, default host `127.0.0.1`, `--no-open`, `--tui`, status, and stop options.
- Upstream does not provide a generic local username/password store. Default remote access therefore uses authenticated SSH forwarding; public V3 uses supported OAuth behind managed TLS and explicitly rejects `--insecure`.

## Alternatives Rejected

### Run Hermes in a container

Rejected for V1. Full Sandbox access would require mounting the Sandbox source, home, Docker socket, repositories, credentials, and service controls into the container. That reproduces host privilege while making paths, systemd, Git worktrees, browser support, and on-demand instance access harder to reason about.

### Give Hermes only the remote HTTP Sandbox MCP endpoint

Rejected. The user requested direct CLI access as well as all Sandbox MCP tools. Co-locating a stdio MCP process avoids creating another bearer token/public endpoint and preserves the remote's local registry semantics.

### Copy the developer's local SSH private key to the remote

Rejected. Provider device flow or a remote-scoped deploy credential keeps credential provenance and revocation clear and avoids duplicating sensitive local key material.

### Filter destructive MCP tools

Rejected for this profile because it violates the explicit complete-access requirement. A future multi-user or public profile should use a separate filtered MCP configuration and authorization design rather than weakening this contract silently.

### Build a custom web dashboard

Rejected. Upstream already exposes configuration, sessions, chat, profiles, models, skills, MCP, gateway, cron, and system views. Sandbox should own lifecycle/gating/exposure, not fork a parallel UI.

### Deliver the dashboard in V1 or V2

Rejected. Before update rollback, restore, resource limits, cleanup, health, log retention, and reboot recovery are proven, a persistent browser-accessible agent surface would increase the blast radius of operational failures.

## Risks Requiring Implementation-Time Revalidation

- Upstream Hermes changes quickly; installer flags, config keys, service names, dashboard authentication, and worktree output must be contract-tested against the pinned revision.
- A signed tag proves source provenance but not dependency immutability. Record dependency lockfiles and installer output, and retain the exact installed commit.
- Full Docker plus full Sandbox MCP access is effectively administrative over the Sandbox workload. This is acceptable only for the trusted remote user described by the specification.
- Public dashboard exposure is blocked unless feature 015 has landed with plan/apply/rollback behavior and supported authentication can be verified without `--insecure`.

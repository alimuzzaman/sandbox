# Data Model: Remote Hermes Agent Integration

## Storage Boundaries

| Store | Owner | Contents | Must not contain |
|---|---|---|---|
| Local `sandbox.local.yml` | Sandbox operator | Per-machine Hermes defaults and references to configured remotes | Provider tokens, Git tokens, dashboard cookies, private keys |
| Remote `$SANDBOX_HOME/runtime/hermes.json` | Sandbox Hermes integration | Install metadata, managed repositories, sessions/jobs, backups, services, limits, acceptance gates | Raw secrets, prompt bodies, unbounded logs |
| Remote `$HOME/.hermes` | Upstream Hermes | Hermes config, profiles, sessions, checkpoints, provider auth, gateway/dashboard state | Sandbox instance identity duplication |
| Remote `$SANDBOX_HOME/runtime/registry.json` | Existing Sandbox instance layer | WordPress instance records keyed by project/worktree | Hermes installs, repos, sessions, or gates |
| Remote `$SANDBOX_HOME/hermes-repos` + `$SANDBOX_HOME/runtime/hermes-worktrees` | Git + Sandbox Hermes integration | Managed primary checkouts and integration-owned isolated worktrees | Paths escaping managed roots or URL credentials |
| Remote system secret environment files | systemd/operator | Gateway/dashboard secrets and supported OAuth values | World-readable permissions or committed values |

`hermes.json` uses an integer `schema_version`, atomic replace, a process lock, owner-only write permissions, and additive migrations. Unknown fields are preserved where safe so rolling back Sandbox does not erase newer non-secret metadata.

## Entity: HermesInstallation

| Field | Type | Rules |
|---|---|---|
| `home` | absolute path | Owned by remote Sandbox user; default `$HOME/.hermes` |
| `install_dir` | absolute path | Default `$HOME/.hermes/hermes-agent`; must not overlap managed repos |
| `launcher` | absolute path | Executable and resolves to the selected installation |
| `release_tag` | string | Signed supported tag, e.g. `v2026.7.7.2` |
| `commit` | 40-hex string | Required immutable checked-out revision |
| `installed_at` | UTC timestamp | Set after successful verification |
| `profile` | string | Sandbox-managed Hermes profile name |
| `status` | enum | `absent`, `installing`, `installed`, `configured`, `healthy`, `degraded`, `updating`, `rollback_required` |

### State transitions

```text
absent -> installing -> installed -> configured -> healthy
                     \-> absent (failed clean install)
healthy -> updating -> healthy
                   \-> rollback_required -> healthy|degraded
healthy|degraded -> configured (setup reconciliation)
```

Transitions that overwrite a healthy installation require a backup and confirmation in V2. V1 reinstall of the identical tag/commit is reconciliation, not update.

## Entity: HermesProfileBinding

| Field | Type | Rules |
|---|---|---|
| `name` | string | Stable Sandbox-managed profile identifier |
| `sandbox_home` | absolute path | Exact remote `$SANDBOX_HOME` |
| `sandbox_sb` | absolute path | Exact remote `sb` launcher |
| `repository_root` | absolute path | Canonically below `$SANDBOX_HOME` |
| `mcp_server_name` | string | `sandbox` |
| `mcp_catalog_fingerprint` | string | Hash of discovered tool/resource/prompt names, not schemas containing secrets |
| `approvals_mode` | enum | `manual` for the supported profile |
| `cron_mode` | enum | `deny` for the supported profile |
| `config_revision` | string | Hash of integration-owned non-secret config |

The binding owns only its marked/generated config section. Setup backs up an existing user config and preserves unrelated Hermes settings.

## Entity: ManagedRepository

| Field | Type | Rules |
|---|---|---|
| `name` | string | Unique slug matching `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$` |
| `canonical_path` | absolute path | Strict child of repository root |
| `origin` | sanitized URL | Scheme/host/path only; userinfo forbidden |
| `default_ref` | string or null | Optional branch/tag/commit selected at clone |
| `provider` | enum/string | `github`, `gitlab`, `generic`, or inferred hostname |
| `created_at` | UTC timestamp | Registration time |
| `last_verified_at` | UTC timestamp | Last successful `git` validation |
| `state` | enum | `cloning`, `ready`, `unavailable`, `invalid` |

Repository names are logical identifiers, never accepted as free-form paths. Clone first uses a temporary sibling and atomically renames only after Git validation succeeds.

## Entity: HermesSession

| Field | Type | Rules |
|---|---|---|
| `id` | 16-hex identifier | Unique within Hermes state |
| `repository` | repository name | Must reference a ready managed repository |
| `mode` | enum | `interactive`, `oneshot`, `gateway`, `dashboard` |
| `worktree` | boolean | Defaults true for coding sessions |
| `worktree_path` | absolute path or null | Canonically within repository `.worktrees` or integration-owned worktree root |
| `branch` | string or null | Generated collision-resistant session branch |
| `profile` | string | Selected Hermes profile |
| `started_at` / `ended_at` | UTC timestamp/null | Lifecycle timestamps |
| `state` | enum | `starting`, `running`, `completed`, `failed`, `cancelled`, `orphaned` |
| `exit_code` | integer/null | Present when completed |

Prompt bodies are not written to `hermes.json`. Hermes may retain session content according to its own profile behavior.

## Entity: HermesJob

| Field | Type | Rules |
|---|---|---|
| `job_id` | existing async job ID | Uses Sandbox host-level job format |
| `session_id` | identifier | References HermesSession |
| `remote` | remote name | Validated configured remote |
| `status` | enum | `queued`, `running`, `completed`, `failed`, `cancelled`, `orphaned` |
| `output_path` | absolute path | Integration-owned log file, never returned without bounds/redaction |
| `bytes_read` | integer | Supports incremental polling |
| `truncated` | boolean | True when more output remains |
| `pid` / `process_group` | integer/null | Used for cancellation/reconciliation |
| `created_at` / `finished_at` | UTC timestamp/null | Retention and diagnostics |

## Entity: GatewayService

| Field | Type | Rules |
|---|---|---|
| `profile` | string | One service instance per profile |
| `unit_name` | string | Generated safe systemd name |
| `allowlist_fingerprint` | string | Non-reversible fingerprint plus entry count; raw identifiers remain in protected Hermes config |
| `enabled` / `active` | boolean | Observed from systemd, not trusted from cached state |
| `last_health` | enum | `unknown`, `healthy`, `degraded`, `failed` |
| `last_checked_at` | UTC timestamp | Diagnostic evidence |

An allowlist with zero entries or an allow-all/wildcard form is invalid.

## Entity: OperationalBackup (V2)

| Field | Type | Rules |
|---|---|---|
| `id` | timestamp plus random suffix | Unique and filesystem-safe |
| `kind` | enum | `pre-update`, `manual`, `pre-restore`, `pre-dashboard` |
| `source_commit` | 40-hex string | Installed revision at capture |
| `schema_version` | integer | Hermes integration schema |
| `archive_path` | absolute path | Below integration backup root |
| `sha256` | 64-hex string | Verified before restore |
| `created_at` | UTC timestamp | Retention ordering |
| `size_bytes` | integer | Disk preflight input |
| `status` | enum | `creating`, `ready`, `invalid`, `restored` |

Backups exclude managed repository contents and WordPress instance data. Those systems have independent Git/Sandbox recovery paths.

## Entity: ResourcePolicy (V2)

| Field | Type | Rules |
|---|---|---|
| `max_running_jobs` | positive integer | Refuse or queue above limit |
| `max_active_worktrees` | positive integer | Dirty retained worktrees still count until resolved |
| `min_free_disk_bytes` | non-negative integer | Checked before install/clone/update/backup/worktree |
| `min_free_memory_bytes` | non-negative integer | Checked before agent/service start |
| `job_retention_seconds` | positive integer | Completed-job metadata/log retention |
| `backup_retention_count` | positive integer | Conservative rotation; never delete only valid backup during update |

## Entity: AcceptanceGate

| Field | Type | Rules |
|---|---|---|
| `name` | enum | `v1_core`, `v2_operations`, `v3_dashboard` |
| `status` | enum | `not_run`, `running`, `passed`, `failed`, `stale` |
| `integration_schema` | integer | Gate invalidates when incompatible schema changes |
| `hermes_commit` | 40-hex string | Gate is revision-specific |
| `sandbox_commit` | 40-hex string | Evidence ties to tested Sandbox code |
| `checks` | list of result summaries | No secrets, prompt content, SSH target, or unbounded logs |
| `recorded_at` | UTC timestamp | Required for passed/failed |

V3 checks only `v2_operations: passed` when its recorded schema and commits match the current compatibility policy. No `--force` flag may synthesize a pass.

## Entity: DashboardService (V3)

| Field | Type | Rules |
|---|---|---|
| `unit_name` | string | Dedicated generated systemd unit |
| `bind_host` | string | `127.0.0.1` for default supported mode |
| `port` | integer | Default 9119; validated available range |
| `access_mode` | enum | `ssh-forward`, `oauth-public` |
| `fqdn` | hostname/null | Required only for public mode |
| `auth_state` | enum | `ssh_required`, `oauth_configured`, `invalid` |
| `route_state_ref` | string/null | Feature 015 rollback record |
| `enabled` / `active` | boolean | Observed service state |
| `last_health` | enum | Must include authenticated probe result for public mode |

## Locking Model

- One integration-state lock guards `hermes.json` migrations and atomic writes.
- One Hermes-home lock guards install, setup, update, restore, and dashboard dependency changes.
- One repository lock guards clone/fetch/worktree create/remove for each repository.
- Service actions serialize per unit.
- Lock acquisition has a bounded timeout and reports the current operation without exposing command arguments or secrets.

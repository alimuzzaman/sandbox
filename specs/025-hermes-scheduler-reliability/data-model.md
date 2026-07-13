# Data Model: Reliable Hermes Scheduled Work

## DesiredCronEntry

| Field | Type | Rules |
| --- | --- | --- |
| `name` | string | Stable unique logical identity; 1–120 safe characters. |
| `schedule` | string | Valid Hermes interval/cron expression; bounded length. |
| `kind` | enum | `script` or `agent`. |
| `script` | string/null | Required committed script filename for `script`; absent for `agent`. |
| `prompt` | string | Non-secret intent; agent task or optional script context. |
| `profile` | enum/null | Required `luna`, `terra`, or `sol` for agent jobs; absent for scripts. |
| `workdir_template` | string/null | Catalog-safe template resolved from `repo_root`, `sandbox_home`, or the managed `worktrees` root. Agent implementation jobs use a dedicated managed worktree, never the primary checkout. |
| `enabled` | boolean | Desired installed state. |
| `deliver` | enum | `local` for the base catalog. |
| `version` | integer | Catalog schema/version for migration. |

Validation rejects duplicate names, unknown fields that affect execution, effort-suffixed models, unsafe paths, missing script assets, agent jobs without profiles, and script jobs with profiles.

## ObservedCronEntry

Extends bounded upstream metadata with `kind`, `route_valid`, `latest_evidence`, `effective_status`, and `false_success`. Prompt/script content and secrets are excluded.

### Status derivation

1. Invalid route or malformed record → `invalid`.
2. Correlated provider/client/request error → `failed`; set `false_success=true` when upstream says `ok`.
3. Upstream terminal failure → `failed`.
4. Running/claimed state → `running`.
5. Successful script with no-work output → `idle_ok`.
6. Successful agent run with bounded final evidence → `ok`.
7. Never run → `never_run`.

## CronReconciliationPlan

| Field | Type | Meaning |
| --- | --- | --- |
| `catalog_version` | integer | Desired catalog schema. |
| `catalog_fingerprint` | string | Deterministic hash of normalized definitions and script assets. |
| `remove` | list | Sanitized observed IDs/names to remove. |
| `create` | list | Desired logical names and classifications to create. |
| `retain` | list | Exact matching entries on ordinary convergence; empty during forced replacement. |
| `blocked_by` | list | Dirty worktrees, invalid catalog, missing target/scripts, or unavailable gateway. |
| `changes` | boolean | Whether apply would mutate the remote. |

### Apply states

`planned → backed_up → removing → installing_scripts → creating → verifying → converged`

Any failure transitions to `partial`, retaining completed step evidence and recovery guidance.

## GatewayOwnershipState

Fields: expected unit, expected active state, observed units and active/restart states, observed gateway PIDs and command classifications, owner count, conflict boolean, restart counter, and bounded observation result.

Healthy requires one active Sandbox-managed owner, zero active legacy owners, one gateway process, scheduler availability, and stable restart counters.

## WorktreeEvidence

Fields: repository name/path alias, worktree path, branch or detached state, HEAD, upstream relation, dirty file counts and paths, validation disposition, and preservation state. Paths are bounded to configured managed roots.

Preservation states: `clean`, `dirty_unreviewed`, `validated`, `committed`, `pushed`, `retained_invalid`.

## VerifiedRunResult

Fields: job ID/name, prior and observed run timestamps, trigger acknowledgement, terminal/effective status, bounded sanitized error/output summary, false-success flag, timeout, and optional worktree evidence delta.

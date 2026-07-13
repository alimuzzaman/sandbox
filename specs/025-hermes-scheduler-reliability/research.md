# Research: Reliable Hermes Scheduled Work

## Observed Failure Evidence

- The remote had one manually launched gateway process while `hermes-gateway.service` restarted more than 1,350 times because the PID was already owned. The Sandbox-managed gateway unit existed separately.
- Both recorded `sandbox-remaining-spec-tasks` agent requests ended in HTTP 400 with an unsupported effective model identifier containing `/high`, while `jobs.json` reported `last_status: ok`.
- The only sandbox scheduled-work prompt explicitly prohibited implementation, commit, and push and limited edits to task ledgers. Its observed dirty worktree therefore reflects exactly that limited behavior.
- Three high-frequency jobs were `no_agent` scripts. Two swallowed command/inspection failures and returned zero; the TODO monitor also removed itself, making catalog drift inevitable.
- The paused Sol readiness job never ran and targeted a planning decision that had already been superseded by later specifications.

## Decision: Treat the gateway as the scheduler owner

**Rationale**: Official Hermes cron documentation states that the gateway ticks the scheduler every 60 seconds and starts isolated sessions for due jobs. A file lock prevents duplicate ticks, but it does not make duplicate gateway ownership operationally healthy. One managed owner keeps loaded code/config and service health observable.

**Primary source**: [Hermes cron documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/cron.md), [Hermes scheduler source](https://github.com/NousResearch/hermes-agent/blob/main/cron/scheduler.py).

**Alternatives considered**:
- Keep a manual gateway and disable all services: rejected because it is not boot-reproducible and bypasses service health/restart policy.
- Allow both and rely only on `.tick.lock`: rejected because the legacy service restart storm remains and old loaded behavior survives configuration/code changes.

## Decision: Validate model and effort separately, then prove compatibility live

**Rationale**: Codex configuration represents `model` and `model_reasoning_effort` as separate fields, and the Responses API request shape carries reasoning separately from model. Concatenating `/high` creates a different model identifier and was rejected by the ChatGPT-backed Codex endpoint. Static route validation catches known malformed forms; a live bounded run catches entitlement/catalog drift.

**Primary source**: [OpenAI Codex configuration source](https://github.com/openai/codex/blob/main/codex-rs/core/src/config/mod.rs), [OpenAI Codex configuration schema](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json), [Hermes configuration documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md).

**Alternatives considered**:
- Encode effort in the model string: rejected by observed provider failure and official configuration shape.
- Remove reasoning entirely: rejected because role routes require bounded effort; compatibility should be discovered and reported, not silently downgraded.

## Decision: Cross-check terminal evidence instead of trusting `last_status`

**Rationale**: The deployed Hermes version produced request dumps with `BadRequestError` while retaining `last_status: ok`. Newer upstream scheduler source explicitly recognizes this failure class and marks unsuccessful agent returns. Sandbox must support the pinned version and upgrades, so it uses a bounded evidence adapter and flags disagreements rather than assuming one upstream field is authoritative.

**Primary source**: [Hermes scheduler source](https://github.com/NousResearch/hermes-agent/blob/main/cron/scheduler.py).

**Alternatives considered**:
- Patch the installed upstream checkout: rejected because upgrades overwrite it and fresh servers would diverge.
- Trust only request dumps: rejected because scripts and successful runs may not create them; evidence must be correlated with metadata and output.

## Decision: Version the desired catalog and scripts in Sandbox

**Rationale**: The current jobs and scripts exist only in operator state and cannot be reproduced from a fresh server. Hermes officially supports per-job scripts, `no_agent`, model/provider overrides, workdirs, and lifecycle commands. A committed catalog makes the intended set reviewable and gives reconciliation deterministic input.

**Primary source**: [Hermes autonomous-agent scheduling guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md).

**Alternatives considered**:
- Back up `jobs.json` only: rejected because it preserves drift, generated IDs, obsolete tasks, and malformed routes.
- Continue one-off job creation commands: rejected because there is no exact desired-state or deletion policy.

## Decision: Operational failures are nonzero; no-work is zero

**Rationale**: Scheduler status can only be truthful when script exit codes distinguish successful inspection/no-work from inability to inspect or dispatch. A no-agent script is the entire job; swallowing a timeout or malformed response as exit zero guarantees a false green.

**Alternatives considered**:
- Keep zero exits and parse free-form output: rejected because status remains misleading and output text is not a stable error contract.
- Make no-work a failure: rejected because an empty queue is a normal successful state.

## Decision: Full replacement is previewed, confirmed, and recoverable

**Rationale**: The user explicitly requested removing and recreating every cron job. A two-phase plan/apply operation supports that requirement without hiding destructive scope. Exact post-apply verification and a restricted backup provide recovery if creation stops midway.

**Alternatives considered**:
- Edit jobs in place: rejected for this migration because stale IDs, paused obsolete work, and unowned fields would remain.
- Remove first with ad hoc shell: rejected because it has no catalog validation, backup, or partial-progress report.

## Decision: Preserve dirty worktrees before cleanup

**Rationale**: The remote contains uncommitted Sandbox, Lenzora, and smoke-test changes. Git worktrees are the inspectable evidence of agent work. Cleanup must never infer that an old or detached worktree is disposable when it is dirty.

**Alternatives considered**:
- Force-remove after cron deletion: rejected due to data loss.
- Automatically commit every dirty tree: rejected because one observed recovery diff is large, untested, and outside its feature artifacts; invalid work must be retained, not laundered into history.

## Decision: Do not recreate obsolete or self-mutating jobs unchanged

**Rationale**: “Recreate all” means replace the complete current inventory with a reviewed complete desired catalog, not preserve known defects. The superseded readiness adjudication is removed. Monitor scripts remain only when committed and fail truthfully. Coding jobs must have an approved bounded task scope and may not grant themselves standing commit/push authority.

**Alternatives considered**:
- Recreate byte-for-byte: rejected because it reproduces the unsupported route, no-op implementation scope, false-green scripts, and self-deletion drift.

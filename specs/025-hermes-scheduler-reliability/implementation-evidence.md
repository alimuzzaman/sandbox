# Implementation Evidence: Reliable Hermes Scheduled Work

## Baseline

- Remote: configured `scaleway-sandbox`; no credentials or prompts retained here.
- Observed cron inventory: five jobs—three no-agent scripts, one paused readiness agent, and one recurring spec-ledger agent.
- The recurring agent recorded `last_status=ok`, while two correlated request dumps recorded HTTP 400 unsupported-model failures. Effective result: false success.
- Gateway ownership: a manually started process, active Sandbox unit, and restarting legacy `hermes-gateway.service`; the legacy restart counter exceeded 1,300.
- Dirty work: Sandbox task-ledger worktree, an incomplete recovery worktree, a Lenzora spec-artifact worktree, and generated Hello Dolly smoke configuration.

## Root causes

1. The old Sandbox cron creator used unsupported `--schedule` and `--prompt` flags; pinned Hermes uses positional `schedule [prompt]`.
2. Reasoning effort was combined with or effectively appended to the model request. Hermes/Codex require model and effort as separate settings.
3. Upstream metadata could remain `ok` when a request dump contained a provider rejection.
4. Monitor scripts returned zero on inspection, timeout, and dispatch failures; the TODO monitor also removed itself.
5. The recurring agent prompt was intentionally ledger-only and prohibited implementation, commit, and push.
6. Multiple gateway owners fought over the same singleton process, creating a restart storm.
7. Cron definitions and scripts lived only in remote mutable state, so fresh-server setup could not reproduce them.

## Worktree review

- Sandbox modular-boundary ledger: validated against existing files and `tests.test_command_composition` (4 tests passed); the seven truthful task markers were integrated locally.
- Sandbox recovery worktree: retained uncommitted. It contains an untested 243-line change with an undefined outer `pathlib` reference, a fixed shared `/tmp` stage, and incorrect loop indentation in the generated backup script.
- Lenzora spec worktree: retained uncommitted. It changes only Spec-Kit artifacts, includes a future-dated record, and has no current-branch verification evidence; repository policy forbids treating hand-edited artifacts as implemented proof.
- Hello Dolly smoke worktree: retained as generated test configuration; it is not product source.

## Verification log

- `./.cli-venv/bin/python -m unittest tests.test_command_composition` → 4 passed.
- `./.cli-venv/bin/python -m unittest tests.test_hermes` → 110 passed after scheduler changes.
- `./.cli-venv/bin/python -m unittest tests.test_mcp` → MCP registration and argument parity passed.
- `./sb selftest` → 498 passed, 1 dependency-isolation skip; pre-existing subprocess `ResourceWarning` messages did not fail the suite.
- `./sb hermes cron catalog --remote scaleway-sandbox --json` → catalog valid; four desired jobs.
- `./sb hermes cron reconcile --remote scaleway-sandbox --force-replace --json` → read-only plan: remove five, create four.
- `./sb hermes health --remote scaleway-sandbox --json` → degraded as expected; false success, gateway conflict, and cron drift all detected in under 10 seconds.
- `./sb hermes worktree list --remote scaleway-sandbox --json` → dirty worktrees retained and reported.

## Trace

Outcome / non-goals: Make Hermes scheduling truthful and reproducible; no unrelated production deployment.

Risk and authority: User explicitly authorized cron replacement, gateway convergence, commits, pushes, and remote synchronization for this task. Dirty invalid worktrees remain preserved.

Model(s), effort, and role: Sol/High architecture and research; Terra/High Spec-Kit artifacts and implementation. Desired scheduled implementation route is Terra/Medium; Spark orchestrates and Luna remains read-only.

Workspace / commits / delegates: Local `codex/hermes-public-access`; remote Hermes worktrees reviewed individually. Commit IDs are recorded after shipping.

Evidence: Commands and live results above; final full-suite, selftest, remote convergence, and verified-run evidence are appended after execution.

Outcome, residual risk, and follow-up: Pending final review and live convergence.

Learning delta: Added durable safeguards for positional CLI compatibility, false-success precedence, nonzero monitor failures, one gateway owner, committed desired state, and bounded verified execution.

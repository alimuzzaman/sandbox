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

## Live convergence and acceptance

- Pushed Sandbox commits through `32877c473e881adf5c9a7729ddb68037d48f74f6` and synchronized the managed remote checkout/runtime to the same commit with `./sb hermes repo sync`.
- Initial setup attempts exposed a reproducible hang in upstream `hermes mcp add`. Sandbox now bounds and labels every remaining Hermes CLI setup step and atomically merges its owned MCP/profile settings into the coordinator plus Luna, Terra, and Sol profiles. The live setup then completed in 23 seconds and sanitized state sync reported `unchanged`.
- Gateway convergence stopped a legacy unit whose restart counter had reached 1,988, disabled it, terminated competing gateway processes, and established one `hermes-gateway-sandbox.service` process. Repeated checks after more than two minutes reported the managed unit active with zero restarts and the legacy unit inactive/disabled with zero restart growth.
- Forced cron reconciliation removed all five old jobs. Its first creation attempt stopped in an explicit partial state because the generated command quoted `$HOME` literally; the pre-change jobs backup was retained. After the launcher-expansion fix and regression test, rerunning reconciliation created exactly four jobs and a repeat preview reported zero changes.
- Installed job IDs at acceptance: `5bba87b3ff38` (`todo-md-monitor`), `531687c80a7b` (`codex-quota-requeue`), `215cacf74a89` (`lenzora-kanban-dispatch`), and `433ce28bea8b` (`sandbox-approved-spec-task`).
- `./sb hermes cron verify 433ce28bea8b --timeout 1200 --confirm --json` reached an evidence-backed terminal result in 78.61 seconds with a valid `openai-codex` / `gpt-5.6-terra` route, no correlated provider failure, and no false success.
- The corresponding bounded saved output reported `NO_APPROVED_WORK`: the worker correctly refused mutation because this specification was still marked Draft. The specification is now explicitly approved; the remaining bounded convergence task will be used to prove an actual scheduled change.
- Latest aggregate health reported no cron drift, no false success, one healthy gateway owner, and no degraded reasons. Six dirty worktrees remain inventoried; reviewed invalid/unrelated changes remain preserved rather than force-committed.
- Post-acceptance script evidence showed `todo-md-monitor` failing every five minutes because Lenzora has no `TODO.md`. The Lenzora Kanban board is the actual work source, already covered by bounded dispatch and quota requeue, so the redundant monitor was removed from the desired catalog instead of preserving a permanent false alarm.

## Additional failures converted into safeguards

1. Gateway convergence previously treated the legacy unit's `deactivating` state as healthy; every non-quiescent transitional state is now a conflict and has a regression test.
2. Setup previously performed many lock-prone Hermes config mutations and omitted the Sandbox MCP contract from isolated worker profiles; setup now uses one schema-preserving atomic merge for all profiles.
3. Cron command composition previously shell-quoted the trusted `$HOME` launcher expression; launcher expansion and catalog argument quoting now have dedicated tests.
4. Sandbox previously could not inspect Hermes' saved cron response. `hermes cron output` now provides validated, bounded, redacted CLI/MCP access without allowing arbitrary paths.
5. Independent Terra/High review found that fresh bootstrap still hard-required a retired script, health performed hidden reconciliation writes, and gateway convergence sampled only five seconds without proving scheduler availability. Bootstrap now derives required scripts from the catalog; health is observation-only; convergence probes `hermes cron status` and ownership/restart state over 120 seconds.
6. The same review found preservation could miss bearer/JWT credentials and revalidate incompletely, while failed repository refresh could lose retained virtual environments. One shared credential detector, tick/worktree lock ordering, reviewed tree identity, `git diff --check HEAD`, and transactional staged runtime replacement now fail closed with regression coverage.
7. Reconciliation previously compared only shallow metadata. Exact convergence now uses secret-safe canonical hashes for every controlled field, guarded prompt, delivery, route, workdir, script identity, and installed script content. Missing evidence blocks ordinary apply before deletion; an explicit force-replacement can replace unknown observed state, but final verification still requires exact hashes.

## Secure remote connection reuse

- Official OpenSSH guidance supports opportunistic session sharing with `ControlMaster=auto`, endpoint-unique `%C` control paths, and bounded `ControlPersist`; the control directory must not be writable by other users.
- Before implementation, three harmless `hermes cron list` calls took 6.15s, 6.85s, and 4.13s because each established a new SSH connection.
- With the shared Sandbox transport policy, the same sequence took 6.84s, 1.54s, and 1.49s. The first call authenticated normally; the next two reused the connection and were approximately 3–4 times faster.
- Local control state was verified as a mode-0700 directory with a mode-0600 `%C`-hashed socket. It contains no readable user, host, port, or credential.
- SSH, SCP, and direct VPS Git pushes share the 60-second policy. Every command launches once, keeps its own timeout/exit/output/confirmation contract, and never retries from stderr after launch. If the local control directory cannot be prepared, one direct non-multiplexed command is selected before launch.

## Trace

Outcome / non-goals: Make Hermes scheduling truthful and reproducible; no unrelated production deployment.

Risk and authority: User explicitly authorized cron replacement, gateway convergence, commits, pushes, and remote synchronization for this task. Dirty invalid worktrees remain preserved.

Model(s), effort, and role: Sol/High architecture and research; Terra/High Spec-Kit artifacts and implementation. Desired scheduled implementation route is Terra/Medium; Spark orchestrates and Luna remains read-only.

Workspace / commits / delegates: Local `codex/hermes-public-access`; remote Hermes worktrees reviewed individually. Commit IDs are recorded after shipping.

Evidence: Commands and live results above; final full-suite and post-worker review evidence are appended after execution.

## Final local verification (T038)

- `python3 -m unittest tests.test_hermes tests.test_cli tests.test_mcp` → 138 passed, 1 skipped (14.892s).
- `python3 -m unittest discover -s tests -v` → 510 passed, 2 skipped (19.327s). Expected negative-path diagnostic output and pre-existing subprocess `ResourceWarning` messages were emitted without test failures.
- `./sb selftest` → 510 passed, 2 skipped (24.495s); final status: `selftest: passed`.
- The scheduled T038 worker used the documented `python3 -m unittest` fallback in its worktree. Final parent verification used the current executable `./.cli-venv/bin/python`. No remote mutation or deployment occurred during T038.

## Final independent review and expanded verification (T039 / US6)

- Two independent Terra/High read-only reviews covered specification completeness and implementation correctness/security. All seven material findings are listed above and resolved with regression tests.
- `./.cli-venv/bin/python -m unittest tests.test_remote tests.test_hermes_catalog_integrity tests.test_hermes tests.test_cli tests.test_mcp` passed after the final transport and scheduler changes.
- `./sb selftest` passed after the final changes. Test discovery contains 534 tests; pre-existing subprocess `ResourceWarning` diagnostics remain non-failing.
- `git diff --check` and Python compilation of `sandbox/core/_remote.py`, `sandbox/core/_hermes.py`, and `sandbox/hermes/scheduler.py` passed.

Outcome, residual risk, and follow-up: Local implementation and review are complete. Remaining live work is to ship/synchronize this revision, replace the four observed jobs with the final three-job catalog, execute every recreated job, and retain final sanitized evidence. Unsupported Hermes configuration layouts deliberately block ordinary reconciliation rather than claiming convergence.

Learning delta: Added durable safeguards for positional CLI compatibility, false-success precedence, nonzero monitor failures, one gateway owner, committed desired state, and bounded verified execution.

# Remote Job Runtime Implementation Evidence

This file records commands actually observed during implementation. It must not contain
secrets, credential-bearing SSH targets, or unredacted project output.

## Phase 1: composition boundaries

- Date: 2026-07-18
- Command: `.cli-venv/bin/python -m unittest tests.test_architecture_boundaries tests.test_command_composition tests.test_mcp_composition -v`
- Result: PASS, 27 tests in 0.812 seconds.
- Evidence: new application, jobs, transports, CI, CLI module, and MCP group boundaries
  are explicitly manifested; exact CLI inventory remains 70 commands; exact MCP
  inventory is 20 groups and 83 uniquely owned tools; no new compatibility-facade or
  wildcard consumers were introduced.

## Phase 2: durable foundation

- Date: 2026-07-18
- Command: `.cli-venv/bin/python -m unittest tests.test_job_models tests.test_job_registry tests.test_runtime_config tests.test_target_resolution tests.test_job_process_identity tests.test_job_contracts tests.test_config_descriptors tests.test_project_config tests.test_runtime_contracts tests.test_runtime_adapters tests.test_architecture_boundaries tests.test_mcp_composition -v`
- Result: PASS, 76 tests in 0.686 seconds.
- Command: `mcp/wp-server/.venv/bin/python -m unittest tests.test_server_transport -v`
- Result: PASS, 5 tests in 0.001 seconds.
- Repository fixture evidence: schema version 1, `journal_mode=wal`, foreign keys enabled,
  atomic first-submit/replay behavior (`False`, then `True`), and tables for jobs,
  process identities, heartbeats, leases, output streams/events, metrics, artifacts,
  and compatibility differences.
- Commit/push: `767a678 feat: add durable job foundation` on `codex/remote-job-runtime`.

## US1 checkpoint: detached retained output

- Date: 2026-07-18
- Command: `./sb --help | rg "job-(start|status|output|list)"`
- Result: PASS; all four feature-owned durable job CLI commands are registered.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_output tests.test_output_profiles tests.test_job_output_cursor tests.test_job_supervisor tests.test_job_service tests.test_remote_job_transport tests.test_architecture_boundaries tests.test_command_composition tests.test_mcp_composition -v`
- Result: PASS, 34 tests in 1.056 seconds.
- Command: `mcp/wp-server/.venv/bin/python -m unittest tests.test_server_transport -v`
- Result: PASS, 5 tests in 0.001 seconds.
- Commit/push: `47062bc feat: add durable detached job execution` on `codex/remote-job-runtime`.

## US1 local CLI smoke

- Date: 2026-07-18
- Command: `./sb exec --local --detach --timeout 60 -- .cli-venv/bin/python -c 'print("durable-ok")'`
- Result: returned durable job ID `7ba1f2aa8844e1fb1edb906428bb36a0` without holding the test process's stdio.
- Follow-up: `./sb job-status <id> --json` reported `lifecycle: succeeded`, exit code 0;
  `./sb job-output <id>` returned the retained `durable-ok` output.
- Command: `./sb exec --local --timeout 60 -- .cli-venv/bin/python -c 'print("wait-ok")'`
- Result: PASS; synchronous wait rendered `wait-ok` from the same retained-output store.

## Later durable runtime increments

- `d1268df` adds evidence-based running-job health and identity-verified cancellation.
- `5487a0a` adds workspace leases, deterministic isolated labels, and explicit workspace lifecycle.
- `757ba87` adds strict, no-side-effect remote CI preflight with named `act` compatibility differences.
- `645c738` adds bounded, contained artifact collection and retrieval.
- `0e01dc1` adds linked retries and explicit terminal-job log/artifact cleanup.
- `ce54edd` queues conflicting durable submissions, dispatches them after lease release, and prevents reset/destroy while a workspace lease is active.
- `0f8b231` adds explicit isolated matrix submission. Live local smoke submitted `matrix-a` and `matrix-b`; both completed successfully with distinct supervisor/child PIDs, retained output, and metric records.
- `db0148d` seals stdout, stderr, and combined indexes on terminal finalization and records a terminal combined-output integrity hash.
- Validation: focused job, workspace, CI, architecture, CLI composition, MCP composition, MCP schema, and server transport suites passed. A full local discovery run advanced through the CLI boundary but the existing `instances` test blocked on a live Docker Compose `ps` operation for a pre-existing instance; it was terminated without changing runtime state.

## Remote parent/matrix and remote-first lifecycle increment

- Date: 2026-07-18
- Implementation: durable matrix submission now creates an aggregate parent row,
  links every isolated child, reconciles aggregate lifecycle/counts, supports parent
  cancellation, and uses one exact-tree deployment for remote batches. Remote CI
  expands selected workflow matrix cells into explicit child argv, gates known
  incompatibilities through preflight, retains artifact declarations, and returns a
  `parent_job_id` for control-plane observation. MCP exposes `job_matrix` and the
  remote-aware CI target/workspace/deadline controls.
- Scheduler evidence: lease renewal and expiry reconciliation are transactional;
  workspace protection distinguishes local and remote namespaces while retaining
  serial exclusive behavior.
- Command: `.cli-venv/bin/python -m unittest tests.test_runtime_config tests.test_target_resolution tests.test_job_models tests.test_job_matrix tests.test_remote_ci_jobs tests.test_remote_job_transport tests.test_job_scheduler tests.test_workspace_runtime tests.test_mcp_composition tests.test_architecture_boundaries -v`
- Result: PASS, 50 tests.
- Command: `.cli-venv/bin/python -m unittest tests.test_command_composition tests.test_cli tests.test_ci tests.test_runtime_test_modes tests.test_mcp -v`
- Result: not completed: the existing CLI resolution test entered a live Docker
  Compose `ps` check for a pre-existing instance and was terminated; this is the
  same environment-bound gate recorded above, not a reported feature assertion.
- Remote acceptance: not run; no provisioned disposable remote is available in
  this workspace. The mocked transport tests verify deploy-before-acceptance,
  one-deploy matrix submission, encoded child plans, bounded output controls, and
  remote preflight blocking without remote side effects.

## Recovery and retention increment

- Implementation: `job-reconcile` verifies recorded supervisor boot/PID/start
  identity and records `interrupted` with partial-output evidence on mismatch;
  `job-retention` applies terminal cleanup to output, metrics, and artifacts and
  records the registry cleanup state. Both controls are available through CLI and
  MCP.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_service tests.test_job_scheduler tests.test_job_matrix tests.test_remote_job_transport tests.test_remote_ci_jobs tests.test_mcp tests.test_architecture_boundaries tests.test_command_composition -v`
- Result: PASS, 29 tests.

## Remote workspace copy isolation increment

- Implementation: remote matrix batches still deploy once, then prepare a
  deterministic hash-derived copy per isolated workspace label. Internal remote
  matrix descriptors point each child at its own contained project root; remote
  workspace lifecycle ensure/status/logs use the same derived path.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_job_transport tests.test_remote_ci_jobs tests.test_job_matrix tests.test_remote -v`
- Result: PASS, 101 tests.
- Live remote acceptance remains pending because no disposable provisioned remote
  is available in this workspace.

## Dependency-aware matrix and CI result increment

- Implementation: durable submissions now persist label-based dependency edges,
  failure policy, queue reason, and queue state. Matrix batches are topologically
  ordered, dependent children wait without attaching to prerequisite stdio, fail-fast
  children are cancelled with an explicit `dependency_failed` reason, and continue
  policies remain independently inspectable. Remote CI carries workflow `needs`,
  matrix fan-out dependencies, retry policy, and accepted compatibility differences
  into the remote durable plan. Parent status continues to aggregate independent
  child lifecycle/result records.
- Implementation: compatibility differences are retained in the job registry and
  snapshots; remote artifact listing and bounded artifact retrieval are available
  through the job CLI control plane.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_registry tests.test_job_models tests.test_job_matrix tests.test_remote_ci_jobs tests.test_remote_job_transport tests.test_command_composition tests.test_architecture_boundaries -v`
- Result: PASS, 38 tests; `git diff --check` PASS.
- Remote acceptance remains pending because no disposable provisioned remote is
  available in this workspace; transport tests remain the evidence boundary.

## Recovery, artifacts, and storage-pressure increment

- Implementation: missing or mismatched supervisor identities are reconciled as
  `interrupted`; remote status normalizes control-plane loss to `health: unreachable`
  without inventing a terminal result. Output writes check the configured disk
  reserve and record `storage_pressure` rather than reporting a false success.
- Implementation: artifact collection rejects symlink paths and non-regular objects,
  enforces per-file, total-size, and count bounds, and supports remote bounded list/get
  through the CLI control plane. Retention can reclaim oldest terminal job data when
  explicitly invoked with `--storage-pressure`; active jobs remain protected.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_service tests.test_remote_job_transport tests.test_job_output tests.test_job_artifacts tests.test_command_composition tests.test_architecture_boundaries -v`
- Result: PASS, 29 tests; `git diff --check` PASS.

## Remote MCP observation compatibility increment

- Implementation: durable job MCP tools now accept optional `remote` routing for
  status/list/output/follow/metrics/reconcile/retention/cancel/artifacts/retry and
  cleanup. Existing required parameters and result keys remain unchanged. WordPress
  `run_tests` accepts an explicit local override, named remote/workspace, finite
  timeout, output profile, and uses the configured project remote when available;
  the existing local PHPUnit response shape is preserved.
- Command: `mcp/wp-server/.venv/bin/python -m unittest tests.test_server_transport -v`
- Result: PASS, 5 tests; `.cli-venv/bin/python -m unittest tests.test_mcp tests.test_architecture_boundaries -v` PASS, 12 tests.

## Full pure regression increment

- Command: `.cli-venv/bin/python -m unittest discover -s tests -v`
- Result: PASS, 947 tests, 1 skipped in 79.800 seconds. The skipped test is the
  MCP server transport under the CLI virtualenv because its optional `httpx`
  dependency is not installed there; the same transport suite passes separately
  under `mcp/wp-server/.venv/bin/python` (5 tests).
- The suite includes the existing local CLI, generic Compose, WordPress,
  asynchronous-job, CI/E2E helper, remote-hosting, architecture, and MCP contract
  coverage. Live Docker/remote acceptance remains environment-dependent and is not
  represented as a passing pure test.

## Final local detached-contract smoke

- Command: `./sb job-start --local --project-dir . --timeout 30 --json -- .cli-venv/bin/python -c 'print("remote-runtime-contract-ok")'`
- Result: accepted job `15541e4b0af7d71922303715b14cc64c`; status reached
  `succeeded` with exit code `0`, complete output indexes, retained metrics, and a
  terminal integrity hash. `./sb job-output 15541e4b0af7d71922303715b14cc64c
  --encoding utf8 --json` returned the retained output through the cursor contract.
- This validates the local half of the detach/reconnect contract only; remote
  transport acceptance still requires a provisioned disposable remote.

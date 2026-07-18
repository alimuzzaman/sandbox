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

## Health, reconciliation, and compatibility convergence increment

- Implementation: health classification now records target reachability, supervisor
  and child identity evidence, output/activity/progress/metric ages, and sustained
  inactivity. It distinguishes unreachable, orphaned, process-missing, quiet,
  suspected-stalled, and stuck observations without converting a health warning into
  a terminal success/failure. Startup reconciliation verifies the recorded child
  identity after supervisor verification and records an interrupted result with
  partial evidence when the child is missing or its PID/start identity no longer
  matches.
- Implementation: `LegacyAsyncJobAdapter` and `DurableHermesJobBackend` provide
  explicit injected compatibility boundaries. The historic async-job identifier and
  result contract remain unchanged; Hermes receives the durable lifecycle and
  retained-output fields without importing the new registry or reading job files.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_health tests.test_job_service tests.test_job_process_identity tests.test_hermes_jobs tests.test_asyncjobs tests.test_architecture_boundaries -v`
- Result: PASS, 32 tests.
- Command: `.cli-venv/bin/python -m unittest discover -s tests -v`
- Result: PASS, 952 tests, 1 skipped in 80.577 seconds. The skip is the MCP server
  transport under the CLI virtualenv because `httpx` is not installed there; the
  dedicated `mcp/wp-server/.venv` transport run remains PASS with 5 tests.
- Remote acceptance remains pending because this workspace has no provisioned
  disposable remote. No remote result is represented as a passing acceptance claim.

## Remote workspace execution-path convergence increment

- Implementation: every remote leaf submission now deploys once, prepares the
  deterministic label-derived workspace path, and invokes the co-located `sb`
  control command with that workspace as its project root. Matrix children retain
  their one-deploy fan-out and independent copied roots.
- Implementation: `job-list` supports project/workspace and active-only filters;
  remote workspace reset/destroy checks the remote durable registry for active jobs
  before invoking the lifecycle mutation. This prevents a local scheduler snapshot
  from claiming that a remote workspace is idle.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_job_transport tests.test_job_registry tests.test_job_cli tests.test_command_composition tests.test_architecture_boundaries -v`
- Result: PASS, 26 tests.
- Command: `.cli-venv/bin/python -m unittest discover -s tests -v`
- Result: PASS, 952 tests, 1 skipped in 80.285 seconds. The skip is the MCP server
  transport under the CLI virtualenv because `httpx` is not installed there; the
  dedicated `mcp/wp-server/.venv` transport run remains PASS with 5 tests.
- Remote acceptance remains pending because this workspace has no provisioned
  disposable remote. The transport tests are the evidence boundary for workspace
  path isolation and deploy-before-acceptance ordering.

## Safe-mode CI and compatibility persistence increment

- Implementation: remote CI safe mode now neutralizes deployment, release, publish,
  and external-mutation steps before `act` sees them, returns a non-blocking semantic
  difference, and records that difference on every affected durable child. Known
  unsupported compatibility differences remain blocking until accepted by exact ID.
- Implementation: `JobService.submit()` persists declared compatibility differences
  for both local and co-located remote submissions, so parent/child status retrieval
  retains the evidence after the acceptance response is gone.
- Command: `.cli-venv/bin/python -m unittest tests.test_ci_workflow tests.test_ci tests.test_remote_ci_jobs tests.test_job_service tests.test_job_registry -v`
- Result: PASS, 64 tests.
- Command: `.cli-venv/bin/python -m unittest discover -s tests -v`
- Result: PASS, 953 tests, 1 skipped in 84.550 seconds. The skip is the MCP server
  transport under the CLI virtualenv because `httpx` is not installed there; the
  dedicated `mcp/wp-server/.venv` transport run remains PASS with 5 tests.
- Remote acceptance remains pending because this workspace has no provisioned
  disposable remote. No CI green result is claimed without that environment.

## Remote E2E submission increment

- Implementation: `sb e2e` and MCP `run_e2e` accept `--local`/`--remote` and a
  reusable workspace label. Remote E2E validates the Playwright configuration
  before deployment, then submits the co-located E2E coordinator as a detached
  durable job with an explicit finite deadline. The co-located coordinator retains
  its existing per-worker isolated instance behavior.
- Command: `.cli-venv/bin/python -m unittest -q tests.test_e2e tests.test_cli tests.test_mcp tests.test_architecture_boundaries && git diff --check`
- Result: PASS; no test failures and `git diff --check` clean.
- Remote acceptance remains pending because this workspace has no provisioned
  disposable remote; no Playwright remote result is claimed.

## Live remote Compose-instance execution acceptance

- Date: 2026-07-18
- Environment: disposable Node 20 Compose project with project runtime defaulting
  to the provisioned `scaleway-sandbox` remote and persistent workspace label
  `node-instance-acceptance`.
- Controller correction: remote generic execution now submits one durable
  host-side supervisor solely for deadline/output/status ownership. Its retained
  command first invokes `sb ensure --local` in the deployed workspace and then
  invokes the private direct in-instance execution path. This prevents a
  remote-first project configuration from recursively submitting a remote job and
  prevents the explicit test argv from running on the VPS host.
- Command: `sb exec --workspace node-instance-acceptance --timeout 180 --detach
  -- node -e '<container assertion; print node-container-pass>'`.
- Result: accepted job `ec418476a51bea22a15111507102b18b` reached `succeeded` with
  exit code 0. The exact deployed source identity was
  `sha256:e37370e1a259a618da0726d2f2c49264ad9db212e3710f72057baef9386521da`
  at commit `7d2d9f5243d333bf1622bab7467f7226b273a172`.
- Retained output: the controller recorded the Compose instance as `ready`, then
  recorded `node-container-pass`. The explicit argv asserted `/.dockerenv` before
  printing, proving execution occurred inside the remote Compose container rather
  than in the VPS host environment. Output indexes were complete and the terminal
  result retained a combined-output integrity hash.
- Lifecycle observation: a prior controller attempt held the same persistent
  workspace; the replacement job was safely queued with
  `workspace_or_capacity_busy`, then started and completed after explicit
  cancellation of that stale disposable job. This is recorded as scheduler/lease
  behavior, not as a concurrent-execution success claim.
- Supersedes the earlier “no provisioned disposable remote” limitation only for
  this Node Compose acceptance. WordPress/E2E, matrix, CI, artifact, cleanup, and
  authenticated remote-MCP acceptance remain open tasks.

## Staged remote CLI contract convergence

- Implementation: every `RemoteJobTransport` control operation now constructs its
  command through the injected staged remote `sb` path. Generic MCP
  `instance_exec` submits `runtime-exec`, which uses the same co-located
  in-instance controller as CLI `sb exec`; generic `job_start` remains a separate
  host-job primitive by design. WordPress MCP `run_tests` now uses a named
  transport factory with the same staged-path injection.
- Command: `.cli-venv/bin/python -m unittest -v tests.test_mcp_composition
  tests.test_remote_job_transport tests.test_mcp`.
- Result: PASS, 26 tests. Coverage asserts the staged path for remote
  cancel/metrics/artifacts/artifact-get/retry/cleanup, MCP job tools, generic MCP
  `instance_exec`, and the WordPress remote-test transport factory.
- Command: `mcp/wp-server/.venv/bin/python -m unittest -v
  tests.test_server_transport`.
- Result: PASS, 5 tests. The WordPress transport-factory assertion runs in the
  MCP virtual environment, which owns its optional `httpx` dependency.

## Remote MCP authentication convergence

- Diagnosis: the remote credential file matched the locally registered bearer
  token, but the running service returned HTTP 401. A migration replaced the
  `EnvironmentFile` and used `systemctl enable --now`; systemd does not restart
  an already-active unit in that case, leaving the bearer middleware with the
  prior process environment.
- Implementation: confirmed service migration now enables then explicitly
  restarts the owned unit after replacing credentials. Tailscale service
  migration no longer treats its private control URL as a public-origin identity,
  so an existing owned Tailscale unit can be updated without a marker mismatch.
- Command: `.cli-venv/bin/python -m unittest -v tests.test_remote
  tests.test_hermes tests.test_mcp tests.test_mcp_composition`.
- Result: PASS, 274 tests.
- Live service status: both provisioned remotes reported `ownership: proven`,
  `listener_expected: true`, and `authenticated: true` after confirmed migration.
- Live MCP session: an authenticated Streamable HTTP client called `job_status`
  and `job_output` against `scaleway-sandbox` for job
  `ec418476a51bea22a15111507102b18b`; status reported `succeeded` and retained
  output contained `node-container-pass`. The verification printed only boolean
  outcomes and did not expose credentials or full headers.

## T153: live remote WordPress integration and workspace lifecycle

- Fixture: the disposable external repository
  `/Users/alim/wp-remote-integration-proof-20260719`, committed at
  `32220075df2358190a8b1162ce5297b97e29c87c`. It uses the provisioned
  `scaleway-sandbox` remote and workspace `wp-integration-acceptance`.
- Retained failure evidence: job `fa601738658a06181051eb64cffc6347` failed
  before test execution because `sb` was not on the remote PATH. It is retained
  as transport-failure evidence only; no PHPUnit output is claimed for it.
- Command: `sb test --workspace wp-integration-acceptance --timeout 1200
  integration --json`, then bounded `job-output` and `job-status` reads.
- Result: job `1b069dd785a6f9c04f144b1aef4ffe4e` reached `succeeded` with exit
  code `0`, complete stdout/stderr/combined retained indexes, and combined-output
  integrity SHA-256 `82b7bd8a701575a60f6ca53428a6dcf2258c99871b27fb7a5421cd47e81cbf56`.
  Its controller ensured the isolated remote WordPress instance before invoking
  the integration suite; retained output records the integration install and
  test run. The accepted source identity references the committed fixture above.
- Lifecycle commands, run from the fixture directory: `workspace create`,
  `workspace status`, `workspace reset`, and `workspace destroy`, each with
  `--remote scaleway-sandbox --workspace wp-integration-acceptance --json`.
  All returned `ok: true`; reset returned `reset: true` and destroy returned
  `destroyed: true`. The remote workspace registry path was separate from the
  local workstation state.
- Follow-up implementation evidence: nested remote test commands now use the
  staged CLI path, put options before the positional test mode, ensure the
  co-located instance, and consume trailing `--json` instead of forwarding it
  to PHPUnit. Remote workspace lifecycle uses the same slug-safe hyphenated
  path as durable job submissions and does not recreate a workspace during
  status/reset/destroy.

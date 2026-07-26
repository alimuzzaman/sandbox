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

## T154: live remote CI matrix, artifact, retry, and cleanup acceptance

- Fixture: the same disposable WordPress repository, at fixture commit
  `23e51c809a6117f3d88e2cead75c2ea96093478d`, with a compatible workflow
  containing two `build` matrix cells, `verify` depending on `build`, one
  `actions/upload-artifact@v4` step per build, and a publish-shaped command
  that safe mode neutralizes. Its incompatible companion declares
  `windows-latest` and preflight returned `ok: false` with blocking difference
  `act.non-linux-runner`; no incompatible workflow was executed.
- Compatible preflight returned `ok: true`, three cells, the `verify -> build`
  dependency edge, and the non-blocking `safe-mode:verify:1` difference.
- Final remote parent `a543676794ec972d2876bf536b3d6c11` reached `succeeded`
  with aggregate `children: 3`, `passed: 3`, `failed: 0`. Build children
  `7d763cc4adb9bad27a370c6e83d275b6` and
  `ec3ffa58b0512bd91d179eb435d437ca` each reached exit code 0 in independent
  isolated workspaces. The initially queued dependency child
  `16c9e07106a4b58de5f8f361e27e1ac1` transitioned to running only after both
  build children succeeded, then completed exit code 0. Its retained output
  integrity hash is
  `c8e7bcb10e0e3d0c3ecf3c684eea2dc8540d2671443e02a8971b780920364380`.
- Safe-mode retained output explicitly reports that `actions/upload-artifact`
  was collected by Sandbox after the job, and the publish-shaped verify command
  was neutralized. The build artifacts were retained as regular `report.txt`
  files; artifact `26d5cb0d1d98c1339f1322de` retrieved from the first build was
  ten bytes and base64-decoded to `build-one\n`.
- Retry and cleanup: failed earlier build
  `11268efb676809fb83146a95fe77cebf` was retained as failure evidence, then
  `job-retry` created attempt-2 job `b2b9964a58b424f984be1d28655015ad` with
  request ID `t154-retry-proof-20260719`; it reached `succeeded`, exit code 0.
  `job-cleanup 11268... --logs --artifacts --metrics` returned
  `cleanup_state: completed` and removed logs and artifacts. Successful
  parent/child evidence was retrieved before this cleanup.
- Live fixes exercised by this acceptance: remote provisioning installs `act`;
  concurrent host port allocation is serialized; `act` invocation is
  host-serialized while workspaces remain isolated; GitHub upload-artifact is
  replaced with durable Sandbox collection using a bound workspace; and a CI
  leaf is not misclassified as a matrix parent during dependency reconciliation.

## T143 Phase 1: local implementation proof (task remains open)

- Local-only proof date: 2026-07-22. No remote runtime, remote cleanup, deployment,
  account mutation, or destructive operation was run for this increment.
- Focused independent-review regression suite passed 53 tests, covering aggregate retry
  rejection, frozen membership/retry attempts, cleanup-after-terminal behavior, metrics
  cleanup, strict artifact bounds/races, snapshot replay/argv, artifact preflight, and MCP
  CI response docs:
  `.cli-venv/bin/python -m unittest tests.test_job_retry tests.test_job_matrix
  tests.test_job_metrics tests.test_job_artifacts tests.test_job_cli
  tests.test_job_registry tests.test_ci_workflow tests.test_remote_ci_jobs -v`.
- Compatibility suite passed 168 tests, including job models/contracts, remote transport
  plan compatibility, CI catalog/workflow behavior, public MCP schema/tool count, command
  composition, and architecture boundaries:
  `.cli-venv/bin/python -m unittest tests.test_job_models tests.test_job_contracts
  tests.test_job_registry tests.test_job_service tests.test_job_retry
  tests.test_job_matrix tests.test_job_metrics tests.test_job_artifacts
  tests.test_job_cli tests.test_remote_job_transport tests.test_remote_ci_jobs
  tests.test_ci_compatibility tests.test_ci_workflow tests.test_ci tests.test_mcp
  tests.test_mcp_composition tests.test_command_composition
  tests.test_architecture_boundaries -v`.
- Live local `./sb` smoke submitted one durable CI parent/child with a 2,500,000-byte
  file artifact and one directory artifact. Child and retry both reached `succeeded`;
  parent returned normalized `result.conclusion=succeeded` and one additive retry
  attempt. `job-artifact-get --max-bytes 524288 --output-file` reconstructed the file
  across bounded pages and `cmp` matched the source. Artifact kinds were `file` and
  `archive`; cleanup completed and both retained artifact rows changed to `expired`.
  Probe artifacts and scratch files were then removed through `./sb job-cleanup` and
  the gitignored `tmp/` directory.
- This entry records local implementation proof only. T143 remains unchecked pending any
  separately required disposable remote acceptance; `tasks.md` was not changed.

## T143 Phase 2: remote acceptance gate (not satisfied)

- A 2026-07-23 safe-mode preflight against the reachable, authenticated
  `scaleway-sandbox` service accepted a two-cell build plus dependent verify workflow,
  its literal file/directory artifact declarations, and the recorded
  `safe-mode:verify:1` neutralization. The disposable submission itself was accepted
  as a three-child durable parent and its retained, bounded stderr was retrieved through
  the remote CLI.
- This is not feature acceptance: the fixture was placed under gitignored `tmp/`, so it
  was intentionally absent from the staged remote working tree and both build children
  failed with `workflow file not found`; the dependent child was then cancelled by the
  existing fail-fast policy. All four disposable retained job records were explicitly
  cleaned up through `./sb job-cleanup --remote ... --logs --artifacts --metrics`.
- The remote invocation used the installed `/home/alim/sandbox/sb-src/sb`, whose parent
  result still has the pre-T143 aggregate shape. The current uncommitted T143 runtime is
  therefore not installed on that host. A successful disposable remote acceptance remains
  blocked until an authorized remote runtime update makes the tested revision available;
  no remote deployment or service update was performed here.

## T155: completion reconciliation and final regression

- Reconciliation date: 2026-07-19. T153 and T154 are checked only because their
  retained remote results above were observed. T155 is checked because this
  reconciliation and regression were performed; it does not imply completion of
  any older unchecked implementation or measurement task.
- Full CLI regression: `.cli-venv/bin/python -m unittest discover -s tests -q`
  completed successfully with exit code 0. The discovered suite contains 969
  tests. Dedicated MCP transport regression
  `mcp/wp-server/.venv/bin/python -m unittest tests.test_server_transport -v`
  passed 5 tests. `bash -n scripts/install-remote.sh` and `git diff --check`
  also passed.
- Current remote evidence is limited to observations actually made: authenticated
  `scaleway-sandbox` service migration; Node Compose execution; WordPress
  integration plus create/status/reset/destroy lifecycle; compatible and
  blocked remote CI preflight; the three-child compatible CI parent; retained
  artifact retrieval; retry; and cleanup. No unobserved test output is claimed
  for the early `sb`-PATH failure or other retained failed attempts.
- Remaining unchecked rows intentionally remain open where their requested
  artifact does not exist or was not observed, including the broad legacy test
  inventories and implementation rows (T031-T140), remote workspace mutable
  rerun/database proof (T142), CI/MCP parity beyond the CLI evidence (T143),
  host-restart reconciliation (T144), durable async/Hermes routing parity
  (T146), remote E2E and disconnect fixtures (T147), and the broader Node/PHP/
  timeout/security/quickstart acceptance set (T148 and T137-T140).
  These are recorded as remaining gates rather than silently checked.
- Worktree integrity at reconciliation: `git diff --check` was clean; no files
  under `runtime/wp/` or `vendor/` were changed. The only untracked path is the
  user-owned `specs/033-agent-aware-remote-sync/`, preserved without edits.

## Legacy async and Hermes compatibility increment

- Date: 2026-07-26. `AsyncJobCompatibilityRouter` preserves the historic
  sixteen-hex async-job status/cancel envelope and maps thirty-two-hex durable
  jobs to the same bounded `stdout`, `bytes_read`, and `truncated` fields.
  Durable cancellation reports its real lifecycle rather than inventing a
  terminal result.
- Command: `.cli-venv/bin/python -m unittest tests.test_asyncjob_compatibility
  tests.test_hermes_job_compatibility tests.test_asyncjobs tests.test_hermes_jobs -v`.
  Result: PASS, 18 tests.
- Live local check: `./sb async-job 3546174b1b12505da3d41f27bd6b49c6 --json`
  returned the retained `spec-audit-live-ok` output and terminal exit code `0`
  through the legacy-compatible response shape.

## T129: remote workflow boundary documentation

- Date: 2026-07-26. Added the missing
  `docs/remote-hosting-implementation.md` and linked it from
  `docs/remote-hosting.md`. Both documents now distinguish the one-way source
  deploy, durable remote development jobs, co-located remote MCP control plane,
  and separately confirmation-gated production hosting workflow.
- Verification: `git diff --check` passed, and a targeted content check found
  each of the four workflow headings and their boundary statements in the new
  implementation guide.

## Remote-first guidance increment

- Date: 2026-07-26. The generated CLI guide now explains configured-remote
  execution, the deliberate `--local` override, workspace/deadline inputs, and
  retained job recovery. MCP baseline and context guidance now direct live
  remote work to the co-located MCP server with durable `job_status` and
  `job_output` recovery.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_first_guidance
  tests.test_remote_first_cli tests.test_cli tests.test_mcp_composition -v`.
  Result: PASS.

## Remote-first CLI/MCP target parity

- Date: 2026-07-26. Added dedicated coverage for configured remote target
  selection in CLI and MCP job submission, bounded remote status/output input
  forwarding, actionable unknown-target errors, and the explicit-local override
  under a remote-first configuration.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_first_cli
  tests.test_remote_first_mcp tests.test_remote_first_guidance
  tests.test_local_override_compatibility -v`. Result: PASS, 10 tests.

## T055: local detached resume smoke

- Date: 2026-07-26. Submitted the prescribed detached local `exec` smoke.
  Job `330eb09401d43802a2d33fa5b14c12f3` accepted before completion, then
  reached `lifecycle=succeeded`, `exit_code=0`, and `health=terminal`.
- The resumed `job-output` read returned the complete retained output
  `start\ndone\n`, two ordered stdout events, a cursor, and `has_more=false`.
  Its submission records the explicit 60-second deadline and local target.

## Security review (local/static scope)

- Date: 2026-07-26. Added `security-review.md` covering redaction, bounded
  output/artifact retrieval, artifact containment, process identity, disk reserve,
  and remote control construction. The reviewed suite passed 34 tests.
- The review records one constrained internal Docker workspace-recovery fallback;
  it is not exposed as a raw Docker CLI/MCP interface, but its live remote boundary
  still needs the disposable acceptance required by T137/T138.

## Acceptance fixture increment

- Date: 2026-07-26. Added environment-gated acceptance fixtures for durable local
  Node/PHP execution, WordPress integration setup, disconnect/resume, matrix labels,
  workspace lifecycle, artifact retrieval, deadline handling, and compatible/
  incompatible/safe-mode CI preflight. These fixtures are not substituted for a
  credentialed VPS run.

## T136: controlled local durability measurement

- Date: 2026-07-26. A temporary isolated runtime submitted 100 detached Python
  jobs concurrently. All 100 reached `succeeded`; each output was read once and
  resumed once by opaque cursor with zero duplicate event sequences. The largest
  observed local `status` read was 0.127 ms.
- The same run collected and bounded-read an 8-byte artifact, classified synthetic
  terminal and unreachable snapshots as `terminal` and `unreachable`, respectively,
  and observed a deadline case as `timed_out` plus a verified process-group cancellation
  case as `cancelled`.
- During the initial stress run, a fast-child race produced a false supervisor error:
  the process could exit between identity capture and `os.getpgid`. The supervisor now
  stores the launch-established PID as its new-session process-group ID and tolerates
  an exit race while signaling a deadline. Targeted supervisor and acceptance fixtures
  pass after that correction.

## Output selector contract increment

- Date: 2026-07-26. Wired retained-output `offset`, `tail-bytes`, `lines`, and
  RFC 3339/Unix-seconds `since` selection through the local store, CLI parser,
  MCP job tool, and remote control transport. Selectors are mutually exclusive
  and validate their bounds before reads. Combined-stream byte offsets now apply
  across the rendered retained event sequence.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_output_cursor
  tests.test_remote_job_transport tests.test_remote_first_mcp tests.test_job_cli -v`.
  Result: PASS, 26 tests.

## Health classification table

- Date: 2026-07-26. Added a single table-driven test for every public health
  state: active, quiet, suspected-stalled, stuck, supervisor-unresponsive,
  orphaned, process-missing, unreachable, unknown, and terminal.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_health -v`.
  Result: PASS, 4 tests.

## Portable metric sampling increment

- Date: 2026-07-26. Metrics now collect best-effort `/proc` CPU/RSS/I/O/state,
  process-count and disk-free evidence, use a bounded `ps` fallback when `/proc`
  data is unavailable, and persist a stable movement digest.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_metrics -v`.
  Result: PASS, 4 tests.

## Cancellation race and US2 verification increment

- Date: 2026-07-26. Added coverage for graceful and force cancellation,
  identity mismatch rejection before signaling, process-group descendant cleanup,
  and parent-to-child matrix cancellation. A broader US2 run exposed a real
  ordering race where a signaled child could finalize as failed before the
  cancellation intent was persisted. Cancellation now verifies the owned
  identity first, persists `cancelling`, and then signals, so the supervisor
  classifies a concurrently reaped child as cancelled.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_health
  tests.test_job_metrics tests.test_job_cancellation tests.test_job_service
  tests.test_job_artifacts tests.test_job_contracts -v`. Result: PASS, 36 tests.
- The task's prior `tests.test_job_reconciliation` and
  `tests.test_job_observation_contracts` module names no longer existed; their
  maintained coverage resides in `tests.test_job_service` and
  `tests.test_job_contracts`, respectively.

## MCP bounded progress increment

- Date: 2026-07-26. `job_follow` now accepts a capped observation window and
  an optional client progress token. It returns request-scoped monotonic
  summaries, waits at least two seconds before a subsequent poll, and does not
  persist notification state or imply child-process completion progress.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_mcp
  tests.test_mcp_composition tests.test_remote_first_mcp -v`. Result: PASS,
  20 tests.

## US1 regression gate

- Date: 2026-07-26. The complete retained-output, supervisor, service,
  remote transport, CLI, MCP, and compatibility gate passed after replacing
  the stale missing `tests.test_job_mcp` reference with its maintained suite.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_output
  tests.test_output_profiles tests.test_job_output_cursor tests.test_job_supervisor
  tests.test_job_service tests.test_remote_job_transport tests.test_job_cli
  tests.test_job_mcp tests.test_asyncjobs tests.test_runtime_transport
  tests.test_mcp_composition -v`. Result: PASS, 67 tests.

## Declarative output profile increment

- Date: 2026-07-26. Retained output reads now apply built-in or injected
  declarative presentation profiles at read time; execution and stored bytes
  remain unchanged. The policy supports full, smart, errors, sampled, quiet,
  and named custom definitions with literal include/exclude matching, context,
  line/event/time sampling, deduplication, timestamp/stream prefixes,
  heartbeat metadata, and byte/event budgets. CLI and MCP reads forward the
  requested profile to local and remote job services.
- Command: `.cli-venv/bin/python -m unittest tests.test_output_profiles
  tests.test_job_output tests.test_job_output_cursor tests.test_job_cli
  tests.test_job_mcp tests.test_remote_job_transport tests.test_remote_first_mcp
  tests.test_mcp_composition -v`. Result: PASS, 51 tests.

## On-read reconciliation increment

- Date: 2026-07-26. A status read now converts a running job with stale
  supervisor heartbeat, orphaned identity, or missing child process into an
  explicit `interrupted` outcome with retained-output completeness marked
  partial. `cancelling` jobs remain under supervisor ownership so a concurrent
  read cannot replace their verified cancellation outcome.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_service
  tests.test_job_health tests.test_job_cancellation tests.test_job_process_identity
  -v`. Result: PASS, 23 tests.

## Job target/deadline presentation increment

- Date: 2026-07-26. Durable job acceptance now returns a structured deadline
  `{seconds, source}` alongside target/workspace metadata. Human `job-start`
  and `job-status` output show resolved target, workspace, deadline, and its
  source; JSON preserves those fields for callers.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_service
  tests.test_job_cli tests.test_remote_job_transport tests.test_remote_first_cli
  tests.test_remote_first_mcp -v`. Result: PASS, 35 tests.

## Durable named output-profile increment

- Date: 2026-07-26. A selected custom output-profile definition is now copied
  from resolved project runtime policy into the bounded job submission snapshot.
  Later reads and retries resolve that exact declarative definition, rather
  than relying on mutable current configuration. The snapshot rejects unknown
  profile keys through the same validated output-profile model.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_first_cli
  tests.test_job_output tests.test_job_retry tests.test_job_models
  tests.test_runtime_config -v`. Result: PASS, 23 tests.

## Segmented retained-output increment

- Date: 2026-07-26. New stdout/stderr retention uses fixed-size numbered,
  owner-only segment files while preserving one logical byte offset stream and
  the existing append-only combined event order. Old single-file streams remain
  readable and appendable without migration. Stream indexes record segment
  count, final-segment size, and a digest over the logical concatenated bytes.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_output
  tests.test_job_output_cursor tests.test_job_supervisor tests.test_job_service
  -v`. Result: PASS, 22 tests.

## Supervisor progress and stall-policy increment

- Date: 2026-07-26. The supervisor now carries declared stall policy into its
  descriptor, records metric movement and retained-output progress in durable
  heartbeats, preserves prior heartbeat observations across updates, and marks
  a sustained stall as warn-only evidence by default. Jobs with
  `cancel_on_stall=true` transition through verified process-group cancellation
  to `cancelled_on_stall`; ordinary deadline behavior remains distinct.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_registry
  tests.test_job_supervisor tests.test_job_service tests.test_job_health
  tests.test_job_cancellation -v`. Result: PASS, 35 tests.

## Service acceptance failure-path increment

- Date: 2026-07-26. Added coverage proving a request is durably accepted before
  launch and that a launcher exception records `failed` with
  `supervisor_launch_failed`, never a misleading running or successful state.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_service
  tests.test_job_supervisor tests.test_job_models -v`. Result: PASS, 20 tests.

## Supervisor deadline and descendant verification increment

- Date: 2026-07-26. Added detached-supervisor coverage for non-zero child exit
  propagation and a finite deadline that terminates a background descendant in
  the owned process group before recording `timed_out` / `deadline_exceeded`.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_supervisor
  tests.test_job_cancellation tests.test_job_output -v`. Result: PASS, 18 tests.

## Reconciliation evidence matrix increment

- Date: 2026-07-26. Added the dedicated reconciliation suite for host-boot
  mismatch, stale heartbeat, missing child finalization evidence, on-read
  orphaned identity, and terminal-row preservation. Each unsafe active state
  becomes an explicit interruption; reconciliation never invents success.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_reconciliation
  tests.test_job_service tests.test_job_health -v`. Result: PASS, 19 tests.

## Matrix workspace-label verification increment

- Date: 2026-07-26. Added concurrent label coverage across canonical cell maps,
  projects, parent IDs, and retry attempts. Labels remain deterministic,
  collision-free for the exercised matrix, and bounded to 21 characters.
- Command: `.cli-venv/bin/python -m unittest tests.test_workspace_labels
  tests.test_job_scheduler tests.test_job_matrix -v`. Result: PASS, 13 tests.

## Persistent workspace lifecycle increment

- Date: 2026-07-26. Local workspace creation now truthfully reports idempotent
  reuse. Tests cover create/list/status/reset/destroy, active lease rejection,
  failure retention until explicit lifecycle action, and remote namespace action
  delegation through the workspace control boundary.
- Command: `.cli-venv/bin/python -m unittest tests.test_workspace_runtime
  tests.test_job_scheduler tests.test_workspace_labels -v`. Result: PASS,
  9 tests.

## Workspace concurrency verification increment

- Date: 2026-07-26. Added local lease coverage for serial shared-workspace
  behavior, explicit shared-safe concurrency, immediate busy guidance, and
  independent isolated-job storage paths.
- Command: `.cli-venv/bin/python -m unittest tests.test_workspace_concurrency
  tests.test_job_scheduler tests.test_workspace_runtime -v`. Result: PASS,
  10 tests.

## Artifact expiry and matrix-policy coverage increment

- Date: 2026-07-26. Added direct proof that a collected artifact remains
  retrievable until scoped cleanup marks it expired, after which retrieval is
  rejected. Expanded matrix coverage proves a failed prerequisite dispatches a
  `continue` child and independent cells queue behind host capacity before
  dispatching after release.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_artifacts
  tests.test_job_matrix tests.test_job_scheduler -v`. Result: PASS, 21 tests.

## Workspace confirmation-contract increment

- Date: 2026-07-26. Workspace reset and destroy now require an explicit CLI
  `--confirm` or MCP `confirm=true` before invoking lifecycle controls. The
  contract suite covers local lifecycle request forwarding, confirmation
  rejection, confirmed mutation dispatch, and isolated matrix submission.
- Command: `.cli-venv/bin/python -m unittest tests.test_workspace_contracts
  tests.test_workspace_runtime tests.test_job_matrix -v`. Result: PASS, 17 tests.

## Local durable-runtime acceptance repair

- Date: 2026-07-26. Corrected the reusable-workspace acceptance assertion to
  reflect the documented idempotent create contract: the first create reports
  `created: true`; the replay reports `created: false`. The full local suite
  passes; the disposable WordPress acceptance remains deliberately gated.
- Command: `.cli-venv/bin/python -m unittest discover -s tests -v`.
  Result: PASS (one gated WordPress acceptance skip).

## Explicit retained-data cleanup increment

- Date: 2026-07-26. CLI `job-cleanup` now requires `--yes` (with `--confirm`
  accepted as an alias), MCP requires `confirm=true`, and remote cleanup
  control transmits the confirmation flag. Observation contracts cover status,
  metrics, artifact paging, cancellation, retry, and confirmed cleanup.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_observation_contracts
  tests.test_job_mcp tests.test_remote_job_transport -v`. Result: PASS,
  16 tests.

## Retained-output integrity coverage increment

- Date: 2026-07-26. Added direct retained-output proof for partial-line
  redaction across chunks, invalid/control-byte-safe presentation, per-stream
  completion, combined event ordering, segmentation, and the persisted
  combined SHA-256 integrity value.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_output
  tests.test_job_output_cursor -v`. Result: PASS, 11 tests.

## Supervisor output-failure increment

- Date: 2026-07-26. Added direct supervisor coverage for a durable output-store
  write failure after child launch. The supervisor atomically records the
  terminal `failed` lifecycle, `output_storage_failed` reason, and
  `write_failed` completeness rather than reporting a child success.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_supervisor -v`.
  Result: PASS, 6 tests.

## Remote transport capability increment

- Date: 2026-07-26. The direct remote job transport now rejects a provisioned
  remote that explicitly lacks `job.exec` before deployment. Transport tests
  also pin exact-tree deployment ordering, bounded JSON-only SSH control, and
  retry request-ID forwarding.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_job_transport
  tests.test_remote_first_cli tests.test_remote_first_mcp -v`. Result: PASS,
  18 tests.

## US3 local orchestration regression checkpoint

- Date: 2026-07-26. Scheduler leases, deterministic workspace labels,
  idempotent lifecycle, shared/isolated concurrency, parent/child matrices,
  fanout, E2E helpers, and runtime test-mode selection all pass together.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_scheduler
  tests.test_workspace_labels tests.test_workspace_runtime
  tests.test_workspace_concurrency tests.test_job_matrix
  tests.test_workspace_contracts tests.test_fanout tests.test_e2e
  tests.test_runtime_test_modes -v`. Result: PASS, 49 tests.

## US4 local CI regression checkpoint

- Date: 2026-07-26. The maintained CI compatibility, workflow/safe-mode,
  remote parent-child/matrix/MCP contract, and core `act` behavior suites pass
  together. The originally listed `test_ci_safe_mode` and `test_ci_contracts`
  module names are obsolete; their coverage lives in the workflow and remote
  CI suites.
- Command: `.cli-venv/bin/python -m unittest tests.test_ci_compatibility
  tests.test_ci_workflow tests.test_remote_ci_jobs tests.test_ci -v`.
  Result: PASS, 66 tests.

## Detached exec context increment

- Date: 2026-07-26. Human-mode detached `sb exec` responses now identify the
  resolved target, workspace, effective deadline, and deadline source instead
  of returning an opaque job ID alone. JSON remains the unchanged accepted-job
  envelope.
- Command: `.cli-venv/bin/python -m unittest tests.test_remote_first_cli
  tests.test_job_cli -v`. Result: PASS, 11 tests.

## Explicit job-start CLI contract increment

- Date: 2026-07-26. Added direct CLI coverage for explicit argv parsing,
  target/workspace/timeout/output-profile/request-ID propagation, durable
  detached acceptance context, and missing-command rejection. Existing command
  tests retain output paging, exit/error, and artifact download behavior.
- Command: `.cli-venv/bin/python -m unittest tests.test_job_cli -v`.
  Result: PASS, 10 tests.

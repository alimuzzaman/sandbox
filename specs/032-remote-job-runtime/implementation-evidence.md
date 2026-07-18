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

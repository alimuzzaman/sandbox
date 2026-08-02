# `sb test` unit mode tries PHPUnit in this workspace without a suite

- Repro date: 2026-08-02
- Commands run: `./sb test`, `./sb test unit`, `./sb test --remote scaleway-sandbox --timeout 1200 --json unit`.
- Immediate failure on local invocation: `./sb test` and `./sb test --remote ... --timeout 1200 unit` both forward `unit`-level options to PHPUnit as passthrough when options are placed after positional `mode`, producing `Unknown option "--remote/--timeout"` and then `Usage: phpunit ...`.
- In one successful remote submission (`job_id: caee7f5bfdacefc33a01c916b9b2893d`), `job-status` showed the actual child command as:
  `... /home/alim/sandbox/sb-src/sb ensure --local ... && /home/alim/sandbox/sb-src/sb test --local --project-dir . unit`.
- The remote child then runs `/Users/alim/sandbox/runtime/test-tools/phpunit.phar` with no config/target file, so PHPUnit prints usage and exits code 2 (`phpunit exit 2`).
- There is no `phpunit.xml` at repo root, but this workspace is Python-test-oriented (`tests/` contains many `test_*.py`).
- Gap: unit-mode execution path for this repo appears misconfigured for PHP-only PHPUnit harness expectations and can produce false-negative red failures before discovering actual runtime bugs.
- Workaround/fix direction: add an explicit test passthrough selector for this project type (or route non-WP test suites to the Python harness) and/or require a valid PHPUnit test argument/config when `unit` mode is selected.

## Additional CLI evidence (2026-08-02)

- Running remote tests with correct argument order works:
  - `./sb test --remote scaleway-sandbox --timeout 1200 unit -- --version` succeeded.
  - Job: `af83372d24115c30f91e2a2b0528c32f` (lifecycle `succeeded`).
- Running remote tests with `unit -- --help` also succeeds and returns PHPUnit help (exit 0).
  - Job: `66ee7f6c0bac7d46e5e7fd8f02924514` (lifecycle `succeeded`).
- Key root cause remains: if `unit` is invoked with no PHPUnit target/config and no `--` passthrough args, command exits with `phpunit exit 2` usage output.
- This is reproducible both local and within remote child command, so test invocation strategy should enforce explicit pass-through targets for `unit` mode in Python-heavy projects.

## CLI cleanup note

- `./sb job-retention --json` executed and reported cleanup across a very large set of historical jobs (removed `logs`/`metrics` from many IDs).
- This appears to execute immediate retention policy with default `--retention-days 7`.
- If historical log retention is needed for this repo, avoid running broad retention without explicit criteria.

## Remote test sweep findings (2026-08-02)

- Confirmed run command:
  - `./sb job-start --remote scaleway-sandbox --timeout 1800 -- ./sb test --project-dir . unit`
  - `job-id: d4cfea9d166ea36172bc950b2874a8b0b`
  - Status: failed (terminal); job output shows PHPUnit help/usage + `phpunit exit 2`.
  - Same behavior with explicit `-- --stop-on-failure` and `-- --list-suites` if no config/path is provided.

- Confirmed runnable variant:
  - `./sb job-start --remote scaleway-sandbox --timeout 1800 -- ./sb test --project-dir . unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist --list-suites`
  - `job-id: ad9e572a4d26346d22d66b90a453f58d`
  - Status: succeeded, output shows suite discovery path available.

- Confirmed command that passes but hides tests:
  - `./sb job-start --remote scaleway-sandbox --timeout 1800 -- ./sb test --project-dir . unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist`
  - `job-id: b12d303aa45b4a893db65ff5d26bc64c`
  - Output: `No tests executed!` plus cache warning below.
  - Indicates default run target is effectively empty unless test directory is explicitly supplied.

- Confirmed warning that likely blocks cache hygiene/parallelism:
  - `file_put_contents(/home/alim/sandbox/deploy-src/sandbox-workspace-37a8eec1ce1968/tests/fixtures/pure-unit/.phpunit.result.cache): Failed to open stream: Permission denied`
  - Seen in explicit config runs (`ad9e...`, `b12d...`, `0df3...`).

- Confirmed explicit test-dir target runs a passing test:
  - `./sb job-start --remote scaleway-sandbox --timeout 1800 -- ./sb test --project-dir . unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist tests/fixtures/pure-unit/tests`
  - `job-id: 0df39813a9897b4f683dc196fa3a549c`
  - Output: `OK (1 test, 1 assertion)`.

- Integration mode smoke checks:
  - `./sb job-start --remote scaleway-sandbox --timeout 900 -- ./sb test --project-dir . integration -- --version`
  - `job-id: 786910a612db6eea266955bf7e507c7e`
  - Status: succeeded; harness provisioned and phpunit `--version` executed.
  - Another attempt with `integration -- --list-suites` produced a job that became `unknown (unreachable)` on status polling (`60be2481b8c3dc24855af15d6acebc9e`) before logs could be retrieved.

### Gaps / action items for next agent

1. Add stable `unit` test invocation defaults for this repo so bare `unit` mode does not call PHPUnit without suite path/config.
2. Investigate why `.phpunit.result.cache` is not writable in workspace mount and decide whether to write cache into temp/var directory.
3. Clarify behavior of `integration -- --list-suites` status transitions, since one job became unreachable; verify whether this is expected cleanup/race or a reporting bug.
# 2026-08-02 remote test pass

- Ran `./sb test --project-dir . --remote scaleway-sandbox unit -- --list-suites`.
  - Result: command submitted and completed as remote job `518a30fd38059d90fc160a0a29f24a26` with `exit_code: 1`.
  - Remote logs confirm phpunit invocation was `... wpcli /phpunit.phar --list-suites` and returned `error: tests failed (phpunit exit 2)`.
  - This indicates unit mode still attempts to run default `phpunit.xml` path and cannot list suites without explicit config.

- Ran `./sb test --project-dir . --remote scaleway-sandbox integration -- --list-suites`.
  - Returned immediate client-side Python traceback:
    - `RuntimeError: could not reset the VPS working tree ... .git/index.lock exists`.
    - `Another git process seems to be running`.
  - No remote job ID created for this invocation.
  - This appears to be a remote deploy path lock contention, not a pure test failure.

- Ran `./sb test --project-dir . --remote scaleway-sandbox unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist --list-suites`.
  - Result: remote job `e7b228d0b1ab4eddea01097bae2b519b` succeeded.
  - Output includes `Available test suite(s): ...` and `✓ tests passed`.

- Ran `./sb job-output --remote scaleway-sandbox 04a4b84224e21d94c1ce6479ae09aacf` and `60be2481b8c3dc24855af15d6acebc9a` to inspect existing remote failures.
  - Both show phpunit output/help mode and no integration suite list output, confirming `integration -- --list-suites` path still fails with `phpunit exit 2` when no suite/list config is supplied.

- Current gap summary:
  1) Inconsistent argument behavior: `unit -- --list-suites` fails remotely, but passes when `-c tests/fixtures/pure-unit/phpunit.xml.dist` is supplied.
  2) Remote test submission can fail before job dispatch due stale `.git/index.lock` in deploy source (`/home/alim/sandbox/deploy-src/sandbox/.git/index.lock`) from concurrent git process.
  3) Existing terminal jobs (`518a30fd...`, `04a4b8...`, `60be24...`) are immutable in cancellation attempts (no `job-cancel` needed in this pass), but still useful for triage via `job-output`.
# 2026-08-02 remote test sweep continuation

- Ran additional CLI surface commands:
  - `./sb guide --project-dir .` confirms configured remote-by-default behavior and lists active optional MCP usage.
  - `./sb --help` enumerated command set and confirms `remote`/`resources`/`mcp` options available.
  - `./sb mcp --help` confirms transport options and flags (stdio default, streamable-http for remote).
  - `./sb workspace list --remote scaleway-sandbox --json` returns `{ "workspaces": [] }`.
- Continued remote/local test mode probing:
  - `./sb test --project-dir . --remote scaleway-sandbox integration -- --list-groups` **still fails before submit** with `.git/index.lock` error.
  - `./sb test --project-dir . --local unit -- --list-groups` runs and fails with PHPUnit usage/exit2 (`--list-groups` on pure unit harness without config).
  - `./sb test --project-dir . --local integration -- --list-groups` runs and fails similarly with PHPUnit usage/exit2 before any test selection.
  - New remote job `dd10fdfe2ccb2a662ef73b5590f353a3` from `unit -- --list-groups` failed (`exit_nonzero`) with phpunit usage output captured via `job-output`.
- Remote job control/telemetry behaviors observed:
  - `./sb job-cancel --remote scaleway-sandbox --force --json 518a30fd...` => `error: remote job control operation failed` (terminal job cancellation not allowed/ignored).
  - `./sb job-metrics --json --remote scaleway-sandbox dd10...` returns a few samples and confirms process state `S` and zero cpu accumulation.
- `resources` and CLI argument validation findings:
  - `./sb resources status --json --scope cache` returns completion with huge output and `completeness`/`partial` depending on `--thorough` and scan depth.
  - `./sb resources status --json --scope stale` succeeds; `--scope runtime` is invalid (allowed `cache|stale` only).
  - `./sb resources plan --json` requires `--scope`; returned `invalid_scope` when missing.
  - `./sb resources status --json --scope cache --thorough` is **partial** with category statuses timing out (`sandbox_runtime`, `host_logs`, `host_package_cache`, `job_artifacts`) and low confidence.

Next gap candidates for follow-up:
1) Resolve remote pre-dispatch lock (`/home/alim/sandbox/deploy-src/sandbox/.git/index.lock`) to unblock `integration -- --list-groups` and other config-less list-suites paths.
2) Resolve why all `--list-groups`/`--list-suites` unit/integration invocations without explicit config still emit PHPUnit usage (possible command/path mapping issue).
3) Determine if `resources --scope cache --thorough` timeout categories are expected or indicate environment scan issues.

# 2026-08-02 remote-focused retest + log sweep

- Ran three remote test commands from `/Users/alim/Sites/git/sandbox` after prior sweep:
  - `./sb test --project-dir . --remote scaleway-sandbox unit -- --list-groups` -> job `b7f3ff255146de1918536ed316910e28`, failed; `job-output` shows PHPUnit usage help and `error: tests failed (phpunit exit 2)`.
  - `./sb test --project-dir . --remote scaleway-sandbox integration -- --list-groups` -> job `9d98a1d9bc8109b60f51621ce89dd0cd`, same usage/`phpunit exit 2` failure.
  - `./sb test --project-dir . --remote scaleway-sandbox unit --` (no passthrough args) -> job `2ec842c80eb57be7f8a462ee294deda9`, same PHPUnit usage failure (`/phpunit.phar` invoked with no suite/config/path).
- Ran `./sb test --project-dir . --remote scaleway-sandbox integration --provision-only`.
  - The command succeeded in creating a job (`b7d76acfbcde6eea78633813d4a92fe1`) but PHPUnit failed with `Unknown option "--provision-only"` and `exit 1`.
  - Root cause: `--provision-only` was appended as PHPUnit passthrough due position after `--`; sb options require CLI flags before mode position.
- Remote log command gap observed:
  - `./sb logs --project-dir . --remote scaleway-sandbox --json` (and with `--instance`) returns
    `no sandbox instance for this directory` despite active `scaleway-sandbox` tests. No fallback path found to pull container logs from this route.
- Confirmed remote job telemetry path still works and is useful for traceability:
  - `./sb job-output --remote scaleway-sandbox <job_id>` captures container bootstrap + phpunit stderr for failed jobs.
  - `./sb job-status --remote scaleway-sandbox <job_id>` marks these as terminal/failed with nonzero exit.

### New gaps to fix
1. `unit` and `integration` modes without explicit phpunit config/path still fail by default and should probably either default to project-known suite(s) or fail with a more actionable validator message.
2. Investigate whether `--provision-only` should be disallowed/rewritten for passthrough mode or surfaced earlier as an sb-level validation error to avoid silent phpunit invocation.
3. Improve/confirm remote `logs` UX: current `./sb logs --remote` behavior appears to require a registered local workspace and does not read remote logs for this project path.

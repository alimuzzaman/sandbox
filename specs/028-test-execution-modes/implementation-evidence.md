# Implementation Evidence: Test Execution Modes

## Planning and research trace — 2026-07-16

- Local research reviewed: `docs/sandbox-notes.md`, `docs/sandbox-improvement-plan.md`,
  `docs/sandbox-mcp-tasks.md`, current `cmd_test`, `_tests.py`, MCP `run_tests`, and
  configuration behavior.
- Luna read-only research was attempted twice but timed out without a report.
- Terra read-only planning completed with the bounded behavior matrix, conservative
  marker rules, interface contract, and file/test scope reflected in `plan.md` and
  `tasks.md`.
- No code, WordPress stack, database, remote, production, or external state was changed
  during planning.

## Implementation and verification — 2026-07-16

- Implemented bounded `auto|unit|integration` resolution with explicit/configuration
  precedence and conservative integration fallback for unknown, mixed, WordPress-marked,
  or unsafe evidence.
- Implemented Docker and Herd pure-unit runners. Unit mode uses project Composer/PHPUnit
  tools with no WordPress suite, polyfills, test database, or `WP_TESTS_*` environment;
  Docker unit runs use `--no-deps`.
- Preserved the existing integration harness path and added CLI/MCP mode observability.
- Focused: `./.cli-venv/bin/python -m unittest tests.test_project_config tests.test_runtime_test_modes tests.test_mcp tests.test_cli -q` — passed with mode/config, MCP, and CLI coverage.
- Full: `./.cli-venv/bin/python -m unittest discover -s tests -q` — 828 tests passed, 1 skipped, 31.358s.
- `./sb test --help` exposes `auto`, `unit`, and `integration`; `git diff --check` passed.

The full suite emits expected negative-path diagnostics and one existing urllib
`ResourceWarning`; the process exits successfully. No production capture, restore,
deletion, deployment, or external mutation was performed.

## Follow-up live acceptance — 2026-07-16

- MCP-first `run_tests` against `templately-modular-rewrite` was attempted but the
  already-running MCP process still returned its pre-restart `No module named 'sandbox'`
  import failure; the CLI fallback was used after that diagnostic.
- `./sb test --project-dir /Users/alim/Sites/git/templately-modular-rewrite integration -- --testsuite integration`
  provisioned the cached WordPress 7.0 suite, PHPUnit 9.6.34, polyfills, and isolated
  `wp_tests` database, then ran 101 integration tests with 349 assertions.
- The harness path completed; two existing Templately Theme Builder behavior tests failed.
  No Sandbox harness error, source edit, composer change, or baseline update occurred.

# Research: Test Execution Modes

## Decision 1: Preserve integration as the safe default

The current `cmd_test` always provisions the external WordPress harness. Existing
WordPress projects and unknown projects must continue to use that path when no mode is
specified. Auto detection therefore falls back to `integration` for missing, mixed,
or ambiguous evidence.

**Alternatives considered**: Defaulting to unit would remove the current isolated
database/harness behavior for existing plugins. Defaulting to a new host-PHP runner
would expand the runtime/security boundary and was rejected.

## Decision 2: Use bounded, non-executing detection

Auto mode may inspect project-local PHPUnit configuration, declared bootstrap paths,
Composer metadata, and PHP test sources. It must never import, invoke, or shell-execute
project code. Candidate paths are resolved beneath the canonical project root; unsafe
paths cause the conservative integration fallback.

Integration markers include `WP_UnitTestCase`, `WP_TESTS_DIR`, `tests_add_filter`, and
WordPress PHPUnit bootstrap references. Pure-unit evidence includes `Brain\\Monkey` or
the `brain/monkey` package. Any WordPress marker wins; only Brain/Monkey-only evidence
selects unit.

**Alternatives considered**: Executing PHPUnit configuration to discover suites was
rejected because repository code is untrusted and detection must be side-effect free.
Heuristic selection based only on directory names was rejected as too ambiguous.

## Decision 3: Keep unit and integration environment boundaries explicit

Integration retains the current suite clone, polyfills mount, isolated `wp_tests` DB,
config file, and `WP_TESTS_*` environment. Unit mode shares only the project Composer
dependency setup and PHPUnit invocation; it does not call harness provisioning or
database setup and does not set WordPress test variables.

The mode selects the execution environment, not a named PHPUnit suite. Existing
passthrough arguments remain responsible for `--testsuite`, filters, and file paths.

## Decision 4: Make mode additive at interfaces

The CLI gains an optional positional `auto|unit|integration` mode. MCP gains an optional
`mode` argument and returns the resolved mode alongside the existing `ok`, `passed`,
`summary`, and `output` keys. Invalid modes fail before capability checks or subprocess
execution. Configuration uses `tests.suite` with `auto` as its canonical default.

## Evidence and uncertainty

- Local research notes compare the two supported project shapes: Templately uses
  `WP_UnitTestCase` integration; Disable Comments uses Brain/Monkey pure unit tests.
- The current code confirms `cmd_test` always provisions the WordPress harness and the
  MCP tool only forwards PHPUnit arguments, so the advertised fast path is incomplete.
- A Luna read-only research attempt timed out twice without a report. Terra's planning
  review and the local primary notes agree on the bounded design above; no external
  behavior is being assumed beyond those sources.
- Live unit-mode execution against a real external plugin remains a protected acceptance
  step; the implementation gate is a disposable pure-PHP fixture plus existing full
  suite evidence.

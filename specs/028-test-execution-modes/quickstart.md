# Quickstart: Test Execution Modes

## Prerequisites

- Python CLI environment at `./.cli-venv/bin/python`.
- A disposable project fixture with a registered Sandbox runtime.
- PHPUnit and Composer dependencies available for the unit fixture.

## Unit mode

1. Run the focused contract tests:

   ```sh
   ./.cli-venv/bin/python -m unittest tests.test_runtime_test_modes -q
   ```

2. Run a pure-unit fixture:

   ```sh
   ./sb test unit --project-dir /path/to/fixture
   ```

   Expected: PHPUnit runs without WordPress suite provisioning, test DB creation,
   `WP_TESTS_*` variables, or a WordPress harness call.

## Integration mode

```sh
./sb test integration --project-dir /path/to/wordpress-project
```

Expected: existing external WordPress suite, polyfills, isolated `wp_tests` database,
and PHPUnit behavior remain in use.

## Auto and interface precedence

```sh
./sb test --project-dir /path/to/project
./sb test unit --project-dir /path/to/project
```

The explicit mode wins over `tests.suite`; auto selects unit only for an unambiguous
Brain/Monkey-only fixture and otherwise selects integration.

For MCP, call `run_tests` with `mode="unit"` or `mode="integration"` and verify the
response includes the resolved `mode` field while preserving the existing result keys.

## Full verification

```sh
./.cli-venv/bin/python -m unittest discover -s tests -q
git diff --check
```

Live WordPress or protected production-like acceptance is separate and must not be
inferred from this fixture/contract quickstart.

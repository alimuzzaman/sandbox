# CLI and MCP Contract: Test Execution Modes

## CLI

```text
sb test [auto|unit|integration] [--project-dir DIR] [--label LABEL]
        [--provision-only] [-- <phpunit arguments>]
```

- Omitted mode preserves current integration-compatible behavior.
- Explicit mode overrides `tests.suite` configuration.
- `--provision-only` is accepted only for integration mode.
- Invalid mode and invalid mode/config combinations fail before harness or PHPUnit
  subprocesses.
- Existing label and passthrough behavior is unchanged.

## Configuration

```json
{ "tests": { "suite": "auto" } }
```

The only accepted suite values are `auto`, `unit`, and `integration`.

## MCP

```text
run_tests(project_dir, phpunit_args="", label=null, mode=null)
```

- `mode` is optional and accepts `auto`, `unit`, or `integration`.
- Existing required `project_dir`, capability gate, timeout, and result keys remain.
- Successful or failed responses add `mode` with the resolved value.
- A remote accepted response resolves and returns `unit` or `integration` before
  durable target selection; it never reports the unresolved `auto` sentinel.
- Invalid mode returns a structured error without capability checks or subprocesses.

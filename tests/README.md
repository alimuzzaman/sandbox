# Sandbox tooling tests

Unit + integration tests for the `sb` CLI / `sandbox` package and the MCP server
(specs 001 per-project-modular + 002 dashboard-snapshots). Stdlib `unittest`
only — no extra deps (the `.cli-venv` already has PyYAML).

## Run

```sh
./sb selftest                                      # the friendly wrapper
.cli-venv/bin/python -m unittest discover -s tests -v # the full Python suite
```

For a focused Python run, target a module, class, method, or filename pattern:

```sh
.cli-venv/bin/python -m unittest tests.test_cli.TestResolutionGate -v
.cli-venv/bin/python -m unittest tests.test_cli.TestResolutionGate.test_test_command_lists_explicit_modes -v
.cli-venv/bin/python -m unittest discover -s tests -p 'test_feedback.py' -v
```

The module and class forms are useful while iterating on one area; `discover -p`
selects test files by their filename pattern. These commands run Sandbox's stdlib
`unittest` suite from this checkout and do not provision a WordPress instance.

Any interpreter may run the suite, but it must have PyYAML (the `.cli-venv`
already does). A bare interpreter without PyYAML fails fast with guidance; the
CLI never re-execs a foreign process to bootstrap it.

`sb test` is a different command. Its `auto` plugin mode resolves to `unit` or
`integration`; `integration` provisions and runs the external WordPress/PHPUnit
harness, while `unit` runs plugin unit PHPUnit with the runner tools. Declared Compose modes and `matrix` are separate
execution paths; none run the Sandbox Python package tests.

## Layout

| File | Covers |
|------|--------|
| `test_sandbox.py` | package structure (40+ commands registered, no `DEFAULT_INSTANCE`, thin `sb`), pure helpers (`deep_merge`, `expand`, image/TLD/domain/site-url resolution, naming, `_php_literal`, wp-config + multisite rendering, server-runtime/herd/extra-mount), snapshot-name traversal guard, registry-sourced resolution + overlay |
| `test_bridge.py` | spec-002 `_bridge_handle` — token auth (403/404/409), routing, **path-traversal rejection** (incl. an "outside dir survives" escape check); registry port overlay |
| `test_cli.py` | end-to-end resolution gate via the real `sb` subprocess — instance-scoped commands error outside a project (never `main`), registry-wide run anywhere, unknown instance rejected |
| `test_compose.py` | `render_compose` for apache/nginx/litespeed — ports, image, per-instance dir, services, `host.docker.internal` extra_hosts |
| `test_mcp.py` | MCP server split — imports the thin `server.py` in its venv and asserts ≥26 tools + ≥8 prompts register (guards the decorator-drop class of bug) |

`test_cli.py` and `test_mcp.py` are subprocess/integration tests; `test_mcp.py`
skips cleanly if the MCP venv isn't built (`./sb mcp-install`). Registry tests
isolate state via the `SANDBOX_RUNTIME` env var.

The remote MCP transport argument tests use a temporary executable placeholder,
so they cover command construction without requiring the optional MCP venv.

## Plugin tests ("from the instance")

For its `auto` and `integration` modes, `sb test --project-dir <plugin>`
resolves a plugin test mode; `integration` provisions the external WP phpunit harness and runs a plugin's suite inside the
instance — covered by the harness code, not this suite. `unit` runs plugin unit
PHPUnit with the runner tools; declared Compose modes and `matrix` do not use this
PHP harness. Two real harness bugs were fixed while exercising it: composer couldn't
fetch git-sourced deps (the `wordpress:cli` image is non-root + has no git), and the
project root wasn't mounted in the test container for projects outside `plugins_home`.

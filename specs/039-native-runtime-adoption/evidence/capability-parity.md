# Capability parity contract evidence

This is contract-test evidence only. It is not live managed-native proof and
does not make any native adapter adoptable.

## Observed contract results

The following focused suite passed on the development host on 2026-08-02:

```text
mcp/wp-server/.venv/bin/python -m unittest -q \
  tests.test_native_capabilities \
  tests.test_native_mode_lifecycle \
  tests.test_native_runtime_service \
  tests.test_native_cli_mcp \
  tests.test_native_execution_gateway \
  tests.test_mcp_composition \
  tests.test_architecture_boundaries
```

The suite verifies that:

- Compose remains the explicit default and a populated instance rejects an
  ordinary mode/adapter switch before adapter dispatch.
- Required operations return a structured capability envelope; unsupported
  optional operations return a limitation and a safe alternative before
  dispatch.
- CLI and MCP execution entry points preflight capabilities before legacy
  Compose/Herd/host execution, and managed payloads use the isolation gateway.
- The WordPress MCP group is registered with explicit dependencies rather than
  importing helpers from `app.py`; registration is import-safe.

An additional focused incumbent/lifecycle/cleanup boundary run completed 38
tests, and the repository-wide run completed 1,628 tests with two intentional
skips. The live Compose gateway then passed `ensure`, `status`, `wp core
version`, and one PHPUnit fixture. Read-only macOS evidence observed Herd and a
declared POSIX profile without any route mutation; see `compose-regression.md`
and `incumbents.md`.

## Adapter envelope parity

| Adapter | Required lifecycle | Payload gateway | Optional gaps are typed | Isolation label |
|---|---|---|---|---|
| Compose | ensure/apply/status | WordPress CLI/exec/test through Compose | yes, with safe alternatives | `compose_container` |
| Ubuntu nspawn | preflight/ensure/status/apply/destroy | one policy-digest isolation launcher | yes, before dispatch | `managed_container` |
| Herd | preflight/ensure/status/destroy | deliberately unwired until adoption proof | yes, before dispatch | `trusted_shared_host` |
| Valet | preflight/ensure/status/destroy | deliberately unwired until adoption proof | yes, before dispatch | `trusted_shared_host` |
| Declared POSIX | preflight/ensure/status/destroy | deliberately unwired until adoption proof | yes, before dispatch | `trusted_shared_host` |

Unsupported optional operations never probe a fallback transport. Managed
payload operations additionally fail before execution when the effective
policy, grant digest, credential boundary, or live evidence gate is absent.

## Still required

The live Ubuntu nginx/Apache hostile matrix remains required before the managed
adapter can be promoted. This contract evidence does not substitute for that
gate and does not make any native adapter adoptable.

# Implementation Evidence: Generic Project Instances

## T001 — Pre-change WordPress baseline

**Recorded:** 2026-07-15

This evidence establishes the WordPress behavior and composed surface inventory
before generic-project implementation. It intentionally excludes generated
credentials and autologin URLs.

### Live project instance

| Check | Command | Result |
| --- | --- | --- |
| Ensure | `./sb ensure` | Exit 0. Created/resolved `sandbox-remaining-spec-t`; WordPress URL `http://localhost:8192`; server `nginx`; ports WP `8192`, DB `3322`, Mailpit `8129`. |
| Status | `./sb status` | Exit 0. `wp`, `nginx`, `db`, and `mailpit` services were running; database health check passed. |
| WP-CLI | `./sb wp core version` | Exit 0; reported WordPress `7.0`. |
| REST | `./sb visit http://localhost:8192/wp-json/` | Exit 0; HTTP status `200`, no browser console errors or network failures; load time `268 ms`. |

### Composed command and tool inventory

The owned-manifest architecture guard reports the following pre-change counts:

- CLI command specs: **68** (`sandbox.commands.manifest` / `sandbox.registry.COMMANDS`).
- MCP tool groups: **18** (`mcp/wp-server/tools/manifest.py`).
- MCP tools: **75** unique declared names.

These counts were independently asserted by
`tests/test_architecture_boundaries.py::TestArchitectureBoundaries::test_exact_owned_cli_and_mcp_inventories_are_enforced`.

### Repository test baseline

Command required by the scheduled-execution contract:

```text
python3 -m unittest discover -s tests -v
```

Result: exit **1** after **663 tests** in **24.472 seconds**: **2 failures**,
**1 error**, **3 skipped**. The failures are pre-change MCP composition
baseline failures in this environment:

1. `test_mcp_composition.TestMcpComposition.test_instance_and_hermes_groups_register_against_an_isolated_fake_context` errored because importing `mcp/wp-server/app.py` raised `ModuleNotFoundError: No module named 'mcp.server'`.
2. `test_mcp_composition.TestMcpComposition.test_instance_and_hermes_groups_have_no_app_import_or_import_registration_side_effect` failed because `mcp/wp-server/tools/hermes.py` contains an `app` import.
3. The suite also skipped `test_server_transport` because the MCP server dependencies are not importable from the selected interpreter.

The feature work has not changed product behavior yet; later task verification
must distinguish this pre-existing environment/dependency baseline from new
regressions.

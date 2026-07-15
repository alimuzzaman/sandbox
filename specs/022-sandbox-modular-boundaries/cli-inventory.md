# CLI Baseline Inventory

**Audit point**: `e52eb8d`; composition replay 2026-07-14
**Count**: 68 registered root commands
**Current composition**: `sandbox/commands/manifest.py` imports the 26 feature modules in a deterministic order. `LEGACY_BRIDGE_COMMANDS` explicitly maps all 68 handlers to their feature owner while `sandbox/cli.py` retains the bounded parser-definition bridge.

| Owner | Commands | Current scope/capability class |
|---|---|---|
| `abilities.py` | `abilities` | WordPress |
| `cache.py` | `cache` | Infrastructure |
| `ci.py` | `ci` | WordPress-oriented orchestration |
| `config_setup.py` | `apply`, `connect`, `global`, `onboard`, `setup` | Mixed project/infrastructure |
| `data.py` | `clean`, `reset`, `restore`, `snapshot`, `snapshots` | WordPress/data-destructive |
| `debug.py` | `dump`, `introspect`, `qm`, `selftest`, `test`, `xdebug` | Mixed WordPress/infrastructure |
| `deploy.py` | `deploy` | WordPress remote |
| `e2e.py` | `e2e` | WordPress-oriented |
| `hermes.py` | `hermes` | Infrastructure/remote/agent |
| `hosting.py` | `host` | Managed hosting |
| `instances_cmd.py` | `ensure`, `focus`, `init`, `instance`, `instances` | Project/instance mixed |
| `integ.py` | `claude`, `mcp`, `mcp-install` | Infrastructure |
| `jobs.py` | `async-job`, `job`, `jobs` | Mixed async/WordPress |
| `license.py` | `license` | WordPress/plugin ecosystem |
| `lifecycle.py` | `doctor`, `down`, `install`, `logs`, `open`, `shell`, `smoke`, `status`, `up`, `update` | Mixed lifecycle |
| `migrate.py` | `home`, `migrate` | Infrastructure/state relocation |
| `net.py` | `domains`, `pxdiff`, `secure`, `server`, `specdiff`, `specextract`, `specgate`, `vrdiff` | Mixed network/visual |
| `plugin_check.py` | `plugin-check` | WordPress plugin |
| `preview.py` | `preview` | WordPress remote preview |
| `remote.py` | `remote` | Remote infrastructure |
| `recovery.py` | `recovery` | Scoped recovery |
| `secrets.py` | `secrets` | Infrastructure/secrets |
| `skill.py` | `skill` | Agent infrastructure |
| `ui_dash.py` | `dashboard`, `ui`, `web` | Infrastructure with WP assumptions |
| `uninstall.py` | `uninstall` | Destructive infrastructure |
| `wp.py` | `seed`, `visit`, `wp` | WordPress |

## Compatibility contract

- Every name above remains invocable and appears exactly once.
- `ui` remains the user-visible dashboard alias.
- Existing options, required arguments, JSON envelopes, parse errors, and exit behavior are characterized before each owner migrates.
- Exact incidental source order is not public, but composed help grouping/order must be deterministic.
- New feature commands must use `CommandSpec`; unmigrated commands must appear in the named bridge.

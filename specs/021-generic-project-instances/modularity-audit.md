# Sandbox Modularity and Feature-Surface Audit

**Audit point**: working tree, 2026-07-16
**Scope**: shipped CLI, core package, shared project config/registry, MCP server/tools, tests, and documented feature families. Generated virtual environments, `runtime/wp/`, and `vendor/` are excluded.

## Executive finding

Sandbox is modular by directory but not yet by dependency boundary. Feature handlers and MCP tools are usefully grouped, yet parser ownership, shared namespaces, project configuration, and several mature features remain centralized. This is workable for WordPress-only evolution but would encourage project-kind branches across the product if generic runtimes were added directly.

The recommended response is a runtime adapter seam plus touch-driven cleanup. A dedicated modularity rewrite is not justified for this side project.

## Structural evidence

| Area | Evidence | Rating | Consequence |
|---|---|---|---|
| Entry/distribution | `sb` is a 60-line polyglot entry and remains stable | Good | Meets the single-entry distribution constraint |
| CLI feature handlers | 67 commands are registered from 25 command modules | Good foundation | Handler ownership is visible and reasonably grouped |
| CLI parsing/routing | `sandbox/cli.py` is 775 lines and retains the compatibility parser bridge plus central project/instance routing sets; lifecycle parsers now register from `sandbox/commands/lifecycle.py` | Partial boundary | Legacy commands remain centralized, while touched lifecycle additions can stay feature-owned |
| Shared command registry | `sandbox/registry.py` stores only name-to-handler mappings | Partial | It cannot own parser config, capability needs, or feature metadata |
| Shared project config/registry | `sandbox_core.py` is 980 lines and independently contains WP config normalization plus registry/locks | Weak boundary | A second “core” exists outside `sandbox/core/`; generic kind selection touches a monolith |
| CLI core package | 24 underscore modules exist | Good foundation | Large concepts have named homes |
| Core dependency model | `sandbox/core/__init__.py` imports modules, combines their symbols, and back-fills every module | Poor isolation | Import order/collisions are hidden; unit boundaries are not explicit |
| Wildcard imports | 21 shipped Python files still wildcard-import `sandbox.core` or MCP `app`; the three touched instance/lifecycle/config modules now use explicit imports | Improving, not complete | Continue touch-driven migration; do not claim repository-wide decomposition |
| MCP grouping | 51 tools are grouped into 17 modules | Good foundation | User-facing tools have recognizable ownership |
| MCP bootstrap/helpers | `app.py` is 607 lines; `server.py` manually imports all tool groups; most tool modules wildcard-import `app` | Weak boundary | New groups grow bootstrap and rely on a broad global surface |
| Runtime model | `_instances.py` and registry records assume WP, DB, Mailpit, WP images, and `wordpress_port` | WordPress-cohesive, not extensible | Generic behavior needs an adapter/common record rather than more optional fields in every path |
| Tests | Focused test modules exist for major recent features | Good foundation | Contract regression is feasible |
| Test concentration | `test_hermes.py` is 1,351 lines; some module boundaries are not independently tested | Maintenance risk | Large features are harder to review and route cheaply |

## Large-module hotspots

| File | Lines | Assessment | This feature |
|---|---:|---|---|
| `sandbox/core/_hermes.py` | 2,193 | Multiple Hermes subdomains share one module | Record only; out of scope |
| `sandbox/core/_provision.py` | 994 | WP provisioning, files, plugins, tests, and helpers are concentrated | Wrap behind WordPress adapter; do not split |
| `sandbox_core.py` | 980 | Project config and registry share a compatibility module | Add only kind/common-record contract; no wholesale move |
| `sandbox/core/_domains.py` | 846 | Proxy, cert, host resolver, and domain behavior are coupled | Reuse through a narrow generic proxy call |
| `sandbox/core/_instances.py` | 836 | Identity, ports, WP boot, proxy, registry, and apply are coupled | Preserve as WordPress implementation behind adapter |
| `sandbox/core/_remote.py` | 756 | Remote registry/provision/deploy behavior is concentrated | Out of MVP |
| `sandbox/cli.py` | 775 | Compatibility parser bridge and routing composition remain centralized | Continue moving only touched command families behind feature-owned registration |
| `sandbox/core/_docker.py` | 744 | WP Compose rendering and container helpers share a module | Do not reuse renderer for project-owned Compose; reuse only safe process primitives |
| `sandbox/core/_dash.py` | 673 | Web dashboard rendering/actions/state share a module | Dashboard generic support deferred |
| `mcp/wp-server/app.py` | 607 | Resolution, process, credentials, URLs, HTTP, and MCP server object coexist | Add explicit runtime helpers; do not add generic logic to wildcard surface |

File size is a symptom, not a migration trigger. Decomposition should follow independently testable responsibilities during future feature work.

## CLI feature inventory — 67 commands

Every registered command appears once below. “Candidate” means the concept can be runtime-neutral after capability dispatch; it does not claim current generic support.

| Feature family / owner | Commands | Classification | Generic-instance decision |
|---|---|---|---|
| Setup/config (`config_setup.py`) | `setup`, `apply`, `onboard`, `global`, `connect` | Mixed infrastructure/WP | Make project-scoped `apply` adapter-aware; keep setup/onboard global or WP-specific |
| Lifecycle (`lifecycle.py`) | `up`, `down`, `status`, `logs`, `shell`, `install`, `smoke`, `doctor`, `update`, `open` | Mixed | Dispatch up/down/status/logs/shell/open; retain WP install/smoke semantics; audit doctor checks by capability |
| Instance identity (`instances_cmd.py`) | `init`, `ensure`, `instances`, `instance`, `focus` | Shared candidate | Add kind-aware init/ensure/status metadata; preserve registry semantics |
| WordPress/application (`wp.py`, `abilities.py`) | `wp`, `seed`, `visit`, `abilities` | Mixed | WP/seed/abilities stay WP-only; visit is runtime-neutral without WP login assumptions |
| Data lifecycle (`data.py`) | `snapshot`, `restore`, `snapshots`, `reset`, `clean` | WP-only today | Reject generic projects; generic volume snapshots are out of scope |
| Debug/test (`debug.py`) | `xdebug`, `dump`, `qm`, `introspect`, `test`, `selftest` | Mixed | Keep WP diagnostics/test WP-only; selftest remains infrastructure-only |
| Jobs (`jobs.py`) | `job`, `jobs`, `async-job` | Mixed | WP jobs remain WP-only; generic async runner remains infrastructure-neutral |
| Local UI (`ui_dash.py`) | `dashboard`, `ui`, `web` | Infrastructure with WP assumptions | Inventory generic records; feature parity deferred |
| Network/visual (`net.py`) | `domains`, `secure`, `server`, `pxdiff`, `vrdiff`, `specextract`, `specdiff`, `specgate` | Mixed | Reuse domains/secure and visual probes where URL-only; server switching remains WP-only |
| MCP/client integration (`integ.py`) | `mcp`, `claude`, `mcp-install` | Infrastructure-only | Unchanged except tool descriptions/registration |
| Cache (`cache.py`) | `cache` | Infrastructure-only | Unchanged |
| License (`license.py`) | `license` | WordPress/plugin ecosystem | Unchanged, generic unsupported |
| Runtime relocation (`migrate.py`) | `migrate`, `home` | Infrastructure-only | Include generic generated artifact relocation/regeneration |
| Uninstall (`uninstall.py`) | `uninstall` | Infrastructure-only/destructive | Include generic Sandbox-owned state only under existing confirmation |
| E2E (`e2e.py`) | `e2e` | WP-only today | Generic E2E matrices deferred |
| CI (`ci.py`) | `ci` | WP-oriented orchestration | Generic CI deferred |
| Plugin Check (`plugin_check.py`) | `plugin-check` | WordPress-only | Explicit capability rejection |
| Remote registry (`remote.py`) | `remote` | Infrastructure with WP provisioning | Generic remote runtimes deferred |
| Deploy (`deploy.py`) | `deploy` | WordPress-only today | Generic deploy deferred |
| Managed hosting (`hosting.py`) | `host` | WordPress-only today | Generic hosting deferred |
| Remote preview (`preview.py`) | `preview` | WordPress-only today | Generic previews deferred |
| Personal secrets (`secrets.py`) | `secrets` | Infrastructure-only | Reuse existing storage; no generic secret copying |
| Hermes (`hermes.py`) | `hermes` | Infrastructure/agent subsystem | Unchanged |
| Skills (`skill.py`) | `skill` | Infrastructure/agent subsystem | Unchanged |

## MCP feature inventory — 51 tools

| Tool group | Tools | Classification | Generic-instance decision |
|---|---|---|---|
| `instances.py` | `ensure_instance`, `destroy_instance`, `recreate_instance`, `setup_domains`, `secure_instance`, `apply_config` | Shared candidate with WP docs/semantics | Make adapter-aware; generic destroy preserves volumes |
| `wp.py` | `wp_cli`, `wp_exec`, `wp_rest`, `run_tests`, `wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill` | WordPress-only | Capability reject; add separate generic exec tool |
| `net.py` | `http_fetch`, `pixelmatch_diff`, `visit` | Runtime-neutral candidate | Reuse with generic URL and no WP login assumption |
| `data.py` | `db_query`, `import_content`, `wp_reset` | WordPress-only | Capability reject |
| `fs.py` | `tail_log`, `fs_read`, `fs_write`, `fs_list` | Bound to WP runtime filesystem | Keep WP-only; add bounded adapter logs, no duplicate source filesystem API |
| `mail.py` | `mail_list`, `mail_get` | WordPress stack/Mailpit | Capability reject |
| `context.py` | `focus_get`, `activate_plugin`, `deactivate_plugin`, `load_context`, `load_workflow`, `load_skill` | Mixed | Make focus metadata kind-aware; plugins stay WP-only; loaders remain infrastructure-only |
| `cache.py` | `cache_info`, `cache_clear` | Infrastructure-only | Unchanged |
| `abilities.py` | `wp_eval_live` | WordPress-only | Capability reject |
| `skills.py` | `list_skills`, `skill_write`, `skill_edit`, `skill_delete` | Infrastructure/agent subsystem | Unchanged |
| `debug.py` | `qm_capture`, `xdebug` | WordPress-only | Capability reject |
| `e2e.py` | `run_e2e` | WordPress-only today | Generic support deferred |
| `ci.py` | `ci_plan`, `ci_run` | WP-oriented orchestration | Generic support deferred |
| `asyncjobs.py` | `async_job_status`, `async_job_kill` | Infrastructure-neutral | Unchanged |
| `plugin_check.py` | `run_plugin_check` | WordPress-only | Capability reject |
| `remote.py` | `remote_deploy` | WordPress-only today | Generic support deferred |
| `hermes.py` | `hermes_status`, `hermes_run`, `hermes_job_status`, `hermes_job_kill` | Infrastructure/agent subsystem | Unchanged |

## Bounded recommendations

### Required for generic instances

1. Create an explicit adapter protocol and one dispatch point.
2. Split common project identity/name from WordPress plugin slug.
3. Choose project kind before applying defaults.
4. Add common registry fields without removing WordPress fields.
5. Add capability preflight to shared dispatch and WordPress MCP wrappers.
6. Keep generic Compose rendering/state out of the current WP `_docker.py` renderer.
7. Move parser configuration for touched instance/lifecycle commands beside their handlers.
8. Load MCP tool groups through a package-owned loader and keep new runtime tools explicitly imported.

### Opportunistic when touched later

- Replace wildcard imports with explicit imports in changed modules.
- Split `sandbox_core.py` config and registry responsibilities behind its compatibility facade.
- Divide `app.py` into resolution, process, URL, and MCP composition helpers.
- Decompose Hermes by state sync, backups, jobs/worktrees, gateway, and public dashboard when those areas next receive feature work.
- Split large tests alongside their production responsibility, preserving scenario coverage.

### Explicitly not a gate for this side project

- Rewriting all 67 parser definitions.
- Eliminating the entire back-filled core namespace.
- Enforcing a universal line-count limit.
- Generic parity for remote hosting, CI/E2E, snapshots, dashboard, databases, or mail.

## Modularity acceptance checks

- Current automated inventory (2026-07-26): 84 CLI commands, 50 decorated MCP
  tools, 20 wildcard imports, and 75 `kind`-referencing conditional expressions.
  The conditional count is a broad static regression proxy (it includes job and CI
  discriminators as well as runtime selection), so it is updated alongside each
  intentional feature addition rather than treated as a runtime-adapter-only limit.
- New `sandbox/runtimes/` files use no wildcard imports.
- Runtime-kind branching is confined to descriptor normalization and adapter selection; a repository search verifies exceptions.
- New CLI parsers are registered from their feature module, not appended directly to `sandbox/cli.py`.
- New MCP tools live in one runtime tool group; `server.py` does not gain per-tool imports.
- Legacy WordPress adapter delegates to current behavior until live parity is proven.
- The inventories above are updated if implementation adds, removes, or reclassifies a surface.

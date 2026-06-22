# Research: In-Instance WP Abilities + MCP Adapter Layer

## Decision: vendor `wordpress/mcp-adapter` into the mu-plugin payload

- **Decision**: Bundle `wordpress/mcp-adapter` inside `wp-content/mu-plugins/sandbox-abilities/`, loaded by `00-sandbox-abilities.php`; do not require it from the focused plugin or from `vendor/`.
- **Rationale**: Keeps the focused plugin's composer tree clean (constitution boundary), survives `composer install`/core pulls, and is self-contained per instance. Matches how other sandbox mu-plugins ship their own logic.
- **Alternatives considered**: (a) require it in each plugin — pollutes deps; (b) install it as a regular plugin via dl-cache — extra activation state + visible to the plugin list; (c) rely on WP core shipping it — not guaranteed on supported versions.

## Decision: re-implement ability callbacks (AGPL boundary)

- **Decision**: Write our own ability callbacks against public WP APIs; do not copy Novamira's AGPL source. Prefix everything `sandbox_*` / `sandbox/`.
- **Rationale**: Novamira is AGPL-3.0; the sandbox is not. The callbacks (eval+output-buffer+error-handler; ABSPATH-jailed file ops; shutdown crash handler) are small and mechanical against documented WP/PHP APIs, so independent re-implementation is straightforward and clean.
- **Alternatives considered**: vendoring Novamira — license incompatibility; skip file abilities — breaks external-client self-sufficiency (see clarification).

## Decision: ship both surfaces (direct endpoint + host-side proxy)

- **Decision**: Expose abilities at the instance's own `/wp-json` MCP endpoint **and** via thin Python-MCP proxy tools (`wp_eval_live`, file proxies) in `mcp/wp-server/tools/abilities.py`.
- **Rationale**: Direct endpoint gives client portability; the proxy gives existing `mcp__sandbox__*` users the capability in-session without switching clients. (Clarification 2026-06-22.)
- **Alternatives considered**: direct-only (less in-session convenience); proxy-only (no portability — defeats the main goal).

## Decision: file-CRUD abilities ship in v1

- **Decision**: Include `read/write/edit/list-file` abilities, ABSPATH-jailed with symlink-escape rejection.
- **Rationale**: External MCP clients hitting the direct endpoint lack the Python MCP's `fs_*`; the endpoint must be self-sufficient. The Python `fs_*` remain the Sandbox-native path. (Clarification.)
- **Alternatives considered**: execute-php only — leaves external clients unable to manage files.

## Decision: enabled on by default, instance-scoped, WP-version gated

- **Decision**: Provision enabled; toggle via `./sb abilities on|off|status`; on WP below the Abilities-API minimum the loader no-ops and logs a notice.
- **Rationale**: Instances are disposable/local, so on-by-default maximizes agent utility; a hard version gate avoids fatals on older WP.
- **Alternatives considered**: off-by-default (adds a step to every new instance for little safety gain on disposable stacks).

## Decision: crash-recovery loader for persistent AI PHP

- **Decision**: Load `wp-content/sandbox-code/*.php` behind a shutdown handler that writes a `.crashed` marker on fatal and drops the site into safe mode (skip all sandbox files), with an admin notice + a manual `?sb_safe_mode=1` override.
- **Rationale**: Lets agents persist functionality on a real stack without the risk of bricking the instance; the shutdown-handler pattern catches fatals even when thrown deep in core/third-party code.
- **Alternatives considered**: no persistence (limits the agent); load without recovery (one bad file white-screens the instance).

## Decision: herd reachability

- **Decision**: The mu-plugin is host-file-based, so it works unchanged on herd; the endpoint is the herd `https://<instance>.test/wp-json/...` URL; `connect` emits that URL for herd instances.
- **Rationale**: No container indirection needed; the bind-mount/host file layout is identical.

## Live findings (WP 6.9.4, verified 2026-06-22)

Probed on a running instance (`templately-rebuild2`): `function_exists('wp_register_ability')` = **yes** — the Abilities API ships in core 6.9.4, so 003 is fully live-verifiable now. `mcp-adapter` is **not** bundled in core (we vendor it). Two non-obvious API contracts caught by live verification while building the execute-php slice:

1. An ability's **category must be registered before** the ability that uses it, or `wp_register_ability` is rejected (`WP_Abilities_Registry::register was called incorrectly`).
2. Categories must be registered on a **separate, earlier action** — `wp_abilities_api_categories_init` — NOT inside `wp_abilities_api_init`. Registering a category from the abilities hook is rejected.

So the mu-plugin uses two hooks: `wp_abilities_api_categories_init` → `wp_register_ability_category('sandbox', …)`, then `wp_abilities_api_init` → `wp_register_ability('sandbox/<name>', …)`. Available API surface: `wp_register_ability`, `wp_get_ability`, `wp_has_ability`, `wp_get_abilities`, `wp_register_ability_category`, `wp_has_ability_category`, `wp_get_ability_categories`.

## Open questions

None — all spec clarifications resolved (surface = hybrid both-surfaces; enabled default on).

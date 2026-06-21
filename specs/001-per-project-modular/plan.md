# Implementation Plan: Per-Project-First Instance Model & Modular `sb`

**Branch**: `cwd-instance-resolution` (worktree) | **Date**: 2026-06-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-per-project-modular/spec.md`

## Summary

Remove the legacy `main`/`DEFAULT_INSTANCE` instance model so every command resolves to
the registered instance owning the current project (error otherwise), and split the
~7050-line `sb` polyglot script into a `sandbox/` Python package — one module per feature
group — while `sb` stays a thin entry file. Delivered in three verified stages: (A)
feature-migrate in place with no behavior loss, (B) remove the old model, (C) extract the
package. Each stage is a separate commit, smoke-tested live before the next.

## Technical Context

**Language/Version**: Python 3 (stdlib only; `sb` is a shell→python polyglot). PyYAML
loaded on demand via the existing `.cli-venv`.

**Primary Dependencies**: Docker / docker-compose, wp-cli (in the `wpcli` service or Herd
host wp), Caddy + mkcert (domains), `sandbox_core.py` (config + registry), the MCP
`wp-server` (`mcp/wp-server/server.py`), the TS web dashboard (`src/web` → vendored
`config/sandbox-web.js`).

**Storage**: `runtime/registry.json` (project→instance, the source of truth);
`sandbox.local.yml` `instances:` (per-instance config + secrets); `runtime/wp-<instance>/`
(WP installs); `runtime/snapshots/<instance>/` (snapshots).

**Testing**: `python3 sandbox_core.py --selftest-registry`; live CLI/MCP smoke against real
registered instances (constitution Principle IV — live-stack verification is the proof).

**Target Platform**: macOS/Linux developer machines.

**Project Type**: CLI tool + MCP server + small web dashboard.

**Constraints**: `sb` MUST remain a single entry file (global symlink, npm bin shim,
release tarball depend on it). No regressions for registered instances. Each old-model
removal gated on live parity proof.

**Scale/Scope**: ~7050-line `sb`, ~1450-line `server.py`, ~35 `resolve_instances` call
sites, ~23 `DEFAULT_INSTANCE` refs, 40 commands across 10 feature groups.

## Constitution Check

*GATE: must pass before and after design.*

- **I. Per-project only** — directly implemented (FR-001..003): resolution ends in an
  error, not `main`. PASS.
- **II. Registry is source of truth** — `resolve_instances` re-sourced from the registry;
  resolution precedence unchanged from the already-landed cwd logic. PASS.
- **III. Single entry file, modular package** — `sb` stays the polyglot entry; logic moves
  to `sandbox/` imported via the existing `sys.path.insert(0, ROOT)`. `sb` never becomes a
  directory. PASS.
- **IV. Live-stack verification** — every stage has a live smoke matrix (below). PASS.
- **V. Idempotency + docs-with-code** — CLAUDE.md / docs updated in the same commits;
  package extraction is pure refactor (behavior-preserving). PASS.
- **VI. Parity before removal** — Stage A proves per-project parity before Stage B deletes
  the old model. PASS.

No violations → Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-per-project-modular/
├── spec.md          # done
├── plan.md          # this file
├── research.md      # Phase 0 — the 3 Explore reports, distilled (to add)
└── tasks.md         # /speckit-tasks output
```

### Source Code (repository root)

`sb` stays a thin polyglot entry file: shell→python bootstrap, `ROOT =
Path(__file__).resolve().parent`, parse args, hand to `sandbox.cli:main`. All logic moves
into the package (imported exactly like today's `sandbox_core.py`):

```text
sb                       # thin entry: bootstrap + dispatch to sandbox.cli
sandbox_core.py          # unchanged role (config + registry); DEFAULT_INSTANCE removed
sandbox/
  __init__.py
  cli.py                 # COMMAND registry, argparse build, resolution gate, dispatch
  core/
    paths.py             # ROOT + path/constants (PROXY_*, MULTISITE_MARKER, image defaults)
    ui.py                # info/ok/die/run + web-stream capture
    config.py            # load_config, deep_merge, expand, _local_yaml/_write_local_yaml
    instances.py         # resolve_instances (registry-sourced), *_dir/*_file, ports
    docker.py            # compose, wpcli, render_compose, write_compose_files, _web_*
    domains.py           # PROXY_*, caddy, mkcert, valet, site_url, _tld
    provision.py         # plugins/themes wiring, mu-plugins, multisite, install internals
    herd.py              # _herd_* host-driver helpers
  commands/              # one module per feature group; each: register(sub) + run(cfg,args)
    lifecycle.py         # up down status logs shell install smoke doctor update
    instances_cmd.py     # init ensure instances instance focus
    config_setup.py      # setup apply onboard global connect
    data.py              # snapshot restore snapshots clean
    wp.py                # wp seed visit
    net.py               # domains secure server
    debug.py             # xdebug introspect test
    integ.py             # mcp claude mcp-install
    ui_dash.py           # dashboard web
    uninstall.py
```

**Structure Decision**: 10-group package (clarified). `commands/*` self-register into a
`COMMANDS` registry in `cli.py`, replacing the hand-maintained `handlers = {...}` dict.
Packaging: add `sandbox/` to `package.json` `files`; `bin/sandbox.js` + `make-release.sh`
need no change for the package (they already ship sibling Python files). `.specify/` and
`skills/speckit-*` are added to the release prune + excluded from `package.json` `files`
(FR-009).

## Phase 0 — Research (distilled from exploration; full notes → research.md)

- **Container boundary / packaging risk** (resolved): turning `sb` into a directory breaks
  the symlink + npm shim + tarball; a `sandbox/` package imported by the thin `sb` file is
  safe (same mechanism as `sandbox_core.py`).
- **Two app-password paths** today: `sb` `save_local_app_password` (~L2028) + doctor reader
  (~L2161) and `server.py` `_resolve_instance` (~L191) special-case `main` to the legacy
  `mcp.wp.application_password` key; non-main already uses `instances.<name>.app_password`.
- **`main` surfaces**: `resolve_instances` synthesis (~L459-465); delete guards in
  `cmd_instance` (~L5168), dashboard (~L5991), web (~L6455); name reservation
  (`_derive_instance_name` ~L4105); web UI (`src/web/src/render.ts` L49, `instance.ts`
  L53); `server.py` `DEFAULT_INSTANCE` (L46).
- **Legacy migration** (`migrate_legacy_layout` ~L1476-1557, `_legacy_stack_running`
  ~L1462, call ~L7036) is dead once `main` is gone.

## Phase 1 — Design: staged implementation

### Stage A — Feature-migrate in place (no behavior change)

1. **Unify app-password on the per-instance key** in both `sb` and `server.py`: always
   read/write `instances.<name>.app_password`; drop the `main`→`mcp.wp.application_password`
   branch (write-side first, keep read fallback until B if any live instance still uses it —
   verify none do via `sb doctor` on each registered instance).
2. **Confirm `ensure_instance`/`apply_config` write a complete instance block** so nothing
   depended on the synthesized `main` runtime defaults (audit `_build_instance_block`).
3. Live-verify: `sb doctor` shows app-password OK for each registered instance; an MCP tool
   needing the password authenticates per-instance.

### Stage B — Remove the old model

1. **Resolution gate** (`cli.py`, was `sb` ~L7005-7027): drop the `→ DEFAULT_INSTANCE`
   fallback; no project resolved + non-project-routed command → `die()` with guidance.
2. **`resolve_instances`**: source instances from the registry (+ their `sandbox.local.yml`
   blocks); stop synthesizing `main`; delete the `setdefault(DEFAULT_INSTANCE, …)`.
3. **Remove `main` special-cases**: delete guards (CLI/dashboard/web), name reservation,
   web UI guard (rebuild `config/sandbox-web.js` via `scripts/build-web-js.sh`).
4. **Remove legacy migration** + `_legacy_stack_running` + call; drop the
   `runtime/.legacy-migrated` handling (the neuter commit becomes moot).
5. **Delete `DEFAULT_INSTANCE`** from `sb` and `server.py`; make `instance` a required arg
   on `compose`/`wpcli`/`save_local_app_password`/`_active_project_name` (+ `server.py`
   equivalents) after auditing every call site passes one.
6. Live-verify the full matrix (below) + `grep` shows zero load-bearing `main`/
   `DEFAULT_INSTANCE`/`migrate_legacy` refs (SC-002).

### Stage C — Extract the `sandbox/` package

1. Move helpers/handlers into `sandbox/core/*` and `sandbox/commands/*` per the tree; keep
   behavior identical (pure refactor). Introduce the `COMMANDS` registry; `sb` becomes the
   thin entry calling `sandbox.cli:main`.
2. Update `package.json` `files` (+`sandbox/`, exclude `.specify/`+`skills/speckit-*`) and
   `make-release.sh` prune. `bin/sandbox.js` unchanged.
3. Live-verify identical behavior from a project dir AND via the global symlink; import-smoke
   the package; rebuild + run `sb web`.

## Verification (per stage; constitution IV)

From a registered project dir (e.g. `templately-fsi-rewrite`, which is running):
`sb status`, `sb wp plugin list`, `sb doctor`, `sb snapshot t1` + `sb snapshots`; from the
sandbox dir (unregistered) confirm the helpful error (Stage B+) instead of a `main` boot;
`--instance X` and `$SANDBOX_INSTANCE` overrides still work. Plus:
`python3 sandbox_core.py --selftest-registry`; `python3 -c "import ast;
ast.parse(open('sb').read())"` and import-smoke `sandbox`; MCP `ensure_instance`+`wp_cli`+a
password-needing tool; `scripts/build-web-js.sh` then `sb web` lists instances with delete
enabled for all (no `main` guard).

## Complexity Tracking

No constitution violations — none required.

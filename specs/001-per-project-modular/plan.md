# Implementation Plan: Per-Project-First Instance Model & Modular `sb`

**Feature**: `001-per-project-modular` | **Branch**: `cwd-instance-resolution` (shared worktree branch) | **Date**: 2026-06-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-per-project-modular/spec.md`

## Summary

Remove the legacy `main`/`DEFAULT_INSTANCE` instance model so every command resolves to the
registered instance owning the current project (error otherwise), and split the ~7050-line
`sb` polyglot script into a `sandbox/` Python package — one module per feature group — while
`sb` stays a thin entry file. Delivered in three verified stages: (A) feature-migrate in
place with no behavior loss, (B) remove the old model, (C) extract the package. Each stage is
a separate commit, smoke-tested live before the next.

## Technical Context

**Language/Version**: Python 3 (stdlib only; `sb` is a shell→python polyglot). PyYAML loaded
on demand via the existing `.cli-venv`.

**Primary Dependencies**: Docker / docker-compose, wp-cli (the `wpcli` service or Herd host
wp), Caddy + mkcert (domains), `sandbox_core.py` (config + registry), the MCP `wp-server`
(`mcp/wp-server/server.py`), the TS web dashboard (`src/web` → vendored `config/sandbox-web.js`).

**Storage**: `runtime/registry.json` (project→instance, the source of truth);
`sandbox.local.yml` `instances:` (per-instance config + secrets); `runtime/wp-<instance>/`;
`runtime/snapshots/<instance>/`.

**Testing**: `python3 sandbox_core.py --selftest-registry`; live CLI/MCP smoke against real
registered instances (constitution Principle IV — live-stack verification is the proof).

**Target Platform**: macOS/Linux developer machines.

**Project Type**: CLI tool + MCP server + small web dashboard.

**Constraints**: `sb` MUST remain a single entry file (global symlink, npm bin shim, release
tarball depend on it). No regressions for registered instances. Each old-model removal gated
on live parity proof.

**Scale/Scope**: ~7050-line `sb`, ~1450-line `server.py`, ~35 `resolve_instances` call sites,
~23 `DEFAULT_INSTANCE` refs, 40 commands across 10 feature groups.

No NEEDS CLARIFICATION remain (resolved in [research.md](./research.md) + the spec's
Clarifications session).

## Constitution Check

*GATE: must pass before and after design.*

- **I. Per-project only** — directly implemented (FR-001..003): resolution ends in an error,
  not `main`. PASS.
- **II. Registry is source of truth** — `resolve_instances` re-sourced from the registry;
  resolution precedence unchanged from the already-landed cwd logic. PASS.
- **III. Single entry file, modular package** — `sb` stays the polyglot entry; logic moves to
  `sandbox/` imported via `sys.path.insert(0, ROOT)`; `sb` never becomes a directory. PASS.
- **IV. Live-stack verification** — every stage has a live smoke matrix ([quickstart.md](./quickstart.md)). PASS.
- **V. Idempotency + docs-with-code** — CLAUDE.md/docs updated in the same commits; package
  extraction is behavior-preserving. PASS.
- **VI. Parity before removal** — Stage A proves per-project parity before Stage B deletes the
  old model. PASS.

No violations → Complexity Tracking empty (both pre- and post-design re-check).

## Project Structure

### Documentation (this feature)

```text
specs/001-per-project-modular/
├── spec.md          # done (+ Clarifications)
├── plan.md          # this file
├── research.md      # Phase 0 — decisions R1-R8
├── data-model.md    # Phase 1 — Instance, Registry entry, Instance config, Feature module, …
├── contracts/
│   └── cli-contract.md   # Phase 1 — resolution behavior + command-registry interface
├── quickstart.md    # Phase 1 — live verification matrix (per stage)
└── tasks.md         # /speckit-tasks output
```

### Source Code (repository root)

`sb` stays a thin polyglot entry file (shell→python bootstrap, `ROOT =
Path(__file__).resolve().parent`, parse args, hand to `sandbox.cli:main`). All logic moves
into the package (imported like today's `sandbox_core.py`):

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
    lifecycle.py · instances_cmd.py · config_setup.py · data.py · wp.py ·
    net.py · debug.py · integ.py · ui_dash.py · uninstall.py
```

**Structure Decision**: 10-group package (clarified). `commands/*` self-register into a
`COMMANDS` registry in `cli.py`, replacing the hand-maintained `handlers = {...}` dict.
Packaging: add `sandbox/` to `package.json` `files`; `bin/sandbox.js` + `make-release.sh`
need no change for the package (they already ship sibling Python files). `.specify/` and
`skills/speckit-*` are added to the release prune + excluded from `package.json` `files`
(FR-009).

## Phase 0 — Research

See [research.md](./research.md): decisions R1–R8 (packaging-safe modularization, app-password
unification, registry-sourced `resolve_instances`, error-on-no-project, `main` surface
inventory, required-arg conversion, staged ordering, web bundle rebuild).

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): Instance, Registry entry, Instance config, Feature module,
  Resolution outcome — fields, validation, transitions.
- [contracts/cli-contract.md](./contracts/cli-contract.md): the instance-resolution contract
  (precedence + error), the `commands/*` `register`/`run` interface, and the
  no-`main`/no-`DEFAULT_INSTANCE` invariant.
- [quickstart.md](./quickstart.md): the per-stage live verification matrix.
- Agent context: `<!-- SPECKIT START -->` block in `CLAUDE.md` references the active plans.

## Phase 1 — Staged implementation

### Stage A — Feature-migrate in place (no behavior change)
Unify app-password on the per-instance key in `sb` (`save_local_app_password` ~L2028, doctor
reader ~L2161) and `server.py` (`_resolve_instance` ~L191); audit `_build_instance_block` for
a complete instance block. Live-verify `sb doctor` + an MCP password-needing tool per instance.

### Stage B — Remove the old model
Drop the `→ DEFAULT_INSTANCE` resolution fallback (error instead); `resolve_instances`
registry-sourced, no `main` synthesis (~L459-465); remove `main` special-cases (delete guards
~L5168/5991/6455, name reservation ~L4105, web UI `render.ts`/`instance.ts` + rebuild bundle);
remove legacy migration (~L1462-1557, call ~L7036); delete `DEFAULT_INSTANCE` from `sb`+
`server.py` and make `instance` a required arg on `compose`/`wpcli`/`save_local_app_password`/
`_active_project_name`. Update docs. Live-verify the matrix + grep-clean (SC-002).

### Stage C — Extract the `sandbox/` package
Move helpers/handlers into `sandbox/core/*` + `sandbox/commands/*`; introduce the `COMMANDS`
registry; reduce `sb` to the thin entry. Update `package.json` `files`. Live-verify identical
behavior from a project dir AND the global symlink; import-smoke; rebuild + run `sb web`.

## Complexity Tracking

No constitution violations — none required.

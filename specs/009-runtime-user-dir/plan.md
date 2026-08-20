# Implementation Plan: Single Swappable Per-User Base for All Sandbox Machine-State

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-runtime-user-dir/spec.md`

## Summary

All Sandbox machine-state (per-instance WP installs, generated compose/orchestration,
snapshots, download cache, seeds, the registry, locks/markers, herd shims, test
suite/tools, proxy/cert material, the shared `wp-cli.phar`, the tools venv) plus all
per-machine config and secrets currently live inside the repo checkout (`<repo>/runtime/`,
`<repo>/sandbox.local.yml`, `<repo>/.env.local`) or split into `~/.config/sandbox/`. This
couples machine-state to the code checkout.

Technical approach: introduce **one base** — `SANDBOX_HOME` (env override; default
`~/sandbox`) — from which `_paths.py` derives `RUNTIME_DIR`, `CONFIG_FILE`, `LOCAL_YML`,
`ENV_LOCAL`. Replace every `ROOT/"runtime"` reference and every `~/.config/sandbox` /
repo-root config-secret reference across the `sandbox/` package **and** the MCP server's
own path copy in `mcp/wp-server/app.py`, so both processes resolve the identical base
(propagated through the registered `.mcp.json` env). Generated compose files switch from
relative `./runtime/...` mounts to **absolute** paths under `RUNTIME_DIR` (regenerated,
not moved). A one-time **idempotent migration** detects old in-repo/`~/.config` state,
moves pure-data artifacts under the base, recreates the baked artifacts (tools venv,
compose, herd shims, Caddyfile), preserves secret perms, and verifies registered
instances boot. A backward-compat fallback reads old locations when the new base is
absent; on conflict the base is authoritative. Docs (`CLAUDE.md`, the config reference,
the constitution's `runtime/registry.json` mentions) update in the same change.

## Technical Context

**Language/Version**: Python 3 (stdlib only — no new dependencies); generated PHP
mu-plugins; Bash entry shim.

**Primary Dependencies**: existing only — the `sb` CLI (`sandbox/` package: `core/` +
`commands/`), the MCP `wp-server` (`mcp/wp-server/app.py` + `tools/`), Docker Compose,
optional Laravel Herd host driver, Caddy proxy. No new libraries.

**Storage**: filesystem under `SANDBOX_HOME` (default `~/sandbox`); WordPress databases
remain in Docker-managed named volumes (NOT under the base — unaffected by relocation).

**Testing**: live-stack verification per constitution IV (`sb status` / `sb doctor` /
`sb ensure` / `wp_cli` against real instances); the project's phpunit harness for any
touched provisioning.

**Target Platform**: developer macOS/Linux workstations (darwin primary).

**Project Type**: CLI tool + companion MCP server (two cooperating processes sharing
on-disk state).

**Performance Goals**: migration of a typical multi-instance setup completes in seconds
to low minutes (dominated by `mv`/venv recreate, not copy); normal command path adds no
measurable overhead (one env read + path joins).

**Constraints**: must not break existing instances (constitution VI parity); no secret
ever written to repo/log/stdout; idempotent (constitution V); single-entry `sb`
(constitution III); registry stays authoritative (constitution II).

**Scale/Scope**: ~94 path references in the `sandbox/` package across 14 modules + ~15 in
`mcp/wp-server/app.py`; ~8 live instances on the author's machine to migrate.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. No instance-model change; the
  registry simply lives under the base. Resolution precedence untouched.
- **II. The Registry Is the Single Source of Truth** — PASS, with a docs update: the
  registry moves to `BASE/runtime/registry.json` and remains authoritative. The
  constitution text naming `runtime/registry.json` is updated in the same change
  (Governance allows amendment with rationale; this is a PATCH-level path clarification).
- **III. Single Entry File, Modular Package (NON-NEGOTIABLE)** — PASS. `sb` stays a single
  entry file; all new logic lands in the `sandbox/` package (a `_paths.py` base resolver +
  a migration module under `commands/`/`core/`). MCP server keeps its own thin copy.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS. Every user story ends
  in a live-boot verification (quickstart.md); migration verifies instances boot.
- **V. Idempotency and Docs-With-Code** — PASS. Migration is safe to re-encounter; docs
  (`CLAUDE.md`, `docs/sandbox-config-reference.md`, constitution) land with the code.
- **VI. Feature Parity Before Removal** — PASS. Old-location reads kept as a fallback
  until the migrated path is proven; the `.gitignore runtime/` entry is dropped only after
  migration is in place.

**Additional constraints**: secrets never echoed (migration moves `.env.local` with
`chmod 600` preserved, never logs contents); WP-touching verification via MCP/`sb`, not
raw docker; spec-kit tooling stays out of the shipped product. No violations → no
Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/009-runtime-user-dir/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (CLI/env/path-resolution contracts)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
sb                                  # single entry (unchanged shape; ROOT still = its dir)
sandbox/
├── core/
│   ├── _paths.py                   # ADD base resolver: SANDBOX_HOME→BASE; RUNTIME_DIR,
│   │                               #   CONFIG_FILE, LOCAL_YML, ENV_LOCAL; rebase every
│   │                               #   ROOT/"runtime" constant (COMPOSE_DIR, WP_DIR,
│   │                               #   SNAPSHOTS_DIR, SEEDS_DIR, DL_CACHE_DIR, TOOLS_VENV,
│   │                               #   PROXY_DIR, _HTTPS_OFFER_MARKER, TEST_SUITE_DIR,
│   │                               #   TEST_TOOLS_DIR, CONFIG_LOCAL) onto RUNTIME_DIR/BASE
│   ├── _config.py                  # config loader: read BASE/config.json (+ old
│   │                               #   ~/.config/sandbox/config.json fallback); LOCAL_YML
│   ├── _docker.py                  # compose render → ABSOLUTE runtime mount paths;
│   │                               #   wp-cli.phar + dl-cache + wp-<inst> abs sources;
│   │                               #   --project-directory points at compose dir
│   ├── _instances.py               # registry path → RUNTIME_DIR/registry.json
│   ├── _provision.py               # wp_dir/herd-shims/test-suite/test-tools → RUNTIME_DIR
│   ├── _domains.py / _bridge.py / _integ.py  # proxy/caddy/cert + bridge paths → BASE
│   └── _paths.py inline builders   # wp_dir(), snapshots_dir(), locks, etc.
├── commands/
│   ├── migrate.py                  # NEW: `sb migrate` (idempotent relocation) + auto-hook
│   ├── lifecycle.py / net.py / wp.py / debug.py / abilities.py / instances_cmd.py
│   │                               # rebase their inline runtime refs
│   └── setup.py (or net/setup)     # write SANDBOX_HOME into .mcp.json env on `sb setup`
└── registry.py                     # (unchanged)
mcp/wp-server/app.py                # mirror base resolver (SANDBOX_ROOT stays = repo for
                                    #   code/skills; ADD SANDBOX_HOME→RUNTIME_DIR for state):
                                    #   COMPOSE_DIR, PROXY_DIR, wp-<inst>, registry.json,
                                    #   herd-shims, TOOLS_VENV_PY, --project-directory
.gitignore                          # drop the now-obsolete `runtime/` entry
CLAUDE.md                           # folder layout + gotchas (#3, #10, #15, #18) rephrased
docs/sandbox-config-reference.md    # base + consolidated config locations
.specify/memory/constitution.md     # registry path mentions → base-relative (PATCH)
```

**Structure Decision**: existing single-entry `sb` + modular `sandbox/` package, plus the
companion MCP server. The change is a cross-cutting path-seam refactor concentrated in
`sandbox/core/_paths.py` (the canonical base + constants) and mirrored in
`mcp/wp-server/app.py`, with a new `sandbox/commands/migrate.py` for the one-time
relocation. No new top-level structure; no new dependencies.

## Complexity Tracking

> No constitution violations — table intentionally omitted.

## Convergence amendment — 2026-08-13 (durable workspace metadata/index)

The existing base relocation plan now includes an additive workspace metadata index. The
index is not a replacement for the project/instance registry and does not alter the
global project identity. Its owner is a workspace repository under
`$SANDBOX_HOME/runtime/workspaces/index.sqlite3`.

### Design and migration gates

1. Create the versioned SQLite schema/repository with WAL, foreign keys, bounded busy
   handling, owner-only permissions, and an opaque `workspace_id` unique for each
   `(project_identity, workspace_label)`.
2. Discover exact-depth legacy metadata under
   `runtime/jobs/workspaces/<legacy-namespace>/<label>/workspace.json` without mutating
   or rewriting it. Correlate only exact job project-root/namespace evidence and a single
   project identity; record adopted, unresolved, conflict, or invalid decisions.
3. Produce a target-bound plan containing the full inventory digest, index generation,
   candidate decisions, and expiry. Hold global/per-workspace locks, rescan before apply,
   reject drift, and commit adoption atomically. An unresolved record keeps the index
   visibly incomplete and never becomes a false empty list.
4. Route workspace create/list/status/reset/destroy through the repository/service. Remote
   controls use project identity and opaque workspace ID; checkout paths are only deploy
   locators. Reset/destroy remain confirmation-gated and busy-locked; startup marks
   interrupted operations indeterminate instead of retrying destructive work.
5. Expose a typed workspace ownership projection to resource monitoring. The resource
   feature consumes that projection and never opens the workspace SQLite file or legacy
   JSON directly. Migration/relocation performs no network cleanup and must preserve
   resource counts.

### Validation gates

- Unit/fixture coverage proves idempotent initialization, exact adoption, unresolved and
  conflict handling, malformed/symlink/oversized rejection, alias collision, missing
  checkout status, plan expiry/digest drift, lock contention, and relocation byte
  preservation.
- CLI/MCP contract coverage proves remote controls do not require a project directory,
  return stable incomplete/busy/ownership errors, and use workspace IDs for destructive
  controls.
- Read-only live evidence records workspace inventory and resource/job/network counts
  before and after an index migration; no reset, destroy, cleanup, or network release is
  part of this gate.

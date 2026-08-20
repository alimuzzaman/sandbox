---
description: "Task list for spec 009 — single swappable per-user base for all machine-state"
---

# Tasks: Single Swappable Per-User Base for All Sandbox Machine-State

**Input**: Design documents from `specs/009-runtime-user-dir/` (plan.md, research.md,
data-model.md, contracts/, quickstart.md)

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with a
**live-stack verification** task (quickstart.md §1–§5). Type-checking/linting are not proof.

**Organization**: Tasks grouped by user story. MVP = US1 (existing setup keeps working
after migration).

## Path Conventions

CLI package: `sandbox/core/*.py`, `sandbox/commands/*.py`. MCP server:
`mcp/wp-server/app.py`. Single-entry `sb` unchanged in shape. Docs: repo root + `docs/` +
`.specify/memory/`.

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Read and inventory the seam: in `sandbox/core/_paths.py` list every constant
  built from `ROOT / "runtime"` (COMPOSE_DIR, WP_DIR, SNAPSHOTS_DIR, SEEDS_DIR,
  DL_CACHE_DIR, TOOLS_VENV, PROXY_DIR, _HTTPS_OFFER_MARKER, TEST_SUITE_DIR, TEST_TOOLS_DIR)
  plus `CONFIG_LOCAL`/`SECRETS_ENV`; and across the package grep the ~94 inline refs
  (`grep -rn 'ROOT.*runtime\|\.config/sandbox\|sandbox\.local\.yml\|\.env\.local' sandbox/`).
  Record the file/line list at the top of this phase as the change checklist (no code yet).
- [x] T002 Confirm registered instances + a baseline serving URL before any change:
  `./sb instances` and note one instance to re-verify after migration (US1 baseline).

## Phase 2: Foundational (BLOCKING — the base resolver every story depends on)

**⚠️ No user story can proceed until the single base resolver exists and is consistent.**

- [x] T003 Add the base resolver to `sandbox/core/_paths.py`: `BASE =
  Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()`;
  `RUNTIME_DIR = BASE/"runtime"`; `CONFIG_FILE = BASE/"config.json"`;
  `LOCAL_YML = BASE/"sandbox.local.yml"`; `ENV_LOCAL = BASE/".env.local"`. Export all via
  `__all__`. Add a tiny `ensure_base()` helper (`mkdir -p BASE, RUNTIME_DIR`).
- [x] T004 Rebase every `ROOT/"runtime"` constant in `sandbox/core/_paths.py` onto
  `RUNTIME_DIR` (COMPOSE_DIR, WP_DIR, SNAPSHOTS_DIR, SEEDS_DIR, DL_CACHE_DIR, TOOLS_VENV,
  PROXY_DIR + PROXY_CERTS_DIR/PROXY_CADDYFILE/PROXY_COMPOSE, _HTTPS_OFFER_MARKER,
  TEST_SUITE_DIR, TEST_TOOLS_DIR) and point `CONFIG_LOCAL`→`LOCAL_YML`,
  `SECRETS_ENV`→`ENV_LOCAL`. Keep `ROOT`-relative code/asset constants unchanged (ENTRY,
  CONFIG/sandbox.yml, MCP_DIR, MCP_VENV, CLI_VENV, TOOLS_DIR, skills/workflows, CLAUDE.md).
- [x] T005 Rebase the inline path builders in `sandbox/core/_paths.py` (e.g. `wp_dir()`,
  `snapshots_dir()`, registry path, any lock/marker/herd-shim joins) to derive from
  `RUNTIME_DIR`; `mkdir -p` targets via `ensure_base()` where they assume existence.
- [x] T006 Mirror the resolver in `mcp/wp-server/app.py`: keep `SANDBOX_ROOT` = repo root
  for CODE assets (skills, workflows, CLAUDE.md, visit.py, `sandbox_core` import), but add
  `SANDBOX_HOME`→`RUNTIME_DIR` and rebase the state paths: `COMPOSE_DIR`, `PROXY_DIR`,
  `_wp_root`/`wp-<inst>`, `registry.json` reads (2 sites), `herd-shims`, `TOOLS_VENV_PY`.
  Use the SAME default `~/sandbox` so CLI and MCP agree (FR-006/SC-005).
- [x] T007 Update the config loader in `sandbox/core/_config.py` to read `CONFIG_FILE`
  (`BASE/config.json`) and `LOCAL_YML`, with a **backward-compat fallback** to the legacy
  locations (`~/.config/sandbox/config.json`, `<repo>/sandbox.local.yml`) when the new
  ones are absent (contract C5). Log fallback once; never log contents.

**Checkpoint**: `./sb status` / `./sb doctor` run without path errors against the existing
(still in-repo) state via the fallback — proves the resolver + fallback before migration.

## Phase 3: User Story 1 — Existing setup keeps working after upgrade (P1) 🎯 MVP

**Goal**: detect in-repo/`~/.config` state, relocate it under the base once, instances
still boot + serve. **Independent test**: quickstart §1.

- [x] T008 [US1] Make generated compose use ABSOLUTE runtime mounts in
  `sandbox/core/_docker.py`: change relative `./runtime/...` volume sources (wp-<inst>,
  dl-cache, wp-cli.phar, any other runtime mounts) to absolute paths under `RUNTIME_DIR`;
  point `compose(..., --project-directory)` at the compose-file dir; preserve gotcha #3
  (plugin sources at same absolute host path). Mirror the `--project-directory` change in
  `mcp/wp-server/app.py` `_compose`.
- [x] T009 [US1] Rebase remaining state paths in the core modules so a booted instance is
  fully base-resident: `sandbox/core/_instances.py` (registry.json read/write),
  `sandbox/core/_provision.py` (wp_dir, test-suite/test-tools, herd-shims, wp-cli.phar,
  mu-plugin writers), `sandbox/core/_domains.py` + `_bridge.py` + `_integ.py`
  (proxy/Caddyfile/certs, bridge, integ paths). Grep-clean: no `ROOT.*runtime` remains in
  these files.
- [x] T010 [US1] Rebase inline refs in the command modules to the new constants:
  `sandbox/commands/lifecycle.py`, `net.py`, `wp.py`, `debug.py`, `abilities.py`,
  `instances_cmd.py` (grep list from T001). Use imported constants, not literals.
- [x] T011 [US1] Recreate-not-move the tools venv: in `ensure_tools_venv`
  (`sandbox/core/_paths.py`/provision) detect a `.venv-tools` whose `bin/python` /
  `pyvenv.cfg` points outside the current base and delete+rebuild it (research D6).
- [x] T012 [US1] Implement `sandbox/commands/migrate.py` — `sb migrate [--dry-run]`
  (contract C3): `ensure_base()`; **move pure-data** (`<repo>/runtime/{wp-*,snapshots,
  dl-cache,seeds,registry.json,test-suite,test-tools,proxy,herd-shims,markers,wp-cli.phar}`
  → `RUNTIME_DIR`; `<repo>/sandbox.local.yml`→`LOCAL_YML`; `<repo>/.env.local`→`ENV_LOCAL`
  preserving mode 600; `~/.config/sandbox/config.json`→`CONFIG_FILE`) via `shutil.move`,
  item-by-item with existence checks so an interrupted run re-runs cleanly; **recreate
  baked** (.venv-tools T011, regenerate compose with absolute mounts, herd shims,
  Caddyfile/proxy compose); **idempotent** (no-op when base populated + repo runtime gone);
  **conflict** (both populated → abort non-zero, base authoritative, no merge). Never print
  secret values. Register in the command registry.
- [x] T013 [US1] Add the lazy auto-hook: when ordinary commands detect legacy state AND an
  empty base, run the same migration once (no manual step) — wire a guarded call in the
  resolution gate (`sandbox/cli.py` dispatch or `ensure`/status entrypoint). Idempotent
  guard so it fires at most once.
- [x] T014 [US1] Live verification per quickstart §1: `./sb migrate` relocates state;
  `ls ~/sandbox/runtime` shows wp-*/compose/registry.json; `git status` shows REPO_CLEAN
  (no runtime/sandbox.local.yml/.env.local); `./sb ensure` + `./sb wp option get siteurl`
  matches baseline; site serves 200; re-run `./sb migrate` is a no-op (SC-001/002/006).

**Checkpoint**: US1 independently shippable — the upgrade path works end-to-end.

## Phase 4: User Story 2 — Fresh clone uses the base, no repo pollution (P1)

**Goal**: a clean clone with an empty base creates instances entirely under the base.
**Independent test**: quickstart §2 (scratch `SANDBOX_HOME`).

- [x] T015 [US2] Ensure the create/boot path provisions only under the base: audit
  `sandbox/commands/lifecycle.py` + `sandbox/core/_provision.py` so first-time `ensure`
  writes wp-<inst>, compose, registry entry, per-instance `sandbox.local.yml` block, and
  mu-plugins under `RUNTIME_DIR`/`LOCAL_YML` — never the repo. Create base dirs on demand.
- [x] T016 [US2] Drop the `runtime/` entry from `.gitignore` (state no longer in tree) and
  remove any other now-obsolete in-repo machine-state ignores; keep ignoring genuinely
  transient repo files. (Docs-with-code, constitution V.)
- [x] T017 [US2] Live verified 2026-07-16 with an isolated `mktemp` base: `./sb ensure` provisioned and served the `sandbox` instance entirely from that base, without writing machine state into the repository. Live verification per quickstart §2: with `SANDBOX_HOME=$(mktemp -d)/sandbox`,
  `./sb ensure` in a project; confirm all generated state under the scratch base and
  `git status` on the repo shows CLEAN (no machine-state); instance serves (SC-003).

## Phase 5: User Story 3 — Relocate the whole base (P2, swappability invariant)

**Goal**: pointing the base at a new dir relocates everything; nothing references the old
base. **Independent test**: quickstart §3.

- [x] T018 [US3] Implement `sb home [<new-dir>]` in `sandbox/commands/migrate.py`
  (contract C3): no-arg prints resolved base + presence; with `<new-dir>` relocates the
  whole base (move pure-data + recreate venv + regenerate compose/herd/caddy for the new
  base) and persists the override hint. (May delegate to the same engine as `sb migrate`.)
- [x] T019 [US3] Live verified 2026-07-16 in an isolated temporary base: `./sb home` migrated a running instance, recreated the tools venv and web tier under the new base, a post-migration `./sb ensure` succeeded, and the generated Compose files contained no old-base references. Live verification per quickstart §3: relocate to a new temp base;
  `./sb ensure` boots instances from it; `grep -rl "$OLD_BASE" $NEW/runtime/compose` →
  NO_STALE_REF; `.venv-tools/bin/python` shebang points into the new base (SC-004).

## Phase 6: Cross-process consistency + secrets verification

- [x] T020 Write `SANDBOX_HOME` into the registered MCP env: on `sb setup` emit
  `"env": {"SANDBOX_HOME": "<resolved base>"}` for the `sandbox` server in the generated
  `.mcp.json` (the setup/`write_claude_mcp_config` path in `sandbox/core/_paths.py` /
  setup command), so the MCP process inherits the exact base (contract C4).
- [x] T021 Live verification per quickstart §4 (CLI↔MCP same base): `./sb home` base equals
  the base the MCP tools resolve (ensure_instance + a path-revealing tool from a Claude
  session). Note gotcha #4 (MCP changes need a Claude Code restart). (SC-005.)
- [x] T022 Live verification per quickstart §5 (secrets safety): `~/sandbox/.env.local`
  mode is 600; `./sb migrate` output contains no secret values (NO_LEAK). (SC-007.)

## Phase 7: Polish & Docs-With-Code (land WITH the code, constitution V)

- [x] T023 [P] Update `CLAUDE.md`: folder-layout section (runtime now under `~/sandbox`),
  the "Where things go" table, and gotchas naming `runtime/...` (#3 absolute mount still
  holds; #10 compose env; #15 dl-cache; #18 wp-cli.phar) to base-relative paths; add a
  short "SANDBOX_HOME base" note.
- [x] T024 [P] Update `docs/sandbox-config-reference.md`: the base (`SANDBOX_HOME`, default
  `~/sandbox`), the consolidated `config.json`/`sandbox.local.yml`/`.env.local`, and the
  user-global layer now at `BASE/config.json` (was `~/.config/sandbox/config.json`).
- [x] T025 [P] Update `.specify/memory/constitution.md` references to
  `runtime/registry.json` → base-relative (`$SANDBOX_HOME/runtime/registry.json`), add a
  Sync Impact Report entry (PATCH bump, rationale: path clarification, principle unchanged).
- [x] T026 [P] Add `sb migrate` / `sb home` + `SANDBOX_HOME` to the MCP `instructions`
  summary and any `./sb` help/README so the relocation path is discoverable.

## Dependencies & Order

- **Setup (T001-T002)** → **Foundational (T003-T007, BLOCKING)** → user stories.
- **US1 (T008-T014)** is the MVP and unblocks US2/US3 (they reuse the migration engine,
  absolute compose, and venv-recreate). T011 precedes T012; T012 precedes T013/T014.
- **US2 (T015-T017)** depends on Foundational + T008/T015 provisioning.
- **US3 (T018-T019)** depends on the T012 migration engine.
- **T020-T022** depend on the resolver (T003/T006) + migration (T012).
- **Polish (T023-T026)** [P] — distinct files; land in the same change as the code they
  document (do not defer).

## Parallel opportunities

- T023/T024/T025/T026 are `[P]` (distinct doc files).
- Within Foundational, T006 (MCP mirror) is largely independent of T004/T005 (CLI consts)
  but both must land before any boot; treat as same-phase.

## MVP scope

**US1 (T001-T014)** is the first shippable increment: existing developers upgrade, state
relocates once, instances keep working. US2 (clean-clone) and US3 (base relocation) build
on the same engine.

## Phase 8: Convergence

- [x] T027 Reject a destination collision before any source is removed, comparing staged/retry artifacts and preserving the authoritative base on conflict per FR-007/FR-013 (partial).
- [x] T028 Serialize relocation with a migration lock and stage pure-data transfers so a failed copy leaves the legacy source usable and an interrupted run can safely resume per US1/AC3 (partial).
- [x] T029 Migrate config-only legacy installations and preserve the restricted `.env.local` mode without treating an absent registry as a no-op per FR-004/FR-007 (missing).
- [x] T030 Explicitly regenerate Compose, Herd shims/proxy routing, and the tooling venv after a transfer so no baked artifact refers to the former base per FR-009/FR-012 (partial).
- [x] T031 Persist `sb home <dir>` as a non-secret base-selection hint, with `SANDBOX_HOME` retaining precedence and the MCP resolver using the same selection per FR-001/FR-006 (missing).
- [x] T032 Invoke one guarded automatic migration for an empty destination on an ordinary first command, while refusing conflicts and leaving read-only/finalization paths safe per FR-007 and C3 (missing).
- [x] T033 Add fixture-driven migration safety coverage and document the persisted selection/automatic migration behavior per SC-004/SC-006/SC-007 (missing).
- [x] T034 Restore the documented `sb migrate --dry-run`/guarded `--force` contract without allowing either flag to merge conflicting state per C3 (missing).

## Phase 9: Convergence — 2026-08-13 (PHP extension cache/provenance)

These tasks are intentionally open. They describe the base/path obligations for the
PHP extension feature; they do not claim that the feature is implemented.

- [x] T035 Add fixture coverage proving identical PHP-extension requirements resolve to
  `$SANDBOX_HOME/runtime/build/php-extensions/<digest>/`, while a changed profile,
  catalog, parent image digest, PHP version, server flavor, platform, or architecture
  yields a new digest and never writes into the checkout.
- [ ] T036 Verify relocation and automatic migration move extension metadata without
  moving database volumes, uploads, snapshots, or project files, then regenerate every
  path-bearing build context under the destination base.
- [ ] T037 Add CLI/MCP parity and redaction tests for extension cache/provenance status;
  output MUST contain no secrets or private source contents and MUST remain safe when
  an entry is missing, stale, or discarded.

## Phase 10: Convergence — durable workspace metadata/index (2026-08-13)

These tasks extend the existing base migration and do not create a new Spec Kit feature
or authorize cleanup, reset, destroy, or network release. Completion marks reflect only
the implementation and evidence actually present in this branch.

- [X] T038 Add the owner-only, versioned SQLite workspace repository at
  `$SANDBOX_HOME/runtime/workspaces/index.sqlite3` with WAL, foreign keys, bounded busy
  handling, schema migrations, opaque IDs, and unique `(project_identity, label)`; add
  initialization/idempotency/rollback/concurrency tests.
- [X] T039 Add exact-depth legacy discovery for
  `runtime/jobs/workspaces/<legacy-namespace>/<label>/workspace.json`; reject symlinks,
  path escapes, oversized/malformed records, and inconsistent namespace/label evidence;
  preserve every source byte-for-byte and test adopted, unresolved, conflict, and invalid
  decisions from exact job/project evidence.
- [X] T040 Add immutable migration plans bound to target identity, complete inventory
  digest, index generation, candidate decisions, and expiry; implement lock-serialized
  rescan/apply and fail-closed `workspace_migration_plan_stale`/
  `workspace_ownership_drift` tests.
- [X] T041 Route workspace lifecycle writes through the repository/service with explicit
  provisioning/ready/resetting/destroying/destroyed/indeterminate states, per-workspace
  busy locks, startup reconciliation, and no automatic destructive retry.
- [ ] T042 Add relocation tests proving the index and pure metadata move safely while
  legacy `workspace.json`, project files, uploads, snapshots, database volumes, and
  network/container/job counts remain unchanged; regenerate only path-bearing locators.
- [X] T043 Add incomplete-index, missing-checkout, alias-collision, and duplicate-owner
  tests so list/status never false-empty and always expose stable safe error codes.
- [X] T044 Add typed workspace-resource binding/projection fixtures and CLI/MCP/resource
  consumer boundary tests proving no caller opens the SQLite index or legacy JSON directly
  and no migration path performs cleanup or network release.
- [ ] T045 Run the isolated migration quickstart and record read-only before/after
  inventory, byte-preservation, relocation, and protected-resource evidence; leave
  unresolved/conflict cases visible for explicit operator review.

# Tasks: First-class WordPress Plugin Check support

**Input**: Design documents from `/specs/013-plugin-check/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-and-mcp.md, quickstart.md

**Tests**: Included — spec FR-017 explicitly requires the parsing/baseline-diff logic be
independently testable without docker, mirroring `tests/test_ci.py`'s existing pattern.

**Organization**: Grouped by user story (spec.md P1-P4), per Constitution-aligned
incremental delivery.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: Config schema + file scaffolding shared by every story.

- [ ] T001 Add `pluginCheck` key to `DEFAULTS` in `sandbox_core.py` (see
  `data-model.md`'s `PluginCheckConfig` table: `slug: None`, `excludeDirectories: []`,
  `versionFile: None`, `baselineFile: "plugin-check-baseline.json"`)
- [ ] T002 [P] Create `sandbox/commands/plugin_check.py` with module docstring
  (mirror `sandbox/commands/ci.py`'s header style — cite `docs/plugin-check.md`)
- [ ] T003 [P] Create `sandbox/core/_plugin_check_report.py` (empty scaffold + module
  docstring, ported content comes in Foundational)
- [ ] T004 [P] Create `mcp/wp-server/tools/plugin_check.py` (empty scaffold + module
  docstring, mirroring `mcp/wp-server/tools/ci.py`'s import style)
- [ ] T005 Add `'plugin_check'` to `sandbox/core/__init__.py`'s `_SUBMODS` list IF
  `_plugin_check_report.py`'s helpers need back-filling into other command modules
  (check whether `sandbox/commands/plugin_check.py` needs bare-name access to report
  functions the way `ci.py` uses back-filled `_core()`-adjacent helpers — if not needed,
  skip this task and import `_plugin_check_report` directly instead)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core logic every user story depends on — parsing, baseline-diff, and the
HTML report renderer. No user-facing command works until this phase is done.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T006 Implement `_parse_findings(output: str) -> list[dict]` in
  `sandbox/commands/plugin_check.py` — parse `wp plugin check --format=json`'s
  `FILE: <path>` + JSON-array-per-file output shape (reference:
  `templately-modular-rewrite/scripts/plugin-check.js`'s `parseFindings`, read-only) into
  a flat list of `{file, type, code, line, column, message}` dicts (see `data-model.md`'s
  `Finding` entity)
- [ ] T007 Implement `_count_by_key(findings: list[dict]) -> dict[str, int]` in
  `sandbox/commands/plugin_check.py` — key is `f"{file}::{code}"`, counting `ERROR`-type
  findings only (see `data-model.md`'s `Baseline` shape; reference: `countByKey`)
- [ ] T008 Implement `_load_baseline(path: Path) -> dict[str, int]` and
  `_write_baseline(path: Path, counts: dict) -> None` in `sandbox/commands/plugin_check.py`
  (missing file → `{}`, matching spec FR-016's "no baseline yet" case — this function
  does NOT decide how to report that; the caller in US1 does)
- [ ] T009 Implement `_diff_against_baseline(current: dict, baseline: dict) -> list[dict]`
  in `sandbox/commands/plugin_check.py` — returns violations
  `[{key, current, baseline, delta}]` only where `current > baseline` for that key (see
  `contracts/cli-and-mcp.md`'s JSON `violations` shape; spec FR-006/FR-007)
- [ ] T010 [P] Port `renderReport` from
  `templately-modular-rewrite/scripts/lib/plugin-check-report.js` (read-only reference)
  to `render_report(findings: list[dict], meta: dict) -> str` in
  `sandbox/core/_plugin_check_report.py` — preserve structure/CSS/dark-light theming/
  client-side search+filter script; DE-BRAND per spec FR-013: masthead title/heading use
  `meta["plugin_slug"]` instead of the hardcoded `"Templately"` string, footer's excluded-
  directories sentence is generated from `meta["exclude_directories"]` instead of a
  hardcoded list; drop the base64 font asset, use the existing system sans-serif stack
  for headline text instead (see `research.md`'s HTML-report decision)
- [ ] T011 [P] Unit tests for T006-T009 in `tests/test_plugin_check.py` (mock-based, no
  docker — mirror `tests/test_ci.py`'s shape): parsing a multi-file
  `FILE:`+JSON-array sample matching the real tool's output shape; `_count_by_key`
  ignoring line/column; `_diff_against_baseline` with baseline-exceeded, baseline-exact,
  and baseline-absent (empty dict) cases
- [ ] T012 [P] Unit test for T010 in `tests/test_plugin_check.py` — `render_report`
  produces valid HTML containing the passed plugin slug and NOT containing the literal
  string `"Templately"` anywhere in its output (regression guard for FR-013)

**Checkpoint**: Foundation ready — parsing, baseline-diff, and report rendering are all
independently proven by unit tests before any CLI/MCP wiring exists.

---

## Phase 3: User Story 1 - Run a baseline-gated compliance check (Priority: P1) 🎯 MVP

**Goal**: `./sb plugin-check` ensures an instance, runs `wp plugin check`, gates on new
findings vs. the committed baseline, and always writes a report.

**Independent Test**: Run `./sb plugin-check` against a project with a matching baseline
(passes) and against one with a simulated new finding (fails, names the finding) — see
`quickstart.md` Runs 1-4.

### Implementation for User Story 1

- [ ] T013 [US1] Implement `_resolve_plugin_check_config(pconf: dict) -> dict` in
  `sandbox/commands/plugin_check.py` — reads `pconf["pluginCheck"]`, `die()`s with a
  clear message naming the missing key if `slug` is unset/empty (spec FR-002/edge case)
- [ ] T014 [US1] Implement `_run_wp_plugin_check(instance, slug, exclude_dirs) -> str` in
  `sandbox/commands/plugin_check.py` — shells `sb wp plugin check <slug>
  --format=json --exclude-directories=<joined list>`; distinguishes "command never ran"
  (empty captured stdout — infrastructure failure, spec FR-010) from "ran, found nothing
  gate-relevant" (real empty JSON output) by checking captured output emptiness, not
  exit code (see `research.md`'s infra-failure decision, ported from `runPluginCheck`)
- [ ] T015 [US1] Implement `_read_version_header(path: Path) -> str` in
  `sandbox/commands/plugin_check.py` for report metadata (default `versionFile`
  resolves to `<slug>.php` per spec FR-004 — implement that default resolution here)
- [ ] T016 [US1] Implement `cmd_plugin_check(cfg, args)` in
  `sandbox/commands/plugin_check.py` wiring T013-T015 + Phase 2's T006-T010 together:
  ensure instance → resolve config → run check → parse → load baseline (report clearly,
  don't gate, if absent — spec FR-016) → diff → write report (always) → print
  human-readable or `--json` output (see `contracts/cli-and-mcp.md`) → exit 0/1
  accordingly; `register({'plugin-check': cmd_plugin_check})` at module bottom
- [ ] T017 [US1] Add `plugin-check` subparser to `sandbox/cli.py` (`--project-dir`
  required like `ci`/`e2e`, `--json` flag; `--update` flag wired but inert until Phase 4)
- [ ] T018 [P] [US1] Unit tests for T013-T016 in `tests/test_plugin_check.py`
  (mock `subprocess.run`, no docker): missing-slug `die()` path; infra-failure path
  (empty captured stdout); full happy-path gate-pass and gate-fail flows producing the
  `contracts/cli-and-mcp.md` JSON shape exactly

**Checkpoint**: `./sb plugin-check` is fully functional and independently testable —
this alone is a viable, valuable increment (MVP).

---

## Phase 4: User Story 2 - Tighten the baseline after fixing findings (Priority: P2)

**Goal**: `./sb plugin-check --update` rewrites the baseline to match current findings.

**Independent Test**: Run `--update` after a simulated fix, confirm the baseline file's
counts drop and a plain subsequent run passes — see `quickstart.md` Run 2.

### Implementation for User Story 2

- [ ] T019 [US2] Wire the `--update` branch in `cmd_plugin_check`
  (`sandbox/commands/plugin_check.py`): when set, skip the baseline-diff gate entirely,
  call `_write_baseline` with current counts, still render the report (spec FR-008), and
  report the new baseline totals instead of a pass/fail violation list
- [ ] T020 [P] [US2] Unit test in `tests/test_plugin_check.py`: `--update` overwrites an
  existing baseline to exactly the current counts (including a case where a count
  DROPS, proving it's a full overwrite, not a merge that only grows)

**Checkpoint**: Both US1 and US2 work independently; a user can now establish AND
tighten a baseline without hand-editing JSON.

---

## Phase 5: User Story 3 - Drive the check from Claude Code / an MCP client (Priority: P2)

**Goal**: `run_plugin_check(project_dir, update=False)` MCP tool returns the same
structured result as the CLI.

**Independent Test**: Call the MCP tool directly against a configured project and
confirm its returned dict matches the CLI's `--json` output shape — see `quickstart.md`
Run 6.

### Implementation for User Story 3

- [ ] T021 [US3] Implement `run_plugin_check(project_dir: str, update: bool = False) ->
  dict` in `mcp/wp-server/tools/plugin_check.py` — thin wrapper shelling to
  `./sb plugin-check --project-dir <dir> --json [--update]`, parsing the last JSON line
  of stdout (mirror `mcp/wp-server/tools/ci.py`'s `ci_run` subprocess/timeout/parse
  pattern exactly, per `contracts/cli-and-mcp.md`)
- [ ] T022 Register the new tools module in `mcp/wp-server/server.py` (add
  `import tools.plugin_check  # noqa: F401` alongside the existing tool imports)
- [ ] T023 [P] [US3] Unit test in `tests/test_plugin_check.py` (or a new
  `tests/test_mcp_plugin_check.py` if that better matches how other MCP tools are
  tested in this repo — check for precedent first): `run_plugin_check` parses a mocked
  subprocess JSON line correctly and surfaces a timeout as `{"ok": false, "error": ...}`

**Checkpoint**: The capability is now available identically from a terminal and from an
agent-driven workflow (spec SC-005).

---

## Phase 6: User Story 4 - Review a human-readable report of findings (Priority: P3)

**Goal**: Validate and polish the report built in Foundational (T010) against the full
spec requirements — multi-project genericness, search/filter, WARNING visibility.

**Independent Test**: Run the check twice against two DIFFERENT plugin configurations
and confirm each report reflects its own project's data with no cross-contamination —
see `quickstart.md` Run 5.

### Implementation for User Story 4

- [ ] T024 [US4] Verify/finish wiring report metadata in `cmd_plugin_check`
  (`sandbox/commands/plugin_check.py`): `plugin_slug`, `plugin_version` (from T015),
  `checker_version` (parsed from the resolved `plugin-check` entry in the project's
  plugin map — see `data-model.md`'s `Report` metadata table), `wp_version`/`php_version`
  (already resolved elsewhere in `sandbox_core`), `baseline_total`, `new_count`
- [ ] T025 [P] [US4] Unit test in `tests/test_plugin_check.py`: render reports for two
  different fake plugin slugs/metadata and assert each report's content differs
  accordingly (no leaked state between renders — regression guard for FR-013's
  "no content hardcoded to one specific plugin")

**Checkpoint**: All four user stories are independently functional and tested.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Docs-with-code (Constitution Principle V) and final live verification
(Constitution Principle IV).

- [ ] T026 [P] Write `docs/plugin-check.md` (mirror `docs/ci-e2e-runner-spec.md`'s
  depth/shape: design recap, config schema, CLI/MCP surface, what's generic vs.
  project-specific, known limitations)
- [ ] T027 [P] Add a short Plugin Check mention to `README.md` (near existing
  test-running documentation, matching how `run_tests`/e2e are already introduced there)
- [ ] T028 Run the full test suite (`.cli-venv/bin/python -m unittest discover -s
  tests`) and confirm it stays green including all new `test_plugin_check.py` cases
- [ ] T029 Execute `quickstart.md` end-to-end against a REAL sandbox instance in a
  scratch project under the session scratchpad (never a real repo) — all 6 runs,
  per Constitution Principle IV; fix anything quickstart surfaces that unit tests
  couldn't catch (mirroring this session's own established pattern of live-verification
  catching real bugs unit tests miss)
- [ ] T030 Clean up all scratch Docker/state created during T029 before considering
  this feature done

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup. BLOCKS all user stories — parsing/
  baseline-diff/report-rendering are shared by every story.
- **User Story 1 (Phase 3)**: Depends on Foundational only. This is the MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational + US1's `cmd_plugin_check`
  existing (adds a branch to it) — not independent of US1's *code*, but independently
  *testable* once US1 exists (spec's own framing: US2 is meaningless without US1).
- **User Story 3 (Phase 5)**: Depends on Foundational + US1 (wraps the CLI command).
  Independent of US2 — works whether or not `--update` has ever been used.
- **User Story 4 (Phase 6)**: Depends on Foundational's T010 (the report renderer
  already exists) — this phase is validation/polish, not fresh construction.
- **Polish (Phase 7)**: Depends on all four user stories being complete.

### Parallel Opportunities

- T002, T003, T004 (Setup) — different files, no dependencies.
- T010, T011, T012 (Foundational) — T010 (report) is independent of T006-T009
  (parsing/diff); T011/T012 (tests) can be written alongside their respective
  implementations once each is drafted.
- T018, T020, T023, T025 — each story's test task can run in parallel with the NEXT
  story's implementation tasks, since stories only depend on Foundational, not on each
  other's tests.
- T026, T027 (docs) — different files, fully parallel with each other and with T028-T030.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1).
2. **STOP and VALIDATE**: run `quickstart.md`'s Runs 1-4 manually against a scratch
   project. This alone (baseline-gated CLI check) is a complete, shippable increment.

### Incremental Delivery

Phase 1+2 → US1 (MVP: CLI gate works) → US2 (`--update` convenience) → US3 (MCP parity)
→ US4 (report polish/validation) → Phase 7 (docs + full live verification). Each story
adds value without breaking the previous one; T029's full quickstart run is the final
gate before considering the feature done, per Constitution Principle IV.

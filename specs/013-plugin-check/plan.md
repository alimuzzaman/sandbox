# Implementation Plan: First-class WordPress Plugin Check support

**Branch**: `013-plugin-check` | **Date**: 2026-07-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-plugin-check/spec.md`

## Summary

Bring the baseline-gated `wp plugin check` pattern — currently a one-off ~700-line Node
script living in a single plugin repo (`templately-modular-rewrite/scripts/plugin-check.js`
+ `scripts/lib/plugin-check-report.js`) — into sandbox itself as a reusable, config-driven
command (`./sb plugin-check`) and MCP tool (`run_plugin_check`), mirroring the existing
`./sb e2e` / `./sb ci` / `run_tests` shapes exactly. The reference implementation's
baseline-diff logic, `wp plugin check --format=json` output parsing, and HTML report
renderer are already ~95% generic (data-driven, no project-specific content beyond a
decorative font choice) — this plan ports that logic to Python, parameterizes the 3
project-specific inputs (plugin slug, exclude-directories, version-header file) via
`sandbox.config.json`, and de-brands the HTML report.

## Exact-release archive extension (runtime-gated)

The source-tree implementation above is complete for its existing contract.
Exact archive checking is a separate, not-yet-authorized extension described in
`archive-mode-design.md`. The pure host preflight, deterministic fixture corpus,
and run-local target/config builder are now implemented as the first three gated
tasks; the CLI remains
disabled until the remaining tasks prove hostile-ZIP limits,
single-descriptor extraction, run-local inherited-state isolation, inactive
static-only target execution, pinned checker provenance, retained owner-only
artifacts, and durable per-plane cleanup/recovery. The first extension is
CLI-only; the current MCP tool remains source-tree-only until parity is tested.

## Technical Context

**Language/Version**: Python 3.9+ (matches the rest of `sandbox/`)

**Primary Dependencies**: none new — `subprocess` (shell to `sb`/`wp`), `json`, stdlib only;
reuses `sandbox.core`'s existing `_ui.py` (`die`/`info`/`ok`), `_config.py`
(`load_project_config`), and the command registry (`sandbox.registry.register`)

**Storage**: two project-local files — a committed `plugin-check-baseline.json` (git-tracked,
same convention as this repo's own `core-module-imports-baseline.json`/`tsc-baseline.json`)
and a generated `tests/test-results/plugin-check-report.html` (git-ignored, ephemeral,
mirrors Playwright's `tests/test-results/` convention)

**Testing**: stdlib `unittest`, mock-based (no docker) for the JSON-parsing and
baseline-diff logic — mirrors `tests/test_ci.py`'s pattern exactly; one live-verification
pass against a real sandbox instance (scratch project, session scratchpad only) before
this is considered done, per Constitution Principle IV

**Target Platform**: wherever `./sb` already runs (macOS/Linux — no new platform surface;
`wp plugin check` itself runs inside the WordPress container, so this is not affected by
the host-platform work already done this session)

**Project Type**: CLI command + MCP tool addition to an existing single-package CLI tool
(matches `sandbox/commands/ci.py` + `mcp/wp-server/tools/ci.py`'s existing shape — not a
new project type)

**Performance Goals**: N/A — this is a local, on-demand developer command; no throughput/
latency target beyond "as fast as the underlying `wp plugin check` invocation itself,"
which this feature does not attempt to optimize

**Constraints**: baseline identity MUST be insensitive to line/column drift (spec FR-007) —
diff on `(file, rule)` pairs only, never line numbers; the report MUST be a single
self-contained file (no external requests) to match the existing convention this repo's
own `check-core-module-imports.js`-style tooling and Playwright's HTML reporter both use

**Scale/Scope**: one plugin checked per project per run (spec Assumptions); typical finding
counts observed in the reference deployment are in the hundreds, not an amount that
stresses any part of this design

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. `./sb plugin-check` takes
  `--project-dir` and resolves through the existing registry exactly like `./sb e2e`/
  `./sb ci`/`./sb run-tests` already do; no new instance-resolution path is introduced.
- **II. The Registry Is the Single Source of Truth** — PASS. This feature reads
  `sandbox.config.json` for its own settings (slug, exclude-dirs, etc.) the same way
  every other per-project config already works; it does not touch the instance registry
  at all beyond the existing `ensure_instance` call every command already makes.
- **III. Single Entry File, Modular Package** — PASS. New logic lives entirely in
  `sandbox/commands/plugin_check.py` (registered via `sandbox.registry.register`, same
  pattern as `ci.py`/`e2e.py`) and `mcp/wp-server/tools/plugin_check.py`. `sb` itself
  gains only an argparse subparser wiring, no structural change.
  *(Filename note: `sandbox/commands/plugin_check.py`, not `plugin-check.py` — Python
  module names can't contain hyphens; this mirrors how the CLI's own hyphenated
  subcommands, e.g. `async-job`, are backed by underscored Python identifiers already.)*
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS, tracked explicitly.
  Unit tests cover parsing/baseline-diff without docker (fast iteration), but this
  feature is NOT considered done until live-verified against a real sandbox instance
  with `plugin-check` installed and active (quickstart.md documents this run).
- **V. Idempotency and Docs-With-Code** — PASS. Every action here is safe to re-run
  (`wp plugin check` is read-only; baseline writes are a full-overwrite of one file, not
  an incremental mutation; the report is regenerated fresh every run, never appended to).
  Docs land with code: this plan/spec/tasks set (spec-kit owns this feature's design
  docs) plus a `docs/plugin-check.md` companion (matching this session's own established
  pattern of a design doc alongside code for CI/e2e and cross-platform work) and a
  README mention, all in the same change as the implementation.
- **VI. Feature Parity Before Removal** — N/A. No old-model code is being removed; this
  is a net-new capability.

**No violations. Complexity Tracking section is not needed.**

## Project Structure

### Documentation (this feature)

```text
specs/013-plugin-check/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   └── cli-and-mcp.md   # Phase 1 output — CLI flag surface + MCP tool signature
└── tasks.md              # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
sandbox/
├── commands/
│   └── plugin_check.py         # NEW — cmd_plugin_check, registered as "plugin-check"
├── core/
│   └── _plugin_check_report.py # NEW — HTML report renderer (ported from
│                                #        templately-modular-rewrite's plugin-check-report.js)
└── cli.py                      # MODIFIED — new `plugin-check` subparser (--update, --json)

mcp/wp-server/tools/
└── plugin_check.py             # NEW — run_plugin_check(project_dir, update=False) MCP tool

tests/
└── test_plugin_check.py        # NEW — mock-based: JSON-parsing + baseline-diff logic,
                                 #       mirrors tests/test_ci.py's shape

docs/
└── plugin-check.md             # NEW — design doc companion (matches this session's
                                 #       docs/ci-e2e-runner-spec.md /
                                 #       docs/cross-platform-support.md pattern)
```

**Structure Decision**: Single-project structure (this IS the single project — sandbox is
one Python package). New code slots into the two already-established extension points
(`sandbox/commands/*.py` for CLI, `mcp/wp-server/tools/*.py` for MCP) exactly the way
`ci.py`/`e2e.py` did earlier this session — no new top-level directories, no new
package boundaries.

## Complexity Tracking

*(Not applicable — no Constitution Check violations.)*

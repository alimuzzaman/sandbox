# Feature Specification: First-class WordPress Plugin Check support

**Feature Branch**: `013-plugin-check`

**Created**: 2026-07-09

**Status**: Implemented (code/docs/unit surface in place), pending post-fix live re-verification

**Input**: User description: "Make WordPress Plugin Check support first-class in the sandbox tool (not project-specific hand-rolled scripts). Bring the baseline-gated `wp plugin check` + HTML report pattern (currently a one-off Node script in a single plugin repo) into sandbox itself as a reusable command/tool any project can opt into via sandbox.config.json, with a CLI command mirroring `./sb e2e`/`./sb ci`, an MCP tool mirroring `run_tests`, and a committed per-project baseline file so only NEW findings fail a run."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a baseline-gated compliance check against my plugin (Priority: P1)

A plugin developer wants to know whether their latest changes introduced any new WordPress.org Plugin Check violations, without being blocked by a large pre-existing backlog of findings that reflect known, accepted trade-offs (e.g. deliberately runtime-guarded use of newer PHP/WP functions to support an older `Requires at least` header).

**Why this priority**: This is the entire point of the feature — everything else (the MCP tool, the HTML report, `--update`) exists to serve this one workflow. Without it there's nothing to demonstrate.

**Independent Test**: Can be fully tested by running `./sb plugin-check` against a project that has opted in via `sandbox.config.json` and a committed baseline file, and observing that the command exits 0 when findings match the baseline and exits non-zero (naming the specific new findings) when a change introduces a new violation.

**Acceptance Scenarios**:

1. **Given** a project configured with a plugin slug and a committed baseline file that matches its current Plugin Check findings, **When** the developer runs `./sb plugin-check`, **Then** the command reports the run passed and exits successfully.
2. **Given** the same project, **When** a code change introduces a genuinely new Plugin Check ERROR-level finding not present in the baseline, **Then** running `./sb plugin-check` fails, and the failure output names the specific new finding(s) (file, rule, count above baseline).
3. **Given** a project with no Plugin Check configuration in `sandbox.config.json`, **When** the developer runs `./sb plugin-check`, **Then** the command explains what configuration is missing and how to add it, rather than failing with a confusing internal error.

---

### User Story 2 - Tighten the baseline after fixing findings (Priority: P2)

A developer has just fixed some of the pre-existing findings and wants the baseline to reflect the new, smaller set going forward, so the fixed findings can't silently regress later without being caught.

**Why this priority**: Without this, the baseline can only ever grow (via manual edits) or requires re-deriving from scratch — the whole "ratchet" value of a baseline gate depends on being able to tighten it easily and safely.

**Independent Test**: Can be fully tested by running `./sb plugin-check --update` after fixing a subset of findings and confirming the committed baseline file's counts drop to match the new, smaller set, while a subsequent plain `./sb plugin-check` run still passes.

**Acceptance Scenarios**:

1. **Given** a project with an existing baseline and some findings that have since been fixed in code, **When** the developer runs `./sb plugin-check --update`, **Then** the baseline file is rewritten to match the current findings exactly (no longer including the fixed ones).
2. **Given** a freshly-updated baseline, **When** the developer immediately re-runs `./sb plugin-check` (no `--update`), **Then** the run passes (the just-written baseline matches current findings by construction).

---

### User Story 3 - Drive the check from Claude Code / an MCP client (Priority: P2)

An agent (or a human working through Claude Code) wants to run the same check and get a structured result back — without parsing terminal output — so it can be used as a step in an automated fix-and-verify loop, the same way `run_tests` already is for PHPUnit.

**Why this priority**: This is what makes the feature usable inside the agent-driven workflows this tool is built around, not just from a human's terminal — but the CLI command (P1) is independently useful and must work standalone first.

**Independent Test**: Can be fully tested by calling the MCP tool directly against a configured project and inspecting the returned structured result for the same information the CLI reports (pass/fail, new findings, counts, report location).

**Acceptance Scenarios**:

1. **Given** a configured project, **When** the MCP tool is called for that project, **Then** it returns a structured result indicating whether the run passed, how many findings of each severity were found, which (if any) are new versus the baseline, and where the generated report lives.
2. **Given** the same project, **When** the MCP tool is called with the update option set, **Then** it performs the same baseline-tightening behavior as the CLI's `--update` flag and reports the new baseline totals.

---

### User Story 4 - Review a human-readable report of findings (Priority: P3)

A developer wants to browse all current findings (including the lower-severity ones that don't gate the run) grouped by file, searchable and filterable, without re-running the tool or digging through raw JSON.

**Why this priority**: Valuable for triage and for understanding WARNING-level findings that are surfaced but never block a run, but the pass/fail gate (P1) delivers the core value on its own even without a polished report.

**Independent Test**: Can be fully tested by running the check once and opening the generated report file, confirming it lists every finding grouped by file with severity, rule, and message, and that search/filter controls narrow the visible set correctly.

**Acceptance Scenarios**:

1. **Given** a completed run with both ERROR and WARNING findings, **When** the developer opens the generated report, **Then** they see every finding grouped by file, with per-file and overall severity counts, and a summary of whether the gate passed.
2. **Given** the report is open, **When** the developer types a filter term or selects a severity filter, **Then** only matching findings remain visible, with an updated visible-count summary.
3. **Given** two different projects with different plugin names, **When** each project's report is generated, **Then** each report reflects that project's own plugin name and configuration — no content is hardcoded to a specific plugin.

### Edge Cases

- What happens when a project's `sandbox.config.json` names a plugin slug that Plugin Check can't find installed/active? The run must fail with a clear message pointing at the missing configuration/plugin, not a confusing raw tool error.
- What happens on the very first run of a project that has never had a baseline file? Running without `--update` must produce a report and a clear message that no baseline exists yet, without treating every current finding as if it were newly regressed noise the developer must parse individually. Running with `--update` establishes the initial baseline.
- What happens if the underlying check tool produces output in an unexpected shape (e.g. the sandbox instance isn't reachable, or the check command itself fails to run at all, as opposed to running and reporting findings)? This must be distinguished from "the check ran and found violations" — the former is an infrastructure failure, the latter is the normal gate outcome.
- What happens to a finding's identity across refactors that shift line numbers but don't change the underlying issue? The baseline must not be sensitive to line/column drift, only to the file and the specific rule violated, so that unrelated line-number shifts elsewhere in a file don't spuriously appear as "new" or "fixed" findings.
- What happens when a project configures an exclude-directories list that covers dev-only/test-only code the shipped plugin doesn't include? Findings from excluded directories must never appear in results, the report, or the baseline.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a command a developer can run against any configured project to execute a Plugin Check run and receive a pass/fail result.
- **FR-002**: The system MUST determine which plugin to check from the project's own existing configuration, with no separate declaration required. **Amended post-implementation, twice**: the original requirement assumed no reasonable default existed and required a project to declare the checked plugin explicitly via a NEW, dedicated setting. In review, this was found to be redundant on two counts: (1) most projects already declare their own plugin's identity elsewhere in the same project configuration for unrelated reasons, and that same value is the correct target in the overwhelming common case; (2) the feature this is ported from has no concept of checking anything other than the project's own plugin at all (it hardcodes its own plugin's identity as a literal, not a configurable value) — Plugin Check is inherently a self-check tool, so a dedicated override setting for checking a *different* plugin was speculative capability nothing in this feature's actual use case calls for. The system MUST use the project's existing plugin-identity declaration (or an equivalent already-available fallback, such as the project directory name) with no separate setting to configure. A clear, actionable error is still required ONLY when even that fallback cannot resolve to something that looks like a valid plugin identifier — never a silent guess.
- **FR-003**: The system MUST let a project optionally declare a list of directories to exclude from the check (to mirror what its own real distribution build excludes), defaulting to no exclusions when not specified.
- **FR-004**: The system MUST let a project optionally declare which file to read a version number from for reporting purposes, defaulting to a same-named PHP file at the project root derived from the configured plugin slug when not specified.
- **FR-005**: The system MUST let a project optionally declare where its committed baseline file lives, defaulting to a conventional filename at the project root when not specified.
- **FR-006**: The system MUST compare each run's findings against the project's committed baseline and fail the run ONLY when a finding's count for a given (file, rule) pair exceeds what the baseline allows for that pair — pre-existing baselined findings must never by themselves cause a failure.
- **FR-007**: The system MUST identify findings for baseline comparison by (file, rule) pair only, not by line or column number, so line-number drift from unrelated changes never produces a spurious pass or fail.
- **FR-008**: The system MUST support an explicit "update" action that rewrites the project's baseline file to exactly match the current findings, tightening (or loosening) it as needed.
- **FR-009**: The system MUST treat only the highest-severity finding tier (errors) as gating; lower-severity findings (warnings) MUST be included in output and the report for visibility but MUST NOT cause a run to fail regardless of baseline state.
- **FR-010**: The system MUST distinguish an infrastructure failure (the check could not be executed at all — e.g. unreachable instance, missing plugin installation) from a normal completed run that found violations, and MUST report these two situations differently so a developer is not left guessing which occurred.
- **FR-011**: The system MUST generate a self-contained, human-readable report of all findings (both gating and non-gating severities) grouped by file, after every run (including `--update` runs).
- **FR-012**: The report MUST support filtering/searching findings by severity and by free-text match against file path, rule, or message, entirely within the report itself (no dependency on re-running the tool to explore results).
- **FR-013**: The report MUST reflect the specific project's own plugin name/slug and configuration in its content — it MUST NOT contain any content hardcoded to one specific plugin or project.
- **FR-014**: The system MUST expose the same capability (run the check, report structured pass/fail + finding details, perform the update action) through the tool-call interface used for driving the sandbox programmatically (mirroring the equivalent existing PHPUnit test-running capability's interface shape), not only through the developer-facing command.
- **FR-015**: The system MUST make the underlying compliance-checking tool (WordPress.org's official Plugin Check) available as an installable dependency the same way other third-party plugins are declared for a project, requiring no separate installation mechanism.
- **FR-016**: On a project's first-ever run with no existing baseline file, the system MUST clearly communicate that no baseline exists yet and how to establish one, rather than silently treating all current findings as newly-introduced violations requiring individual justification.
- **FR-017**: The parsing of check-tool output and the baseline-comparison logic MUST be independently verifiable without requiring a running WordPress instance, so this core logic can be exercised in fast automated tests; end-to-end execution against a real instance remains necessary to validate the feature as a whole.

### Key Entities

- **Plugin Check configuration**: Per-project settings declaring which plugin to check, which directories to exclude, which file holds the version header, and where the baseline file lives. Attached to a project's existing configuration; entirely optional until a project opts in.
- **Finding**: A single reported issue from a check run — which file it's in, its severity tier, which rule it violates, its location within the file, and a human-readable message. Line/column location is informational only and never part of a finding's identity for baseline purposes.
- **Baseline**: A project-owned, version-controlled record of the finding counts (grouped by file and rule) considered already accounted for. Compared against every run's current findings; updated only via an explicit action, never silently by a normal run.
- **Report**: A single self-contained, shareable artifact summarizing one run's findings, gate outcome, and per-project metadata (plugin name/version, check-tool version, environment), regenerated on every run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A project can adopt baseline-gated Plugin Check by adding no more than a few lines of project configuration — no custom scripting required, compared to today's requirement to hand-write and maintain a project-local script.
- **SC-002**: A developer can determine, within a single command invocation, whether their latest change introduced any new Plugin Check violation, without needing to manually cross-reference a findings list against a separate baseline file themselves.
- **SC-003**: Tightening the baseline after fixing findings takes exactly one command invocation, with no manual JSON editing required.
- **SC-004**: The generated report lets a developer locate any specific finding (by file, rule, or message text) in under a few seconds of searching/filtering, without needing to open or grep the raw findings data directly.
- **SC-005**: The same pass/fail/finding-detail information is available identically whether the check is triggered from a terminal command or from an agent/MCP-driven workflow — no information is exclusive to one interface.
- **SC-006**: Re-running the check against unchanged code and an unchanged baseline never produces a different pass/fail outcome, even when unrelated line numbers in the file have shifted (baseline identity is insensitive to location drift).

## Assumptions

- The WordPress.org Plugin Check plugin itself is the check tool being wrapped; this feature does not implement any compliance rules itself, only orchestrates the existing tool, interprets its output, and applies the baseline-gate pattern around it.
- A project opting into this feature is expected to commit its baseline file to version control, the same established convention this repository already uses for other baseline-gated checks — the baseline is project-owned data, not transient output.
- "Static checks" (as opposed to a runtime/bootstrapped check mode) are the intended default scope, consistent with the fastest, most broadly-applicable way to run the underlying tool without extra environment setup; a project needing runtime checks is out of scope for this feature's first version.
- Exactly one plugin is checked per project per run. Checking multiple plugins/slugs from a single project configuration in one invocation is out of scope for this feature's first version.
- The report is a local, on-disk artifact intended for a single developer's own inspection (mirroring how existing local test-report tooling in this ecosystem already behaves) — publishing, hosting, or sharing the report beyond the local machine is out of scope.

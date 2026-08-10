# Phase 0 Research: First-class WordPress Plugin Check support

No `NEEDS CLARIFICATION` markers remained in the plan's Technical Context — this feature
ports an already-working reference implementation rather than exploring open technical
unknowns. This document records the concrete decisions made while translating that
reference into sandbox's own conventions, each with rationale and the alternative
considered.

## Decision: config schema shape

**Decision**: add a nested `pluginCheck` object to `sandbox_core.py`'s `DEFAULTS` dict —
with NO `slug` key at all:

```python
"pluginCheck": {
    "excludeDirectories": [],                   # empty/unset falls back to .distignore
    "versionFile": None,                        # default: guessed "<slug>.php"
    "baselineFile": "plugin-check-baseline.json",
},
```

**Rationale**: matches the existing `"tests": {"suite": "auto"}` precedent exactly — a
nested settings object for one feature area, picked up automatically by the existing
generic `_deep_merge` (project config overrides these per-key, no new merge logic
needed). The object is optional for Plugin Check itself: absence has the same resolved
defaults as an empty object, so projects need not add redundant feature-specific
configuration merely to check their own plugin.

**Revised, twice, after initial implementation** (see spec.md's FR-002 amendment for the
full story): the FIRST design had `pluginCheck.slug` as a required key with no default.
Review caught that this was redundant — most projects (verified:
`templately-modular-rewrite/sandbox.config.json`) already declare a root-level `slug` for
unrelated reasons (spec 010's plugin map), and that's the correct default target in the
overwhelming common case. So `pluginCheck.slug` became an OPTIONAL override, falling back
to `_project_slug(pconf.get("slug"), root.name)` — the exact same resolution legacy
`plugins: ["."]` self-entries already use.

A second review question went further: does the override capability need to exist at
all? Checking the reference implementation settled it — it hardcodes its own plugin's
name as a literal string in the script, with no config concept of checking a *different*
plugin whatsoever. Plugin Check is inherently a self-check tool; "check some other
plugin" isn't a real scenario in the feature this was ported from or in this spec's user
stories. Kept adding it would have been speculative capability for a need nothing
actually has. Removed the override key entirely — `pluginCheck.slug` no longer exists;
the checked plugin is unconditionally the project's own resolved slug.

**Alternative considered**: flat top-level keys (`pluginCheckSlug`,
`pluginCheckExcludeDirectories`, …). Rejected — pollutes the top-level namespace for a
single feature's settings when a nested object already has a working precedent in this
exact schema.

## Decision: baseline file identity and format

**Decision**: JSON object, `{"<file>::<code>": <count>}`, git-tracked at the project root
(default filename `plugin-check-baseline.json`, overridable via `pluginCheck.baselineFile`).

**Rationale**: identical shape to the reference implementation's `countByKey`, which is
already correct per spec FR-007 (identity by file+rule, never line/column — verified by
reading the reference's own `countByKey`/baseline-diff logic, which never touches
`line`/`column` at all). Reusing the exact format means a project migrating from the
Node-script version (like `templately-modular-rewrite`, if it adopts this) can keep its
existing baseline file unchanged.

**Alternative considered**: a richer per-finding baseline (storing message text, exact
locations) — rejected; spec FR-007 explicitly requires line/column-insensitivity, and a
flat count-by-key is the simplest structure that satisfies it exactly (nothing to get
out of sync when unrelated lines shift).

## Decision: distinguishing infrastructure failure from completed-with-findings

**Decision**: mirror the reference implementation's `runPluginCheck` logic — `wp plugin
check`'s own process exit code is NOT a pass/fail signal (findings alone don't make it
non-zero); what matters is whether stdout captured ANY output at all. Empty/no captured
output means the command never ran for real (bad flag, instance down, plugin not
installed) and that MUST be reported as an infrastructure failure (spec FR-010),
distinct from "ran and found violations."

**Rationale**: verified directly against the reference implementation's own comment and
logic (`scripts/plugin-check.js`'s `runPluginCheck` function) — this is empirically
how `wp plugin check` actually behaves, not a guess.

## Decision: HTML report — port near-verbatim, de-branded

**Decision**: `sandbox/core/_plugin_check_report.py` ports the reference's
`renderReport` function (structure, CSS, dark/light theme handling, client-side
search/filter script) to a Python string-template equivalent, with two concrete changes:
(1) the masthead title/heading uses the checked plugin's name from run metadata instead
of the hardcoded string `"Templately"`; (2) the footer's prose listing excluded
directories is generated from the actual configured list instead of a hardcoded sentence
naming specific directories.

**Rationale**: the reference's report is ~95% already generic per this session's earlier
review (data-driven tables, no other plugin-specific content) — a near-verbatim port
preserves a proven, already-polished design (dark/light theming, accessible-enough
contrast, working search/filter) rather than reinventing it, while the two identified
hardcoded spots are exactly what spec FR-013 requires fixing.

**Alternative considered**: a minimal/plain-text report deferring styling entirely.
Rejected — the existing report already meets spec FR-011/FR-012 (grouped-by-file,
searchable/filterable) well; discarding a working, already-verified-in-production design
for a lesser one has no upside.

**Note on the font asset**: the reference report inlines a base64 font
(`big-shoulders-text.woff2`) for the masthead headline typeface. This is decorative only
— sandbox's port uses the system UI sans-serif stack (already used for all non-headline
text in the reference itself) rather than shipping a font binary as a new sandbox
dependency; this changes zero functional behavior (report still self-contained, zero
external requests) and only the headline's specific typeface.

## Decision: output location

**Decision**: `tests/test-results/plugin-check.json`, `tests/test-results/plugin-check.log`,
and `tests/test-results/plugin-check-report.html`, at the project root (matching the
reference implementation's own paths and Playwright's own `tests/test-results/`
convention already established in this ecosystem).

**Rationale**: consistency — a project adopting this feature likely already has
`tests/test-results/` gitignored from its Playwright setup; reusing the same directory
means no new gitignore entry is needed for the common case, and matches user expectation
("test artifacts live here") already set by the reference tool this ports.

## Decision: CLI/MCP command naming

**Decision**: `./sb plugin-check` (CLI), `run_plugin_check` (MCP tool) — Python module
`sandbox/commands/plugin_check.py` (underscore; Python identifiers can't contain
hyphens — same reason `./sb async-job` is backed by a module that doesn't need to match
the hyphenated command string 1:1, since `register()` maps the STRING key, not the
filename).

**Rationale**: `run_plugin_check` mirrors `run_tests`'s naming exactly (verb + noun,
matching the existing PHPUnit-running tool this session's spec repeatedly cites as the
shape to mirror). `plugin-check` (CLI) matches the reference tool's own name
(WordPress.org's plugin is literally named "Plugin Check") rather than inventing a new
term.

# Phase 1 Data Model: First-class WordPress Plugin Check support

## PluginCheckConfig

Optional per-project settings, read from `sandbox.config.json`'s `pluginCheck` key (see
`research.md`'s config-schema decision). The object may be absent entirely; the command
still uses the project's resolved slug and defaults. It is not a runtime object with
behavior — only a plain resolved dict, same as every other `sandbox_core.load_project_config()`
section.

There is no `slug` field in this config — the checked plugin is ALWAYS the project's own
resolved slug (see below), not a separately-configured value.

| Field | Type | Default | Notes |
|---|---|---|---|
| `excludeDirectories` | `list[str]` | Entries from `.distignore`, or `[]` when none exist | Directories excluded from the check, relative to the project root. A non-empty explicit list wins; otherwise the project's `.distignore` is read. Passed verbatim to `wp plugin check --exclude-directories=`. |
| `versionFile` | `str \| None` | `None` | Path (relative to project root) to read a `Version:` header from for report metadata. `None` resolves at run time to `<slug>.php` at the project root (spec FR-004's stated default). |
| `baselineFile` | `str` | `"plugin-check-baseline.json"` | Path (relative to project root) to the committed baseline file (spec FR-005). |

**Which plugin is checked** (spec FR-002, amended twice — see spec.md and research.md for
the full history): always `_project_slug(pconf.get("slug"), root.name)` — the project's
resolved top-level `slug` (already used elsewhere for legacy `plugins: ["."]`
self-entries), falling back to the project directory name. No separate
`pluginCheck.slug` setting exists to override this; Plugin Check is a self-check tool
only, matching the reference implementation's own hardcoded-self-name behavior.

Validation rules:
- An unresolvable slug (the project directory name doesn't look like a valid WP plugin
  slug, and neither does an explicit top-level `slug`) → `die()` with `_project_slug`'s
  own validation message, before any instance/docker work happens (fail fast, cheap).
- A non-empty `excludeDirectories` list takes priority over `.distignore`; an absent or
  empty list falls back to that file. The resulting entries are joined with `,` for the
  `--exclude-directories` flag, exactly as the reference implementation does (no
  per-entry validation beyond being strings — an invalid directory name is the
  underlying tool's problem to report, not this feature's to pre-validate).

## Finding

One reported issue from a single `wp plugin check` run. Ephemeral — never persisted on
its own (only aggregated into a `Baseline` or serialized into a `Report`).

| Field | Type | Notes |
|---|---|---|
| `file` | `str` | Project-relative path (converted from the tool's absolute path). |
| `type` | `"ERROR" \| "WARNING"` | Severity tier. Only `ERROR` findings gate the run (spec FR-009). |
| `code` | `str` | The rule identifier (e.g. `wp_function_not_compatible_with_requires_wp`). |
| `line` | `int` | Informational only — **never** part of a finding's identity for baseline purposes (spec FR-007). |
| `column` | `int` | Informational only, same rule as `line`. |
| `message` | `str` | Human-readable description from the underlying tool. |

Identity key for baseline comparison (spec FR-007): `f"{file}::{code}"` — a plain string,
matching `research.md`'s baseline-format decision. Two findings with the same `(file,
code)` pair are the same finding for gating purposes regardless of `line`/`column`/exact
`message` wording.

## Baseline

A project-owned, git-tracked record of previously-accepted `ERROR`-level finding counts.

Shape: `dict[str, int]`, e.g. `{"includes/class-foo.php::wp_deprecated_function": 3}`.

Lifecycle:
1. **Absent** (first run, no file at `pluginCheck.baselineFile`) — a plain run reports
   this clearly (spec FR-016) rather than treating every current finding as newly
   regressed; `--update` creates it from the current findings.
2. **Present, unchanged findings** — plain run passes; every current count is `<=` its
   baselined count.
3. **Present, new/increased findings** — plain run fails, naming exactly which
   `(file, code)` pairs exceeded their baselined count and by how much (spec FR-006).
4. **Present, `--update` requested** — fully overwritten (not merged) to match the
   CURRENT findings exactly, whether that grows or shrinks the total (spec FR-008).

A baseline is never partially/incrementally mutated by a plain run — only a full
overwrite via the explicit update action changes it (Constitution Principle V,
idempotency: a plain run is side-effect-free on the baseline file).

## Report

A single self-contained HTML file, regenerated on every run (including `--update` runs).

Inputs: the full `Finding` list for this run, plus run metadata:

| Metadata field | Source |
|---|---|
| `pluginSlug` | The project's resolved top-level `slug` or project directory fallback |
| `pluginVersion` | Parsed `Version:` header from `versionFile` (or `"unknown"` if unreadable) |
| `checkerVersion` | Parsed from the `plugin-check` entry in the project's resolved plugin map (mirrors reference's own approach of reading the pinned zip URL/version) |
| `wpVersion` / `phpVersion` | From the resolved project config (already available — same fields `ensure_instance` already reads) |
| `baselineTotal` | Sum of baseline counts before this run |
| `newCount` | Sum of `max(0, current - baselined)` across all `(file, code)` pairs — zero means gate passed |

Not persisted as a separate entity beyond the one HTML file — `Report` is a rendering of
`Finding`s + metadata, not additional stored state.

## Relationships

```
PluginCheckConfig (1) ---- drives ----> one `wp plugin check` invocation
                                              |
                                              v
                                     list[Finding] (this run)
                                              |
                            +-----------------+-----------------+
                            v                                   v
                   compared against Baseline              rendered into Report
                   (gate pass/fail decision)               (always, regardless
                                                             of gate outcome)
```

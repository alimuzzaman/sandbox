# Plugin Check — first-class WordPress.org compliance gate

Author: drafted 2026-07-09 (design-fidelity-diff session). Status: implemented, unit-
tested; live-verification pending (see §6). Spec: `specs/013-plugin-check/`.

## 1. What this is

`./sb plugin-check` runs WordPress.org's official
[Plugin Check](https://wordpress.org/plugins/plugin-check/) tool against a project's
plugin via `wp plugin check`, gated by a committed baseline: only NEW `(file, rule)`
findings beyond that baseline fail the run. Every run also renders a self-contained,
searchable HTML report.

This brings first-class support for a pattern that started as a one-off ~700-line Node
script in a single plugin repo (`templately-modular-rewrite/scripts/plugin-check.js` +
`scripts/lib/plugin-check-report.js`) — that implementation was already ~95% generic
(baseline-diff logic, `wp plugin check --format=json` parsing, and the HTML report
renderer had no project-specific content beyond a decorative font). This module ports
that design to Python, parameterizes the handful of genuinely project-specific inputs,
and de-brands the report.

## 2. Why a baseline, not a flat pass/fail

A real plugin's `Requires at least` header can sit well below several functions it
actually calls (e.g. `str_contains`, `wp_register_ability`) — all deliberately guarded
at runtime (`function_exists`/`class_exists` checks) so they never execute on an
incompatible WP version, but Plugin Check can't see the guard and flags every such call
as an ERROR. A flat "any ERROR fails" gate would be permanently red on a codebase like
that and teach everyone to ignore it. Freezing the CURRENT error set in a baseline and
failing only on NEW findings above it keeps the gate meaningful — it catches an actual
regression without demanding an entire pre-existing backlog get fixed first.

WARNING-level findings (nonce checks on read-only GET params, direct DB queries, etc.)
are shown in the report for visibility but never gate a run — they're a noisier,
lower-severity tier that isn't a useful regression signal at the volume typically seen.

## 3. Config schema

```jsonc
// sandbox.config.json
{
  "pluginCheck": {
    "slug": "my-plugin",                        // REQUIRED — no default is possible
    "excludeDirectories": ["tests", "docs"],     // optional, default: none
    "versionFile": "my-plugin.php",              // optional, default: "<slug>.php"
    "baselineFile": "plugin-check-baseline.json" // optional, this is already the default
  }
}
```

`slug` has no reasonable default — a project with it unset gets a clear `die()` message
naming exactly what's missing, rather than a guess or a silent no-op.

The `plugin-check` WordPress.org plugin itself installs the same way any other plugin
dependency does — it's already in sandbox's own default scaffold plugin list
(`sandbox_core.py`'s `DEFAULTS["plugins"]`), so most projects need no extra step beyond
declaring `pluginCheck.slug`.

## 4. CLI + MCP surface

```
./sb plugin-check --project-dir DIR [--update] [--json]
```

`--update` rewrites the baseline file to match current findings EXACTLY (a full
overwrite, never a merge — so a count can drop as well as rise, e.g. after fixing a
finding). Every run — plain or `--update` — regenerates the HTML report.

Exit codes: `0` on gate pass (or successful `--update`); `1` on gate failure (new
finding(s) beyond baseline) OR an infrastructure failure (instance unreachable, plugin
not installed/active, `pluginCheck.slug` unset).

MCP tool, mirroring `run_tests`'s calling convention:

```python
def run_plugin_check(project_dir: str, update: bool = False) -> dict
```

Both interfaces return the identical JSON shape:

```jsonc
{
  "ok": true,
  "action": "check",              // "check" | "update"
  "plugin_slug": "my-plugin",
  "errors": 198, "warnings": 42,
  "baseline_total": 198, "new_count": 0,
  "violations": [],                // only populated on gate failure
  "report_path": "tests/test-results/plugin-check-report.html",
  "error": null
}
```

## 5. What changed porting from the reference implementation

- **Baseline identity**: `(file, rule)` pairs only — verified the reference's own
  `countByKey` never touches `line`/`column`, confirming line-number drift from
  unrelated refactors was already correctly excluded from the design being ported.
- **Infra-failure detection**: `wp plugin check`'s own exit code is NOT a pass/fail
  signal (findings alone don't make it non-zero) — what matters is whether ANY output
  was captured at all. Ported directly from the reference's `runPluginCheck`.
- **No subprocess-to-subprocess indirection**: the reference (a separate Node script)
  shells to `sb wp plugin check ...` as an external process. This module runs INSIDE
  the same Python process already handling `ensure_instance`, so it calls the existing
  `wpcli()` helper directly — one fewer subprocess hop than the pattern it's based on.
- **Report de-branding** (spec FR-013): the reference's masthead/title hardcode
  `"Templately"`; this port uses the checked plugin's own slug from run metadata. The
  reference's footer hardcodes a sentence naming specific excluded directories; this
  port generates that sentence from the actually-configured `excludeDirectories` list.
  A regression test (`tests/test_plugin_check.py`'s
  `test_report_never_hardcodes_a_specific_plugin_name`) asserts the literal string
  `"Templately"` never appears in a rendered report.
- **Font asset dropped**: the reference inlines a base64 WOFF2 font for the headline
  typeface. This port uses the system UI sans-serif stack instead — purely cosmetic,
  changes nothing functional (the report is still one self-contained file, zero
  external requests either way), and avoids shipping a font binary as a new sandbox
  dependency for a decorative-only difference.

## 6. Known limitation / next step

**Not yet live-verified against a real sandbox instance** (Constitution Principle IV —
unit tests alone aren't proof of done). `specs/013-plugin-check/quickstart.md`
documents the exact verification scenario (6 runs: first-run-no-baseline,
establish-baseline, plain-pass, simulated-regression, report-content-inspection,
MCP-parity) to execute against a scratch project before this feature is considered
fully done. 198 unit tests pass (32 new for this feature), covering every pure-logic
path (parsing, baseline-diff, report rendering, config resolution) without requiring
docker — but the live pipeline (real `wp plugin check` invocation through a real
instance) has not yet been exercised end-to-end.

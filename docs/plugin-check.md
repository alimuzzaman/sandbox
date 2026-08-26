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
  "slug": "my-plugin",                          // fallback/project identity
  "plugins": {"my-plugin": "."},                // canonical self-plugin install key
  "pluginCheck": {
    "excludeDirectories": ["tests", "docs"],     // optional; otherwise use .distignore when present
    "versionFile": "my-plugin.php",              // optional, default: "<slug>.php"
    "baselineFile": "plugin-check-baseline.json" // optional, this is already the default
  }
}
```

There is no `pluginCheck.slug` key — the checked plugin is ALWAYS the project's own
resolved install. When the canonical `plugins` map has a path entry for the project root
(normally `"my-plugin": "."`), its map key is the authoritative WordPress plugin slug.
This preserves the real plugin identity when a disposable review directory uses a unique
top-level `slug` only for isolation. If no self-path map entry exists, Plugin Check falls
back to the top-level `slug`, then the project directory name (the same legacy
`plugins: ["."]` resolution). A directory name/slug that doesn't look like a valid WP
plugin slug still gets a clear `die()` message rather than a silent guess.

The `pluginCheck` object itself is optional. With no object, Plugin Check still runs
using the resolved project slug, `<slug>.php`, and `plugin-check-baseline.json`; it uses
entries from `.distignore` as excludes when that file exists, otherwise no exclusions.
A non-empty `excludeDirectories` list overrides `.distignore`; an absent or empty list
uses the fallback.

The `plugin-check` WordPress.org plugin itself installs the same way any other plugin
dependency does — it's already in sandbox's own default scaffold plugin list
(`sandbox_core.py`'s `DEFAULTS["plugins"]`), so most projects need no extra step at all
beyond having a resolvable `slug` (which most already have, for spec 010's plugin map).

## 4. CLI + MCP surface

```
./sb plugin-check --project-dir DIR [--update] [--json]
```

`--update` rewrites the baseline file to match current findings EXACTLY (a full
overwrite, never a merge — so a count can drop as well as rise, e.g. after fixing a
finding). Every run — plain or `--update` — regenerates the HTML report.

Exit codes: `0` on gate pass (or successful `--update`); `1` on gate failure (new
finding(s) beyond baseline) OR an infrastructure failure (instance unreachable, plugin
not installed/active, or an unresolvable plugin slug).

A first run with no baseline is a successful, non-gating setup state: `ok` is `true`,
`new_count` is `0`, `baseline_exists` is `false`, and `message` tells the caller to run
`--update`. Plugin Check's exact documented `Success: Checks complete. No errors found.`
summary is accepted as the zero-finding result, including when it follows structured
warning findings. By contrast, other malformed or unrecognised output is rejected as
an infrastructure failure; it is never treated as an empty finding set.

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
  "baseline_exists": true,          // false on a first, non-gating run
  "message": null,                 // setup guidance when baseline_exists is false
  "report_path": "tests/test-results/plugin-check-report.html",
  "error": null
}
```

## Exact-release archive mode (runtime-gated)

The proposed archive command is still gated and has no CLI implementation:

```bash
./sb plugin-check --project-dir DIR --archive FILE [--update] [--json]
```

`FILE` is a regular, non-symlink ZIP resolved from the caller project. It is
validated and hashed on the host, extracted outside the checkout, and checked
in a new local disposable Compose instance. The caller instance, database,
registry, descriptor, checkout, and baseline are never reused or overwritten.
The target plugin stays inactive and read-only; only the pinned Plugin Check
dependency is active, and runtime hooks are not run.

The host-only preflight layer is implemented in
`sandbox/plugin_check/archive.py`. It opens one regular input with
`O_NOFOLLOW`, validates every canonical member, hashes the validated manifest,
and can stream extraction through the same open descriptor. The deterministic
stdlib fixture corpus and focused tests live in
`tests/fixtures/plugin_check_archive.py` and
`tests/test_plugin_check_archive.py`. This layer has no lifecycle, registry, or
WordPress side effects.

The run-local target builder is implemented in
`sandbox/plugin_check/target.py`. It writes a fresh owner-only descriptor under
`SANDBOX_HOME/runtime/plugin-check/<run-id>/`, exposes only that review project
through `SANDBOX_PROJECT_ROOTS`, forces local Compose metadata, keeps the archive
plugin inactive/read-only, and activates only the pinned Plugin Check entry.
It does not start the runtime or rewrite the caller baseline; journaled cleanup
and CLI integration remain separate gates.

The journal and cleanup primitives are implemented in
`sandbox/plugin_check/journal.py`. A mode-0600 journal records lifecycle intent
before each phase, and cleanup checks container, network, volume, runtime,
registry, extraction, and retained-report planes independently. Any failed or
unproven plane is retained as `unknown` with `recovery_required: true`; retrying
the same callbacks through `recover_archive_cleanup` is idempotent. These
primitives still do not choose Docker/registry operations themselves.

Finding/baseline/artifact helpers are implemented in
`sandbox/plugin_check/result.py`. They normalize archive findings to the same
relative `(file, rule)` identity as source checks, refuse a baseline update
unless all cleanup planes are proven complete, atomically replace only the
caller baseline, and retain sanitized result/report files below the owner-only
Sandbox report directory (20 runs or 7 days, whichever is smaller).

Archive mode has an explicit threat-model contract in
`specs/013-plugin-check/archive-mode-design.md`: exact size/member/path/expansion
limits, Unicode/case-fold collision checks, traversal and special-file rejection,
same-descriptor hashing/extraction, run-local `SANDBOX_HOME`, an owner-only
cleanup journal, per-plane absence receipt, retained artifacts, and pinned
checker/WP/PHP/Sandbox provenance. Any unknown cleanup plane forces `ok: false`.
MCP archive support is deferred until it can provide identical artifact, cleanup,
and failure evidence. Do not treat the preflight layer as permission to add
`--archive` or close the exact-archive feedback records; isolated runtime,
cleanup/recovery, CLI integration, and disposable live acceptance are still
required.

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
- **No `pluginCheck.slug` key** (design revised twice, post-implementation, via code
  review — see spec.md's FR-002 amendment and research.md for the full history): the
  first version required a dedicated `pluginCheck.slug` setting with no default.
  Review caught that most projects already declare a root-level `slug` for unrelated
  reasons (spec 010's plugin map) — real example, `templately-modular-rewrite`'s own
  `sandbox.config.json` already has `"slug": "templately"` — making a second, separate
  slug setting pure duplication for the common case. A follow-up question went
  further: the reference implementation has no concept of checking a plugin other
  than its own at all (it hardcodes its own plugin's name as a literal). Kept adding
  speculative override capability for a scenario nothing actually needs would have
  been unjustified complexity, so the key was removed entirely — Plugin Check always
  checks the project's own resolved install: a canonical self-path plugin-map key when
  present, then top-level `slug` or the project directory name. This keeps isolated
  review roots from being mistaken for the installed plugin slug.
- **`.distignore` auto-detection** (found via a real live run, see §6): the reference
  hardcoded its own `EXCLUDE_DIRECTORIES` list, commented as mirroring `.distignore` —
  meaning it was already being kept in sync BY HAND with a file that could have been
  read directly. This port reads a project's own `.distignore` (when present) as the
  default `excludeDirectories` when a project hasn't set its own list explicitly,
  removing that manual-sync burden.

## 6. Live verification — real bugs found via a real run

A live run against `templately-modular-rewrite` (a real plugin repo, via the MCP tool
after the sandbox-side merge/restart) surfaced two real gaps unit tests couldn't have
caught, both now fixed:

1. **Path-format mismatch (a real bug in this port, not a design question).** `wp
   plugin check` may report each finding's file as an ABSOLUTE path — and because
   Sandbox bind-mounts plugin source at the SAME absolute path inside the container as
   on the host, that path looks like a normal host path (e.g.
   `/Users/you/project/includes/Admin.php`), not something container-specific. The
   reference implementation converts this to a project-relative path
   (`path.relative(REPO_ROOT, ...)`) before using it as a baseline key; this port's
   initial translation of `parseFindings` DROPPED that conversion. Current Plugin Check
   releases can also emit project-relative paths directly; treating those as absolute
   accidentally resolved them against the Sandbox checkout and produced keys such as
   `../sandbox/includes/Admin.php`. Fixed: `_parse_findings` now converts absolute
   paths with `os.path.relpath` and preserves relative paths as-is.
2. **No `.distignore` awareness** (see §5's design note above) — without it, the SAME
   live run scanned dev-only directories (`.claude/`, `.specify/`, `tests/`, `scripts/`,
   etc.) that never ship, producing a large amount of additional noise on top of bug
   #1. Fixed by the `.distignore` auto-detection described in §5.

Both fixes are unit-tested (`tests/test_plugin_check.py`: `TestParseFindings`'s
absolute- and relative-path cases, plus the `TestReadDistignoreDirectories` and
`TestResolvePluginCheckConfig` fallback-priority tests). A 2026-07-16 live run against
`alims-builder-authoring` confirmed the current relative-path format stays in project
identity space: the gate reported 17 errors and 8 warnings, with no `../sandbox` keys.
That project has no baseline, so the expected first-run result is non-gating setup
guidance; establishing a baseline is a separate, explicitly approved acceptance action. The
`specs/013-plugin-check/quickstart.md` file documents the from-scratch scratch-project
verification scenario.

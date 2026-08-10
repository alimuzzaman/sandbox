# Contracts: CLI + MCP surface

## CLI: `./sb plugin-check`

```
./sb plugin-check --project-dir DIR [--update] [--json]
```

| Flag | Meaning |
|---|---|
| `--project-dir DIR` | The plugin project to check (same convention as `./sb e2e`/`./sb ci`). |
| `--update` | Rewrite `pluginCheck.baselineFile` to match current findings exactly, instead of gating against it. |
| `--json` | Print the result as JSON on stdout (for the MCP server / scripting), instead of human-readable terminal output. |

**Exit codes**: `0` on gate pass (or successful `--update`), `1` on gate failure (new
finding(s) beyond baseline) OR on infrastructure failure (instance unreachable, plugin
not installed/active, or an unresolvable plugin slug — see data-model.md's
`PluginCheckConfig`).

**Human-readable output** (no `--json`): a one-line pass/fail summary, the list of any
new-vs-baseline violations (file, code, current count, baselined count) on failure, and
the path to the generated HTML report.

On a first run with no baseline, the JSON and MCP result are a successful non-gating
setup state (`ok: true`, `baseline_exists: false`, `new_count: 0`) with the `--update`
instruction in `message`. Malformed or unrecognised Plugin Check output is an
infrastructure failure, never an empty successful finding set.

**JSON output shape** (`--json`):

```jsonc
{
  "ok": true,                    // gate passed (or --update succeeded)
  "action": "check",              // "check" | "update"
  "plugin_slug": "templately",
  "errors": 198,                  // total ERROR findings this run
  "warnings": 42,                 // total WARNING findings this run (never gates)
  "baseline_total": 198,           // sum of baselined counts before this run
  "new_count": 0,                  // sum of (current - baselined) above zero; 0 == pass
  "violations": [                  // only when new_count > 0
    {"key": "includes/foo.php::some_rule", "current": 3, "baseline": 1, "delta": 2}
  ],
  "baseline_exists": true,         // false on a successful first run; that run is not gated
  "message": null,                 // first-run setup guidance; errors use `error` instead
  "report_path": "tests/test-results/plugin-check-report.html",
  "error": null                    // set (string) instead of the above on infra failure
}
```

## MCP tool: `run_plugin_check`

```python
def run_plugin_check(project_dir: str, update: bool = False) -> dict
```

Mirrors `run_tests(project_dir)`'s calling convention exactly — no new parameter shapes
introduced beyond what `--update` needs. Returns the SAME JSON shape as the CLI's
`--json` output above (the MCP tool is a thin wrapper shelling to
`./sb plugin-check --json [--update]`, matching how every other MCP tool in this
codebase wraps its CLI counterpart — see `mcp/wp-server/tools/ci.py`/`e2e.py` for the
established pattern: build argv, `subprocess.run` with a timeout, parse the last JSON
line of stdout, return it directly on success or a `{"ok": false, "error": ...}` shape
on timeout/parse failure).

## Optional `sandbox.config.json` overrides: `pluginCheck`

```jsonc
{
  "slug": "my-plugin",                          // top-level — Plugin Check reuses THIS
  "pluginCheck": {
    "excludeDirectories": ["tests", "docs"],     // optional; otherwise use .distignore when present
    "versionFile": "my-plugin.php",              // optional, default "<slug>.php"
    "baselineFile": "plugin-check-baseline.json" // optional, this is already the default
  }
}
```

There is no `pluginCheck.slug` key — the checked plugin is ALWAYS the project's own
resolved slug (the top-level `slug` above, or the project directory name if that's
unset), the same resolution legacy `plugins: ["."]` self-entries already use. A project
whose directory name (and top-level `slug`, if set) doesn't look like a valid WP plugin
slug gets a clear `die()` message — the command must never guess or silently no-op, but
it also never asks for a value the project has typically already declared elsewhere.
The `pluginCheck` object itself is optional: omitting it uses the derived version-file
and baseline-file defaults plus `.distignore` entries (or no exclusions when the file is
absent). A first run with no baseline is a successful non-gating result with
`baseline_exists: false` and setup guidance in `message`, not an `error`.

# Contracts: CLI + MCP surface

## CLI: `./sb plugin-check`

```
./sb plugin-check --project-dir DIR [--update] [--json]
```

The exact-release archive extension is intentionally separate from the source-tree
contract. Its CLI-only shape is implemented; final disposable live acceptance remains
open:

```text
./sb plugin-check --project-dir DIR --archive FILE [--update] [--json]
```

`--archive` is mutually exclusive with source-tree execution. `FILE` resolves
relative to the caller project, must be a regular non-symlink ZIP, and is never
installed into the caller instance. Archive mode uses the typed result and
cleanup/provenance contract in `archive-mode-design.md`; the current MCP tool
does not accept an archive and remains source-tree-only until MCP parity is
implemented and tested. The caller project must declare a pinned
`pluginCheck.archive` provenance block (checker HTTPS ZIP/version/SHA-256, WordPress
version, and PHP version; optional Sandbox revision defaults to the current Git SHA).

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

Archive results add `input_mode`, `archive_sha256`, `archive_slug`, `main_file`,
`checker_provenance`, and a cleanup receipt. A cleanup plane with unknown state
forces `ok: false`; it is never reduced to a passing gate result.

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
  "slug": "my-plugin",                          // fallback/project identity
  "plugins": {"my-plugin": "."},                // canonical self-plugin install key
  "pluginCheck": {
    "excludeDirectories": ["tests", "docs"],     // optional; otherwise use .distignore when present
    "versionFile": "my-plugin.php",              // optional, default "<slug>.php"
    "baselineFile": "plugin-check-baseline.json", // optional, this is already the default
    "archive": {                                  // required only with --archive
      "source": "https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip",
      "version": "2.0.0",
      "sha256": "<64-hex-digest>",
      "wordpressVersion": "6.8.2",
      "phpVersion": "8.3",
      "sandboxRevision": "<40-hex-git-sha>"
    }
  }
}
```

There is no `pluginCheck.slug` key — the checked plugin is ALWAYS the project's own
resolved install. A canonical `plugins` map entry whose path is the project root (normally
`"my-plugin": "."`) supplies the authoritative install slug; this prevents an isolated
review directory's unique top-level `slug` from being passed to WordPress. Without a
self-path entry, use the top-level `slug` above or the project directory name, matching
legacy `plugins: ["."]` resolution. A project with multiple self-path keys gets a clear
ambiguity error; if no candidate looks like a valid WP plugin slug, the command must fail
before instance/docker work rather than guess or silently no-op.
The `pluginCheck` object itself is optional: omitting it uses the derived version-file
and baseline-file defaults plus `.distignore` entries (or no exclusions when the file is
absent). A first run with no baseline is a successful non-gating result with
`baseline_exists: false` and setup guidance in `message`, not an `error`.

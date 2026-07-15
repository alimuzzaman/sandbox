# Quickstart: validating First-class Plugin Check support

Live-verification scenario (Constitution Principle IV — unit tests alone don't count as
done). Run in a **scratch project under the session scratchpad**, never a real repo.

## Setup

```bash
SCRATCH=<session-scratchpad>/plugin-check-proj
HOME_DIR=<session-scratchpad>/plugin-check-home
mkdir -p "$SCRATCH"
cat > "$SCRATCH/sandbox.config.json" << 'EOF'
{
  "slug": "plugin-check",
  "plugins": {"plugin-check": true}
}
EOF
```

There is no `pluginCheck.slug` to set (see data-model.md — that key doesn't exist).
Plugin Check always checks the project's OWN resolved slug: the top-level `slug` above
(`"plugin-check-proj"` here), or the project directory name if that's unset. Either
scaffold a minimal throwaway plugin directory under the scratch project with a single
PHP file bearing a proper plugin header at `plugin-check-proj.php` (matching the
default `versionFile` guess), or set `"slug"` to a real, already-installed plugin's slug
(e.g. `"query-monitor"`, already a sandbox default) to exercise the FULL pipeline
(ensure instance → activate plugin-check → run `wp plugin check` → parse →
baseline-diff → render report) without needing a real Templately-sized codebase.

## Run 1 — first-ever run, no baseline

```bash
export SANDBOX_HOME="$HOME_DIR"
export SANDBOX_PROJECT_ROOTS="$(dirname "$SCRATCH")"
cd /path/to/sandbox
./sb plugin-check --project-dir "$SCRATCH" --json
```

**Expected**: clear message that no baseline exists yet (spec FR-016) — NOT a gate
failure treating every finding as newly regressed. Exit code reflects "no baseline yet,"
distinct from a genuine gate failure.

## Run 2 — establish the baseline

```bash
./sb plugin-check --project-dir "$SCRATCH" --update --json
```

**Expected**: `plugin-check-baseline.json` is written at the project root, exit 0,
reported `new_count: 0`. Inspect the file — its keys are `file::code` pairs with
integer counts, no `line`/`column` present anywhere in it (spec FR-007).

## Run 3 — plain run against the just-written baseline

```bash
./sb plugin-check --project-dir "$SCRATCH" --json
```

**Expected**: exit 0, `ok: true`, `new_count: 0` — the baseline just written matches
current findings by construction.

## Run 4 — simulate a regression

Temporarily edit the checked plugin's code to introduce something Plugin Check flags
that wasn't there before (or manually inflate one count in the baseline file downward
to simulate the same effect without needing a real new violation).

```bash
./sb plugin-check --project-dir "$SCRATCH" --json
```

**Expected**: exit 1, `ok: false`, `violations` names the specific `(file, code)` pair
and shows current vs. baselined counts (spec FR-006).

## Run 5 — report content

Open `tests/test-results/plugin-check-report.html` (from `$SCRATCH`) in a browser.

**Expected**:
- Masthead names the CHECKED PLUGIN's slug/version, not "Templately" or any other
  hardcoded plugin name (spec FR-013).
- WARNING-level findings are visible in the report even though they never gated any of
  the runs above (spec FR-009).
- The search box and severity filter buttons narrow the visible findings list, with an
  updated visible-count line (spec FR-012).
- The report renders correctly with zero network requests (open dev tools, confirm no
  external font/asset fetches — spec's self-contained-report requirement).

## Run 6 — MCP tool parity

Via the MCP server (or a direct Python call into `mcp/wp-server/tools/plugin_check.py`
for a faster check), call `run_plugin_check(project_dir=SCRATCH)` and confirm the
returned dict matches Run 3's `--json` output shape exactly (spec SC-005 — same
information via either interface).

## Cleanup

Tear down the scratch instance and delete `$SCRATCH`/`$HOME_DIR` — never leave scratch
Docker state running after this quickstart completes.

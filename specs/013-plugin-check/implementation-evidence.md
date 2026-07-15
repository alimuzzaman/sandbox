# Implementation Evidence: Plugin Check

## Live quickstart replay — 2026-07-16

- Used an isolated scratch project at `tmp/plugin-check-proj` and an isolated
  `SANDBOX_HOME` at `tmp/plugin-check-home`; no real repository or user-owned
  instance was targeted.
- Run 1 (no baseline): returned exit 1 with the documented `no baseline exists`
  error and reported 128 errors/131 warnings without treating the absence as an
  infrastructure failure.
- Run 2 (`--update`): returned exit 0, wrote the baseline, and reported
  `new_count: 0` with `baseline_total: 128`.
- Run 3 (plain repeat): returned exit 0 with `ok: true` and `new_count: 0`.
- Run 4 (deliberately lowered one baseline count): returned exit 1 with exactly
  one violation, showing current `1`, baseline `0`, and delta `1`.
- Run 5: rendered report inspection confirmed the checked slug is `plugin-check`,
  warning findings are visible, the search/severity controls are present, and
  the report contains no external asset URL. The baseline contained 60 keys and
  no line/column fields.
- Run 6: direct MCP `run_plugin_check(project_dir=...)` returned the same JSON
  shape and values as the CLI regression run (`ok=false`, `baseline_total=127`,
  `new_count=1`, one matching violation).
- The original quickstart fixture slug was corrected from `plugin-check-proj` to
  the installed `plugin-check` slug; this was a documentation/fixture defect,
  not a product runtime defect.

## Cleanup verification

`./sb instance delete plugin-check-proj --yes` removed the exact scratch
containers, named volume, network, generated runtime, registry record, and
machine-local instance override. The scratch project and isolated
`SANDBOX_HOME` were then removed. Post-cleanup checks found no matching Docker
containers or volumes and both paths were absent. No Hermes, Lenzora, remote,
Drive, recovery, commit, or push action occurred.

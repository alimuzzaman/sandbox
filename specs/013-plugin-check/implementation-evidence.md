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

## Exact-release archive acceptance — 2026-08-26

- Focused archive slice: 108 tests passed, including preflight, target
  isolation, child-run ordering, result/artifact handling, CLI dispatch, and
  the per-review port-lane and source/archive finding-key parity regressions.
  The journal/result/CLI recovery slice
  was rerun separately: 28 tests passed.
- Fixed live run used the deterministic `valid` fixture. The observed archive
  SHA was `34de3e374abf0aad08753f3a582be384c845ed7052f9b70dd0d0b2af686c5cfd`,
  the Plugin Check pin was `2.0.0@d744ee1f93866527aedf7d0a73df40bd87018f02cd5465fa39230bf4c2b3a3fa`,
  and the runtime pins were WordPress `6.8.2`, PHP `8.3`, and the exact
  executing Sandbox revision. The target reported inactive; the fixture's
  4 ERROR/2 WARNING findings were retained in the result and report. Exit 0
  was expected because no caller baseline existed yet.
- Two concurrent live runs both exited 0 with different review instances,
  matching archive/member-manifest/provenance identities, retained reports,
  and complete `container`, `network`, `volume`, `runtime`, `registry`,
  `extraction`, and `report` cleanup planes. The per-review port lanes were
  required after the first overlap exposed a shared-default port race.
- Independent postconditions found the caller baseline absent and untouched,
  the fixture side-effect sentinel absent, no review registry/runtime paths,
  and no matching Docker containers, networks, or volumes. The stale exact
  archive-test stacks from earlier debugging were also removed by exact name.
- A full isolated unittest discovery (excluding only the unbounded
  `test_resource_remote` module) executed 3,371 tests and ended with 7
  failures, 3 errors, and 18 skips. The failures are existing architecture,
  CLI-baseline, and runtime-transport drift; this is recorded as an open
  repository gate, not as archive acceptance success. The earlier unbounded
  discovery was stopped after it entered that resource-scan module.

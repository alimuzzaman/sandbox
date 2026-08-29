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
- A live source/archive parity run used the same deterministic fixture and
  caller project. Source and archive both exited 0 with 4 ERROR/2 WARNING
  findings, and the four normalized ERROR identities matched exactly:
  `entrypoint.php::plugin_header_missing_plugin_description`,
  `entrypoint.php::plugin_header_no_license`,
  `readme.txt::no_plugin_readme`, and
  `side-effect-sentinel.php::missing_direct_file_access_protection`. The
  archive result recorded SHA `34de3e374abf0aad08753f3a582be384c845ed7052f9b70dd0d0b2af686c5cfd`,
  member manifest SHA `d83c045a2dd10a824a8e3a8aa12c465268d6931c8da87cb3b8f4e80a55f42588`,
  review instance `plugin-check-r302bd32e2`, receipt `89297c55bd8b957088c6a24b`,
  and complete cleanup. The source caller stack was removed by exact-name
  postconditions after the comparison.
- Independent postconditions found the caller baseline absent and untouched,
  the fixture side-effect sentinel absent, no review registry/runtime paths,
  and no matching Docker containers, networks, or volumes. The stale exact
  archive-test stacks from earlier debugging were also removed by exact name.
- The bounded repository regression was rerun after the archive and baseline
  fixes: 3,298 tests passed with 19 skips. It excluded only the unbounded
  `test_resource_remote` module, the optional `test_server_transport` module
  (its MCP dependencies are not installed in `.cli-venv`), and the full
  `test_cli` module, which was independently rerun at 82 tests and passed.
  The audit-agent pair was independently rerun at 25 tests and passed. The
  previously reported architecture, command-inventory, modularity,
  status-envelope, and remote-ensure fixture failures are therefore cleared.
  T041 remains open only for a bounded remote-resource acceptance and the
  optional MCP transport environment; this is not an archive-path failure.

## Remaining acceptance replay — 2026-08-29

- The optional MCP transport module was run with its dedicated environment and a
  120-second process bound: all 12 tests passed in 0.013 seconds.
- The previously omitted `tests.test_resource_remote` module was run separately
  with a 300-second process bound. Many individual cases reported `ok`, but the
  module emitted very large probe payloads and did not reach a terminal unittest
  summary before the bound expired. This is incomplete evidence, not a pass or
  failure verdict for the module.
- T041 therefore remains open for a bounded terminal remote-resource result. The
  optional MCP transport environment is no longer an open part of the gate.

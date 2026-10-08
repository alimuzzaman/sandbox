# Implementation evidence

Recorded 2026-10-08 against working-tree base `4ece30f` on `latest`.

## Local fake acceptance

The CLI handler and MCP recovery functions were exercised against isolated
synthetic state and the shipped helper through a local fake transport. No live
remote, SSH session, Docker stack, or real credential was used. Results:

| Flow | Result |
|---|---|
| Capture confirmation and CLI/MCP envelope parity | passed |
| Capture replay after declaration drift | refused with `capture_binding_conflict` |
| Status CLI/MCP parity | passed |
| Promote confirmation parity | passed |
| Remote list with Drive unconfigured | passed |
| Retention-plan parity | passed |
| Confirmed retirement through CLI and MCP | passed |

## Local tests

- Quickstart step 1 plus T053 regressions and the architecture gate:
  durable local job `fd846b5419a2205e6a66b88458370946`; 227 tests run, 219
  passed, 8 skipped, none failed (38.552 seconds).
- Quickstart step 2, `tests.test_server_capture_memory`:
  durable local job `812fbf659b5aad62b0f52cafb51ac9e8`; 2 tests passed
  (16.888 seconds). Peak RSS was 36,241,408 bytes for the helper job and
  53,985,280 bytes for promote transfer/publication, below 256 MiB.
- In the broad run, the eight skips were the five `TestMcpServerSplit` tests,
  two `TestMcpDataBoundaries` tests, and one `TestMcpPhpExtensionBoundaries`
  test. They required the absent MCP virtualenv. A separate supported follow-up
  then ran `./sb mcp-install` with a synthetic environment and isolated home,
  pinned `mcp==1.25.0`, and ran the full `tests.test_mcp` module with
  `.cli-venv/bin/python` 3.12.8: 10 tests passed in 5.673 seconds, no skips or
  failures. This included `test_public_tool_schema_snapshot`,
  `test_tools_and_prompts_register`, and all eight previously skipped checks.
  The MCP virtualenv and temporary home created for that run were removed; no
  global client settings or remote state changed. The direct recovery MCP
  functions also ran in fake CLI/MCP parity tests, and the architecture gate
  passed with the 148-tool inventory.
- `git diff --stat origin/latest -- sandbox/commands/hosting.py
  sandbox/delivery/hosting.py sandbox/core/_remote.py tests/test_hosting.py`
  was empty.

## Live proof

T056 remains pending. No live remote verification was run or claimed; it needs
the separate explicit approval described in `quickstart.md`.
